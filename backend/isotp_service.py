"""ISO-TP (ISO 15765-2) transport-layer sender, classic addressing.

Single Frame is used for payloads up to 7 bytes and sent immediately. Longer
payloads (up to the classic 12-bit length field's 4095 bytes) are sent as a
First Frame followed by Consecutive Frames, waiting for a Flow Control frame
from the receiver (arriving on `fc_id`) before each block and honoring its
Block Size (BS) and STmin, per ISO 15765-2. All frames are padded to 8 bytes.

This module only implements the sender side; it does not implement ISO-TP
reception/reassembly.
"""

import time
from typing import Optional

import can

SF_MAX_LEN = 7
FF_DATA_LEN = 6
CF_DATA_LEN = 7
MAX_ISOTP_LEN = 4095
PAD_BYTE = 0x00


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
        if (msg.data[0] & 0xF0) != 0x30:
            continue  # not a Flow Control PCI, keep waiting
        return bytes(msg.data[:3])


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
    ff = bytes([0x10 | ((total_len >> 8) & 0x0F), total_len & 0xFF]) + data[:FF_DATA_LEN]
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
            if fs == 1:  # WAIT: reset the timer and wait for the next FC
                wait_count += 1
                if wait_count > max_wait_frames:
                    raise IsoTpError("Flow Control WAIT 횟수를 초과했습니다")
                continue
            if fs == 2:  # Overflow / abort
                raise IsoTpError("수신측이 Flow Control Overflow(중단)를 보냈습니다")
            if fs != 0:
                raise IsoTpError(f"알 수 없는 Flow Control 상태 값({fs})입니다")

            block_size = fc[1]
            stmin = _decode_stmin(fc[2])
            block_count = 0
            while remaining and (block_size == 0 or block_count < block_size):
                if stmin > 0 and block_count > 0:
                    time.sleep(stmin)
                chunk, remaining = remaining[:CF_DATA_LEN], remaining[CF_DATA_LEN:]
                can_manager.send(tx_id, _pad(bytes([0x20 | (sn & 0x0F)]) + chunk), is_extended_id)
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
