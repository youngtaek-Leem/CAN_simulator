"""Tests for ota_tester_download_manager.py.

Covers: PDU param-name correctness (regression for the diagnosticSessionType /
dataIdentifier / routineControlOptionRecord attribute-name bugs), the pure
seekAddress/writeSize chunking helper, and full multi-case sequential runs
driven through fake isotp send/receive functions (no real CAN bus needed --
the manager takes these as injected callables).
"""

from __future__ import annotations

import textwrap
import threading
import time

import pytest

from ota_tester_download_manager import OtaTesterDownloadManager, iter_transfer_chunks
from uds_xml_parser import parse_test_rule_xml


class _FakeNotifier:
    """`_uds_request_with_retry` now keeps one CAN listener registered
    across its whole NRC-0x78 retry sequence (see its docstring), so every
    fake CAN manager below needs a notifier that at least accepts
    add_listener/remove_listener -- these tests fake `_isotp_send`/
    `_isotp_receive` entirely, so the listener itself is never actually
    used to receive anything."""

    def add_listener(self, listener) -> None:
        pass

    def remove_listener(self, listener) -> None:
        pass


# ---- _build_pdu param-name regression tests --------------------------------


def _mgr():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    return OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")


def test_build_pdu_diagnostic_session_control_uses_diagnosticSessionType():
    mgr = _mgr()
    pdu = mgr._build_pdu("diagnosticSessionControl", {"diagnosticSessionType": "0x02"}, [])
    assert pdu == bytearray([0x10, 0x02])


def test_build_pdu_read_data_by_identifier_uses_dataIdentifier():
    mgr = _mgr()
    pdu = mgr._build_pdu("readDataByIdentifier", {"dataIdentifier": "0xF187"}, [])
    assert pdu == bytearray([0x22, 0xF1, 0x87])


def test_build_pdu_routine_control_includes_option_record():
    mgr = _mgr()
    pdu = mgr._build_pdu(
        "routineControl",
        {
            "routineControlType": "0x01",
            "routineIdentifier": "0xFF00",
            "routineControlOptionRecord": "0xF1B1",
        },
        [],
    )
    assert pdu == bytearray([0x31, 0x01, 0xFF, 0x00, 0xF1, 0xB1])


def test_build_pdu_ecu_reset():
    mgr = _mgr()
    pdu = mgr._build_pdu("ecuReset", {"resetMode": "0x01"}, [])
    assert pdu == bytearray([0x11, 0x01])


# ---- iter_transfer_chunks (pure) -------------------------------------------


def test_iter_transfer_chunks_covers_full_range_in_order():
    binary = bytes(range(256)) * 4  # 1024 bytes
    seek_addr = 0x200
    write_size = 0x10
    block_size = 4

    chunks = list(iter_transfer_chunks(binary, seek_addr, write_size, block_size))

    assert len(chunks) == 4
    offsets = [c[1] for c in chunks]
    assert offsets == [0x200, 0x204, 0x208, 0x20C]
    seq_nums = [c[0] for c in chunks]
    assert seq_nums == [1, 2, 3, 4]
    reconstructed = b"".join(c[2] for c in chunks)
    assert reconstructed == binary[seek_addr:seek_addr + write_size]


def test_iter_transfer_chunks_clamps_to_binary_end():
    binary = bytes(20)
    chunks = list(iter_transfer_chunks(binary, seek_addr=10, write_size=100, block_size=4))
    total = sum(len(c[2]) for c in chunks)
    assert total == 10  # only 10 bytes remain after offset 10


def test_iter_transfer_chunks_wraps_sequence_number_at_255():
    binary = bytes(2000)
    chunks = list(iter_transfer_chunks(binary, seek_addr=0, write_size=2000, block_size=1))
    assert chunks[254][0] == 255
    assert chunks[255][0] == 0  # 256 & 0xFF


# ---- Full sequential run against a fake ECU --------------------------------


HOOK_XML = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="">
    <xfrm:rule comment="VersionCheck">
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x81" confirmPositiveResponse="no" />
      <xfrm:readDataByIdentifier dataIdentifier="0xF187" confirmPositiveResponse="yes" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""

