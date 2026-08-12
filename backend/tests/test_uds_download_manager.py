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

import pytest

from uds_download_manager import UdsDownloadManager
from uds_xml_parser import UdsProcedure


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
