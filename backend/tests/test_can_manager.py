import time

import can

import can_manager
from can_manager import CanManager


def test_virtual_roundtrip():
    cm = CanManager()
    cm.connect("virtual", "t_roundtrip")
    peer = can.Bus(interface="virtual", channel="t_roundtrip")
    try:
        cm.send(0x123, b"\x01\x02\x03")
        msg = peer.recv(timeout=1.0)
        assert msg is not None
        assert msg.arbitration_id == 0x123
        assert bytes(msg.data) == b"\x01\x02\x03"

        peer.send(can.Message(arbitration_id=0x321, data=b"\x05", is_extended_id=False))
        time.sleep(0.3)
        ids = [m.arbitration_id for m in cm.drain_rx()]
        assert 0x123 in ids  # own message (receive_own_messages=True)
        assert 0x321 in ids
        assert cm.counters["tx"] == 1
    finally:
        peer.shutdown()
        cm.disconnect()


def test_unsupported_interface():
    cm = CanManager()
    try:
        cm.connect("bogus", "x")
        assert False, "should have raised"
    except ValueError:
        pass


def test_send_without_connection():
    cm = CanManager()
    try:
        cm.send(0x1, b"")
        assert False, "should have raised"
    except RuntimeError:
        pass


def test_fd_roundtrip_32_bytes():
    cm = CanManager()
    status = cm.connect("virtual", "t_fd", fd=True, data_bitrate=2_000_000)
    assert status["config"]["fd"] is True
    assert status["config"]["data_bitrate"] == 2_000_000
    peer = can.Bus(interface="virtual", channel="t_fd")
    try:
        payload = bytes(range(32))
        cm.send(0x456, payload, is_fd=True, bitrate_switch=True)
        msg = peer.recv(timeout=1.0)
        assert msg is not None
        assert len(msg.data) == 32
        assert msg.is_fd is True
        assert msg.bitrate_switch is True
    finally:
        peer.shutdown()
        cm.disconnect()


def test_classic_bus_rejects_oversized_payload():
    cm = CanManager()
    cm.connect("virtual", "t_classic_guard", fd=False)
    try:
        cm.send(0x1, bytes(20))
        assert False, "should have raised"
    except ValueError as exc:
        assert "CAN-FD" in str(exc)
    finally:
        cm.disconnect()


def test_virtual_connection_reports_epoch_aligned_timestamps():
    """virtual (and Vector) timestamps are always wall-clock epoch seconds,
    unlike PCAN without the `uptime` package -- widgets that compare CAN
    timestamps against another epoch-based timeline (e.g. the CAN-audio
    latency widget) rely on this flag."""
    cm = CanManager()
    status = cm.connect("virtual", "t_epoch_aligned")
    try:
        assert status["config"]["epoch_aligned"] is True
    finally:
        cm.disconnect()


def test_classic_bus_forces_classic_frame_even_if_fd_requested():
    """HS-CAN(classic) 연결에서는 행/DBC의 FD 체크가 켜져 있어도 실제로는
    classic 프레임으로 나가야 한다 -- 연결 설정이 우선한다."""
    cm = CanManager()
    cm.connect("virtual", "t_classic_forced", fd=False)
    peer = can.Bus(interface="virtual", channel="t_classic_forced")
    try:
        cm.send(0x123, b"\x01\x02\x03", is_fd=True, bitrate_switch=True)
        msg = peer.recv(timeout=1.0)
        assert msg is not None
        assert msg.is_fd is False
        assert msg.bitrate_switch is False
    finally:
        peer.shutdown()
        cm.disconnect()


def test_pcan_fd_timing_constants_resolve_to_intended_bitrates():
    """PCAN-FD 연결 시 사용하는 고정 레지스터 값(FD_CLOCK_HZ/FD_NOM_*/FD_DATA_*)이
    실제로 의도한 비트레이트/샘플포인트를 만들어내는지 확인 -- 상수를 실수로
    잘못 고치면 이 테스트가 잡아준다. can.Bus(interface="pcan", ...)는 실제
    하드웨어/드라이버가 있어야 해서 여기서 connect()까지 호출하지는 않는다."""
    timing = can.BitTimingFd(
        f_clock=can_manager.FD_CLOCK_HZ,
        nom_brp=can_manager.FD_NOM_BRP,
        nom_tseg1=can_manager.FD_NOM_TSEG1,
        nom_tseg2=can_manager.FD_NOM_TSEG2,
        nom_sjw=can_manager.FD_NOM_SJW,
        data_brp=can_manager.FD_DATA_BRP,
        data_tseg1=can_manager.FD_DATA_TSEG1,
        data_tseg2=can_manager.FD_DATA_TSEG2,
        data_sjw=can_manager.FD_DATA_SJW,
    )
    assert timing.f_clock == 80_000_000
    assert timing.nom_bitrate == 500_000
    assert timing.nom_sample_point == 80.0
    assert timing.data_bitrate == 1_000_000
    assert timing.data_sample_point == 75.0