TESTBLOCK_XML = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="fw/RomData01.bin">
    <xfrm:rule funcTP="false">
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x02" confirmPositiveResponse="yes" />
      <xfrm:securityAccess confirmPositiveResponse="yes" />
      <xfrm:routineControl routineControlType="0x01" routineIdentifier="0xFF00" routineControlOptionRecord="0xF1B1" confirmPositiveResponse="yes" />
      <xfrm:requestDownload dataFormatIdentifier="0x0A" addressAndLengthFormatIdentifier="0x44" memoryAddress="0x00000000" memorySize="0x00000010" confirmPositiveResponse="yes" />
      <xfrm:transferData maxNumberOfBlockLength="0x08" seekAddress="0x0200" writeSize="0x00000010" confirmPositiveResponse="yes" />
      <xfrm:requestTransferExit confirmPositiveResponse="yes" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""


class FakeEcu:
    """Fake ECU: inspects the outgoing PDU's SID and returns a canned
    positive response, tracking every request it received."""

    def __init__(self, ecu_max_block_length: int = 4, fail_sid: int | None = None):
        self.sent: list[bytes] = []
        self.ecu_max_block_length = ecu_max_block_length
        self.fail_sid = fail_sid
        self._lock = threading.Lock()

    def send(self, can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        with self._lock:
            self.sent.append(bytes(data))
        return {"sent": True}

    def receive(self, can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw) -> bytes:
        last = self.sent[-1]
        sid = last[0]
        if self.fail_sid is not None and sid == self.fail_sid:
            return bytes([0x7F, sid, 0x22])  # conditionsNotCorrect

        if sid == 0x10:
            return bytes([0x50, last[1]])
        if sid == 0x22:
            return bytes([0x62, last[1], last[2], 0x01, 0x02])
        if sid == 0x27:
            if last[1] == 0x11:  # request seed
                return bytes([0x67, 0x11]) + bytes([0x11] * 8)
            return bytes([0x67, 0x12])  # send key
        if sid == 0x31:
            return bytes([0x71]) + last[1:4]
        if sid == 0x34:
            # lengthFormatIdentifier nibble=1 (one length byte) -> max_length=ecu_max_block_length
            return bytes([0x74, 0x11, self.ecu_max_block_length])
        if sid == 0x36:
            return bytes([0x76, last[1]])
        if sid == 0x37:
            return bytes([0x77])
        return bytes([0x7F, sid, 0x11])  # serviceNotSupported (unrecognized)


STMIN_XML = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="">
    <xfrm:rule funcTP="false">
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x02" confirmPositiveResponse="yes" localSTMinTx="0x0A" />
      <xfrm:readDataByIdentifier dataIdentifier="0xF187" confirmPositiveResponse="yes" localSTMinTx="" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""


SINGLE_STEP_XML = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="">
    <xfrm:rule comment="Single">
      <xfrm:readDataByIdentifier dataIdentifier="0xF187" confirmPositiveResponse="yes" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""


def _write_xml(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _write_bin(tmp_path, name: str, size: int) -> str:
    p = tmp_path / name
    p.write_bytes(bytes(range(256)) * (size // 256 + 1))
    return str(p)


def test_full_sequence_runs_hook_then_testblock_with_binary_transfer(tmp_path):
    ecu = FakeEcu(ecu_max_block_length=4)
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    bin_path = _write_bin(tmp_path, "fw.bin", 1024)

    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=1)
    mgr.set_case_binary("block-1", bin_path)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    status = mgr.status()
    assert status["state"] == "COMPLETED", status["events"]
    assert not status["running"]

    # transferData: writeSize=0x10 clamped to ecu_max_block_length=4 -> 4 chunks of 4 bytes
    transfer_pdus = [b for b in ecu.sent if b[0] == 0x36]
    assert len(transfer_pdus) == 4
    assert [p[1] for p in transfer_pdus] == [1, 2, 3, 4]
    with open(bin_path, "rb") as f:
        binary = f.read()
    expected = binary[0x200:0x210]
    reconstructed = b"".join(p[2:] for p in transfer_pdus)
    assert reconstructed == expected


def test_disabled_case_is_skipped_entirely(tmp_path):
    ecu = FakeEcu()
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0, enabled=False)

    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    bin_path = _write_bin(tmp_path, "fw.bin", 1024)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=1)
    mgr.set_case_binary("block-1", bin_path)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    # Only the enabled testBlock's steps should have produced traffic --
    # the disabled hook's readDataByIdentifier (SID 0x22) must never appear.
    assert not any(b[0] == 0x22 for b in ecu.sent)
    assert mgr.status()["state"] == "COMPLETED"


def test_uds_request_with_retry_registers_listener_before_sending(tmp_path):
    """Regression for "TransferData 응답이 0.004688초에 왔는데 놓치고 에러
    처리했다": the listener used to be created only *after* send()
    returned, leaving a gap in which an unusually fast ECU response could
    arrive before anything was registered to catch it. The listener must
    now be registered before the request is even sent, and the same
    reader passed to both send() and receive()."""
    log: list[str] = []

    class _OrderTrackingNotifier:
        def add_listener(self, listener) -> None:
            log.append("add_listener")

        def remove_listener(self, listener) -> None:
            log.append("remove_listener")

    def fake_send(can, tx_id, rx_id, data, **kw):
        log.append("send")
        assert kw.get("reader") is not None
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, **kw):
        log.append("receive")
        assert kw.get("reader") is not None
        return bytes([0x62, 0xF1, 0x87, 0x01])

    can_obj = type("FakeCan", (), {"notifier": _OrderTrackingNotifier()})()
    mgr = OtaTesterDownloadManager(can_obj, fake_send, fake_receive)

    xml_path = _write_xml(tmp_path, "single.xml", SINGLE_STEP_XML)
    mgr.add_case("c1", "Single", "hook", xml_path, order=0)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    assert mgr.status()["state"] == "COMPLETED", mgr.status()["events"]
    assert log == ["add_listener", "send", "receive", "remove_listener"]


