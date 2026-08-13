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


def test_min_stmin_s_raises_gap_when_ecu_says_zero(stack):
    """send()'s min_stmin_s is a floor the caller can use to deliberately
    slow a multi-frame send down (e.g. CAN-SWDL/OTA Tester's STmin UI
    setting) even when the ECU's own Flow Control asks for no delay at
    all -- per ISO 15765-2 the sender may always wait *longer* than the
    receiver's stated minimum, just never shorter."""
    cm, peer = stack
    stop, t, _ = start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x00)
    try:
        data = bytes(range(20))  # FF(6) + CF(7) + CF(7) -> exactly one inter-CF gap
        result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0, min_stmin_s=0.08)
    finally:
        stop.set()
        t.join(timeout=1)
    assert result["frames_sent"] == 3
    assert result["duration_ms"] >= 70  # ECU asked for 0ms; floor forces >=80ms (small tolerance)


def test_min_stmin_s_never_shrinks_a_larger_ecu_required_stmin(stack):
    """The floor is one-directional -- it must never let a send go *faster*
    than what the ECU's real Flow Control requires."""
    cm, peer = stack
    stop, t, _ = start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x32)  # ECU asks for 50ms
    try:
        data = bytes(range(20))
        result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0, min_stmin_s=0.001)
    finally:
        stop.set()
        t.join(timeout=1)
    assert result["frames_sent"] == 3
    assert result["duration_ms"] >= 45  # ECU's 50ms must still be honored, not shrunk to 1ms


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


def test_send_defaults_to_fd_when_bus_is_fd():
    cm = CanManager()
    cm.connect("virtual", "t_isotp_fd", receive_own_messages=False, fd=True)
    peer = can.Bus(interface="virtual", channel="t_isotp_fd", fd=True)
    try:
        isotp_service.send(cm, TX_ID, FC_ID, bytes.fromhex("0102030405"))
        msg = peer.recv(timeout=1.0)
        assert msg.is_fd is True
        assert msg.bitrate_switch is True
    finally:
        peer.shutdown()
        cm.disconnect()


def test_send_stays_classic_when_bus_is_classic(stack):
    cm, peer = stack
    isotp_service.send(cm, TX_ID, FC_ID, bytes.fromhex("0102030405"))
    msg = peer.recv(timeout=1.0)
    assert msg.is_fd is False
    assert msg.bitrate_switch is False


def test_send_explicit_is_fd_still_forced_classic_when_bus_is_classic(stack):
    """An explicit is_fd=True request can't make a classic-connected bus emit
    an FD frame -- CanManager.send() clamps to the connection's actual
    fd_enabled state, since a classic-mode connection can't transmit FD
    frames on real hardware."""
    cm, peer = stack
    isotp_service.send(cm, TX_ID, FC_ID, bytes.fromhex("0102030405"), is_fd=True, bitrate_switch=True)
    msg = peer.recv(timeout=1.0)
    assert msg.is_fd is False
    assert msg.bitrate_switch is False


@pytest.fixture
def fd_stack():
    cm = CanManager()
    cm.connect("virtual", "t_isotp_fd2", receive_own_messages=False, fd=True)
    peer = can.Bus(interface="virtual", channel="t_isotp_fd2", fd=True)
    yield cm, peer
    peer.shutdown()
    cm.disconnect()


def test_fd_single_frame_escape_form_for_8_to_62_bytes(fd_stack):
    cm, peer = fd_stack
    data = bytes(range(20))  # 20 bytes: too big for classic SF, fits FD escape SF
    result = isotp_service.send(cm, TX_ID, FC_ID, data)
    assert result == {
        "sent": True,
        "frame_type": "single",
        "frames_sent": 1,
        "bytes_sent": 20,
        "duration_ms": result["duration_ms"],
    }
    msg = peer.recv(timeout=1.0)
    assert msg.is_fd is True
    assert len(msg.data) == 24  # smallest valid CAN-FD length >= 2 (PCI) + 20
    assert msg.data[0] == 0x00  # SF escape PCI
    assert msg.data[1] == 20  # explicit SF_DL
    assert bytes(msg.data[2:22]) == data


def test_fd_multi_frame_uses_up_to_64_byte_frames(fd_stack):
    cm, peer = fd_stack
    monitor = can.Bus(interface="virtual", channel="t_isotp_fd2", fd=True)
    try:
        stop, t, _ = start_fc_responder(peer, fs=0x0, bs=0x00, stmin=0x00)
        try:
            data = bytes((i % 256) for i in range(150))  # forces FD FF(62) + 2 CF(63,25)
            result = isotp_service.send(cm, TX_ID, FC_ID, data, fc_timeout_s=1.0)
            assert result["frame_type"] == "multi"
            assert result["frames_sent"] == 3  # FF + 2 CF
            assert result["bytes_sent"] == 150
        finally:
            stop.set()
            t.join(timeout=1)

        frames = [f for f in drain(monitor, 4) if f.arbitration_id == TX_ID]
        ff, cf1, cf2 = frames[0], frames[1], frames[2]
        assert ff.is_fd and cf1.is_fd and cf2.is_fd
        assert len(ff.data) == 64  # PCI(2) + 62 data bytes, exact valid length
        assert ff.data[0] & 0xF0 == 0x10
        assert bytes(ff.data[2:64]) == data[:62]
        assert len(cf1.data) == 64  # PCI(1) + 63 data bytes, exact valid length
        assert bytes(cf1.data[1:64]) == data[62:125]
        remaining_len = 150 - 62 - 63  # 25 bytes left in the last CF
        assert len(cf2.data) == _min_fd_len(1 + remaining_len)
        assert bytes(cf2.data[1:1 + remaining_len]) == data[125:150]
    finally:
        monitor.shutdown()


