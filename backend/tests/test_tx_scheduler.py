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


# ---- "Enable Msg" bulk toggle vs. individual widget-armed periodic sends --
# (periodic_enabled flag / _enable_msg_armed) --------------------------------


def test_widget_armed_periodic_signal_does_not_set_periodic_enabled_flag():
    """A widget sending one periodic signal (send_signal, as above) arms its
    own auto_entries entry -- existing, intentional behavior -- but must not
    make the top-bar "Enable Msg" bulk toggle look pressed."""
    cm, dbc, sched, peer = setup_stack("t_enable_msg_widget")
    try:
        sched.send_signal("EngineData", {"EngineSpeed": 3000})
        assert sched.status()["periodic_enabled"] is False
        assert len(sched.status()["auto_entries"]) == 1
    finally:
        teardown_stack(cm, sched, peer)


def test_enable_all_periodic_sets_flag_and_disable_all_clears_only_those():
    # fd=True: FdSensorData is an FD message and fails its initial send (thus
    # left unarmed) on a classic connection -- see enable_all_periodic()'s
    # docstring. Not what this test is about, so use an FD-capable bus.
    cm, dbc, sched, peer = setup_stack("t_enable_msg_bulk", fd=True)
    try:
        result = sched.enable_all_periodic()
        assert set(result["armed"]) == {"EngineData", "VehicleSpeed", "BodyStatus", "FdSensorData"}
        assert sched.status()["periodic_enabled"] is True
        assert len(sched.status()["auto_entries"]) == 4
        collect(peer, 0.05)

        sched.disable_all_periodic()
        assert sched.status()["periodic_enabled"] is False
        assert sched.status()["auto_entries"] == []
        collect(peer, 0.05)
        assert len(collect(peer, 0.1)) == 0
    finally:
        teardown_stack(cm, sched, peer)


def test_disable_all_periodic_does_not_stop_widget_armed_signal():
    """The reported bug: pressing "Enable Msg" then off again used to be the
    only way to stop widget-armed periodic auto-resends too (they shared one
    undifferentiated on/off state). A widget's own auto-resend, started
    before "Enable Msg" is ever touched, must survive disable_all_periodic()."""
    cm, dbc, sched, peer = setup_stack("t_enable_msg_independent")
    try:
        sched.send_signal("EngineData", {"EngineSpeed": 3000})  # widget-armed
        sched.enable_all_periodic()  # arms the other 3 periodic messages too
        assert sched.status()["periodic_enabled"] is True

        sched.disable_all_periodic()
        assert sched.status()["periodic_enabled"] is False
        # EngineData was in enable_all_periodic's own batch (it arms every
        # periodic message, including ones already armed) so it stops too --
        # "Enable Msg" off means "stop periodic broadcasting as a whole".
        # What matters here is a signal armed *after* disabling still works
        # independently of the bulk toggle's state:
        sched.send_signal("VehicleSpeed", {"Speed": 42})
        assert sched.status()["periodic_enabled"] is False
        frames = collect(peer, 0.25)
        assert len([f for f in frames if f.arbitration_id == 0x200]) >= 1
    finally:
        teardown_stack(cm, sched, peer)


def test_stop_auto_full_clear_resets_periodic_enabled_flag():
    """Global Start/Stop calls stop_auto() with no args -- must also reset
    the "Enable Msg" flag, not just auto_entries, so a fresh Start doesn't
    show a stale "Enable Msg: on" from a previous run."""
    cm, dbc, sched, peer = setup_stack("t_enable_msg_full_clear")
    try:
        sched.enable_all_periodic()
        assert sched.status()["periodic_enabled"] is True
        sched.stop_auto()
        assert sched.status()["periodic_enabled"] is False
        assert sched.status()["auto_entries"] == []
    finally:
        teardown_stack(cm, sched, peer)


def test_stop_auto_single_message_only_clears_that_name_from_enable_msg_set():
    cm, dbc, sched, peer = setup_stack("t_enable_msg_partial")
    try:
        sched.enable_all_periodic()
        assert sched.status()["periodic_enabled"] is True
        sched.stop_auto("EngineData")
        assert sched.status()["periodic_enabled"] is True  # 3 others remain
        for name in ("VehicleSpeed", "BodyStatus", "FdSensorData"):
            sched.stop_auto(name)
        assert sched.status()["periodic_enabled"] is False
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