def test_local_stmin_tx_empty_attribute_parses_to_none(tmp_path):
    """Regression: nearly every real GITAuto export has a localSTMinTx
    attribute present on every step but almost always empty (""). The old
    parsing (`"localSTMinTx" in step_info.params`) treated that presence
    alone as a real override of 0, silently forcing STmin=0 on virtually
    every step. Only a genuinely non-empty value should count as a step's
    own override."""
    xml_path = _write_xml(tmp_path, "stmin.xml", STMIN_XML)
    steps = parse_test_rule_xml(xml_path)
    assert steps[0]["local_stmin_tx"] == 0x0A
    assert steps[1]["local_stmin_tx"] is None


def test_local_stmin_tx_override_applied_during_step_and_cleared_after(tmp_path):
    """A step's own XML localSTMinTx must win over the shared global STmin
    override while that step runs, and must not leak into the next step
    that has no override of its own (which should fall back to the global
    value)."""
    fc_stmins_by_sid: dict[int, list[int]] = {}
    last_sid = {}

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        last_sid["v"] = data[0]
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        sid = last_sid["v"]
        fc_stmins_by_sid.setdefault(sid, []).append(fc_stmin)
        if sid == 0x10:
            return bytes([0x50, 0x02])
        if sid == 0x22:
            return bytes([0x62, 0xF1, 0x87, 0x01])
        return bytes([0x7F, sid, 0x11])

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)
    mgr._global_stmin_tx = 0x05  # shared UI override

    xml_path = _write_xml(tmp_path, "stmin.xml", STMIN_XML)
    mgr.add_case("c1", "StminCase", "testBlock", xml_path, order=0)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    assert mgr.status()["state"] == "COMPLETED", mgr.status()["events"]
    # diagnosticSessionControl (SID 0x10) has its own localSTMinTx="0x0A" -> wins over the global 0x05
    assert fc_stmins_by_sid[0x10] == [0x0A]
    # readDataByIdentifier (SID 0x22) has an empty localSTMinTx -> no leftover override, falls back to global 0x05
    assert fc_stmins_by_sid[0x22] == [0x05]
    # override must not remain set once the run is over
    assert mgr._local_stmin_override is None


