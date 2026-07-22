import time

import can
import pytest

from can_manager import CanManager
from log_service import LogService


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
