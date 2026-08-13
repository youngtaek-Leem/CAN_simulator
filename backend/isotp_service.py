"""ISO-TP (ISO 15765-2) transport-layer sender/receiver, classic addressing.

Single Frame is used for payloads up to 7 bytes (classic CAN) and sent
immediately. Longer payloads (up to the classic 12-bit length field's 4095
bytes) are sent as a First Frame followed by Consecutive Frames, waiting for
a Flow Control frame from the receiver (arriving on `fc_id`) before each
block and honoring its Block Size (BS) and STmin, per ISO 15765-2.

``is_fd``/``bitrate_switch`` select the CAN frame type. When not given
explicitly, both default to ``can_manager.fd_enabled`` so that diagnostic
traffic automatically goes out as CAN-FD frames whenever the bus is
connected in FD mode, and as classic CAN frames otherwise. When ``is_fd`` is
true, frames also carry more payload per the CAN-FD extension in ISO
15765-2:2016: Single Frame up to 62 bytes (via the length-escape PCI form),
First Frame data up to 62 bytes, Consecutive Frame data up to 63 bytes --
each frame padded up to the nearest valid CAN-FD length (8/12/16/20/24/32/
48/64). Classic (non-FD) frames are always padded to exactly 8 bytes as
before. Passing ``is_fd=True`` explicitly only affects this module's own
framing decisions -- ``CanManager.send()`` still clamps the frame actually
put on the wire to classic whenever the connection itself isn't in FD mode,
since classic hardware can't transmit FD frames.

Reception (receive function):
- Waits for an incoming ISO-TP message on a given arbitration ID
- Single Frame: returned immediately (classic short form or CAN-FD escape
  form, decided by the sender's actual PCI/frame length -- no is_fd branching
  needed to parse it)
- Multi-frame: receives First Frame, sends Flow Control, then receives
  Consecutive Frames (each frame's own length determines its data bytes, so
  classic and CAN-FD senders are both handled automatically), reassembles
  the full payload
- Timeout handling for each phase
"""

import threading
import time
from typing import Optional

import can

SF_MAX_LEN = 7
FF_DATA_LEN = 6
CF_DATA_LEN = 7
MAX_ISOTP_LEN = 4095
PAD_BYTE = 0x55

# CAN-FD (ISO 15765-2:2016) framing limits: escape-form Single Frame and
# First Frame carry up to 62 data bytes (frame length 64 minus a 2-byte PCI),
# Consecutive Frame up to 63 (minus its 1-byte PCI).
FD_MAX_LEN = 64
FD_SF_MAX_LEN = FD_MAX_LEN - 2
FD_FF_DATA_LEN = FD_MAX_LEN - 2
FD_CF_DATA_LEN = FD_MAX_LEN - 1
# Valid CAN-FD data lengths (DLC 8-15), per ISO 11898-1.
FD_VALID_LENGTHS = (8, 12, 16, 20, 24, 32, 48, 64)

# PCI types
PCI_SF = 0x00
PCI_FF = 0x10
PCI_CF = 0x20
PCI_FC = 0x30

# Flow Status
FS_CTS = 0x00  # Continue To Send
FS_WAIT = 0x01
FS_OVERFLOW = 0x02


class IsoTpError(Exception):
    pass


def _pad(data: bytes) -> bytes:
    if len(data) < 8:
        return data + bytes([PAD_BYTE]) * (8 - len(data))
    return data


def _fd_frame_len(n: int) -> int:
    """Smallest valid CAN-FD data length that fits ``n`` bytes."""
    for length in FD_VALID_LENGTHS:
        if n <= length:
            return length
    raise IsoTpError(f"CAN-FD 프레임 최대 길이({FD_MAX_LEN}바이트)를 초과했습니다")


def _pad_fd(data: bytes) -> bytes:
    target = _fd_frame_len(len(data))
    return data + bytes([PAD_BYTE]) * (target - len(data))