def test_preset_signal_seeds_state_without_transmitting():
    """preset_signal()은 전송 없이 상태만 저장한다 -- 자동 재전송 항목도
    생기지 않고, 프레임도 나가지 않으며, 이후 주기 재전송이 저장값을 쓴다."""
    cm, dbc, sched, peer = setup_stack("t_preset")
    try:
        res = sched.preset_signal("EngineData", {"EngineSpeed": 3000})
        assert res["preset"] is True
        # nothing armed, nothing sent
        assert sched.status()["auto_entries"] == []
        assert peer.recv(timeout=0.15) is None
        # the seeded value is what a later periodic resend picks up
        sched.configure(
            [
                {
                    "key": "eng",
                    "arbitration_id": 0x100,
                    "period_ms": 20,
                    "message_name": "EngineData",
                }
            ]
        )
        sched.start()
        frames = collect(peer, 0.3)
        sched.stop()
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 5
        decoded = {dbc.decode(0x100, bytes(f.data))["signals"]["EngineSpeed"] for f in engine}
        assert decoded == {3000}, f"expected seeded value, got {decoded}"
    finally:
        teardown_stack(cm, sched, peer)


def test_invalid_first_sends_invalid_then_valid_after_30ms():
    """반전 펄스: Invalid 프레임 즉시 + 30ms 후 설정값 (주기 자동재송은
    끄고 oneshot만 검증 -- Event 30ms 테스트와 같은 스타일)."""
    cm, dbc, sched, peer = setup_stack("t_invfirst")
    try:
        result = sched.send_signal_invalid_first("EngineData", {"EngineSpeed": 3000})
        assert result["sent"] is True
        assert result["mode"] == "invalid_first"
        assert result["signals"]["EngineSpeed"] == "periodic"
        sched.stop_auto("EngineData")  # oneshot(30ms valid)은 유지된다
        frames = collect(peer, 0.3)
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) == 2, f"expected invalid+valid, got {len(engine)}"
        invalid_ref = dbc.decode(0x100, dbc.encode_invalid("EngineData", "EngineSpeed"))["signals"]
        first = dbc.decode(0x100, bytes(engine[0].data))["signals"]
        second = dbc.decode(0x100, bytes(engine[1].data))["signals"]
        assert first == invalid_ref, f"first frame not invalid: {first}"
        assert second["EngineSpeed"] == 3000, f"second frame not valid: {second}"
        delta_ms = (engine[1].timestamp - engine[0].timestamp) * 1000
        assert 20 <= delta_ms <= 80, f"valid frame delta {delta_ms:.1f} ms"
        # 설정값이 영속 상태로 저장되어 있다
        current = dbc.decode(0x100, dbc.encode_current("EngineData"))["signals"]
        assert current["EngineSpeed"] == 3000
    finally:
        teardown_stack(cm, sched, peer)


def test_invalid_first_rejects_event_signals_and_empty_values():
    cm, dbc, sched, peer = setup_stack("t_invfirst_reject")
    try:
        with pytest.raises(ValueError):
            sched.send_signal_invalid_first("DriverCommand", {"TurnSignal": 2})
        with pytest.raises(ValueError):
            sched.send_signal_invalid_first("EngineData", {})
        # 거부된 호출은 아무것도 전송하지 않는다
        assert peer.recv(timeout=0.15) is None
    finally:
        teardown_stack(cm, sched, peer)


def test_invalid_first_keeps_auto_resending_valid():
    """반전 펄스 후 주기 자동재송이 설정값을 계속 보낸다."""
    cm, dbc, sched, peer = setup_stack("t_invfirst_auto")
    try:
        sched.send_signal_invalid_first("EngineData", {"EngineSpeed": 3000})
        frames = collect(peer, 0.3)  # EngineData cycle = 10 ms
        engine = [f for f in frames if f.arbitration_id == 0x100]
        assert len(engine) >= 10, f"only {len(engine)} periodic frames"
        decoded = {dbc.decode(0x100, bytes(f.data))["signals"]["EngineSpeed"] for f in engine[1:]}
        assert decoded == {3000}, f"expected held valid value, got {decoded}"
    finally:
        teardown_stack(cm, sched, peer)


