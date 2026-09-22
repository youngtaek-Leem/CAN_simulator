"""Tests for can_log_service.get_frames (message table under the graphs)."""

from conftest import SAMPLES_DIR

from can_log_service import CanLogService
from dbc_service import DbcService


def make_service(with_dbc: bool = True) -> CanLogService:
    dbc = DbcService()
    if with_dbc:
        dbc.load_string((SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8"), "sample.dbc")
    svc = CanLogService(dbc)
    svc.load_log((SAMPLES_DIR / "sample.blf").read_bytes(), "sample.blf")
    return svc


def test_frames_window_and_limit():
    svc = make_service()
    full = svc.get_frames(0, 10**9, None, 2000)
    assert full["total"] == 906
    assert full["truncated"] is False
    assert len(full["frames"]) == 906
    # time-ordered
    xs = [f["x_ms"] for f in full["frames"]]
    assert xs == sorted(xs)
    limited = svc.get_frames(0, 10**9, None, 10)
    assert limited["total"] == 906
    assert limited["truncated"] is True
    assert len(limited["frames"]) == 10


def test_frames_window_slice():
    svc = make_service()
    full = svc.get_frames(0, 10**9, None, 2000)
    mid_x = full["frames"][len(full["frames"]) // 2]["x_ms"]
    part = svc.get_frames(mid_x, 10**9, None, 2000)
    assert 0 < part["total"] < full["total"]
    assert all(f["x_ms"] >= mid_x for f in part["frames"])
    empty = svc.get_frames(10**12, 10**13, None, 500)
    assert empty == {"frames": [], "total": 0, "truncated": False}


def test_frames_message_filter_and_decode():
    svc = make_service()
    only = svc.get_frames(0, 10**9, ["EngineData"], 2000)
    assert only["total"] > 0
    assert all(f["message"] == "EngineData" for f in only["frames"])
    row = only["frames"][0]
    assert row["frame_id_hex"].startswith("0x")
    assert row["dlc"] == len(bytes.fromhex(row["data_hex"].replace(" ", "")))
    assert row["signals"] and "EngineSpeed" in row["signals"]
    unknown = svc.get_frames(0, 10**9, ["NoSuchMessage"], 2000)
    assert unknown["total"] == 0


def test_frames_without_dbc():
    svc = make_service(with_dbc=False)
    res = svc.get_frames(0, 10**9, None, 3)
    assert res["total"] == 906
    row = res["frames"][0]
    assert row["message"] is None
    assert row["signals"] is None
    assert row["data_hex"]


def test_frames_empty_log():
    svc = CanLogService(DbcService())
    assert svc.get_frames(0, 100, None, 500) == {"frames": [], "total": 0, "truncated": False}