def test_real_negative_response_is_detected_not_ignored(tmp_path):
    """Regression test for the removed TX-ONLY-TEST-MODE hardcoding: a real
    negative response from the fake ECU must actually fail the run."""
    ecu = FakeEcu(fail_sid=0x31)  # routineControl always fails
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    bin_path = _write_bin(tmp_path, "fw.bin", 1024)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)
    mgr.set_case_binary("block-1", bin_path)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    status = mgr.status()
    assert status["state"] == "ERROR"
    assert status["error"]
    # transferData (after the failing routineControl) must never have run
    assert not any(b[0] == 0x36 for b in ecu.sent)


def test_confirm_positive_response_no_treats_negative_as_success(tmp_path):
    """HOOK_XML's diagnosticSessionControl has confirmPositiveResponse="no";
    the fake ECU's default 0x50 positive reply would normally be fine, but
    verify the negative-is-expected path explicitly."""
    ecu = FakeEcu(fail_sid=0x10)  # diagnosticSessionControl returns negative
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    status = mgr.status()
    # diagnosticSessionControl's negative was expected (confirmPositiveResponse=no);
    # readDataByIdentifier (confirmPositiveResponse=yes) still gets a real positive
    # reply from the fake ECU, so the whole case should complete.
    assert status["state"] == "COMPLETED", status["events"]


def test_set_all_enabled_and_clear_cases(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0, enabled=True)
    mgr.add_case("hook-2", "Other", "hook", hook_path, order=1, enabled=True)

    status = mgr.set_all_enabled(False)
    assert all(not c["enabled"] for c in status["cases"])

    status = mgr.set_case_enabled("hook-1", True)
    assert [c["enabled"] for c in status["cases"] if c["id"] == "hook-1"] == [True]

    status = mgr.clear_cases()
    assert status["cases"] == []
    assert status["state"] == "IDLE"


def test_add_case_replaces_existing_case_with_same_id(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)
    mgr.add_case("hook-1", "VersionCheck (재로드)", "hook", hook_path, order=0)

    status = mgr.status()
    assert status["total_cases"] == 1
    assert status["cases"][0]["label"] == "VersionCheck (재로드)"


def test_start_requires_at_least_one_case():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    with pytest.raises(RuntimeError):
        mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)


def test_start_requires_connected_can(tmp_path):
    can = type("FakeCan", (), {"notifier": None})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)
    with pytest.raises(RuntimeError):
        mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)


# ---- Per-step selection (get_case_steps / set_case_selected_steps) --------


def test_get_case_steps_returns_service_and_params(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)

    steps = mgr.get_case_steps("hook-1")
    assert len(steps) == 2
    assert steps[0]["service"] == "diagnosticSessionControl"
    assert steps[0]["params"]["diagnosticSessionType"] == "0x81"
    assert steps[1]["service"] == "readDataByIdentifier"


def test_set_case_selected_steps_skips_deselected_step(tmp_path):
    """Deselecting the (always-failing, in this test) diagnosticSessionControl
    step should make it never execute, while the rest of the case still runs."""
    ecu = FakeEcu(fail_sid=0x10)
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)
    # HOOK_XML step 0 = diagnosticSessionControl, step 1 = readDataByIdentifier
    mgr.set_case_selected_steps("hook-1", [1])

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    status = mgr.status()
    assert status["state"] == "COMPLETED", status["events"]
    # diagnosticSessionControl (SID 0x10) must never have been sent
    assert not any(b[0] == 0x10 for b in ecu.sent)
    assert any(b[0] == 0x22 for b in ecu.sent)


def test_set_case_selected_steps_empty_list_skips_all(tmp_path):
    ecu = FakeEcu()
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, ecu.send, ecu.receive)

    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)
    mgr.set_case_selected_steps("hook-1", [])

    mgr.start(request_id=0x18DA00F1, response_id=0x18DA00F1)
    mgr._thread.join(timeout=5.0)

    assert mgr.status()["state"] == "COMPLETED"
    assert ecu.sent == []


