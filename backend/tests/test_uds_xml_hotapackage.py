"""Tests for H-OTA package XML format (xfrm:root → external-reprogram-rule).

Covers CAN-SWDL loading of files like
reference/H-OTA_Packages_RG3HEV_05.04.03/RG3HEV_96370JQ510_*.xml:
  - no legacy <document> wrapper, yet parses without error
  - simulator perspective TX=request_id=moduleId(0x783), RX=response_id=testerId(0x78B)
  - <delay time="ms"/> steps preserved in order
  - legacy <document> format keeps working (backward compat)
  - delay steps execute as stop-aware waits (and skip via selection logic)
"""

import os
import tempfile
import threading
import time

import pytest

from uds_xml_parser import parse_xml, UdsStep
import uds_download_manager as udm

REF_DIR_050403 = os.path.join(
    os.path.dirname(__file__), "..", "..", "reference", "H-OTA_Packages_RG3HEV_05.04.03"
)
REF_FILES = [
    os.path.join(REF_DIR_050403, "RG3HEV_96370JQ510_01_1.05.xml"),
    os.path.join(REF_DIR_050403, "RG3HEV_96370JQ510_02_2.04.xml"),
    os.path.join(REF_DIR_050403, "RG3HEV_96370JQ510_03_3.03.xml"),
]

LEGACY_XML = """<xfrm:document xmlns:xfrm="http://gitauto.com/xfrm/" xmlns:information="http://gitauto.com/information/">
  <xfrm:instance>
    <information:commInfo network="CAN" requestId="0x783" responseId="0x78B" dataBitRate="2" p6Time="100"/>
  </xfrm:instance>
  <xfrm:processing-rule>
    <xfrm:rule-preparation/>
    <xfrm:rule-unit>
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x02"/>
    </xfrm:rule-unit>
    <xfrm:rule-complete/>
  </xfrm:processing-rule>
</xfrm:document>"""


def _write_tmp(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".xml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


@pytest.mark.skipif(
    not os.path.isdir(REF_DIR_050403), reason="reference H-OTA package not present"
)
@pytest.mark.parametrize("xml_path", REF_FILES)
def test_hotapackage_parses_with_tx783_rx78b(xml_path):
    proc = parse_xml(xml_path)
    assert proc.request_id == 0x783
    assert proc.response_id == 0x78B
    services = [s.service for s in proc.processing_rule.unit]
    assert services[0] == "startCommunication"
    assert services[-1] == "stopCommunication"
    assert "delay" in services
    # completeDecision flattened into complete phase
    complete_services = [s.service for s in proc.processing_rule.complete]
    assert "completeDecision" not in complete_services
    assert "readDataByIdentifier" in complete_services


@pytest.mark.skipif(
    not os.path.isdir(REF_DIR_050403), reason="reference H-OTA package not present"
)
def test_hotapackage_delay_times_preserved_in_order():
    proc = parse_xml(REF_FILES[0])
    delays = [s.params.get("time") for s in proc.processing_rule.unit if s.service == "delay"]
    assert delays[0] == "2000"  # after session control
    assert "15000" in delays
    assert "100" in delays


def test_legacy_document_format_still_works():
    path = _write_tmp(LEGACY_XML)
    try:
        proc = parse_xml(path)
        assert proc.request_id == 0x783
        assert proc.response_id == 0x78B
        assert [s.service for s in proc.processing_rule.unit] == ["diagnosticSessionControl"]
    finally:
        os.unlink(path)


def test_garbage_xml_keeps_legacy_error():
    path = _write_tmp("<root/>")
    try:
        with pytest.raises(ValueError, match="XML에 <document> 요소를 찾을 수 없습니다"):
            parse_xml(path)
    finally:
        os.unlink(path)


def _bare_manager():
    mgr = udm.UdsDownloadManager.__new__(udm.UdsDownloadManager)
    mgr._stop_event = threading.Event()
    mgr._events = []
    mgr._lock = threading.RLock()
    mgr._progress = {}
    return mgr


def test_delay_step_waits_specified_ms():
    mgr = _bare_manager()
    t0 = time.time()
    mgr._execute_step(UdsStep(service="delay", params={"time": "200"}), "unit")
    dt_ms = (time.time() - t0) * 1000.0
    assert 150 <= dt_ms <= 600


def test_delay_zero_is_noop():
    mgr = _bare_manager()
    t0 = time.time()
    mgr._execute_step(UdsStep(service="delay", params={"time": "0"}), "unit")
    assert (time.time() - t0) * 1000.0 < 150


def test_delay_interrupted_by_stop():
    mgr = _bare_manager()
    threading.Timer(0.05, mgr._stop_event.set).start()
    t0 = time.time()
    with pytest.raises(RuntimeError, match="중단"):
        mgr._execute_step(UdsStep(service="delay", params={"time": "5000"}), "unit")
    assert (time.time() - t0) * 1000.0 < 2000


def test_delay_keepalive_sends_tp_per_interval(monkeypatch):
    """Long delay (>= 1 interval) sends one suppressed TesterPresent per
    full interval elapsed (functional 0x7DF via _send_tester_present)."""
    monkeypatch.setattr(udm, "TESTER_PRESENT_INTERVAL_S", 0.1)
    mgr = _bare_manager()
    calls: list = []
    mgr._send_tester_present = lambda: calls.append(1)  # type: ignore[method-assign]
    t0 = time.time()
    mgr._execute_step(UdsStep(service="delay", params={"time": "350"}), "unit")
    assert len(calls) == 3
    assert 0.30 <= time.time() - t0 <= 1.5


def test_delay_keepalive_none_when_short(monkeypatch):
    """Short delay (< 1 interval) sends no TesterPresent — behavior unchanged."""
    monkeypatch.setattr(udm, "TESTER_PRESENT_INTERVAL_S", 10.0)
    mgr = _bare_manager()
    calls: list = []
    mgr._send_tester_present = lambda: calls.append(1)  # type: ignore[method-assign]
    mgr._execute_step(UdsStep(service="delay", params={"time": "200"}), "unit")
    assert calls == []


def test_send_tester_present_failure_swallowed():
    """A bus failure during keep-alive must never abort the run."""
    mgr = _bare_manager()
    mgr._procedure = object()  # type: ignore[assignment]
    mgr._can = object()  # type: ignore[assignment]

    def _boom(*args, **kwargs):
        raise RuntimeError("bus down")

    mgr._isotp_send = _boom  # type: ignore[assignment]
    mgr._send_tester_present()  # must not raise


def test_ota_step_delay_keepalive(monkeypatch):
    """OTA Tester endDelay >= 1 interval also emits TesterPresent keep-alive."""
    import ota_tester_download_manager as otdm

    monkeypatch.setattr(otdm, "TESTER_PRESENT_INTERVAL_S", 0.1)
    mgr = otdm.OtaTesterDownloadManager(None, None, None)
    calls: list = []
    mgr._send_tester_present = lambda: calls.append(1)  # type: ignore[method-assign]
    mgr._step_delay({"params": {"endDelay": "350"}})
    assert len(calls) == 3


def test_ota_step_delay_no_keepalive_when_short(monkeypatch):
    import ota_tester_download_manager as otdm

    monkeypatch.setattr(otdm, "TESTER_PRESENT_INTERVAL_S", 10.0)
    mgr = otdm.OtaTesterDownloadManager(None, None, None)
    calls: list = []
    mgr._send_tester_present = lambda: calls.append(1)  # type: ignore[method-assign]
    mgr._step_delay({"params": {"endDelay": "200"}})
    assert calls == []