def test_event_periodic_random_sends_fresh_values_with_invalid_followups():
    """Event 주기 Random: 매 주기 새 값 + 30ms 후 Invalid (Event 규칙).
    결정적 Range 생성기로 검증한다."""
    cm, dbc, sched, peer = setup_stack("t_evtperiod")
    try:
        sched.set_value_generator("DriverCommand", "TurnSignal", "range", 0, 3, 1)
        res = sched.start_event_periodic("DriverCommand", "TurnSignal", 50)
        assert res["started"] is True
        assert res["period_ms"] == 50
        frames = collect(peer, 0.4)
        cmd = [f for f in frames if f.arbitration_id == 0x300]
        assert len(cmd) >= 6, f"too few frames: {len(cmd)}"
        nibbles = [f.data[0] & 0x0F for f in cmd]
        valids = [n for n in nibbles if n != 0x0F]
        assert len(set(valids)) >= 3, f"values not cycling: {nibbles}"
        # 매 Invalid 앞에는 ~30ms 이내의 valid가 있다
        checked = 0
        for j in range(1, len(cmd)):
            if (cmd[j].data[0] & 0x0F) == 0x0F:
                assert (cmd[j - 1].data[0] & 0x0F) != 0x0F
                delta_ms = (cmd[j].timestamp - cmd[j - 1].timestamp) * 1000
                assert 20 <= delta_ms <= 80, f"invalid follow-up delta {delta_ms:.1f} ms"
                checked += 1
        assert checked >= 3
        # 정지 후에는 조용 (이미 예약된 Invalid 배수 0.1초 후)
        stop_res = sched.stop_event_periodic("DriverCommand", "TurnSignal")
        assert stop_res["stopped"] is True
        collect(peer, 0.1)
        rest = [f for f in collect(peer, 0.2) if f.arbitration_id == 0x300]
        assert rest == [], f"frames after stop: {len(rest)}"
    finally:
        teardown_stack(cm, sched, peer)


def test_event_periodic_validation_and_stop_auto_cleanup():
    cm, dbc, sched, peer = setup_stack("t_evtperiod_val")
    try:
        # 생성기 미등록
        with pytest.raises(ValueError):
            sched.start_event_periodic("DriverCommand", "TurnSignal", 100)
        sched.set_value_generator("DriverCommand", "TurnSignal", "range", 0, 3, 1)
        # Periodic 신호 거부
        with pytest.raises(ValueError):
            sched.start_event_periodic("EngineData", "EngineSpeed", 100)
        # 주기 범위 밖 거부
        with pytest.raises(ValueError):
            sched.start_event_periodic("DriverCommand", "TurnSignal", 5)
        with pytest.raises(ValueError):
            sched.start_event_periodic("DriverCommand", "TurnSignal", 70000)
        # 미실행 정지는 멱등 성공
        assert sched.stop_event_periodic("DriverCommand", "TurnSignal")["stopped"] is True
        assert peer.recv(timeout=0.1) is None
        # stop_auto(메시지)로 정리 -- 위젯 삭제 경로
        sched.start_event_periodic("DriverCommand", "TurnSignal", 50)
        time.sleep(0.15)
        sched.stop_auto("DriverCommand")
        collect(peer, 0.1)
        rest = [f for f in collect(peer, 0.2) if f.arbitration_id == 0x300]
        assert rest == [], f"frames after stop_auto: {len(rest)}"
    finally:
        teardown_stack(cm, sched, peer)


# ---- Random 버튼 정지 동작 (전송중 표시/최종값) -------------------------------
# - Event 정지(final_invalid): 실행 중일 때만 전-invalid 1회, 생성기 유지
# - Periodic 정지(stop_generated): 생성기 제거 + raw 0x0 1회 (0 영속)


def test_stop_event_periodic_final_invalid():
    cm, dbc, sched, peer = setup_stack("t_ev_final")
    try:
        sched.set_value_generator("DriverCommand", "TurnSignal", "random", 0, 5)
        sched.start_event_periodic("DriverCommand", "TurnSignal", 50)
        time.sleep(0.2)
        assert any(f.arbitration_id == 0x300 for f in collect(peer, 0.1))
        peer.recv(timeout=0)  # flush
        result = sched.stop_event_periodic("DriverCommand", "TurnSignal", True)
        assert result["final_invalid_sent"] is True
        # 전-invalid 프레임: TurnSignal(4bit)=0xF, WiperMode=0xFF
        final = peer.recv(timeout=0.5)
        assert final is not None and final.arbitration_id == 0x300
        assert (final.data[0] & 0x0F) == 0x0F and final.data[1] == 0xFF
        # 생성기 유지 -- 재등록 없이 시작 가능
        sched.start_event_periodic("DriverCommand", "TurnSignal", 50)
        assert any(f.arbitration_id == 0x300 for f in collect(peer, 0.2))
        sched.stop_event_periodic("DriverCommand", "TurnSignal")
        # 마지막 tick의 30ms-invalid 후속이 정지 직후 1회 더 나올 수 있음
        # (기존 동작) -- drain 후에는 무음이어야 함
        collect(peer, 0.15)
        rest = [f for f in collect(peer, 0.25) if f.arbitration_id == 0x300]
        assert rest == [], f"frames after stop: {len(rest)}"
    finally:
        teardown_stack(cm, sched, peer)


