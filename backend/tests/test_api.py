import json
import threading
import time

import can
from conftest import SAMPLES_DIR
from fastapi.testclient import TestClient

import main


def make_client():
    return TestClient(main.app)


def test_full_api_flow():
    with make_client() as client:
        # status before anything
        status = client.get("/api/status").json()
        assert status["can"]["connected"] is False

        # connect virtual bus
        r = client.post(
            "/api/connect",
            json={"interface": "virtual", "channel": "t_api"},
        )
        assert r.status_code == 200
        assert r.json()["connected"] is True

        # upload DBC
        r = client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        assert r.status_code == 200
        assert r.json()["loaded"] is True

        # send a periodic signal -> auto entry appears
        r = client.post(
            "/api/tx/signal",
            json={"message_name": "EngineData", "values": {"EngineSpeed": 1500}},
        )
        assert r.status_code == 200
        assert r.json()["signals"]["EngineSpeed"] == "periodic"
        status = client.get("/api/status").json()
        assert len(status["tx"]["auto_entries"]) == 1
        # a widget-armed periodic signal must not look like "Enable Msg" was
        # pressed (see tx_scheduler.py's _enable_msg_armed)
        assert status["tx"]["periodic_enabled"] is False
        client.post("/api/tx/auto/stop", json={})

        # configure + start/stop TX list
        r = client.post(
            "/api/tx/configure",
            json={
                "entries": [
                    {"key": "1", "arbitration_id": 0x111, "period_ms": 50, "data": "AABB"}
                ]
            },
        )
        assert r.status_code == 200
        assert client.post("/api/tx/start").json()["running"] is True
        assert client.post("/api/tx/stop").json()["running"] is False

        # layouts CRUD
        client.post("/api/layouts/test1", json={"widgets": [1, 2, 3]})
        assert "test1" in client.get("/api/layouts").json()["layouts"]
        assert client.get("/api/layouts/test1").json()["widgets"] == [1, 2, 3]
        client.delete("/api/layouts/test1")
        assert "test1" not in client.get("/api/layouts").json()["layouts"]

        client.post("/api/disconnect")


def test_global_run_gate():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_run"})
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        # global stop blocks all TX-side actions
        r = client.post("/api/run/stop")
        assert r.json()["run"]["running"] is False
        assert r.json()["tx"]["paused"] is True
        for path, body in (
            ("/api/tx/start", None),
            ("/api/tx/signal", {"message_name": "EngineData", "values": {"EngineSpeed": 1}}),
            ("/api/replay/start", {"mode": "pass", "frame_ids": []}),
        ):
            resp = client.post(path, json=body)
            assert resp.status_code == 400, path
        # start re-enables
        r = client.post("/api/run/start")
        assert r.json()["run"]["running"] is True
        assert r.json()["tx"]["paused"] is False
        r = client.post(
            "/api/tx/signal",
            json={"message_name": "EngineData", "values": {"EngineSpeed": 1}},
        )
        assert r.status_code == 200
        client.post("/api/tx/auto/stop", json={})
        client.post("/api/disconnect")


def test_connect_with_fd_and_signal_send():
    with make_client() as client:
        r = client.post(
            "/api/connect",
            json={
                "interface": "virtual",
                "channel": "t_api_fd",
                "fd": True,
                "data_bitrate": 4_000_000,
            },
        )
        assert r.status_code == 200
        assert r.json()["config"]["fd"] is True
        assert r.json()["config"]["data_bitrate"] == 4_000_000

        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        r = client.post(
            "/api/tx/signal",
            json={"message_name": "FdSensorData", "values": {"Pressure": 100.0}},
        )
        assert r.status_code == 200
        client.post("/api/tx/auto/stop", json={})
        client.post("/api/disconnect")


