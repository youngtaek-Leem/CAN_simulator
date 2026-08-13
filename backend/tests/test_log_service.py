import time

import can
import pytest

from can_manager import CanManager
from log_service import AutoCanLogger, LogService


@pytest.fixture
def stack(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_log", receive_own_messages=False)
    peer = can.Bus(interface="virtual", channel="t_log")
    svc = LogService(cm, tmp_path)
    yield cm, peer, svc, tmp_path
    svc.stop()
    peer.shutdown()
    cm.disconnect()


def test_start_requires_connection(tmp_path):
    cm = CanManager()
    svc = LogService(cm, tmp_path)
    with pytest.raises(RuntimeError):
        svc.start()


def test_start_records_and_stop_flushes(stack):
    cm, peer, svc, tmp_path = stack
    status = svc.start()
    assert status["recording"] is True
    assert status["filename"].endswith(".blf")

    for i in range(5):
        peer.send(can.Message(arbitration_id=0x100 + i, data=bytes([i] * 4), is_extended_id=False))
    time.sleep(0.3)

    mid = svc.status()
    assert mid["count"] == 5

    final = svc.stop()
    assert final["recording"] is False
    assert final["count"] == 5

    path = tmp_path / status["filename"]
    with can.BLFReader(str(path)) as reader:
        frames = list(reader)
    assert len(frames) == 5
    assert {f.arbitration_id for f in frames} == {0x100, 0x101, 0x102, 0x103, 0x104}


def test_double_start_raises(stack):
    cm, peer, svc, tmp_path = stack
    svc.start()
    with pytest.raises(RuntimeError):
        svc.start()


def test_stop_without_start_is_noop(stack):
    cm, peer, svc, tmp_path = stack
    status = svc.stop()
    assert status["recording"] is False
    assert status["count"] == 0


def test_status_while_idle(tmp_path):
    cm = CanManager()
    svc = LogService(cm, tmp_path)
    status = svc.status()
    assert status == {
        "recording": False,
        "filename": None,
        "count": 0,
        "duration_s": 0.0,
    }


# ---- AutoCanLogger (per-slot/per-case auto ASCII log, CAN-SWDL/OTA Tester) ----


def test_auto_can_logger_writes_asc_and_renames_on_success(stack):
    cm, peer, _svc, tmp_path = stack
    logger = AutoCanLogger(cm, tmp_path)
    logger.start("slot1_RS4PE_01")

    for i in range(3):
        peer.send(can.Message(arbitration_id=0x783, data=bytes([i] * 4), is_extended_id=False))
    time.sleep(0.2)

    logger.stop(success=True)

    files = list(tmp_path.glob("canlog_*_slot1_RS4PE_01_success.asc"))
    assert len(files) == 1
    with can.ASCReader(str(files[0])) as reader:
        frames = list(reader)
    assert len(frames) == 3
    assert all(f.arbitration_id == 0x783 for f in frames)


def test_auto_can_logger_renames_on_failure(stack):
    cm, peer, _svc, tmp_path = stack
    logger = AutoCanLogger(cm, tmp_path)
    logger.start("case-A")
    logger.stop(success=False)

    files = list(tmp_path.glob("canlog_*_case-A_fail.asc"))
    assert len(files) == 1


def test_auto_can_logger_start_is_noop_when_not_connected(tmp_path):
    cm = CanManager()  # never connected
    logger = AutoCanLogger(cm, tmp_path)
    logger.start("slot1")
    assert logger.active is False
    assert list(tmp_path.glob("*.asc")) == []


def test_auto_can_logger_double_start_is_noop(stack):
    cm, _peer, _svc, tmp_path = stack
    logger = AutoCanLogger(cm, tmp_path)
    logger.start("slot1")
    logger.start("slot1-again")  # ignored -- already logging
    logger.stop(success=True)
    # Only the first start's file exists (the second start() call never opened one).
    assert len(list(tmp_path.glob("canlog_*.asc"))) == 1


def test_auto_can_logger_stop_without_start_is_noop(tmp_path):
    cm = CanManager()
    logger = AutoCanLogger(cm, tmp_path)
    logger.stop(success=True)  # must not raise
    assert logger.active is False


def test_auto_can_logger_sanitizes_unsafe_label_characters(stack):
    cm, _peer, _svc, tmp_path = stack
    logger = AutoCanLogger(cm, tmp_path)
    logger.start("weird label/with:chars*?")
    logger.stop(success=True)

    files = list(tmp_path.glob("canlog_*_success.asc"))
    assert len(files) == 1
    # None of the filesystem-unsafe characters survived into the filename.
    assert not any(c in files[0].name for c in "/:*?")