def test_get_case_steps_requires_existing_case():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    with pytest.raises(RuntimeError):
        mgr.get_case_steps("nonexistent")


# ---- PDU preview (get_case_steps: pdu_preview / pdu_note) -------------------


def test_get_case_steps_includes_pdu_preview_for_routine_control(tmp_path):
    """User-reported example: routineControl(type=0x01, id=0xFF00,
    optionRecord=0xF1B1) must preview as '31 01 FF 00 F1 B1'."""
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)

    steps = mgr.get_case_steps("block-1")
    routine_step = next(s for s in steps if s["service"] == "routineControl")
    assert routine_step["pdu_preview"] == "31 01 FF 00 F1 B1"
    assert routine_step["pdu_note"] is None


def test_get_case_steps_transfer_data_preview_without_binary(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)

    steps = mgr.get_case_steps("block-1")
    td_step = next(s for s in steps if s["service"] == "transferData")
    assert td_step["pdu_preview"] is None
    assert "바이너리 미로드" in td_step["pdu_note"]


def test_get_case_steps_transfer_data_preview_with_binary(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    bin_path = _write_bin(tmp_path, "fw.bin", 1024)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)
    mgr.set_case_binary("block-1", bin_path)

    steps = mgr.get_case_steps("block-1")
    td_step = next(s for s in steps if s["service"] == "transferData")
    # TESTBLOCK_XML: seekAddress=0x0200, writeSize=0x10, maxNumberOfBlockLength=0x08
    with open(bin_path, "rb") as f:
        binary = f.read()
    expected_first_chunk = binary[0x200:0x208].hex(" ").upper()
    assert td_step["pdu_preview"] == f"36 01 {expected_first_chunk}"
    assert "총 2개 블록" in td_step["pdu_note"]


def test_get_case_steps_security_access_preview(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    block_path = _write_xml(tmp_path, "block.xml", TESTBLOCK_XML)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)

    steps = mgr.get_case_steps("block-1")
    sa_step = next(s for s in steps if s["service"] == "securityAccess")
    assert sa_step["pdu_preview"] == "27 11"
    assert sa_step["pdu_note"]


def test_get_case_steps_diagnostic_session_control_preview(tmp_path):
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    hook_path = _write_xml(tmp_path, "hook.xml", HOOK_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", hook_path, order=0)

    steps = mgr.get_case_steps("hook-1")
    assert steps[0]["pdu_preview"] == "10 81"
    assert steps[1]["pdu_preview"] == "22 F1 87"


def test_get_case_steps_transfer_data_preview_truncates_large_block(tmp_path):
    """A real maxNumberOfBlockLength (e.g. 0x0C02 = 3074 bytes) must not be
    dumped in full into the checklist -- regression for an early version that
    printed the entire first block (thousands of hex bytes) inline."""
    xml = TESTBLOCK_XML.replace('maxNumberOfBlockLength="0x08"', 'maxNumberOfBlockLength="0x0C02"') \
                        .replace('writeSize="0x00000010"', 'writeSize="0x00186508"')
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    block_path = _write_xml(tmp_path, "block.xml", xml)
    bin_path = _write_bin(tmp_path, "fw.bin", 1_600_000)
    mgr.add_case("block-1", "Unit1", "testBlock", block_path, order=0)
    mgr.set_case_binary("block-1", bin_path)

    steps = mgr.get_case_steps("block-1")
    td_step = next(s for s in steps if s["service"] == "transferData")
    assert td_step["pdu_preview"].endswith("...")
    # "36 01" + 12 preview bytes (2 hex chars + 1 space each) + " ..."
    assert len(td_step["pdu_preview"]) < 100
    assert "블록당 3074 bytes" in td_step["pdu_note"]
    assert "총 521개 블록" in td_step["pdu_note"]


# ---- Global STmin override (shared with CAN-SWDL via the frontend) --------


def test_get_fc_stmin_defaults_when_no_override():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    assert mgr._get_fc_stmin() == 0x0A


def test_get_fc_stmin_uses_global_override():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: None, lambda *a, **kw: b"")
    mgr._global_stmin_tx = 0x1F
    assert mgr._get_fc_stmin() == 0x1F


