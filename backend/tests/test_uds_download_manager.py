"""Tests for uds_download_manager.py's NRC 0x78 (ResponsePending) handling.

Bug report (CAN-SWDL / OTA Tester share this exact logic): on NRC 0x78 the
old code retransmitted the original request (a spec violation per ISO
14229-1 -- the client must not resend while the server is still
processing) and re-listened with the same short P2 timeout instead of the
extended P2*Server_max, so a real ECU that replies ~1s after a pending
frame was never caught before the timeout. See
UdsDownloadManager._uds_request_with_retry.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import uds_download_manager as udm
from uds_download_manager import UdsDownloadManager
from uds_xml_parser import UdsProcedure, UdsRule, UdsStep


class _FakeNotifier:
    """`_uds_request_with_retry` now keeps one CAN listener registered
    across its whole NRC-0x78 retry sequence (see its docstring), so the
    fake CAN manager these tests use needs a notifier that at least accepts
    add_listener/remove_listener -- these tests fake `_isotp_send`/
    `_isotp_receive` entirely, so the listener itself is never actually
    used to receive anything."""

    def add_listener(self, listener) -> None:
        pass

    def remove_listener(self, listener) -> None:
        pass


def _manager(fake_send, fake_receive) -> UdsDownloadManager:
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, fake_send, fake_receive)
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)
    return mgr


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

    mgr = _manager(fake_send, fake_receive)

    result = mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    assert len(send_calls) == 1  # never retransmitted on 0x78
    assert len(receive_calls) == 2


def test_uds_request_with_retry_extends_timeout_after_nrc78():
    receive_timeouts = []
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        receive_timeouts.append(timeout_s)
        return responses.pop(0)

    mgr = _manager(fake_send, fake_receive)

    mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert receive_timeouts[0] == 0.05  # first wait: the normal short P2 timeout
    assert receive_timeouts[1] == mgr._procedure.p2_star_can_server_max / 1000.0  # 5.0s by default


def test_uds_request_with_retry_uses_procedures_own_p2_star_value():
    """The extended wait must come from whatever the loaded XML actually
    specifies, not a hardcoded constant."""
    receive_timeouts = []
    responses = [bytes([0x7F, 0x10, 0x78]), bytes([0x50, 0x02])]

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        receive_timeouts.append(timeout_s)
        return responses.pop(0)

    mgr = _manager(fake_send, fake_receive)
    mgr._procedure.p2_star_can_server_max = 8000  # non-default value from XML

    mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert receive_timeouts[1] == 8.0


def test_uds_request_with_retry_raises_after_max_consecutive_pending():
    send_calls = []

    def fake_send(can, tx_id, rx_id, data, is_extended_id=False, fc_timeout_s=1.0, **kw):
        send_calls.append(bytes(data))
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, timeout_s=1.0, is_extended_id=False, fc_stmin=0, **kw):
        return bytes([0x7F, 0x10, 0x78])  # always pending, never resolves

    mgr = _manager(fake_send, fake_receive)

    with pytest.raises(Exception) as exc_info:
        mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl", max_retries=3)

    assert getattr(exc_info.value, "nrc", None) == 0x78
    assert len(send_calls) == 1  # still only ever sent once


def test_uds_request_with_retry_raises_without_procedure_loaded():
    mgr = _manager(lambda *a, **k: {"sent": True}, lambda *a, **k: bytes([0x50, 0x02]))
    mgr._procedure = None
    with pytest.raises(RuntimeError):
        mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "test")


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

    mgr = _manager(lambda *a, **kw: {"sent": True}, fake_receive)
    result = mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    assert len(readers_seen) == 2
    assert readers_seen[0] is not None
    assert readers_seen[0] is readers_seen[1]


class _OrderTrackingNotifier:
    """Records add_listener/remove_listener calls (interleaved with fake
    send/receive calls appending their own markers to the same list) so a
    test can assert the exact order they happened in."""

    def __init__(self, log: list[str]):
        self._log = log

    def add_listener(self, listener) -> None:
        self._log.append("add_listener")

    def remove_listener(self, listener) -> None:
        self._log.append("remove_listener")


def test_uds_request_registers_listener_before_sending():
    """Regression for "TransferData 응답이 0.004688초에 왔는데 놓치고 에러
    처리했다": the listener used to be created only *after* send()
    returned, leaving a gap in which an unusually fast ECU response could
    arrive on the bus before anything was registered to catch it --
    python-can's Notifier only delivers to listeners registered at the
    moment a frame arrives. The listener must now be registered before the
    request is even sent, and the same reader passed to both send() and
    receive()."""
    log: list[str] = []

    def fake_send(can, tx_id, rx_id, data, **kw):
        log.append("send")
        assert kw.get("reader") is not None
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, **kw):
        log.append("receive")
        assert kw.get("reader") is not None
        return bytes([0x50, 0x02])

    can_obj = type("FakeCan", (), {"notifier": _OrderTrackingNotifier(log)})()
    mgr = UdsDownloadManager(can_obj, fake_send, fake_receive)
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)

    result = mgr._uds_request(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    assert log == ["add_listener", "send", "receive", "remove_listener"]


def test_uds_request_with_retry_registers_listener_before_sending():
    log: list[str] = []

    def fake_send(can, tx_id, rx_id, data, **kw):
        log.append("send")
        assert kw.get("reader") is not None
        return {"sent": True}

    def fake_receive(can, rx_id, tx_id, **kw):
        log.append("receive")
        assert kw.get("reader") is not None
        return bytes([0x50, 0x02])

    can_obj = type("FakeCan", (), {"notifier": _OrderTrackingNotifier(log)})()
    mgr = UdsDownloadManager(can_obj, fake_send, fake_receive)
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)

    result = mgr._uds_request_with_retry(bytearray([0x10, 0x02]), 0.05, "diagnosticSessionControl")

    assert result["positive"] is True
    assert log == ["add_listener", "send", "receive", "remove_listener"]


# ---- Suppress Positive Response bit (subfunction | 0x80) ------------------


def test_suppress_bit_timeout_is_success_not_failure():
    """0x81 = session 0x01 | suppress bit -- the ECU is required to send
    nothing back, so a plain receive timeout must be treated as success."""

    def fake_receive(can, rx_id, tx_id, **kw):
        raise Exception("응답 프레임을 기다리다 시간 초과되었습니다")

    mgr = _manager(lambda *a, **kw: {"sent": True}, fake_receive)

    result = mgr._uds_request(bytearray([0x10, 0x81]), 0.05, "diagnosticSessionControl(suppressed)")
    assert result["positive"] is True
    assert result.get("suppressed") is True


def test_suppress_bit_timeout_is_success_via_retry_path_too():
    def fake_receive(can, rx_id, tx_id, **kw):
        raise Exception("Consecutive Frame 수신 중 시간 초과되었습니다")

    mgr = _manager(lambda *a, **kw: {"sent": True}, fake_receive)

    result = mgr._uds_request_with_retry(bytearray([0x11, 0x81]), 0.05, "ecuReset(suppressed)")
    assert result["positive"] is True


def test_suppress_bit_does_not_forgive_an_actual_negative_response():
    """The suppress bit only means "no *positive* response" -- an actual
    negative response arriving is still a real failure regardless."""

    def fake_receive(can, rx_id, tx_id, **kw):
        return bytes([0x7F, 0x10, 0x22])  # conditionsNotCorrect

    mgr = _manager(lambda *a, **kw: {"sent": True}, fake_receive)

    with pytest.raises(Exception) as exc_info:
        mgr._uds_request(bytearray([0x10, 0x81]), 0.05, "diagnosticSessionControl(suppressed)")
    assert getattr(exc_info.value, "nrc", None) == 0x22


def test_non_suppress_request_still_fails_on_timeout():
    """No suppress bit (0x01, not 0x81) -> a timeout is a real failure, same
    as before this fix."""

    def fake_receive(can, rx_id, tx_id, **kw):
        raise Exception("응답 프레임을 기다리다 시간 초과되었습니다")

    mgr = _manager(lambda *a, **kw: {"sent": True}, fake_receive)

    with pytest.raises(Exception):
        mgr._uds_request(bytearray([0x10, 0x01]), 0.05, "diagnosticSessionControl")


def test_second_diagnostic_session_control_step_uses_its_own_session_choice():
    """Bug report: a procedure with TWO diagnosticSessionControl steps (each
    offering both diagnosticSessionType and background_diagnosticSessionType,
    both defaulted by the widget to "diagnosticSessionType") sent an
    unrequested extra background SessionControl on top of the real one.

    Root cause: modified_params is keyed by *service name* only, so
    _get_effective_params() merges every _sessionType_<idx> override for
    every diagnosticSessionControl step in the procedure into each
    occurrence's own params. The old code picked `next(iter(...))` off the
    resulting set of _sessionType_* keys -- for step[1] that could just as
    easily grab step[3]'s override key as its own (Python's str hash
    randomization makes which one non-deterministic across process runs),
    and when the picked key's value didn't match a real param on *this*
    step, it silently fell through to the "no selection" branch that sends
    both the main and background session. See uds_download_manager.py's
    diagnosticSessionControl handler in _execute_step.
    """
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, None, None)

    proc = UdsProcedure(request_id=0x783, response_id=0x78B)
    proc.processing_rule = UdsRule(
        preparation=[
            UdsStep(service="startCommunication", params={}, sub_steps=[UdsStep(service="cfg", params={})]),
            UdsStep(service="diagnosticSessionControl",
                    params={"diagnosticSessionType": "0x02", "background_diagnosticSessionType": "0x03"}),
        ],
        unit=[],
        complete=[
            UdsStep(service="diagnosticSessionControl",
                    params={"diagnosticSessionType": "0x01", "background_diagnosticSessionType": "0x03"}),
        ],
    )
    mgr._procedure = proc
    mgr._binary_data = b"x"

    # Mirrors what UdsSwdlWidget.tsx auto-selects for every dual-session-type
    # step: "diagnosticSessionType" (the immediate session), keyed by each
    # step's own global index (1 and 2 here).
    modified_params = {
        "diagnosticSessionControl": {
            "_sessionType_1": "diagnosticSessionType",
            "_sessionType_2": "diagnosticSessionType",
        }
    }

    sent: list[str] = []

    def fake_retry(self, request, timeout_s, label, retry_delay_s=0.1):
        sent.append(label)
        return {"data": bytearray([0x50, request[1]])}

    with patch.object(UdsDownloadManager, "_uds_request_with_retry", fake_retry):
        mgr._run_procedure(selected_steps=None, modified_params=modified_params)

    # Only the two chosen session switches -- no extra "Background" send for
    # either step.
    assert sent == ["SessionControl(0x02)", "SessionControl(0x01)"]


def test_progress_current_step_idx_tracks_the_running_step_and_resets_when_done():
    """UI feature: highlight the currently-executing step in the checklist.
    _run_steps must report each selected step's global index via
    _update_progress(current_step_idx=...) as it runs, and reset it to -1
    once the whole procedure completes."""
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, lambda *a, **kw: {"sent": True}, lambda *a, **kw: b"")

    proc = UdsProcedure(request_id=0x783, response_id=0x78B)
    proc.processing_rule = UdsRule(
        preparation=[UdsStep(service="startCommunication", params={}, sub_steps=[UdsStep(service="cfg", params={})])],
        unit=[UdsStep(service="controlDTCSetting", params={}), UdsStep(service="communicationControl", params={})],
        complete=[],
    )
    mgr._procedure = proc
    mgr._binary_data = b"x"

    seen_idx: list[int] = []
    orig_update_progress = UdsDownloadManager._update_progress

    def spy_update_progress(self, **fields):
        if "current_step_idx" in fields:
            seen_idx.append(fields["current_step_idx"])
        orig_update_progress(self, **fields)

    with patch.object(UdsDownloadManager, "_update_progress", spy_update_progress):
        mgr._run_procedure(selected_steps=None, modified_params=None)

    # step[0] startCommunication, step[1]/step[2] the two logging-only
    # services -- then reset to -1 once the run finishes.
    assert seen_idx == [0, 1, 2, -1]
    assert mgr._progress["current_step_idx"] == -1


class _SpyAutoLogger:
    """Records start()/stop() calls instead of touching a real CAN bus/file
    -- see uds_download_manager.py's _run() for how the real AutoCanLogger
    is used."""

    def __init__(self):
        self.calls: list[tuple] = []

    def start(self, label):
        self.calls.append(("start", label))

    def stop(self, success):
        self.calls.append(("stop", success))


def test_run_auto_logs_success_with_xml_stem_label():
    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, lambda *a, **kw: {"sent": True}, lambda *a, **kw: b"")
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)
    mgr._procedure.processing_rule = UdsRule(
        preparation=[UdsStep(service="startCommunication", params={}, sub_steps=[UdsStep(service="cfg", params={})])],
    )
    mgr._binary_data = b"x"
    mgr._xml_path = "/uploads/udswdl/RS4PE_96370T4AA0_01_2672.xml"
    spy = _SpyAutoLogger()
    mgr._auto_logger = spy

    mgr._run(selected_steps=None, modified_params=None)

    assert spy.calls == [("start", "RS4PE_96370T4AA0_01_2672"), ("stop", True)]


def test_run_auto_logs_failure_on_uds_error():
    def fake_receive(can, rx_id, tx_id, **kw):
        raise Exception("응답 프레임을 기다리다 시간 초과되었습니다")

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, lambda *a, **kw: {"sent": True}, fake_receive)
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)
    mgr._procedure.processing_rule = UdsRule(
        preparation=[UdsStep(service="diagnosticSessionControl", params={"diagnosticSessionType": "0x03"})],
    )
    mgr._binary_data = b"x"
    mgr._xml_path = "/uploads/udswdl/foo.xml"
    spy = _SpyAutoLogger()
    mgr._auto_logger = spy

    mgr._run(selected_steps=None, modified_params=None)

    assert spy.calls[0] == ("start", "foo")
    assert spy.calls[-1] == ("stop", False)


def test_send_tester_present_sends_suppressed_pdu_functionally_without_waiting():
    """[3E 80] (suppress positive response) -- sent on the functional
    broadcast ID (0x7DF), not the procedure's own physical request_id, and
    fire-and-forget (no response wait, since a Single Frame send never
    blocks on Flow Control and nothing here ever calls receive())."""
    sent: list[tuple] = []

    def fake_send(can, tx_id, rx_id, data, **kw):
        sent.append((tx_id, rx_id, bytes(data)))
        return {"sent": True}

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, fake_send, lambda *a, **kw: b"")
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)

    mgr._send_tester_present()

    assert sent == [(0x7DF, 0x7DF, bytes([0x3E, 0x80]))]


def test_transfer_data_sends_tester_present_keepalive_periodically(monkeypatch):
    """Bug report: a long TransferData block transfer runs long enough
    without any other diagnostic traffic to trip the ECU's S3 session timer.
    A suppressed TesterPresent must go out at least every
    TESTER_PRESENT_INTERVAL_S while blocks are still being sent."""
    monkeypatch.setattr(udm, "TESTER_PRESENT_INTERVAL_S", 0.05)

    can = type("FakeCan", (), {"notifier": _FakeNotifier()})()
    mgr = UdsDownloadManager(can, lambda *a, **kw: {"sent": True}, lambda *a, **kw: b"")
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)
    mgr._binary_data = bytes(range(24))
    mgr._download_block_size = 4  # -> 6 blocks

    tp_calls = []
    mgr._send_tester_present = lambda: tp_calls.append(time.time())

    def fake_retry(self, request, timeout_s, label, retry_delay_s=0.1):
        time.sleep(0.03)  # let real elapsed time cross the (shrunk) interval
        return {"data": bytearray([0x76, request[1]])}

    step = UdsStep(service="transferData", params={"seekAddress": "0x0000", "writeSize": "0x18"})
    with patch.object(UdsDownloadManager, "_uds_request_with_retry", fake_retry):
        mgr._execute_transfer_data(step, modified_params=None)

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
    mgr = UdsDownloadManager(can, fake_send, fake_receive)
    mgr._procedure = UdsProcedure(request_id=0x783, response_id=0x78B)
    mgr._global_stmin_tx = 0x32  # 50ms, matches decode_stmin(0x32) == 0.05s

    mgr._uds_request_with_retry(bytearray([0x36, 0x01, 0xAA]), 0.05, "TransferData(seq=1)")

    assert sent_kwargs["min_stmin_s"] == pytest.approx(0.05)