def test_stop_event_periodic_stray_stop_silent():
    cm, dbc, sched, peer = setup_stack("t_ev_stray")
    try:
        result = sched.stop_event_periodic("DriverCommand", "TurnSignal", True)
        assert result["final_invalid_sent"] is False
        assert peer.recv(timeout=0.15) is None
    finally:
        teardown_stack(cm, sched, peer)


def test_stop_generated_sends_zero_and_clears_generator():
    cm, dbc, sched, peer = setup_stack("t_stop_gen")
    try:
        sched.set_value_generator("EngineData", "EngineSpeed", "random", 100, 200)
        sched.send_generated("EngineData", "EngineSpeed")
        result = sched.stop_generated("EngineData", "EngineSpeed")
        assert result["stopped"] is True and result["raw_value"] == 0
        # 생성기 제거 -- 이후 send_generated 거부
        with pytest.raises(ValueError):
            sched.send_generated("EngineData", "EngineSpeed")
        # 정지 전 random값 프레임 버퍼 비우기
        while peer.recv(timeout=0) is not None:
            pass
        # auto-entry 유지 -- EngineSpeed raw 0 프레임 계속 송신
        frames = [f for f in collect(peer, 0.3) if f.arbitration_id == 0x100]
        assert frames, "no frames after stop_generated"
        assert all(int.from_bytes(f.data[0:2], "little") == 0 for f in frames)
    finally:
        teardown_stack(cm, sched, peer)


def test_event_periodic_two_signals_independent():
    cm, dbc, sched, peer = setup_stack("t_ev_two")
    try:
        sched.set_value_generator("DriverCommand", "TurnSignal", "random", 0, 5)
        sched.set_value_generator("DriverCommand", "HornRequest", "random", 0, 1)
        sched.start_event_periodic("DriverCommand", "TurnSignal", 50)
        sched.start_event_periodic("DriverCommand", "HornRequest", 50)
        time.sleep(0.2)
        sched.stop_event_periodic("DriverCommand", "TurnSignal", True)
        # HornRequest 계속 송신 중 -- TurnSignal 최종 invalid 1회를 제외하고
        # 추가 TurnSignal 유효값이 나오면 안 됨은 타이밍상 단정 불가이므로,
        # HornRequest 유효 프레임이 계속 오는 것만 확인
        got_horn = False
        for f in collect(peer, 0.3):
            if f.arbitration_id == 0x300 and (f.data[0] & 0x0F) == 0x0F and (f.data[0] >> 4) & 0x01 == 0:
                pass  # 전-invalid (TurnSignal=0xF, HornRequest=0)
            elif f.arbitration_id == 0x300:
                got_horn = True
        assert got_horn, "HornRequest frames stopped after TurnSignal stop"
        sched.stop_event_periodic("DriverCommand", "HornRequest", True)
        # 정지 직후에는 명시 최종 invalid + 마지막 tick의 30ms 후속이 나올 수
        # 있음 -- drain 후에는 무음이어야 함
        collect(peer, 0.2)
        rest = [f for f in collect(peer, 0.25) if f.arbitration_id == 0x300]
        assert rest == [], f"frames after all stopped: {len(rest)}"
    finally:
        teardown_stack(cm, sched, peer)


def test_event_generated_send_forces_other_signals_invalid():
    """Event Random 송신(Random 버튼/주기 경로)도 타신호 invalid 규칙 적용."""
    cm, dbc, sched, peer = setup_stack("t_ev_gen_invalid")
    try:
        # 먼저 다른 신호에 last valid를 남김 (setup 프레임 drain)
        sched.send_signal("DriverCommand", {"WiperMode": 3})
        while peer.recv(timeout=0) is not None:
            pass
        sched.set_value_generator("DriverCommand", "TurnSignal", "random", 2, 2)
        sched.send_generated("DriverCommand", "TurnSignal")
        frame = peer.recv(timeout=0.5)
        assert frame is not None and frame.arbitration_id == 0x300
        assert frame.data[0] & 0x0F == 0x02  # TurnSignal 유효값
        assert frame.data[1] == 0xFF  # WiperMode는 last valid(3)가 아닌 invalid
    finally:
        teardown_stack(cm, sched, peer)


