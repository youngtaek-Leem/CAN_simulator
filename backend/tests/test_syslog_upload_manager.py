"""Tests for syslog_upload_manager.py (UDS sysLog memory upload).

A background virtual-ECU thread speaks the fixed sequence from the approved
spec (10 03 / 22 F1 20 / 27 11-12 / 35 / 36 ZZ / 37) against a virtual bus --
no hardware needed.
"""

import threading
import time

import can
import pytest

from can_manager import CanManager
import isotp_service
from syslog_upload_manager import SysLogUploadManager

TX_ID = 0x6D1
RX_ID = 0x6B0

SEED = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])


def block_data(block_index: int, length: int) -> bytes:
    return bytes(((block_index + j) & 0xFF) for j in range(length))


def send_sf(peer, payload: bytes):
    peer.send(can.Message(
        arbitration_id=RX_ID,
        data=bytes([len(payload)]) + payload + bytes(8 - len(payload)),
        is_extended_id=False,
    ))


def send_multi(peer, payload: bytes):
    total = len(payload)
    peer.send(can.Message(
        arbitration_id=RX_ID,
        data=bytes([0x10 | ((total >> 8) & 0x0F), total & 0xFF]) + payload[:6],
        is_extended_id=False,
    ))
    # app's ISO-TP receiver answers with one FC (BS=0) on TX_ID
    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        m = peer.recv(timeout=0.2)
        if m is not None and m.arbitration_id == TX_ID and (m.data[0] & 0xF0) == 0x30:
            break
    rest = payload[6:]
    sn = 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        peer.send(can.Message(
            arbitration_id=RX_ID,
            data=bytes([0x20 | (sn & 0x0F)]) + chunk + bytes(7 - len(chunk)),
            is_extended_id=False,
        ))
        sn = (sn + 1) % 16


def recv_request(peer, timeout=2.0) -> bytes | None:
    """Read one full ISO-TP request on TX_ID (SF or FF+CF)."""
    deadline = time.perf_counter() + timeout
    buf = None
    total = 0
    while time.perf_counter() < deadline:
        m = peer.recv(timeout=0.2)
        if m is None:
            continue
        if m.arbitration_id != TX_ID:
            continue
        pci = m.data[0]
        if (pci & 0xF0) == 0x00:
            return bytes(m.data[1:1 + (pci & 0x0F)])
        if (pci & 0xF0) == 0x10:
            total = ((pci & 0x0F) << 8) | m.data[1]
            buf = bytearray(m.data[2:8])
            peer.send(can.Message(
                arbitration_id=RX_ID,
                data=bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0]),
                is_extended_id=False,
            ))
            while len(buf) < total:
                cf = peer.recv(timeout=1.0)
                if cf is None or cf.arbitration_id != TX_ID:
                    continue
                buf.extend(cf.data[1:8])
            return bytes(buf[:total])
    return buf


def make_ecu(peer, mem_size: int, use_security: bool = True):
    """Background virtual ECU. Returns (stop_event, thread, seen dict)."""
    stop = threading.Event()
    seen: dict = {"ask": False, "blocks": 0}

    def run():
        while not stop.is_set():
            req = recv_request(peer, timeout=0.3)
            if req is None:
                continue
            if req[:2] == bytes([0x10, 0x03]):
                send_sf(peer, bytes([0x50, 0x03]))
            elif req[:3] == bytes([0x22, 0xF1, 0x20]):
                send_sf(peer, bytes([0x62, 0xF1, 0x20]) + mem_size.to_bytes(4, "big"))
            elif req[:2] == bytes([0x27, 0x11]):
                seen["ask"] = True
                assert use_security, "ASK should have been skipped"
                send_multi(peer, bytes([0x67, 0x11]) + SEED)
            elif req[:2] == bytes([0x27, 0x12]):
                send_sf(peer, bytes([0x67, 0x12]))
            elif req[:1] == bytes([0x35]):
                send_sf(peer, bytes([0x75, 0x20, 0x04, 0x02]))
            elif req[:1] == bytes([0x36]) and len(req) == 2:
                seq = req[1]
                offset = seen["blocks"] * 1024
                chunk = block_data(seen["blocks"], min(1024, mem_size - offset))
                seen["blocks"] += 1
                send_multi(peer, bytes([0x76, seq]) + chunk)
            elif req[:1] == bytes([0x37]):
                send_sf(peer, bytes([0x77]))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop, t, seen


@pytest.fixture
def bus(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_syslog_up", receive_own_messages=False)
    peer = can.Bus(interface="virtual", channel="t_syslog_up")
    mgr = SysLogUploadManager(
        cm, isotp_service.send, isotp_service.receive,
        seedkey_service=None, log_dir=tmp_path,
    )
    yield cm, peer, mgr
    try:
        mgr.stop()
    except Exception:
        pass
    peer.shutdown()
    cm.disconnect()


def wait_done(mgr, timeout_s=30.0) -> dict:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        st = mgr.status()
        if st["state"] in ("COMPLETED", "ERROR"):
            return st
        time.sleep(0.1)
    raise TimeoutError("upload did not finish in time")


def test_full_upload_flow(bus, tmp_path):
    cm, peer, mgr = bus
    mem_size = 3000
    stop, t, seen = make_ecu(peer, mem_size)
    try:
        mgr.start(request_id=TX_ID, response_id=RX_ID, address=0, security_enable=True)
        st = wait_done(mgr)
        assert st["state"] == "COMPLETED", st["error"]
        assert seen["ask"] is True
        assert seen["blocks"] == 3  # 1024 + 1024 + 952
        expected = (
            block_data(0, 1024) + block_data(1, 1024) + block_data(2, 952)
        )
        assert (tmp_path / st["saved_filename"]).read_bytes() == expected
        assert st["saved_size"] == mem_size
        assert st["progress"]["percent"] == 100.0
    finally:
        stop.set()
        t.join(timeout=2)


def test_security_disabled_skips_ask(bus, tmp_path):
    cm, peer, mgr = bus
    stop, t, seen = make_ecu(peer, 100, use_security=False)
    try:
        mgr.start(request_id=TX_ID, response_id=RX_ID, address=0, security_enable=False)
        st = wait_done(mgr)
        assert st["state"] == "COMPLETED", st["error"]
        assert seen["ask"] is False
        assert (tmp_path / st["saved_filename"]).read_bytes() == block_data(0, 100)
    finally:
        stop.set()
        t.join(timeout=2)


def test_zero_size_fails(bus):
    cm, peer, mgr = bus
    stop, t, seen = make_ecu(peer, 0)
    try:
        mgr.start(request_id=TX_ID, response_id=RX_ID, address=0, security_enable=False)
        st = wait_done(mgr)
        assert st["state"] == "ERROR"
        assert "크기" in (st["error"] or "")
    finally:
        stop.set()
        t.join(timeout=2)


def test_stop_mid_transfer(bus):
    cm, peer, mgr = bus
    stop, t, seen = make_ecu(peer, 5000)
    try:
        mgr.start(request_id=TX_ID, response_id=RX_ID, address=0, security_enable=False)
        time.sleep(0.5)
        mgr.stop()
        assert mgr.status()["running"] is False
    finally:
        stop.set()
        t.join(timeout=2)