def decode_stmin(byte: int) -> float:
    """STmin byte -> seconds. 0x00-0x7F = 0-127 ms, 0xF1-0xF9 = 100-900 us.
    Public (not module-private) since callers of send() need it to convert
    their own configured STmin override into the ``min_stmin_s`` it takes."""
    if byte <= 0x7F:
        return byte / 1000.0
    if 0xF1 <= byte <= 0xF9:
        return (byte - 0xF0) * 100 / 1_000_000.0
    return 0.0  # reserved values treated as no delay


def _build_fc(flow_status: int, block_size: int = 0, stmin: int = 0) -> bytes:
    """Build a Flow Control frame payload (3 bytes)."""
    return bytes([PCI_FC | flow_status, block_size & 0xFF, stmin & 0xFF])


# When a stop_event is given, waits below poll in chunks of at most this
# long instead of blocking for the full remaining timeout in one
# reader.get_message() call -- a plain blocking wait has no way to notice
# stop_event.set() from another thread until it either gets a frame or
# times out, which is exactly what let a UDS download's Stop button sit
# unresponsive for as long as a single P2*Server_max wait (or, compounded
# over several NRC 0x78 ResponsePending retries, tens of seconds -- see
# Requirement.md's TransferData Stop-delay investigation).
_STOP_POLL_S = 0.05


def _wait_for_frame(
    reader: can.BufferedReader, rx_id: int, timeout_s: float,
    stop_event: Optional[threading.Event] = None,
) -> Optional[can.Message]:
    """Wait for a CAN message with the given arbitration ID."""
    deadline = time.perf_counter() + timeout_s
    while True:
        if stop_event is not None and stop_event.is_set():
            raise IsoTpError("사용자에 의해 중단됨")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        wait_s = min(remaining, _STOP_POLL_S) if stop_event is not None else remaining
        msg = reader.get_message(timeout=wait_s)
        if msg is None:
            continue
        if msg.arbitration_id == rx_id:
            return msg
        # ignore unrelated frames


def _wait_for_fc(
    reader: can.BufferedReader, fc_id: int, timeout_s: float,
    stop_event: Optional[threading.Event] = None,
) -> Optional[bytes]:
    deadline = time.perf_counter() + timeout_s
    while True:
        if stop_event is not None and stop_event.is_set():
            raise IsoTpError("사용자에 의해 중단됨")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        wait_s = min(remaining, _STOP_POLL_S) if stop_event is not None else remaining
        msg = reader.get_message(timeout=wait_s)
        if msg is None:
            continue
        if msg.arbitration_id != fc_id or len(msg.data) < 3:
            continue
        if (msg.data[0] & 0xF0) != PCI_FC:
            continue  # not a Flow Control PCI, keep waiting
        return bytes(msg.data[:3])


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------


