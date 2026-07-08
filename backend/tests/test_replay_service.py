import time
from pathlib import Path

import can
import pytest

from can_manager import CanManager
from replay_service import ReplayService


def write_log(path: Path, writer_cls):
    """10 frames over ~0.45 s; ids alternate 0x100 / 0x101."""
    with writer_cls(str(path)) as writer:
        for i in range(10):
            writer.on_message_received(
                can.Message(
                    arbitration_id=0x100 + (i % 2),
                    data=bytes([i] * 4),
                    is_extended_id=False,
                    timestamp=i * 0.05,
                    is_rx=(i % 2 == 0),
                )
            )


@pytest.fixture
def stack(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_replay", receive_own_messages=False)
    peer = can.Bus(interface="virtual", channel="t_replay")
    svc = ReplayService(cm)
    yield cm, peer, svc, tmp_path
    svc.stop()
    peer.shutdown()
    cm.disconnect()


def drain(peer):
    frames = []
    while True:
        msg = peer.recv(timeout=0.05)
        if msg is None:
            return frames
        frames.append(msg)


def test_replay_no_filter_sends_all(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.asc"
    write_log(log, can.ASCWriter)
    info = svc.load(str(log))
    assert info["message_count"] == 10

    svc.start(mode="pass", frame_ids=[])
    time.sleep(0.8)
    frames = drain(peer)
    assert len(frames) == 10
    assert svc.info()["progress"]["sent"] == 10


def test_replay_pass_filter_selected_only(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.asc"
    write_log(log, can.ASCWriter)
    svc.load(str(log))

    svc.start(mode="pass", frame_ids=[0x100])
    time.sleep(0.8)
    frames = drain(peer)
    assert len(frames) == 5
    assert all(f.arbitration_id == 0x100 for f in frames)
    progress = svc.info()["progress"]
    assert progress["sent"] == 5
    assert progress["skipped"] == 5


def test_replay_stop_filter_excludes_selected(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.asc"
    write_log(log, can.ASCWriter)
    svc.load(str(log))

    svc.start(mode="stop", frame_ids=[0x100])
    time.sleep(0.8)
    frames = drain(peer)
    assert len(frames) == 5
    assert all(f.arbitration_id == 0x101 for f in frames)
    progress = svc.info()["progress"]
    assert progress["sent"] == 5
    assert progress["skipped"] == 5


def test_blf_load_and_replay(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.blf"
    write_log(log, can.BLFWriter)
    info = svc.load(str(log))
    assert info["message_count"] == 10

    svc.start(mode="pass", frame_ids=[])
    time.sleep(0.8)
    assert len(drain(peer)) == 10


def test_stop_mid_replay(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.asc"
    write_log(log, can.ASCWriter)
    svc.load(str(log))
    svc.start(mode="pass")
    time.sleep(0.15)
    info = svc.stop()
    assert info["progress"]["running"] is False
    assert info["progress"]["sent"] < 10


def test_invalid_mode(stack):
    cm, peer, svc, tmp_path = stack
    log = tmp_path / "log.asc"
    write_log(log, can.ASCWriter)
    svc.load(str(log))
    with pytest.raises(ValueError):
        svc.start(mode="bogus")


def test_unsupported_format(stack):
    cm, peer, svc, tmp_path = stack
    bad = tmp_path / "log.txt"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        svc.load(str(bad))


def test_replay_preserves_fd_frames(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_replay_fd", receive_own_messages=False, fd=True)
    peer = can.Bus(interface="virtual", channel="t_replay_fd")
    svc = ReplayService(cm)
    try:
        log = tmp_path / "fd.asc"
        with can.ASCWriter(str(log)) as writer:
            writer.on_message_received(
                can.Message(
                    arbitration_id=0x500,
                    data=bytes(range(20)),
                    is_fd=True,
                    bitrate_switch=True,
                    timestamp=0.0,
                    is_rx=True,
                )
            )
        svc.load(str(log))
        svc.start(mode="pass")
        time.sleep(0.3)
        frames = drain(peer)
        assert len(frames) == 1
        assert len(frames[0].data) == 20
        assert frames[0].is_fd is True
        assert frames[0].bitrate_switch is True
    finally:
        svc.stop()
        peer.shutdown()
        cm.disconnect()
