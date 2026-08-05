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


def _manager(fake_send, fake_receive) -> UdsDownloadManager:
    can = type("FakeCan", (), {"notifier": object()})()
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
