"""MCP server tests — virtual bus only, no hardware required.

Covers: service binder, guard rails (running/connected/DBC), Tier 0
connect/status/run, DBC tools, artifact_load dispatch, TX tools,
replay/testrunner controls, confirm-gate rejections for all Tier 3
destructive ops, layout CRUD, log/audio/syslog/canlog probes.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server
from mcp_server import bind_services

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _fresh_stack(tmp_path):
    """Bind a fully virtual service set (mirrors main.py globals)."""
    import isotp_service as _isotp
    from audio_service import AudioService
    from can_log_service import CanLogService
    from can_manager import CanManager
    from dbc_service import DbcService
    from log_service import LogService
    from ota_tester_download_manager import OtaTesterDownloadManager
    from power_supply_service import PowerSupplyService
    from replay_service import ReplayService
    from seedkey_client import SeedKeyService
    from syslog_service import SysLogService
    from syslog_upload_manager import SysLogUploadManager
    from test_runner_service import TestRunnerService
    from tx_scheduler import TxScheduler
    from uds_download_manager import MultiUdsDownloadManager

    base = tmp_path / "mcp"
    (base / "can_logs").mkdir(parents=True)
    can = CanManager()
    dbc = DbcService()
    tx = TxScheduler(can, dbc)
    replay = ReplayService(can)
    seed = SeedKeyService()
    svcs = dict(
        can_manager=can, dbc_service=dbc, tx_scheduler=tx,
        replay_service=replay,
        log_service=LogService(can, base / "can_logs"),
        power_supply_service=PowerSupplyService(),
        audio_service=AudioService(base / "aud", base / "gold"),
        syslog_service=SysLogService(), can_log_service=CanLogService(dbc),
        test_runner_service=TestRunnerService(
            can, dbc, tx, replay, base / "logs", base / "results"),
        seedkey_service=seed,
        uds_download_manager=MultiUdsDownloadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        ota_tester_manager=OtaTesterDownloadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        syslog_upload_manager=SysLogUploadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        settings={"ws_flush_ms": 30}, run_state={"running": True},
        layout_dir=base / "layouts", base_dir=base,
    )
    bind_services(**svcs)
    # virtual bus + sample DBC for every test (fast, <50ms)
    mcp_server.can_connect(interface="virtual", channel="mcp_ut")
    mcp_server.artifact_load(
        target="dbc",
        path=str(SAMPLES / "sample.dbc"), filename="sample.dbc",
    )
    return svcs


@pytest.fixture()
def stack(tmp_path):
    svcs = _fresh_stack(tmp_path)
    yield svcs
    try:
        svcs["tx_scheduler"].shutdown()
    except Exception:
        pass
    try:
        svcs["can_manager"].disconnect()
    except Exception:
        pass


# ---- binder / guards -----------------------------------------------------


def test_unbound_service_raises(tmp_path):
    mcp_server._SERVICES.clear()
    with pytest.raises(RuntimeError, match="바인딩"):
        mcp_server.can_status()
    _fresh_stack(tmp_path)  # rebind for subsequent tests (fixture rebinds anyway)


def test_guards_require_running_and_dbc(tmp_path):
    svcs = _fresh_stack(tmp_path)
    try:
        svcs["run_state"]["running"] = False
        with pytest.raises(RuntimeError, match="정지"):
            mcp_server.tx_signal("EngineData", {"EngineSpeed": 1000})
        svcs["run_state"]["running"] = True
        svcs["can_manager"].disconnect()
        with pytest.raises(RuntimeError, match="not connected"):
            mcp_server.tx_signal("EngineData", {"EngineSpeed": 1000})
    finally:
        svcs["tx_scheduler"].shutdown()


# ---- Tier 0 ---------------------------------------------------------------


def test_can_status_shape(stack):
    st = mcp_server.can_status()
    for key in ("can", "tx", "replay", "dbc", "run", "test_runner",
                "uds", "ota_tester", "power", "audio", "log"):
        assert key in st, key
    assert st["dbc"]["loaded"] is True
    assert st["can"]["connected"] is True


def test_run_stop_starts_clean(stack):
    mcp_server.tx_signal("EngineData", {"EngineSpeed": 1500})
    assert mcp_server.can_status()["tx"]["auto_entries"]
    mcp_server.run_stop()
    assert mcp_server.can_status()["run"]["running"] is False
    assert mcp_server.can_status()["tx"]["auto_entries"] == []
    with pytest.raises(RuntimeError, match="정지"):
        mcp_server.tx_signal("EngineData", {"EngineSpeed": 1500})
    mcp_server.run_start()
    assert mcp_server.can_status()["run"]["running"] is True


def test_tool_registry_covers_all_tiers():
    async def _list():
        return await mcp_server.mcp.list_tools()

    import asyncio

    names = {t.name for t in asyncio.run(_list())}
    for expected in (
        "can_connect", "can_disconnect", "can_status", "run_start", "run_stop",
        "dbc_summary", "dbc_signal_info", "dbc_set_send_type", "dbc_message_initial",
        "artifact_load", "tx_signal", "tx_signal_pulse", "tx_send_once",
        "tx_table_configure", "tx_table_control", "tx_row_control",
        "tx_periodic_all", "tx_auto_stop", "tx_generator",
        "isotp_send", "isotp_security_access", "replay_control",
        "testrunner_control", "testrunner_status",
        "uds_swdl_control", "uds_swdl_status", "uds_swdl_steps",
        "ota_tester_control", "ota_tester_status", "ota_case_manage",
        "power_measure", "power_control", "audio_probe", "audio_control",
        "syslog_query", "canlog_query", "syslog_upload_control", "log_control",
        "seedkey_status", "seedkey_load", "layout_control", "server_shutdown",
    ):
        assert expected in names, expected


# ---- DBC ------------------------------------------------------------------


def test_dbc_tools(stack):
    summary = mcp_server.dbc_summary()
    assert summary["messages"], "sample.dbc should expose messages"
    info = mcp_server.dbc_signal_info("EngineData", "EngineSpeed")
    assert info["name"] == "EngineSpeed" and "send_type" in info
    init = mcp_server.dbc_message_initial("EngineData")
    assert init["data_hex"] and init["length"] > 0
    mcp_server.dbc_set_send_type("EngineData", "EngineSpeed", "event")
    assert mcp_server.dbc_signal_info("EngineData", "EngineSpeed")["send_type"] == "event"
    mcp_server.dbc_set_send_type("EngineData", "EngineSpeed", "periodic")
    with pytest.raises(RuntimeError):
        mcp_server.dbc_set_send_type("EngineData", "EngineSpeed", "bogus")
    with pytest.raises(RuntimeError, match="메시지 없음"):
        mcp_server.dbc_signal_info("Nope", "X")


# ---- TX -------------------------------------------------------------------


def test_tx_signal_periodic_and_event(stack):
    # EngineData.EngineSpeed is periodic in sample.dbc -> arms auto entry
    mcp_server.tx_signal("EngineData", {"EngineSpeed": 2000})
    assert mcp_server.can_status()["tx"]["auto_entries"]
    mcp_server.tx_auto_stop("EngineData")
    assert mcp_server.can_status()["tx"]["auto_entries"] == []
    # once=True must not arm anything
    mcp_server.tx_signal("EngineData", {"EngineSpeed": 2000}, once=True)
    assert mcp_server.can_status()["tx"]["auto_entries"] == []


def test_tx_send_once_classic_rejects_fd_payload(stack):
    with pytest.raises(RuntimeError):
        mcp_server.tx_send_once(0x123, "00 " * 20)  # 20B on classic bus
    assert isinstance(mcp_server.tx_send_once(0x123, "01 02"), dict)


def test_tx_table_and_row(stack):
    mcp_server.tx_table_configure([{
        "key": "mcp1", "arbitration_id": 0x321,
        "period_ms": 100, "data": "0102030405060708",
    }])
    mcp_server.tx_table_control("start")
    mcp_server.tx_table_control("stop")
    mcp_server.tx_row_control("start", key="mcp-row", arbitration_id=0x322,
                              data_hex="AA", period_ms=100)
    mcp_server.tx_row_control("update", key="mcp-row", period_ms=200)
    assert mcp_server.tx_row_control("stop", key="mcp-row") is not None
    with pytest.raises(RuntimeError):
        mcp_server.tx_table_control("bogus")


def test_tx_periodic_all_and_generator(stack):
    mcp_server.tx_periodic_all("enable")
    assert mcp_server.can_status()["tx"]["auto_entries"]
    mcp_server.tx_periodic_all("disable")
    mcp_server.tx_generator("set", "DriverCommand", "TurnSignal",
                            mode="random", range_min=2, range_max=5)
    mcp_server.tx_generator("send", "DriverCommand", "TurnSignal")
    assert mcp_server.tx_generator("stop", "DriverCommand", "TurnSignal") is not None
    mcp_server.tx_generator("invalid", "DriverCommand", "TurnSignal")
    with pytest.raises(RuntimeError):
        mcp_server.tx_generator("bogus", "DriverCommand", "TurnSignal")


def test_isotp_guards(stack):
    # virtual bus, no responder: short FC timeout -> clean RuntimeError, not hang
    with pytest.raises(RuntimeError):
        mcp_server.isotp_send(0x700, 0x780, "11 22 33 44 55 66 77 88 99",
                              fc_timeout_ms=100)
    with pytest.raises(RuntimeError):
        mcp_server.isotp_security_access(0x700, 0x780, 0x700,
                                        fc_timeout_ms=50, resp_timeout_ms=50)


# ---- replay / testrunner / logs -------------------------------------------


def test_replay_control(stack):
    import time

    # sample.blf contains FD frames -> replay on an FD virtual bus,
    # otherwise CanManager.send rejects >8B payloads and the replay
    # thread stops at the first FD frame (replay_service._run).
    mcp_server.can_disconnect()
    mcp_server.can_connect(interface="virtual", channel="mcp_ut_fd", fd=True)
    mcp_server.artifact_load(
        target="replay", path=str(SAMPLES / "sample.blf"), filename="sample.blf")
    info = mcp_server.replay_control("start", mode="stop")
    assert info["loaded"] is True
    # sample.blf is short — the run may finish before we pause; poll first.
    paused = False
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        if mcp_server.can_status()["replay"]["progress"].get("running"):
            mcp_server.replay_control("pause")
            mcp_server.replay_control("resume")
            paused = True
            break
        time.sleep(0.01)
    mcp_server.replay_control("stop")
    assert mcp_server.can_status()["replay"]["progress"].get("running") is False
    assert paused, "replay finished before pause could be exercised (sample too short?)"


def test_testrunner_load_and_status(stack):
    mcp_server.artifact_load(
        target="testrunner_script",
        path=str(SAMPLES / "test_script_Test01.json"),
        filename="test_script_Test01.json",
    )
    st = mcp_server.testrunner_status()
    assert st
    mcp_server.testrunner_control("stop")
    with pytest.raises(RuntimeError):
        mcp_server.testrunner_control("start_function")  # missing name


def test_syslog_canlog_log_audio_probes(stack):
    assert mcp_server.syslog_query("status")
    assert mcp_server.canlog_query("status")
    assert mcp_server.log_control("status") is not None
    mcp_server.log_control("start")
    mcp_server.log_control("stop")
    assert mcp_server.audio_probe("info")
    assert mcp_server.syslog_upload_control("status") is not None
    assert mcp_server.seedkey_status() is not None
    assert mcp_server.uds_swdl_status()["slots"] is not None
    assert mcp_server.ota_tester_status() is not None
    assert mcp_server.ota_case_manage("list") is not None
    assert mcp_server.power_measure() is not None


def test_layout_crud(stack):
    assert mcp_server.layout_control("list") == {"layouts": []}
    body = {"pages": [], "canConfig": {"iface": "virtual"}}
    assert mcp_server.layout_control("save", name="mcp1", body=body) == {"saved": "mcp1"}
    assert mcp_server.layout_control("list") == {"layouts": ["mcp1"]}
    assert mcp_server.layout_control("get", name="mcp1") == body
    assert mcp_server.layout_control("delete", name="mcp1") == {"deleted": "mcp1"}
    with pytest.raises(RuntimeError):
        mcp_server.layout_control("get", name="mcp1")


def test_artifact_load_rejects_bad_target(stack):
    with pytest.raises(RuntimeError, match="target은"):
        mcp_server.artifact_load(target="bogus", content="x", filename="x")


# ---- Tier 3 confirm gates (must refuse without confirm) --------------------


def test_destructive_ops_require_confirm(stack):
    with pytest.raises(RuntimeError, match="사전 확인"):
        mcp_server.power_control("battery", voltage=12.0, current=1.0)
    with pytest.raises(RuntimeError, match="사전 확인"):
        mcp_server.uds_swdl_control("start", slot_index=0)
    with pytest.raises(RuntimeError, match="사전 확인"):
        mcp_server.ota_tester_control("start", request_id=1, response_id=2)
    with pytest.raises(RuntimeError, match="사전 확인"):
        mcp_server.seedkey_load("/nonexistent.dll")
    with pytest.raises(RuntimeError, match="사전 확인"):
        mcp_server.server_shutdown()
    # non-destructive power ops stay open
    assert mcp_server.power_control("disconnect") is not None
    # confirmed-but-invalid still surfaces the real error, not the gate
    with pytest.raises(RuntimeError):
        mcp_server.seedkey_load("/nonexistent.dll", confirm=True)