def send(
    can_manager,
    tx_id: int,
    fc_id: int,
    data: bytes,
    is_extended_id: bool = False,
    fc_timeout_s: float = 1.0,
    max_wait_frames: int = 10,
    is_fd: Optional[bool] = None,
    bitrate_switch: Optional[bool] = None,
    reader: Optional[can.BufferedReader] = None,
    min_stmin_s: float = 0.0,
    stop_event: Optional[threading.Event] = None,
) -> dict:
    """``reader``, when given, is reused for waiting on Flow Control frames
    during a multi-frame send instead of creating/tearing down one just for
    this call (irrelevant for a Single Frame send, which never waits on
    anything). A caller that will also call receive() right after sending
    -- e.g. a UDS request/response exchange -- should create one reader,
    register it *before* calling send(), and pass it to both send() and
    receive(), tearing it down itself only once the whole exchange is done.
    Otherwise there's a gap between this call returning and a fresh
    listener being registered for the receive() that follows, during which
    python-can's Notifier has nowhere to dispatch an arriving frame -- an
    ECU that answers fast enough (observed: 4.688ms after a TransferData
    request) can have its response land exactly in that gap and be lost for
    good, timing out the receive despite the ECU having actually answered.

    ``min_stmin_s``: per ISO 15765-2, the receiver's Flow Control STmin is a
    *minimum* the sender must honor -- the sender is always free to wait
    longer between Consecutive Frames, just never shorter. Passing a value
    here raises the actual inter-CF delay to at least this many seconds
    even when the peer's own FC asks for less (or none), without ever
    going below what the peer required. Deliberately one-directional: this
    can only slow a multi-frame send down, never speed it up past what the
    receiving ECU said it can handle -- doing the latter would violate the
    spec and risk a real ECU's RX buffer overflowing mid-transfer. 0.0
    (default) leaves this exactly as before (peer's own STmin only).

    ``stop_event``: when given and set (by another thread) while this call
    is blocked waiting on a Flow Control frame or sleeping between
    Consecutive Frames, raises IsoTpError("사용자에 의해 중단됨") instead of
    continuing to wait out the full timeout -- lets a caller's Stop button
    interrupt a multi-frame send promptly."""
    if not data:
        raise IsoTpError("전송할 데이터가 없습니다")
    if len(data) > MAX_ISOTP_LEN:
        raise IsoTpError(f"ISO-TP 최대 길이({MAX_ISOTP_LEN}바이트)를 초과했습니다")
    if can_manager.notifier is None:
        raise IsoTpError("CAN 버스가 연결되어 있지 않습니다")

    if is_fd is None:
        is_fd = can_manager.fd_enabled
    if bitrate_switch is None:
        bitrate_switch = is_fd

    sf_max_len = FD_SF_MAX_LEN if is_fd else SF_MAX_LEN
    ff_data_len = FD_FF_DATA_LEN if is_fd else FF_DATA_LEN
    cf_data_len = FD_CF_DATA_LEN if is_fd else CF_DATA_LEN
    pad = _pad_fd if is_fd else _pad

    t0 = time.perf_counter()

    if len(data) <= sf_max_len:
        if len(data) <= SF_MAX_LEN:
            frame = pad(bytes([len(data)]) + data)
        else:
            # CAN-FD Single Frame escape form: PCI 0x00, explicit length byte
            frame = pad(bytes([PCI_SF, len(data)]) + data)
        can_manager.send(tx_id, frame, is_extended_id, is_fd=is_fd, bitrate_switch=bitrate_switch)
        return {
            "sent": True,
            "frame_type": "single",
            "frames_sent": 1,
            "bytes_sent": len(data),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    total_len = len(data)
    ff = bytes([PCI_FF | ((total_len >> 8) & 0x0F), total_len & 0xFF]) + data[:ff_data_len]
    owns_reader = reader is None
    if owns_reader:
        reader = can.BufferedReader()
        can_manager.notifier.add_listener(reader)
    try:
        can_manager.send(tx_id, pad(ff), is_extended_id, is_fd=is_fd, bitrate_switch=bitrate_switch)
        remaining = data[ff_data_len:]
        frames_sent = 1
        sn = 1
        wait_count = 0

        while remaining:
            fc = _wait_for_fc(reader, fc_id, fc_timeout_s, stop_event=stop_event)
            if fc is None:
                raise IsoTpError("Flow Control 프레임을 기다리다 시간 초과되었습니다")
            fs = fc[0] & 0x0F
            if fs == FS_WAIT:
                wait_count += 1
                if wait_count > max_wait_frames:
                    raise IsoTpError("Flow Control WAIT 횟수를 초과했습니다")
                continue
            if fs == FS_OVERFLOW:
                raise IsoTpError("수신측이 Flow Control Overflow(중단)를 보냈습니다")
            if fs != FS_CTS:
                raise IsoTpError(f"알 수 없는 Flow Control 상태 값({fs})입니다")

            block_size = fc[1]
            stmin = max(decode_stmin(fc[2]), min_stmin_s)
            block_count = 0
            while remaining and (block_size == 0 or block_count < block_size):
                if stmin > 0 and block_count > 0:
                    if stop_event is not None:
                        if stop_event.wait(stmin):
                            raise IsoTpError("사용자에 의해 중단됨")
                    else:
                        time.sleep(stmin)
                chunk, remaining = remaining[:cf_data_len], remaining[cf_data_len:]
                can_manager.send(
                    tx_id,
                    pad(bytes([PCI_CF | (sn & 0x0F)]) + chunk),
                    is_extended_id,
                    is_fd=is_fd,
                    bitrate_switch=bitrate_switch,
                )
                frames_sent += 1
                sn = (sn + 1) % 16
                block_count += 1
    finally:
        if owns_reader:
            can_manager.notifier.remove_listener(reader)

    return {
        "sent": True,
        "frame_type": "multi",
        "frames_sent": frames_sent,
        "bytes_sent": total_len,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


def receive(
    can_manager,
    rx_id: int,
    tx_id: int,
    timeout_s: float = 1.0,
    is_extended_id: bool = False,
    fc_stmin: int = 0x00,
    fc_block_size: int = 0,
    is_fd: Optional[bool] = None,
    bitrate_switch: Optional[bool] = None,
    reader: Optional[can.BufferedReader] = None,
    stop_event: Optional[threading.Event] = None,
) -> bytes:
    """Receive an ISO-TP message on the given arbitration ID.

    Parameters
    ----------
    can_manager : CanManager
        The CAN manager instance.
    rx_id : int
        Arbitration ID on which to receive the response.
    tx_id : int
        Arbitration ID to send Flow Control frames (if multi-frame).
    timeout_s : float
        Overall timeout for the receive operation.
    is_extended_id : bool
        Whether the CAN IDs use 29-bit extended addressing.
    fc_stmin : int
        STmin value to send in Flow Control (default 0x00 = 0ms).
    fc_block_size : int
        Block Size to send in Flow Control (default 0 = unlimited).
    reader : can.BufferedReader, optional
        Reuse an already-registered listener instead of creating and tearing
        one down for just this call. A caller that needs to issue several
        receive() calls back-to-back -- e.g. re-waiting with a longer
        timeout after an NRC 0x78 "response pending" -- should create one
        reader, pass it to every call in that sequence, and remove it itself
        only once at the end. Without this, each call's own
        add_listener()/remove_listener() leaves a gap between calls during
        which python-can's Notifier has nowhere registered to dispatch an
        arriving frame to for this consumer -- if the real final response
        lands in that gap, it's gone, and the next call times out waiting
        for a frame that already went by. When omitted (the default), this
        function creates and tears down its own reader exactly as before.
    is_fd : bool, optional
        CAN frame type for the outgoing Flow Control frame. Defaults to
        ``can_manager.fd_enabled`` when not given.
    bitrate_switch : bool, optional
        Defaults to ``is_fd`` when not given.
    stop_event : threading.Event, optional
        When given and set (by another thread) while this call is blocked
        waiting for the first frame or a Consecutive Frame, raises
        IsoTpError("사용자에 의해 중단됨") instead of continuing to wait out
        the full timeout -- lets a caller's Stop button interrupt a
        multi-frame receive promptly.

    Returns
    -------
    bytes
        The complete reassembled payload.

    Raises
    ------
    IsoTpError
        If the receive fails (timeout, protocol error, etc.).
    """
    if can_manager.notifier is None:
        raise IsoTpError("CAN 버스가 연결되어 있지 않습니다")

    if is_fd is None:
        is_fd = can_manager.fd_enabled
    if bitrate_switch is None:
        bitrate_switch = is_fd

    owns_reader = reader is None
    if owns_reader:
        reader = can.BufferedReader()
        can_manager.notifier.add_listener(reader)
    t0 = time.perf_counter()

    try:
        # Wait for the first frame (either SF or FF)
        msg = _wait_for_frame(reader, rx_id, timeout_s, stop_event=stop_event)
        if msg is None:
            raise IsoTpError("응답 프레임을 기다리다 시간 초과되었습니다")

        data = bytes(msg.data)
        if len(data) < 1:
            raise IsoTpError("빈 프레임을 수신했습니다")

        pci = data[0]
        pci_type = pci & 0xF0

        if pci_type == PCI_SF:
            # Single Frame: length is in lower nibble (or byte 1 if needed)
            length = pci & 0x0F
            if length == 0 and len(data) > 1:
                # Extended single frame (rare, but handle)
                length = data[1]
                payload = data[2:2 + length]
            else:
                payload = data[1:1 + length]
            return payload

        elif pci_type == PCI_FF:
            # First Frame: 12-bit length
            total_length = ((pci & 0x0F) << 8) | data[1]
            if total_length > MAX_ISOTP_LEN:
                raise IsoTpError(f"ISO-TP 길이({total_length})가 최대값({MAX_ISOTP_LEN})을 초과했습니다")

            # Data carried in FF (after the 2-byte length). Uses the actual
            # received frame length rather than a fixed constant, so both
            # classic (8-byte) and CAN-FD (up to 64-byte) First Frames are
            # decoded the same way.
            ff_data_len = min(len(data) - 2, total_length)
            payload = bytearray(data[2:2 + ff_data_len])

            # Send Flow Control (CTS)
            fc = _build_fc(FS_CTS, fc_block_size, fc_stmin)
            can_manager.send(tx_id, _pad(fc), is_extended_id, is_fd=is_fd, bitrate_switch=bitrate_switch)

            # Receive Consecutive Frames. Per ISO 15765-2, a nonzero Block
            # Size means the sender only streams that many CFs per FC before
            # pausing to wait for another Flow Control frame -- this used to
            # send exactly one FC (the one above, right after the First
            # Frame) and then never again, so any sender that actually
            # honored a nonzero fc_block_size would send its first block and
            # then wait forever for a follow-up FC that never came. block_size
            # == 0 (the default) means "unlimited", i.e. the original
            # single-FC behavior is unchanged.
            expected_sn = 1
            remaining = total_length - ff_data_len
            block_count = 0
            while remaining > 0:
                cf_timeout = max(0.1, timeout_s - (time.perf_counter() - t0))
                if cf_timeout <= 0:
                    raise IsoTpError("Consecutive Frame 수신 중 시간 초과되었습니다")

                cf_msg = _wait_for_frame(reader, rx_id, cf_timeout, stop_event=stop_event)
                if cf_msg is None:
                    raise IsoTpError("Consecutive Frame 수신 중 시간 초과되었습니다")

                cf_data = bytes(cf_msg.data)
                if len(cf_data) < 1:
                    raise IsoTpError("빈 Consecutive Frame을 수신했습니다")

                cf_pci = cf_data[0]
                if (cf_pci & 0xF0) != PCI_CF:
                    raise IsoTpError(f"예상치 못한 PCI 타입: 0x{cf_pci:02x} (CF 기대)")

                cf_sn = cf_pci & 0x0F
                if cf_sn != expected_sn:
                    raise IsoTpError(
                        f"Consecutive Frame SN 불일치: 기대={expected_sn}, 수신={cf_sn}"
                    )

                chunk_len = min(len(cf_data) - 1, remaining)
                payload.extend(cf_data[1:1 + chunk_len])
                remaining -= chunk_len
                expected_sn = (expected_sn + 1) % 16
                block_count += 1

                if fc_block_size and remaining > 0 and block_count >= fc_block_size:
                    fc = _build_fc(FS_CTS, fc_block_size, fc_stmin)
                    can_manager.send(tx_id, _pad(fc), is_extended_id, is_fd=is_fd, bitrate_switch=bitrate_switch)
                    block_count = 0

            return bytes(payload)

        else:
            raise IsoTpError(f"알 수 없는 PCI 타입: 0x{pci:02x}")

    finally:
        if owns_reader:
            can_manager.notifier.remove_listener(reader)