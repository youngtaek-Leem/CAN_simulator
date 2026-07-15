import time

import can
import pytest
from conftest import SAMPLES_DIR

from can_manager import CanManager
from dbc_service import DbcService
from tx_scheduler import TxScheduler


def setup_stack(channel: str, fd: bool = False):
    cm = CanManager()
    cm.connect("virtual", channel, receive_own_messages=False, fd=fd)
    dbc = DbcService()
    dbc.load_string((SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8"), "sample.dbc")
    sched = TxScheduler(cm, dbc)
    peer = can.Bus(interface="virtual", channel=channel)
    return cm, dbc, sched, peer


def collect(peer, duration_s: float):
    frames = []
    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        msg = peer.recv(timeout=0.05)
        if msg is not None:
            frames.append(msg)
    return frames


def teardown_stack(cm, sched, peer):
    sched.shutdown()
    peer.shutdown()
    cm.disconnect()


def test_periodic_tx_list():
    cm, dbc, sched, peer = setup_stack("t_periodic")
    try:
        sched.configure(
            [
                {
                    "key": "1",
                    "arbitration_id": 0x111,
                    "period_ms": 20,
                    "data": "0102030405060708",
                }
            ]
        )
        sched.start()
        frames = collect(peer, 0.5)
        sched.stop()
        count = sum(1 for f in frames if f.arbitration_id == 0x111)
        # 0.5 s at 20 ms -> ~25 frames; allow generous OS-jitter tolerance
        assert 15 <= count <= 35, f"unexpected frame count: {count}"

        # after stop, nothing more is sent
        time.sleep(0.1)
        peer.recv(timeout=0)  # flush
        assert len(collect(peer, 0.2)) == 0
    finally:
        teardown_stack(cm, sched, peer)


def test_max_20_entries():
    cm, dbc, sched, peer = setup_stack("t_max20")
    try:
        entries = [
            {"key": str(i), "arbitration_id": i + 1, "period_ms": 100, "data": "00"}
            for i in range(21)
        ]
        try:
            sched.configure(entries)
            assert False, "should have raised"
        except ValueError:
            pass
    finally:
        teardown_stack(cm, sched, peer)


def test_event_signal_sends_invalid_after_30ms():
    cm, dbc, sched, peer = setup_stack("t_event")
    try:
        result = sched.send_signal("DriverCommand", {"TurnSignal": 2})
        assert result["signals"]["TurnSignal"] == "event"
        frames = collect(peer, 0.3)
        cmd_frames = [f for f in frames if f.arbitration_id == 0x300]
        assert len(cmd_frames) == 2, f"expected valid+invalid, got {len(cmd_frames)}"
        valid, invalid = cmd_frames
        assert valid.data[0] & 0x0F == 0x02
        assert invalid.data[0] & 0x0F == 0x0F  # 4-bit invalid value
        delta_ms = (invalid.timestamp - valid.timestamp) * 1000
        assert 20 <= delta_ms <= 80, f"invalid frame delta {delta_ms:.1f} ms"
    finally:
        teardown_stack(cm, sched, peer)


def test_periodic_signal_keeps_sending():
    cm, dbc, sched, peer = setup_stack("t_auto")
    try:
        result = sched.send_signal("EngineData", {"EngineSpeed": 3000})
        assert result["signals"]["EngineSpeed"] == "periodic"
        frames = collect(peer, 0.3)  # EngineData cycle = 10 ms
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 10, f"only {len(engine)} periodic frames"
        # value is held across cycles
        db_decoded = dbc.decode(0x100, bytes(engine[-1].data))
        assert db_decoded["signals"]["EngineSpeed"] == 3000

        sched.stop_auto("EngineData")
        time.sleep(0.05)
        collect(peer, 0.1)
        assert len(collect(peer, 0.15)) == 0
    finally:
        teardown_stack(cm, sched, peer)


def test_fd_signal_sends_32_byte_fd_frame():
    cm, dbc, sched, peer = setup_stack("t_fd_signal", fd=True)
    try:
        result = sched.send_signal("FdSensorData", {"Pressure": 1013.2})
        assert result["signals"]["Pressure"] == "periodic"
        frames = collect(peer, 0.3)  # FdSensorData cycle = 20 ms
        fd_frames = [f for f in frames if f.arbitration_id == 0x500]
        assert len(fd_frames) >= 5
        assert all(len(f.data) == 32 for f in fd_frames)
        assert all(f.is_fd for f in fd_frames)
        assert all(f.bitrate_switch for f in fd_frames)
        sched.stop_auto("FdSensorData")
    finally:
        teardown_stack(cm, sched, peer)


def test_generator_random_stays_within_bit_range():
    cm, dbc, sched, peer = setup_stack("t_gen_random")
    try:
        # TurnSignal is 4 bits unsigned -> raw range 0..15
        sched.set_value_generator("DriverCommand", "TurnSignal", "random")
        seen = set()
        for _ in range(15):
            sched.send_generated("DriverCommand", "TurnSignal")
            seen.add(dbc._signal_state["DriverCommand"]["TurnSignal"])
        assert seen and all(0 <= v <= 15 for v in seen)
    finally:
        teardown_stack(cm, sched, peer)


def test_generator_random_respects_range():
    cm, dbc, sched, peer = setup_stack("t_gen_random_range")
    try:
        # TurnSignal is 4 bits (0..15) -- narrow the random draw to 2..5
        sched.set_value_generator("DriverCommand", "TurnSignal", "random", range_min=2, range_max=5)
        seen = set()
        for _ in range(30):
            sched.send_generated("DriverCommand", "TurnSignal")
            seen.add(dbc._signal_state["DriverCommand"]["TurnSignal"])
        assert seen and all(2 <= v <= 5 for v in seen)
    finally:
        teardown_stack(cm, sched, peer)


def test_generator_range_cycles_and_wraps():
    cm, dbc, sched, peer = setup_stack("t_gen_range")
    try:
        sched.set_value_generator("DriverCommand", "TurnSignal", "range", range_min=2, range_max=5, step=2)
        values = []
        for _ in range(4):
            sched.send_generated("DriverCommand", "TurnSignal")
            values.append(dbc._signal_state["DriverCommand"]["TurnSignal"])
        assert values == [2, 4, 2, 4]
    finally:
        teardown_stack(cm, sched, peer)


def test_generator_range_clamps_to_bit_bounds():
    cm, dbc, sched, peer = setup_stack("t_gen_clamp")
    try:
        # requested range far exceeds TurnSignal's 4-bit (0..15) range
        sched.set_value_generator("DriverCommand", "TurnSignal", "range", range_min=-100, range_max=1000, step=1)
        for _ in range(20):
            sched.send_generated("DriverCommand", "TurnSignal")
            assert 0 <= dbc._signal_state["DriverCommand"]["TurnSignal"] <= 15
    finally:
        teardown_stack(cm, sched, peer)


def test_periodic_generator_changes_value_every_tick():
    cm, dbc, sched, peer = setup_stack("t_gen_periodic")
    try:
        # EngineSpeed is periodic (EngineData cycle = 10ms), 16-bit raw range
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.send_generated("EngineData", "EngineSpeed")  # arms the auto entry
        frames = collect(peer, 0.3)
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 5
        raws = {int.from_bytes(f.data[0:2], "little") for f in engine}
        assert len(raws) > 1, "periodic frames should show changing (random) values"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_event_generator_does_not_auto_resend():
    cm, dbc, sched, peer = setup_stack("t_gen_event")
    try:
        # DriverCommand (TurnSignal's message) is event-typed -- a registered
        # generator must not create periodic auto-resend for it.
        sched.set_value_generator("DriverCommand", "TurnSignal", "random")
        sched.send_generated("DriverCommand", "TurnSignal")
        frames = collect(peer, 0.3)
        cmd_frames = [f for f in frames if f.arbitration_id == 0x300]
        assert len(cmd_frames) == 2, f"expected exactly valid+invalid, got {len(cmd_frames)}"
    finally:
        teardown_stack(cm, sched, peer)


def test_send_generated_without_registered_generator_raises():
    cm, dbc, sched, peer = setup_stack("t_gen_missing")
    try:
        with pytest.raises(ValueError):
            sched.send_generated("DriverCommand", "TurnSignal")
    finally:
        teardown_stack(cm, sched, peer)


def test_set_value_generator_fixed_clears_it():
    cm, dbc, sched, peer = setup_stack("t_gen_clear")
    try:
        sched.set_value_generator("DriverCommand", "TurnSignal", "random")
        sched.send_generated("DriverCommand", "TurnSignal")  # no raise
        sched.set_value_generator("DriverCommand", "TurnSignal", "fixed")
        with pytest.raises(ValueError):
            sched.send_generated("DriverCommand", "TurnSignal")
    finally:
        teardown_stack(cm, sched, peer)


def test_send_invalid_persists_on_periodic_ticks():
    cm, dbc, sched, peer = setup_stack("t_invalid_periodic")
    try:
        # EngineSpeed periodic, 16-bit unsigned -> invalid raw = 0xFFFF
        sched.send_signal("EngineData", {"EngineSpeed": 3000})
        collect(peer, 0.05)  # drain send_signal's own immediate frame
        result = sched.send_invalid("EngineData", "EngineSpeed")
        assert result == {"sent": True, "raw_value": 0xFFFF, "send_type": "periodic"}
        frames = collect(peer, 0.3)
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 5
        raws = {int.from_bytes(f.data[0:2], "little") for f in engine}
        assert raws == {0xFFFF}, f"expected every periodic tick to stay invalid, got {raws}"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_send_invalid_clears_registered_generator():
    cm, dbc, sched, peer = setup_stack("t_invalid_clears_gen")
    try:
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.send_generated("EngineData", "EngineSpeed")
        collect(peer, 0.05)  # drain send_generated's own immediate frame
        sched.send_invalid("EngineData", "EngineSpeed")
        frames = collect(peer, 0.3)
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 5
        raws = {int.from_bytes(f.data[0:2], "little") for f in engine}
        assert raws == {0xFFFF}, f"generator should not overwrite the invalid value, got {raws}"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_send_signal_after_invalid_restores_valid_value():
    cm, dbc, sched, peer = setup_stack("t_invalid_restore")
    try:
        sched.send_invalid("EngineData", "EngineSpeed")
        collect(peer, 0.1)
        sched.send_signal("EngineData", {"EngineSpeed": 1500})
        frames = collect(peer, 0.3)
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 5
        decoded = {dbc.decode(0x100, bytes(f.data))["signals"]["EngineSpeed"] for f in engine}
        assert decoded == {1500}, f"expected restored value to stick, got {decoded}"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_fd_tx_row_configurable_flags():
    cm, dbc, sched, peer = setup_stack("t_fd_row", fd=True)
    try:
        sched.configure(
            [
                {
                    "key": "fd1",
                    "arbitration_id": 0x777,
                    "period_ms": 20,
                    "data": "00" * 24,
                    "is_fd": True,
                    "bitrate_switch": True,
                }
            ]
        )
        sched.start()
        frames = collect(peer, 0.3)
        sched.stop()
        matching = [f for f in frames if f.arbitration_id == 0x777]
        assert len(matching) >= 5
        assert all(len(f.data) == 24 and f.is_fd and f.bitrate_switch for f in matching)
    finally:
        teardown_stack(cm, sched, peer)