def _min_fd_len(n):
    for length in (8, 12, 16, 20, 24, 32, 48, 64):
        if n <= length:
            return length
    raise AssertionError("length exceeds CAN-FD max")


RESP_ID = 0x7A3  # arbitration ID the simulated ECU response arrives on


def _pad_to_fd_len(data: bytes) -> bytes:
    return data + bytes(_min_fd_len(len(data)) - len(data))


def test_receive_decodes_fd_single_frame_escape_form(fd_stack):
    cm, peer = fd_stack
    data = bytes(range(30))
    frame = _pad_to_fd_len(bytes([0x00, len(data)]) + data)

    # receive() only attaches its BufferedReader to cm's notifier once
    # called; sending from the main thread beforehand (or immediately after,
    # with no synchronization) races that attachment against the notifier's
    # own background dispatch thread. Running receive() in a background
    # thread and giving it a moment to attach before sending removes the
    # race deterministically.
    result = {}

    def run():
        result["value"] = isotp_service.receive(cm, RESP_ID, FC_ID, timeout_s=2.0)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.1)
    peer.send(can.Message(arbitration_id=RESP_ID, data=frame, is_fd=True))
    t.join(timeout=2)
    assert result["value"] == data


def test_receive_decodes_fd_multi_frame(fd_stack):
    cm, peer = fd_stack
    data = bytes((i % 256) for i in range(150))
    ff_frame = bytes([0x10 | ((150 >> 8) & 0x0F), 150 & 0xFF]) + data[:62]

    result = {}

    def run():
        result["value"] = isotp_service.receive(cm, RESP_ID, FC_ID, timeout_s=2.0)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.1)  # let receive() attach its BufferedReader first (see above)

    peer.send(can.Message(arbitration_id=RESP_ID, data=ff_frame, is_fd=True))
    fc = peer.recv(timeout=1.0)
    assert fc is not None and fc.arbitration_id == FC_ID
    cf1 = bytes([0x21]) + data[62:125]
    peer.send(can.Message(arbitration_id=RESP_ID, data=cf1, is_fd=True))
    cf2 = _pad_to_fd_len(bytes([0x22]) + data[125:150])
    peer.send(can.Message(arbitration_id=RESP_ID, data=cf2, is_fd=True))

    t.join(timeout=2)
    assert result["value"] == data


def test_receive_sends_follow_up_fc_after_each_block(stack):
    """Regression: a nonzero Block Size means the sender only streams that
    many CFs before pausing for another Flow Control frame (ISO 15765-2) --
    receive() used to send exactly one FC (right after the First Frame) and
    never again, so a real sender honoring fc_block_size would send its
    first block and then wait forever. 21 bytes -> FF carries 6, leaving 15
    (3 CFs of 7/7/1) with fc_block_size=1 -> expect FC before CF1, FC after
    CF1, FC after CF2, no FC needed after CF3 (nothing left)."""
    cm, peer = stack
    data = bytes(range(1, 22))
    ff_frame = bytes([0x10 | ((len(data) >> 8) & 0x0F), len(data) & 0xFF]) + data[:6]

    result = {}

    def run():
        result["value"] = isotp_service.receive(cm, RESP_ID, FC_ID, timeout_s=2.0, fc_block_size=1)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.1)

    peer.send(can.Message(arbitration_id=RESP_ID, data=ff_frame, is_extended_id=False))
    fc1 = peer.recv(timeout=1.0)
    assert fc1 is not None and fc1.arbitration_id == FC_ID
    assert fc1.data[0] & 0x0F == 0x00  # CTS
    assert fc1.data[1] == 1  # block size echoed back

    rest = data[6:]
    peer.send(can.Message(arbitration_id=RESP_ID, data=bytes([0x21]) + rest[:7], is_extended_id=False))
    fc2 = peer.recv(timeout=1.0)
    assert fc2 is not None and fc2.arbitration_id == FC_ID  # the actual bug: this used to never arrive

    rest = rest[7:]
    peer.send(can.Message(arbitration_id=RESP_ID, data=bytes([0x22]) + rest[:7], is_extended_id=False))
    fc3 = peer.recv(timeout=1.0)
    assert fc3 is not None and fc3.arbitration_id == FC_ID

    rest = rest[7:]
    peer.send(can.Message(arbitration_id=RESP_ID, data=bytes([0x23]) + rest, is_extended_id=False))

    t.join(timeout=2)
    assert result["value"] == data


def test_receive_bs_zero_sends_only_the_initial_fc(stack):
    """The default (block_size=0, "unlimited") must keep its original
    behavior exactly -- a single FC upfront, no follow-ups -- since that's
    what every other existing receive() test already relies on."""
    cm, peer = stack
    data = bytes(range(1, 16))  # FF(6) + CF(7) + CF(2), no block boundary
    ff_frame = bytes([0x10 | ((len(data) >> 8) & 0x0F), len(data) & 0xFF]) + data[:6]

    result = {}

    def run():
        result["value"] = isotp_service.receive(cm, RESP_ID, FC_ID, timeout_s=2.0)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.1)

    peer.send(can.Message(arbitration_id=RESP_ID, data=ff_frame, is_extended_id=False))
    fc1 = peer.recv(timeout=1.0)
    assert fc1 is not None and fc1.arbitration_id == FC_ID

    rest = data[6:]
    peer.send(can.Message(arbitration_id=RESP_ID, data=bytes([0x21]) + rest[:7], is_extended_id=False))
    peer.send(can.Message(arbitration_id=RESP_ID, data=bytes([0x22]) + rest[7:], is_extended_id=False))

    # no second FC should ever arrive
    assert peer.recv(timeout=0.3) is None

    t.join(timeout=2)
    assert result["value"] == data