def test_isotp_send_single_and_multi_frame():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_isotp"})
        peer = can.Bus(interface="virtual", channel="t_api_isotp")
        try:
            # single frame: no FC needed
            r = client.post(
                "/api/isotp/send",
                json={"tx_id": 0x783, "fc_id": 0x78B, "data": "01 02 03"},
            )
            assert r.status_code == 200
            assert r.json()["frame_type"] == "single"
            msg = peer.recv(timeout=1.0)
            assert msg.data[0] == 0x03

            # multi-frame: needs a Flow Control responder
            def fc_responder():
                m = peer.recv(timeout=1.0)
                assert m.data[0] & 0xF0 == 0x10
                peer.send(
                    can.Message(
                        arbitration_id=0x78B,
                        data=bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0]),
                        is_extended_id=False,
                    )
                )

            t = threading.Thread(target=fc_responder, daemon=True)
            t.start()
            r = client.post(
                "/api/isotp/send",
                json={
                    "tx_id": 0x783,
                    "fc_id": 0x78B,
                    "data": "010203040506070809101112131415",
                    "fc_timeout_ms": 1000,
                },
            )
            t.join(timeout=2)
            assert r.status_code == 200
            body = r.json()
            assert body["frame_type"] == "multi"
            assert body["frames_sent"] == 3
            assert body["bytes_sent"] == 15
        finally:
            peer.shutdown()
            client.post("/api/disconnect")


def test_isotp_send_requires_connection():
    with make_client() as client:
        r = client.post(
            "/api/isotp/send",
            json={"tx_id": 0x783, "fc_id": 0x78B, "data": "0102"},
        )
        assert r.status_code == 400


def test_isotp_send_odd_hex_length_rejected():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_isotp2"})
        r = client.post(
            "/api/isotp/send",
            json={"tx_id": 0x783, "fc_id": 0x78B, "data": "010"},
        )
        assert r.status_code == 400
        client.post("/api/disconnect")


def test_isotp_send_blocked_when_globally_stopped():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_isotp3"})
        client.post("/api/run/stop")
        r = client.post(
            "/api/isotp/send",
            json={"tx_id": 0x783, "fc_id": 0x78B, "data": "0102"},
        )
        assert r.status_code == 400
        client.post("/api/disconnect")