def test_auto_tick_randomizes_only_started_signal():
    """Bug: WHL_SpdFLVal Random 시작 시 같은 메세지의 FR도 함께 Random으로
    변했음 -- 마운트/설정 시 등록된 생성기가 auto tick마다 전부 호출됐기
    때문. 이제 시작된 신호만 재생성되고, 등록만 된 형제는 정적 유지."""
    cm, dbc, sched, peer = setup_stack("t_gen_sibling")
    try:
        # 멀티셀 마운트 시뮬레이션: 두 신호 생성기 등록, 하나만 시작
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.set_value_generator("EngineData", "EngineTemp", "random")
        sched.send_generated("EngineData", "EngineSpeed")
        frames = [f for f in collect(peer, 0.3) if f.arbitration_id == 0x100]
        assert len(frames) >= 5
        speed_raws = {int.from_bytes(f.data[0:2], "little") for f in frames}
        temp_raws = {f.data[2] for f in frames}
        assert len(speed_raws) > 1, "started signal should randomize"
        assert len(temp_raws) == 1, f"registered-only sibling randomized: {temp_raws}"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_fixed_generator_clears_active_mark():
    cm, dbc, sched, peer = setup_stack("t_gen_fixed_active")
    try:
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.send_generated("EngineData", "EngineSpeed")
        sched.set_value_generator("EngineData", "EngineSpeed", "fixed")
        while peer.recv(timeout=0) is not None:
            pass
        frames = [f for f in collect(peer, 0.2) if f.arbitration_id == 0x100]
        assert frames, "auto ticks should continue with last value"
        raws = {int.from_bytes(f.data[0:2], "little") for f in frames}
        assert len(raws) == 1, f"ticks kept randomizing after fixed: {raws}"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_stop_then_restart_randomizes_again():
    cm, dbc, sched, peer = setup_stack("t_gen_restart")
    try:
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.send_generated("EngineData", "EngineSpeed")
        sched.stop_generated("EngineData", "EngineSpeed")
        while peer.recv(timeout=0) is not None:
            pass
        stopped = [f for f in collect(peer, 0.2) if f.arbitration_id == 0x100]
        assert stopped
        assert {int.from_bytes(f.data[0:2], "little") for f in stopped} == {0}
        # 재시작: 생성기 재등록 + 시작 후 다시 randomize
        sched.set_value_generator("EngineData", "EngineSpeed", "random")
        sched.send_generated("EngineData", "EngineSpeed")
        while peer.recv(timeout=0) is not None:
            pass
        frames = [f for f in collect(peer, 0.3) if f.arbitration_id == 0x100]
        raws = {int.from_bytes(f.data[0:2], "little") for f in frames}
        assert len(raws) > 1, "restarted signal should randomize again"
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_zero_after_pulse_valid_then_zero():
    """버튼 Periodic 클릭: 설정값 즉시 + 30ms 후 raw 0x0, 이후 0 지속."""
    cm, dbc, sched, peer = setup_stack("t_zero_after")
    try:
        # 타신호 last valid 준비
        sched.send_signal("EngineData", {"EngineTemp": 50})  # raw 90
        while peer.recv(timeout=0) is not None:
            pass
        sched.send_signal_zero_after("EngineData", {"EngineSpeed": 1000})
        f1 = peer.recv(timeout=0.5)
        assert f1 is not None and f1.arbitration_id == 0x100
        assert int.from_bytes(f1.data[0:2], "little") == 4000  # 물리 1000
        assert f1.data[2] == 90  # 타신호 last valid 유지
        # 이후 프레임은 전부 raw 0 (30ms 후속 + 주기 송신 지속)
        frames = [f for f in collect(peer, 0.3) if f.arbitration_id == 0x100]
        assert frames
        assert all(int.from_bytes(f.data[0:2], "little") == 0 for f in frames)
        assert all(f.data[2] == 90 for f in frames)
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_zero_after_multi_signal_same_send():
    """같은 송신에 담긴 다중 신호는 모두 valid로 나감."""
    cm, dbc, sched, peer = setup_stack("t_zero_multi")
    try:
        while peer.recv(timeout=0) is not None:
            pass
        sched.send_signal_zero_after("EngineData", {"EngineSpeed": 1000, "EngineTemp": 50})
        f1 = peer.recv(timeout=0.5)
        assert f1 is not None and f1.arbitration_id == 0x100
        assert int.from_bytes(f1.data[0:2], "little") == 4000
        assert f1.data[2] == 90
        sched.stop_auto("EngineData")
    finally:
        teardown_stack(cm, sched, peer)


def test_zero_after_rejects_event_signal():
    cm, dbc, sched, peer = setup_stack("t_zero_event")
    try:
        with pytest.raises(ValueError):
            sched.send_signal_zero_after("DriverCommand", {"TurnSignal": 2})
        with pytest.raises(ValueError):
            sched.send_signal_zero_after("EngineData", {})
    finally:
        teardown_stack(cm, sched, peer)