def test_uds_request_with_retry_passes_stmin_override_to_receive(tmp_path):
    """Regression: the override must actually reach isotp_receive's fc_stmin,
    not just be stored -- verified via a fake ECU that records the kwarg."""
    received_stmin = []

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        received_stmin.append(fc_stmin)
        return bytes([0x50, 0x02])  # positive diagnosticSessionControl response

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)
    mgr._global_stmin_tx = 0x2A

    mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "test")

    assert received_stmin == [0x2A]


# ---- NRC 0x78 (ResponsePending) regression tests ---------------------------
#
# Bug report: the ECU sent 7F 10 78 (pending), then the real positive
# response ~800ms later -- but the old code retransmitted the request on
# 0x78 (a spec violation per ISO 14229-1) and re-listened with the same
# short P2 timeout instead of the extended P2* timeout, so it never caught
# the delayed response and timed out. See ota_tester_download_manager.py's
# _uds_request_with_retry.


def test_uds_request_with_retry_does_not_retransmit_on_nrc78():
    send_calls = []
    receive_calls = []
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]  # pending, then positive

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        send_calls.append(bytes(data))
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        receive_calls.append(timeout_s)
        return responses.pop(0)

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)

    result = mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    # the request must be sent exactly once -- never retransmitted on 0x78
    assert len(send_calls) == 1
    # two receive()s: the initial short-P2 wait (pending), then one more
    assert len(receive_calls) == 2


def test_uds_request_with_retry_extends_timeout_after_nrc78():
    """The wait after a 0x78 must use P2*Server_max, not the original
    (much shorter) first-attempt timeout."""
    receive_timeouts = []
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        receive_timeouts.append(timeout_s)
        return responses.pop(0)

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)

    mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert receive_timeouts[0] == 0.05  # first wait: the normal short P2 timeout
    assert receive_timeouts[1] == mgr._p2_star_can_server_max / 1000.0  # 5.0s by default


def test_uds_request_with_retry_raises_after_max_consecutive_pending():
    send_calls = []

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        send_calls.append(bytes(data))
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        return bytes([0x7F, 0x10, 0x78])  # always pending, never resolves

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)

    with pytest.raises(Exception) as exc_info:
        mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl", max_retries=3)

    assert getattr(exc_info.value, "nrc", None) == 0x78
    # still only ever sent once, even after exhausting every pending retry
    assert len(send_calls) == 1


def test_uds_request_with_retry_reuses_same_reader_across_pending_retries():
    """The actual fix for "0x78 다음 실제 응답이 왔는데도 타임아웃으로 실패한다":
    a fresh listener per attempt leaves a gap between attempts where the
    real final response can arrive and be missed entirely (see
    isotp_service.receive()'s `reader` docstring). Verify the manager
    passes the *same* reader object into every _isotp_receive() call within
    one retry sequence instead of a new one each time."""
    readers_seen = []
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, reader=None, **kw):
        readers_seen.append(reader)
        return responses.pop(0)

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: {"sent": True}, fake_receive)

    result = mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    assert len(readers_seen) == 2
    assert readers_seen[0] is not None
    assert readers_seen[0] is readers_seen[1]


# ---- Suppress Positive Response bit (subfunction | 0x80) ------------------


def test_ota_suppress_bit_timeout_is_success_not_failure():
    """0x81 = session 0x01 | suppress bit -- the ECU is required to send
    nothing back, so a plain receive timeout must be treated as success
    instead of propagating as an uncaught isotp_service.IsoTpError (which
    _execute_step()'s `except UdsError` could never catch, aborting the
    entire run instead of just this one step)."""

    def fake_receive(can, rx_id, tx_id, **kw):
        raise Exception("응답 프레임을 기다리다 시간 초과되었습니다")

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: {"sent": True}, fake_receive)

    result = mgr._uds_request_with_retry(bytearray([0x10, 0x81]), 0.05, "diagnosticSessionControl(suppressed)")
    assert result["positive"] is True
    assert result.get("suppressed") is True


