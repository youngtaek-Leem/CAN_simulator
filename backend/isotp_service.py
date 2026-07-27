"""ISO-TP (ISO 15765-2) transport-layer sender/receiver, classic addressing.

Single Frame is used for payloads up to 7 bytes and sent immediately. Longer
payloads (up to the classic 12-bit length field's 4095 bytes) are sent as a
First Frame followed by Consecutive Frames, waiting for a Flow Control frame
from the receiver (arriving on `fc_id`) before each block and honoring its
Block Size (BS) and STmin, per ISO 15765-2. All frames are padded to 8 bytes.

Reception (receive function):
- Waits for an incoming ISO-TP message on a given arbitration ID
- Single Frame (1-7 bytes payload): returned immediately
- Multi-frame: receives First Frame, sends Flow Control, then receives
  Consecutive Frames, reassembles the full payload
- Timeout handling for each phase
"""

import time
from typing import Optional

import can

SF_MAX_LEN = 7
FF_DATA_LEN = 6
CF_DATA_LEN = 7
MAX_ISOTP_LEN = 4095
PAD_BYTE = 0x00

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


def _decode_stmin(byte: int) -> float:
    """STmin byte -> seconds. 0x00-0x7F = 0-127 ms, 0xF1-0xF9 = 100-900 us."""
    if byte <= 0x7F:
        return byte / 1000.0
    if 0xF1 <= byte <= 0xF9:
        return (byte - 0xF0) * 100 / 1_000_000.0
    return 0.0  # reserved values treated as no delay


def _build_fc(flow_status: int, block_size: int = 0, stmin: int = 0) -> bytes:
    """Build a Flow Control frame payload (3 bytes)."""
    return bytes([PCI_FC | flow_status, block_size & 0xFF, stmin & 0xFF])


def _wait_for_frame(reader: can.BufferedReader, rx_id: int, timeout_s: float) -> Optional[can.Message]:
    """Wait for a CAN message with the given arbitration ID."""
    deadline = time.perf_counter() + timeout_s
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        msg = reader.get_message(timeout=remaining)
        if msg is None:
            return None
        if msg.arbitration_id == rx_id:
            return msg
        # ignore unrelated frames


def _wait_for_fc(reader: can.BufferedReader, fc_id: int, timeout_s: float) -> Optional[bytes]:
    deadline = time.perf_counter() + timeout_s
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        msg = reader.get_message(timeout=remaining)
        if msg is None:
            return None
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
) -> dict:
    if not data:
        raise IsoTpError("전송할 데이터가 없습니다")
    if len(data) > MAX_ISOTP_LEN:
        raise IsoTpError(f"ISO-TP 최대 길이({MAX_ISOTP_LEN}바이트)를 초과했습니다")
    if can_manager.notifier is None:
        raise IsoTpError("CAN 버스가 연결되어 있지 않습니다")

    t0 = time.perf_counter()

    if len(data) <= SF_MAX_LEN:
        frame = _pad(bytes([len(data)]) + data)
        can_manager.send(tx_id, frame, is_extended_id)
        return {
            "sent": True,
            "frame_type": "single",
            "frames_sent": 1,
            "bytes_sent": len(data),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    total_len = len(data)
    ff = bytes([PCI_FF | ((total_len >> 8) & 0x0F), total_len & 0xFF]) + data[:FF_DATA_LEN]
    reader = can.BufferedReader()
    can_manager.notifier.add_listener(reader)
    try:
        can_manager.send(tx_id, _pad(ff), is_extended_id)
        remaining = data[FF_DATA_LEN:]
        frames_sent = 1
        sn = 1
        wait_count = 0

        while remaining:
            fc = _wait_for_fc(reader, fc_id, fc_timeout_s)
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
            stmin = _decode_stmin(fc[2])
            block_count = 0
            while remaining and (block_size == 0 or block_count < block_size):
                if stmin > 0 and block_count > 0:
                    time.sleep(stmin)
                chunk, remaining = remaining[:CF_DATA_LEN], remaining[CF_DATA_LEN:]
                can_manager.send(tx_id, _pad(bytes([PCI_CF | (sn & 0x0F)]) + chunk), is_extended_id)
                frames_sent += 1
                sn = (sn + 1) % 16
                block_count += 1
    finally:
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

    reader = can.BufferedReader()
    can_manager.notifier.add_listener(reader)
    t0 = time.perf_counter()

    try:
        # Wait for the first frame (either SF or FF)
        msg = _wait_for_frame(reader, rx_id, timeout_s)
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

            # Data carried in FF (after the 2-byte length)
            payload = bytearray(data[2:2 + FF_DATA_LEN])

            # Send Flow Control (CTS, unlimited block size)
            fc = _build_fc(FS_CTS, fc_block_size, fc_stmin)
            can_manager.send(tx_id, _pad(fc), is_extended_id)

            # Receive Consecutive Frames
            expected_sn = 1
            remaining = total_length - FF_DATA_LEN
            while remaining > 0:
                cf_timeout = max(0.1, timeout_s - (time.perf_counter() - t0))
                if cf_timeout <= 0:
                    raise IsoTpError("Consecutive Frame 수신 중 시간 초과되었습니다")

                cf_msg = _wait_for_frame(reader, rx_id, cf_timeout)
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

                chunk_len = min(CF_DATA_LEN, remaining)
                payload.extend(cf_data[1:1 + chunk_len])
                remaining -= chunk_len
                expected_sn = (expected_sn + 1) % 16

            return bytes(payload)

        else:
            raise IsoTpError(f"알 수 없는 PCI 타입: 0x{pci:02x}")

    finally:
        can_manager.notifier.remove_listener(reader)