def test_testrunner_upload_and_run():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_runner"})
        client.post("/api/run/start")  # earlier tests may have left the global run gate stopped
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        script = json.dumps(
            [
                {"type": "ID", "num": "1", "Cycle": 1},
                {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
                {"type": "delay", "ms": 50},
            ]
        )
        r = client.post(
            "/api/testrunner/upload",
            files={"file": ("t.json", script.encode("utf-8"))},
        )
        assert r.status_code == 200
        assert r.json() == {
            "loaded": True,
            "filename": "t.json",
            "running": False,
            "running_case": None,
            "case_count": 1,
            "result_count": 0,
            "functions": {"loaded": False, "filename": None, "names": []},
        }

        assert client.get("/api/status").json()["test_runner"]["loaded"] is True

        r = client.post("/api/testrunner/start")
        assert r.status_code == 200
        assert r.json()["running"] is True

        deadline = time.time() + 3.0
        while time.time() < deadline and client.get("/api/testrunner/status").json()["running"]:
            time.sleep(0.05)

        status = client.get("/api/testrunner/status").json()
        assert status["running"] is False
        assert status["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]
        # the script's own periodic auto-send must not still be armed
        assert client.get("/api/status").json()["tx"]["auto_entries"] == []

        client.post("/api/disconnect")


def test_testrunner_stop_and_requires_connection():
    with make_client() as client:
        # disconnected at this point (previous test cleaned up) -> rejected
        r = client.post("/api/testrunner/start")
        assert r.status_code == 400

        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_runner2"})
        client.post("/api/run/start")  # earlier tests may have left the global run gate stopped
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        script = json.dumps([{"type": "ID", "num": "1", "Cycle": 1}, {"type": "delay", "ms": 3000}])
        client.post("/api/testrunner/upload", files={"file": ("t.json", script.encode("utf-8"))})
        r = client.post("/api/testrunner/start")
        assert r.status_code == 200
        assert client.get("/api/testrunner/status").json()["running"] is True

        r = client.post("/api/testrunner/stop")
        assert r.json()["running"] is False
        client.post("/api/disconnect")


def test_testrunner_functions_upload_and_run():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_funcs"})
        client.post("/api/run/start")  # earlier tests may have left the global run gate stopped
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        script = json.dumps(
            [
                {"type": "FUNC", "name": "SendSpeed", "Cycle": 1},
                {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
                {"type": "FUNC", "name": "SendTurn", "Cycle": 1},
                {"type": "CANReq", "Message": "DriverCommand", "Signal": "TurnSignal", "Value": "0x01"},
                {"type": "delay", "ms": 50},
            ]
        )
        r = client.post(
            "/api/testrunner/functions/upload",
            files={"file": ("funcs.json", script.encode("utf-8"))},
        )
        assert r.status_code == 200
        assert r.json()["functions"] == {
            "loaded": True,
            "filename": "funcs.json",
            "names": ["SendSpeed", "SendTurn"],
        }
        assert client.get("/api/status").json()["test_runner"]["functions"]["names"] == [
            "SendSpeed",
            "SendTurn",
        ]

        r = client.post("/api/testrunner/functions/start", json={"name": "SendTurn"})
        assert r.status_code == 200
        assert r.json()["running"] is True

        deadline = time.time() + 3.0
        while time.time() < deadline and client.get("/api/testrunner/status").json()["running"]:
            time.sleep(0.05)

        status = client.get("/api/testrunner/status").json()
        assert status["results"] == [{"case": "SendTurn", "cycle": 1, "status": "OK"}]

        r = client.post("/api/testrunner/functions/start", json={"name": "NoSuchFunc"})
        assert r.status_code == 400

        client.post("/api/disconnect")


def test_dbc_and_function_script_raw_endpoints():
    # dbc_service/test_runner_service are module-level singletons shared by
    # every TestClient in this process, so "nothing loaded yet" isn't a safe
    # assumption here -- other tests in the suite may have already loaded a
    # DBC/function script before this one runs. Only assert the post-upload
    # round-trip.
    with make_client() as client:
        dbc_text = (SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8")
        client.post("/api/dbc/upload", files={"file": ("sample.dbc", dbc_text.encode("utf-8"))})
        r = client.get("/api/dbc/raw")
        assert r.status_code == 200
        assert r.json() == {"filename": "sample.dbc", "content": dbc_text}

        script = json.dumps([{"type": "FUNC", "name": "SendSpeed", "Cycle": 1}])
        client.post("/api/testrunner/functions/upload", files={"file": ("funcs.json", script.encode("utf-8"))})
        r = client.get("/api/testrunner/functions/raw")
        assert r.status_code == 200
        assert r.json() == {"filename": "funcs.json", "content": script}


def test_testrunner_functions_upload_rejects_script_without_func_blocks():
    with make_client() as client:
        # an ordinary ID-based scenario script, not a FUNC master script
        script = json.dumps([{"type": "ID", "num": "1", "Cycle": 1}, {"type": "delay", "ms": 5}])
        r = client.post(
            "/api/testrunner/functions/upload",
            files={"file": ("scenario.json", script.encode("utf-8"))},
        )
        assert r.status_code == 400


def test_power_api_degrades_gracefully_without_hardware():
    with make_client() as client:
        r = client.post("/api/power/connect")
        assert r.status_code == 200
        assert r.json()["initialized"] is False  # no real VISA instrument in CI/dev
        assert client.get("/api/power/status").json()["initialized"] is False
        r = client.post("/api/power/disconnect")
        assert r.status_code == 200


def test_power_control_widget_routes_degrade_gracefully_without_hardware():
    with make_client() as client:
        # not connected -- every action route reports ok:false rather than 5xx
        r = client.post("/api/power/battery", json={"voltage": 12.6, "current": 5})
        assert r.status_code == 200
        assert r.json()["ok"] is False

        r = client.post("/api/power/acc_ign", json={"command": "ACC_On"})
        assert r.status_code == 200
        assert r.json()["ok"] is False

        r = client.post(
            "/api/power/onoff/start",
            json={"on_voltage": 12.0, "on_current": 5, "on_s": 1, "off_voltage": 1.0, "off_current": 0, "off_s": 1},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False

        r = client.post("/api/power/onoff/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r = client.post(
            "/api/power/sweep/start",
            json={"low": 9.0, "high": 15.0, "current": 10, "leg_s": 5},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False

        r = client.post("/api/power/sweep/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        status = client.get("/api/power/status").json()
        assert set(status) >= {"acc", "ign", "battery_voltage", "battery_current", "onoff", "sweep"}


def test_audio_devices_and_selection_api():
    with make_client() as client:
        r = client.get("/api/audio/devices")
        assert r.status_code == 200
        assert "devices" in r.json()

        r = client.post("/api/audio/device", json={"index": 0})
        assert r.status_code == 200
        assert r.json()["device_index"] == 0
        assert client.get("/api/audio/status").json()["device_index"] == 0


def test_audio_monitor_endpoints():
    # main.audio_service is a process-wide singleton shared by every test in
    # this file, so device_index may already be set by
    # test_audio_devices_and_selection_api above -- reset it to force the
    # deterministic, hardware-free rejection path instead of actually trying
    # to open a real device stream.
    with make_client() as client:
        main.audio_service.device_index = None

        r = client.get("/api/audio/level")
        assert r.status_code == 200
        body = r.json()
        assert body["active"] is False
        assert len(body["channels"]) == 2

        r = client.post("/api/audio/monitor/start")
        assert r.status_code == 200
        assert r.json()["ok"] is False  # no device selected

        r = client.post("/api/audio/monitor/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True  # nothing active -> no-op, not an error


def test_audio_waveform_and_record_endpoints():
    with make_client() as client:
        main.audio_service.device_index = None

        r = client.get("/api/audio/waveform?from_ms=0&to_ms=1000&max_points=50")
        assert r.status_code == 200
        body = r.json()
        assert body["active"] is False
        assert all(ch["points"] == [] for ch in body["channels"])

        r = client.post("/api/audio/record/start")
        assert r.status_code == 200
        assert r.json()["ok"] is False  # no device selected

        r = client.post("/api/audio/record/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is False  # widget never started a recording


def test_testrunner_golden_upload():
    with make_client() as client:
        r = client.post(
            "/api/testrunner/golden/upload",
            files={"file": ("case1_golden.wav", b"RIFF....WAVEfmt ")},
        )
        assert r.status_code == 200
        assert r.json() == {"saved": "case1_golden.wav"}

        r = client.post(
            "/api/testrunner/golden/upload",
            files={"file": ("not_a_wav.txt", b"nope")},
        )
        assert r.status_code == 400


def test_send_type_override_api():
    with make_client() as client:
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        r = client.post(
            "/api/dbc/send-type",
            json={
                "message_name": "EngineData",
                "signal_name": "EngineSpeed",
                "send_type": "event",
            },
        )
        assert r.status_code == 200
        engine = next(m for m in r.json()["messages"] if m["name"] == "EngineData")
        speed = next(s for s in engine["signals"] if s["name"] == "EngineSpeed")
        assert speed["send_type"] == "event"


def test_signal_generator_random_and_generate_api():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_gen"})
        client.post("/api/run/start")  # earlier tests may have left the global run gate stopped
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        r = client.post(
            "/api/tx/signal/generator",
            json={"message_name": "DriverCommand", "signal_name": "TurnSignal", "mode": "random"},
        )
        assert r.status_code == 200

        r = client.post(
            "/api/tx/signal/generate",
            json={"message_name": "DriverCommand", "signal_name": "TurnSignal"},
        )
        assert r.status_code == 200
        assert r.json()["sent"] is True
        assert 0 <= r.json()["raw_value"] <= 15

        # clearing the generator makes further generate calls fail
        client.post(
            "/api/tx/signal/generator",
            json={"message_name": "DriverCommand", "signal_name": "TurnSignal", "mode": "fixed"},
        )
        r = client.post(
            "/api/tx/signal/generate",
            json={"message_name": "DriverCommand", "signal_name": "TurnSignal"},
        )
        assert r.status_code == 400

        client.post("/api/disconnect")


def test_signal_invalid_send_api():
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_invalid"})
        client.post("/api/run/start")  # earlier tests may have left the global run gate stopped
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        r = client.post(
            "/api/tx/signal/invalid",
            json={"message_name": "EngineData", "signal_name": "EngineSpeed"},
        )
        assert r.status_code == 200
        assert r.json() == {"sent": True, "raw_value": 0xFFFF, "send_type": "periodic"}
        client.post("/api/disconnect")


def test_log_start_stop_api():
    with make_client() as client:
        r = client.post("/api/log/start")
        assert r.status_code == 400  # not connected yet

        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_log"})
        r = client.post("/api/log/start")
        assert r.status_code == 200
        assert r.json()["recording"] is True
        assert r.json()["filename"].endswith(".blf")

        assert client.get("/api/status").json()["log"]["recording"] is True

        r = client.post("/api/log/stop")
        assert r.status_code == 200
        assert r.json()["recording"] is False

        client.post("/api/disconnect")


HOOK_XML_FOR_API_TEST = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="">
    <xfrm:rule comment="VersionCheck">
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x81" confirmPositiveResponse="no" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""


def test_ota_tester_case_endpoints_wiring():
    """HTTP-level smoke test for the new folder-driven case endpoints
    (upload/enable/set_all_enabled/clear) -- verifies multipart + query-param
    binding end-to-end, independent of the manager-level unit tests in
    test_ota_tester_download_manager.py (which bypass HTTP/main.py entirely)."""
    with make_client() as client:
        client.post("/api/ota_tester/cases/clear")

        r = client.post(
            "/api/ota_tester/case/xml_upload"
            "?case_id=hook-api-1&label=VersionCheck&kind=hook&order=0&enabled=true",
            files={"file": ("hook.xml", HOOK_XML_FOR_API_TEST.encode("utf-8"))},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_cases"] == 1
        assert body["cases"][0]["id"] == "hook-api-1"
        assert body["cases"][0]["total_steps"] == 1
        assert body["cases"][0]["enabled"] is True

        r = client.post("/api/ota_tester/case/enable", json={"case_id": "hook-api-1", "enabled": False})
        assert r.status_code == 200
        assert r.json()["cases"][0]["enabled"] is False

        r = client.post("/api/ota_tester/cases/set_all_enabled", json={"enabled": True})
        assert r.json()["cases"][0]["enabled"] is True

        # Wrong extension is rejected
        r = client.post(
            "/api/ota_tester/case/xml_upload?case_id=bad&label=x&kind=hook&order=0",
            files={"file": ("hook.json", b"{}")},
        )
        assert r.status_code == 400

        assert client.get("/api/ota_tester/status").json()["total_cases"] == 1

        r = client.post("/api/ota_tester/cases/clear")
        assert r.json()["total_cases"] == 0


def test_ota_tester_case_steps_and_selected_steps_endpoints():
    with make_client() as client:
        client.post("/api/ota_tester/cases/clear")
        r = client.post(
            "/api/ota_tester/case/xml_upload"
            "?case_id=hook-api-2&label=VersionCheck&kind=hook&order=0&enabled=true",
            files={"file": ("hook.xml", HOOK_XML_FOR_API_TEST.encode("utf-8"))},
        )
        assert r.status_code == 200

        r = client.get("/api/ota_tester/case/steps", params={"case_id": "hook-api-2"})
        assert r.status_code == 200
        steps = r.json()
        assert len(steps) == 1
        assert steps[0]["service"] == "diagnosticSessionControl"
        assert steps[0]["params"]["diagnosticSessionType"] == "0x81"

        r = client.put(
            "/api/ota_tester/case/selected_steps",
            json={"case_id": "hook-api-2", "selected_steps": []},
        )
        assert r.status_code == 200
        assert r.json()["cases"][0]["selected_steps"] == []

        r = client.get("/api/ota_tester/case/steps", params={"case_id": "does-not-exist"})
        assert r.status_code == 400

        client.post("/api/ota_tester/cases/clear")


def test_periodic_enable_disable_all_wiring_and_flag_independent_of_widget_sends():
    """/api/tx/periodic/enable_all + /disable_all REST wiring, and that the
    "Enable Msg" on/off flag stays decoupled from a widget's own periodic
    signal sends (the reported bug: sending a periodic signal from a widget
    used to make the "Enable Msg" button look pressed)."""
    with make_client() as client:
        client.post("/api/connect", json={"interface": "virtual", "channel": "t_api_periodic"})
        client.post(
            "/api/dbc/upload",
            files={"file": ("sample.dbc", (SAMPLES_DIR / "sample.dbc").read_bytes())},
        )
        client.post("/api/run/start")

        # widget sends a periodic signal on its own -- must not flip the flag
        client.post(
            "/api/tx/signal",
            json={"message_name": "EngineData", "values": {"EngineSpeed": 1500}},
        )
        assert client.get("/api/status").json()["tx"]["periodic_enabled"] is False

        # "Enable Msg" pressed -- flag on, all periodic messages armed
        r = client.post("/api/tx/periodic/enable_all", json={"rx_node": ""})
        assert r.status_code == 200
        assert set(r.json()["armed"]) >= {"EngineData", "VehicleSpeed", "BodyStatus"}
        assert client.get("/api/status").json()["tx"]["periodic_enabled"] is True

        # "Enable Msg" pressed again -- flag off, those entries stop
        r = client.post("/api/tx/periodic/disable_all")
        assert r.status_code == 200
        status = client.get("/api/status").json()
        assert status["tx"]["periodic_enabled"] is False
        assert status["tx"]["auto_entries"] == []

        client.post("/api/run/stop")