def test_ota_suppress_bit_does_not_forgive_an_actual_negative_response():
    def fake_receive(can, rx_id, tx_id, **kw):
        return bytes([0x7F, 0x10, 0x22])  # conditionsNotCorrect

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, lambda *a, **kw: {"sent": True}, fake_receive)

    with pytest.raises(Exception) as exc_info:
        mgr._uds_request_with_retry(bytearray([0x10, 0x81]), 0.05, "diagnosticSessionControl(suppressed)")
    assert getattr(exc_info.value, "nrc", None) == 0x22


# ---- P2/P2* timing read from a case's XML (startCommunication step) -------
#
# No real-world test-rule sample seen so far actually has a
# startCommunication step (unlike CAN-SWDL's schema) -- these tests exercise
# the case where one is present, and confirm the existing hardcoded
# defaults still apply when it's absent (i.e. every real file so far).

COMM_CONFIG_XML = """<?xml version="1.0" encoding="utf-8"?>
<xfrm:root xmlns:xfrm="http://gitauto.com/xfrm/">
  <xfrm:test-rule binaryPath="">
    <xfrm:rule comment="VersionCheck">
      <xfrm:startCommunication>
        <xfrm:config stminTx="0x0A" p2CanServerMax="100" p2StarCanServerMax="8000" NRC78Repetitiontimeout="300" />
      </xfrm:startCommunication>
      <xfrm:diagnosticSessionControl diagnosticSessionType="0x81" confirmPositiveResponse="no" />
    </xfrm:rule>
  </xfrm:test-rule>
</xfrm:root>
"""


def test_add_case_reads_p2_timing_from_startcommunication_step(tmp_path):
    mgr = _mgr()
    path = _write_xml(tmp_path, "comm.xml", COMM_CONFIG_XML)

    mgr.add_case("hook-1", "VersionCheck", "hook", path, order=0)

    assert mgr._p2_can_server_max == 100.0
    assert mgr._p2_star_can_server_max == 8000.0


def test_add_case_keeps_defaults_when_no_startcommunication_step(tmp_path):
    """Matches every real test-rule sample seen in practice -- no timing
    config in the file, so the hardcoded ISO 14229-1-typical defaults
    (P2=50ms, P2*=5000ms) still apply."""
    mgr = _mgr()
    path = _write_xml(tmp_path, "hook.xml", HOOK_XML)

    mgr.add_case("hook-1", "VersionCheck", "hook", path, order=0)

    assert mgr._p2_can_server_max == 50.0
    assert mgr._p2_star_can_server_max == 5000.0


def test_uds_request_with_retry_uses_the_xml_derived_p2_star_value(tmp_path):
    """End-to-end: once a case with a startCommunication config is loaded,
    a later NRC 0x78 pending-wait must use that case's P2* value, not the
    generic hardcoded default."""
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]
    receive_timeouts = []

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        receive_timeouts.append(timeout_s)
        return responses.pop(0)

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)
    path = _write_xml(tmp_path, "comm.xml", COMM_CONFIG_XML)
    mgr.add_case("hook-1", "VersionCheck", "hook", path, order=0)

    mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert receive_timeouts[1] == 8.0  # p2StarCanServerMax="8000" from the XML


# ---- Auto CAN log per case (see log_service.AutoCanLogger) -----------------


class _SpyAutoLogger:
    """Records start()/stop() calls instead of touching a real CAN bus/file
    -- see ota_tester_download_manager.py's _run_case_steps() for how the
    real AutoCanLogger is used."""

    def __init__(self):
        self.calls: list[tuple] = []

    def start(self, label):
        self.calls.append(("start", label))

    def stop(self, success):
        self.calls.append(("stop", success))


