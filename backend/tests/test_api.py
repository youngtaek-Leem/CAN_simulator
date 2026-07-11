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
        assert r.json() == {"loaded": True, "filename": "t.json", "running": False, "case_count": 1, "result_count": 0}

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


def test_power_api_degrades_gracefully_without_hardware():
    with make_client() as client:
        r = client.post("/api/power/connect")
        assert r.status_code == 200
        assert r.json()["initialized"] is False  # no real VISA instrument in CI/dev
        assert client.get("/api/power/status").json()["initialized"] is False
        r = client.post("/api/power/disconnect")
        assert r.status_code == 200


def test_audio_devices_and_selection_api():
    with make_client() as client:
        r = client.get("/api/audio/devices")
        assert r.status_code == 200
        assert "devices" in r.json()

        r = client.post("/api/audio/device", json={"index": 0})
        assert r.status_code == 200
        assert r.json()["device_index"] == 0
        assert client.get("/api/audio/status").json()["device_index"] == 0


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
