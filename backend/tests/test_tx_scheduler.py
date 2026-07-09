import time

import can
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
