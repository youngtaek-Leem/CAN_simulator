import threading
import time

import can
import pytest

from can_manager import CanManager
import isotp_service

TX_ID = 0x783
FC_ID = 0x78B


@pytest.fixture
def stack():
    cm = CanManager()
    cm.connect("virtual", "t_isotp", receive_own_messages=False)
    peer = can.Bus(interface="virtual", channel="t_isotp")
    yield cm, peer
    peer.shutdown()
    cm.disconnect()


def drain(peer, count, timeout=2.0):
    frames = []
    deadline = time.perf_counter() + timeout
    while len(frames) < count and time.perf_counter() < deadline:
        msg = peer.recv(timeout=0.2)
        if msg is not None:
            frames.append(msg)
    return frames


def start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x00, on_pci_types=(0x1, 0x2), max_replies=99):
    """Background thread: reply with a fixed FC frame whenever a FF/CF arrives."""
    stop = threading.Event()
    sent = []

    def run():
        count = 0
        while not stop.is_set() and count < max_replies:
            msg = peer.recv(timeout=0.3)
            if msg is None:
                continue
            pci_type = msg.data[0] >> 4
            if pci_type in on_pci_types:
                fc = can.Message(
                    arbitration_id=FC_ID,
                    data=bytes([0x30 | fs, bs, stmin, 0, 0, 0, 0, 0]),
                    is_extended_id=False,
                )
                peer.send(fc)
                sent.append(fc)
                count += 1

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop, t, sent


def test_single_frame_no_fc_needed(stack):
    cm, peer = stack
    data = bytes.fromhex("0102030405")
    result = isotp_service.send(cm, TX_ID, FC_ID, data)
    assert result == {
        "sent": True,
        "frame_type": "single",
        "frames_sent": 1,
        "bytes_sent": 5,
        "duration_ms": result["duration_ms"],
    }
    msg = peer.recv(timeout=1.0)
    assert msg.data == bytes([0x05, 0x01, 0x02, 0x03, 0x04, 0x05, 0x00, 0x00])


def test_single_frame_boundary_7_bytes(stack):
    cm, peer = stack
    data = bytes(range(1, 8))
    isotp_service.send(cm, TX_ID, FC_ID, data)
    msg = peer.recv(timeout=1.0)
    assert msg.data[0] == 0x07
    assert bytes(msg.data[1:8]) == data


def test_multi_frame_bs_zero_sends_all_cf_at_once(stack):
    cm, peer = stack
    # separate observer bus: each virtual-bus instance gets its own inbound
    # queue, so this doesn't race with the FC-responder thread reading `peer`
    monitor = can.Bus(interface="virtual", channel="t_isotp")
    try:
        stop, t, _ = start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x00)
        try:
            data = bytes.fromhex("010203040506070809101112131415")  # 15 bytes
            result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
            assert result["frame_type"] == "multi"
            assert result["frames_sent"] == 3  # FF + 2 CF
            assert result["bytes_sent"] == 15
        finally:
            stop.set()
            t.join(timeout=1)

        frames = [f for f in drain(monitor, 4) if f.arbitration_id == TX_ID]
        assert bytes(frames[0].data) == bytes([0x10, 0x0F]) + data[:6]
        assert bytes(frames[1].data) == bytes([0x21]) + data[6:13]
        assert bytes(frames[2].data) == bytes([0x22]) + data[13:15] + bytes([0, 0, 0, 0, 0])
    finally:
        monitor.shutdown()


def test_multi_frame_reassembles_correctly_for_various_lengths(stack):
    cm, peer = stack
    for n in (8, 20, 62, 100):
        # fresh monitor bus per iteration: avoids stray frames from a
        # previous iteration lingering in a shared queue
        monitor = can.Bus(interface="virtual", channel="t_isotp")
        stop, t, _ = start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x00)
        try:
            data = bytes((i % 256) for i in range(n))
            isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)

            expected_cf_count = -(-(n - 6) // 7)  # ceil((n-6)/7)
            frames = [f for f in drain(monitor, expected_cf_count + 3) if f.arbitration_id == TX_ID]
            assert frames[0].data[0] & 0xF0 == 0x10

            reassembled = bytearray(bytes(frames[0].data[2:8]))
            for f in frames[1 : 1 + expected_cf_count]:
                assert f.data[0] & 0xF0 == 0x20
                reassembled.extend(bytes(f.data[1:8]))
            assert bytes(reassembled[:n]) == data
        finally:
            stop.set()
            t.join(timeout=1)
            monitor.shutdown()


def test_fc_block_size_limits_frames_per_block(stack):
    cm, peer = stack
    # BS=1: DUT must send a fresh FC before every single CF
    stop, t, sent = start_fc_responder(peer, fs=0x0, bs=0x01, stmin=0x00)
    try:
        data = bytes(range(1, 21))  # 20 bytes -> FF + 2 CF
        result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
        assert result["frames_sent"] == 3
    finally:
        stop.set()
        t.join(timeout=1)
    # one FC after FF, one after each of the 2 CFs the sender needed
    assert len(sent) >= 2


def test_fc_wait_status_is_honored(stack):
    cm, peer = stack

    def run():
        msg = peer.recv(timeout=1.0)
        assert msg.data[0] & 0xF0 == 0x10  # FF
        # first: WAIT
        peer.send(can.Message(arbitration_id=FC_ID, data=bytes([0x31, 0, 0, 0, 0, 0, 0, 0])))
        time.sleep(0.05)
        # then: continue-to-send, BS=0
        peer.send(can.Message(arbitration_id=FC_ID, data=bytes([0x30, 0, 0, 0, 0, 0, 0, 0])))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    data = bytes.fromhex("010203040506070809101112131415")
    result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
    t.join(timeout=2)
    assert result["frames_sent"] == 3


def test_fc_overflow_aborts(stack):
    cm, peer = stack

    def run():
        peer.recv(timeout=1.0)
        peer.send(can.Message(arbitration_id=FC_ID, data=bytes([0x32, 0, 0, 0, 0, 0, 0, 0])))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    data = bytes.fromhex("010203040506070809101112131415")
    with pytest.raises(isotp_service.IsoTpError, match="Overflow"):
        isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
    t.join(timeout=2)


def test_fc_timeout_raises(stack):
    cm, peer = stack
    data = bytes.fromhex("010203040506070809101112131415")
    t0 = time.perf_counter()
    with pytest.raises(isotp_service.IsoTpError, match="시간 초과"):
        isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=0.2)
    assert time.perf_counter() - t0 < 1.0


def test_ignores_fc_on_other_ids(stack):
    cm, peer = stack

    def run():
        peer.recv(timeout=1.0)
        # noise on a different ID -- must be ignored
        peer.send(can.Message(arbitration_id=0x111, data=bytes([0x30, 0, 0, 0, 0, 0, 0, 0])))
        time.sleep(0.05)
        peer.send(can.Message(arbitration_id=FC_ID, data=bytes([0x30, 0, 0, 0, 0, 0, 0, 0])))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    data = bytes.fromhex("010203040506070809101112131415")
    result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
    t.join(timeout=2)
    assert result["sent"] is True


def test_empty_data_raises(stack):
    cm, peer = stack
    with pytest.raises(isotp_service.IsoTpError):
        isotp_service.send(cm, TX_ID, FC_ID, b"")


def test_not_connected_raises():
    cm = CanManager()
    with pytest.raises(isotp_service.IsoTpError):
        isotp_service.send(cm, TX_ID, FC_ID, b"\x01\x02")