def test_run_case_steps_auto_logs_pass_and_fail_per_case():
    mgr = _mgr()
    spy = _SpyAutoLogger()
    mgr._auto_logger = spy

    passing_case = {"label": "case-A", "steps": [], "selected_steps": None}
    assert mgr._run_case_steps(passing_case) is True
    assert spy.calls == [("start", "case-A"), ("stop", True)]

    spy.calls.clear()
    mgr._stop_event.set()  # simulate a user Stop mid-case -> counts as a failed case
    failing_case = {"label": "case-B", "steps": [{"service": "startCommunication", "params": {}}], "selected_steps": None}
    assert mgr._run_case_steps(failing_case) is False
    assert spy.calls == [("start", "case-B"), ("stop", False)]


# ---- TransferData TesterPresent keep-alive ----------------------------------


def test_send_tester_present_sends_suppressed_pdu_functionally_without_waiting():
    """[3E 80] -- sent on the functional broadcast ID (0x7DF), not this
    case's own physical request_id, and fire-and-forget."""
    sent: list[tuple] = []

    def fake_send(can, tx_id, rx_id, data, **kw):
        sent.append((tx_id, rx_id, bytes(data)))
        return {"sent": True}

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, lambda *a, **kw: b"")

    mgr._send_tester_present()

    assert sent == [(0x7DF, 0x7DF, bytes([0x3E, 0x80]))]


def test_transfer_data_sends_tester_present_keepalive_periodically(monkeypatch):
    """Bug report: a long TransferData block transfer runs long enough
    without any other diagnostic traffic to trip the ECU's S3 session timer.
    A suppressed TesterPresent must go out at least every
    TESTER_PRESENT_INTERVAL_S while blocks are still being sent."""
    import ota_tester_download_manager as otdm
    monkeypatch.setattr(otdm, "TESTER_PRESENT_INTERVAL_S", 0.05)

    def slow_send(can, tx_id, rx_id, data, **kw):
        time.sleep(0.03)  # let real elapsed time cross the (shrunk) interval
        return {"sent": True}

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, slow_send, lambda *a, **kw: bytes([0x76, 0x01]))

    tp_calls = []
    mgr._send_tester_present = lambda: tp_calls.append(time.time())

    case = {"label": "block-1", "binary_data": bytes(range(24))}
    mgr._execute_transfer_data(case, {"seekAddress": "0x0000", "writeSize": "0x18", "maxNumberOfBlockLength": "0x04"})

    assert len(tp_calls) >= 2


def test_uds_request_with_retry_passes_configured_stmin_as_send_floor():
    """The UI's global STmin override must reach isotp_service.send() as
    min_stmin_s so it can deliberately slow a TransferData block send down
    even when the ECU's own Flow Control asks for less -- see
    isotp_service.send()'s min_stmin_s docstring."""
    sent_kwargs = {}

    def fake_send(can, tx_id, rx_id, request, **kw):
        sent_kwargs.update(kw)
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, **kw):
        return bytes([0x76, 0x01])

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)
    mgr._global_stmin_tx = 0x32  # 50ms, matches decode_stmin(0x32) == 0.05s

    mgr._uds_request_with_retry(bytearray([0x36, 0x01, 0xAA]), 0.05, "TransferData(seq=1)")

    assert sent_kwargs["min_stmin_s"] == pytest.approx(0.05)


def test_uds_request_with_retry_no_send_floor_when_stmin_checkbox_off():
    """Unchecking the "STmin" checkbox (no _global_stmin_tx override) must
    go back to sending as fast as the ECU's own Flow Control allows -- the
    0x0A default (used for our own FC when *receiving*) must not leak into
    the send-side floor."""
    sent_kwargs = {}

    def fake_send(can, tx_id, rx_id, request, **kw):
        sent_kwargs.update(kw)
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, **kw):
        return bytes([0x76, 0x01])

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = OtaTesterDownloadManager(can, fake_send, fake_receive)
    assert mgr._global_stmin_tx is None  # checkbox off
    assert mgr._local_stmin_override is None

    mgr._uds_request_with_retry(bytearray([0x36, 0x01, 0xAA]), 0.05, "TransferData(seq=1)")

    assert sent_kwargs["min_stmin_s"] == 0.0
