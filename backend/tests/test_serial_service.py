"""SerialService (dual-port UART RX monitor) tests -- no hardware required.

Port I/O uses pyserial's ``loop://`` URL (each open gets an independent
loopback: writes to the service's own handle are read back by its reader
thread). REST wiring is exercised through main.app's TestClient.
"""

import time

import pytest
from fastapi.testclient import TestClient

import main
from serial_service import SerialService, _format_log_ts


@pytest.fixture()
def svc(tmp_path):
    service = SerialService(tmp_path / "serial_logs")
    yield service
    service.shutdown()


def test_set_config_validation(svc):
    with pytest.raises(RuntimeError):
        svc.set_config(a_port="COM1", a_baud=0)
    with pytest.raises(RuntimeError):
        svc.set_config(a_port="COM1", a_baud="fast")
    st = svc.set_config(a_port="loop://", a_baud=115200, b_port="", b_baud=9600)
    assert st["config"]["a"] == {"port": "loop://", "baud": 115200}
    assert st["config"]["b"] == {"port": "", "baud": 9600}


def test_list_ports_shape(svc):
    result = svc.list_ports()
    assert result["available"] is True
    assert isinstance(result["ports"], list)


def _wait_for(predicate, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_open_write_drain_loopback(svc):
    svc.set_config(a_port="loop://", a_baud=115200)
    result = svc.open_all()
    assert result["results"]["a"]["ok"] is True
    assert result["results"]["b"]["ok"] is False  # no port configured
    assert svc.status()["open"] == {"a": True, "b": False}
    # write through the service's own handle -- loopback echoes to the reader
    # (possibly in several chunks, so reassemble before comparing)
    svc._handles["a"].write(b"HELLO")
    assert _wait_for(
        lambda: sum(len(e["data"]) for e in svc._scrollback["a"]) >= len(b"HELLO".hex())
    )
    drained = svc.drain()
    assert len(drained["a"]) >= 1
    assert "".join(e["data"] for e in drained["a"]) == b"HELLO".hex().upper()
    assert drained["a"][0]["ts"] > 0
    # drained entries are gone from the pending queue but kept in scrollback
    assert svc.drain() == {"a": [], "b": []}
    assert len(svc._scrollback["a"]) >= 1
    svc.close_all()
    assert svc.status()["open"] == {"a": False, "b": False}


def test_open_missing_port_never_raises(svc):
    svc.set_config(a_port="COM_DOES_NOT_EXIST_99", a_baud=115200)
    result = svc.open_all()  # must not raise -- CAN Start depends on this
    assert result["results"]["a"]["ok"] is False
    assert svc.status()["open"]["a"] is False


def test_lines_scrollback_filter(svc):
    svc.set_config(a_port="loop://", a_baud=115200)
    svc.open_all()
    svc._handles["a"].write(b"\x01\x02")
    assert _wait_for(lambda: len(svc._scrollback["a"]) > 0)
    all_lines = svc.lines("a", 0.0, 500)["lines"]
    assert len(all_lines) >= 1
    latest_ts = all_lines[-1]["ts"]
    assert svc.lines("a", latest_ts + 1, 500)["lines"] == []
    assert svc.lines("a", 0.0, 1)["lines"] == all_lines[-1:]
    with pytest.raises(RuntimeError):
        svc.lines("c")


def test_logging_writes_two_files(svc):
    svc.set_config(a_port="loop://", a_baud=115200)
    svc.open_all()
    st = svc.start_logging()
    assert st["active"] is True
    assert st["files"]["a"].startswith("serial_A_")
    assert st["files"]["b"].startswith("serial_B_")
    svc._handles["a"].write(b"Hi")
    assert _wait_for(lambda: svc._logging_status()["counts"]["a"] > 0)
    st = svc.stop_logging()
    assert st["active"] is False
    assert st["counts"]["a"] >= 1
    # A port (data) file has timestamped hex|ascii lines; B file exists empty.
    # (loopback may split "Hi" into chunks -- reassemble the hex payloads)
    text = (svc._log_dir / st["files"]["a"]).read_text(encoding="utf-8")
    hex_all = "".join(line.split("] ", 1)[1].split("  |")[0].replace(" ", "") for line in text.splitlines())
    assert hex_all == b"Hi".hex().upper()
    assert (svc._log_dir / st["files"]["b"]).exists()
    # idempotent double-stop
    svc.stop_logging()


def test_format_log_ts_ms():
    assert _format_log_ts(0.0).endswith(".000")
    assert _format_log_ts(1720000000123.0).endswith(".123")


def test_rest_wiring():
    with TestClient(main.app) as client:
        assert "serial" in client.get("/api/status").json()
        r = client.get("/api/serial/ports")
        assert r.status_code == 200
        assert r.json()["available"] is True
        r = client.post(
            "/api/serial/config",
            json={"a": {"port": "loop://", "baud": 115200}, "b": {"port": "", "baud": 9600}},
        )
        assert r.status_code == 200
        assert r.json()["config"]["a"]["baud"] == 115200
        r = client.post("/api/serial/config", json={"a": {"port": "x", "baud": 0}})
        assert r.status_code == 400
        r = client.get("/api/serial/status")
        assert r.status_code == 200
        r = client.get("/api/serial/lines", params={"port": "a", "limit": 10})
        assert r.status_code == 200
        assert r.json()["port"] == "a"
        r = client.get("/api/serial/lines", params={"port": "z"})
        assert r.status_code == 400
