import time

import can

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
