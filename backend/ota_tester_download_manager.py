"""OTA Tester Download Manager.

Executes a sequence parsed from a GITAuto xfrm:test-rule XML.

Key differences from the existing UDS Download Manager:
- Input XML uses the new xfrm:test-rule / xfrm:rule layout (flat list).
- No activation/version-checking phases — just the steps in order.
- Each step may have confirmPositiveResponse="no", meaning that an
  expected UDS negative response (NRC 0x78 or any NRC) should be treated
  as a success instead of an error.
- No binary download (no TransferData loops).  The file attribute
  "binaryPath" is informational only."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

try:
    from uds_xml_parser import parse_test_rule_xml
except ImportError:
    parse_test_rule_xml = None  # type: ignore

MAX_EVENTS = 500

_STATE_IDLE = "IDLE"
_STATE_LOADING = "LOADING"
_STATE_READY = "READY"
_STATE_RUNNING = "RUNNING"
_STATE_COMPLETED = "COMPLETED"
_STATE_ERROR = "ERROR"


class OtaTesterDownloadManager:
    """Manage OTA_Tester download sequence from test-rule XML."""

    def __init__(
        self,
        can_manager,
        isotp_send_fn: Callable[[int, bytearray], bool],
        isotp_receive_fn: Callable[[int, float], bytes],
    ):
        self._can = can_manager
        self._isotp_send: Callable[[int, bytearray], bool] = isotp_send_fn
        self._isotp_receive: Callable[[int, float], bytes] = isotp_receive_fn

        self._lock = threading.RLock()
        self._state = _STATE_IDLE
        self._steps: list[dict] = []
        self._current_step_index = -1
        self._xml_path: Optional[str] = None
        self._events: list[dict] = []
        self._error_message: Optional[str] = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Communication settings (defaults, overridden in start_sequence)
        self._request_id: int = 0x18DA00F1
        self._response_id: int = 0x18DA00F1
        self._p2_star_can_server_max: float = 5.0  # 5000ms

    # ---- Public API ---------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def xml_path(self) -> Optional[str]:
        return self._xml_path

    @property
    def steps(self) -> list[dict]:
        return list(self._steps)

    @property
    def current_step_index(self) -> int:
        return self._current_step_index

    @property
    def current_step_name(self) -> Optional[str]:
        with self._lock:
            if 0 <= self._current_step_index < len(self._steps):
                return self._steps[self._current_step_index].get("service")
            return None

    @property
    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events[-MAX_EVENTS:])

    @property
    def error(self) -> Optional[str]:
        return self._error_message

    def load_xml(self, path: str) -> dict:
        """Load and parse a test-rule XML file."""
        with self._lock:
            self._state = _STATE_LOADING
        try:
            steps = parse_test_rule_xml(path)
            with self._lock:
                self._steps = steps
                self._xml_path = path
                self._state = _STATE_READY
                self._current_step_index = -1
                self._events = []
                self._error_message = None

            self._log(
                level="INFO",
                msg=f"OTA Tester XML 로드 완료: {os.path.basename(path)}",
            )
            self._log(
                level="INFO",
                msg=f"Step 수: {len(steps)}",
            )
            return {"ok": True, "total_steps": len(steps)}
        except Exception as exc:
            with self._lock:
                self._state = _STATE_ERROR
                self._error_message = str(exc)
            raise

    def start_sequence(
        self,
        request_id: int = 0x18DA00F1,
        response_id: int = 0x18DA00F1,
        stmin_tx: int = 0x0A,
        p2_can_server_max: float = 0.05,
        p2_star_can_server_max: float = 5.0,
        fr_pre_step_delay_ms: float = 0,
    ) -> None:
        """Start the step execution sequence in a background thread."""
        with self._lock:
            if self._running:
                raise RuntimeError("Sequence already running")
            self._request_id = request_id
            self._response_id = response_id
            self._p2_star_can_server_max = p2_star_can_server_max

        self._stop_event.clear()
        self._thread = threading.Thread(name="ota_tester", target=self._run_sequence)
        self._thread.daemon = True
        self._thread.start()

    def stop(self) -> None:
        """Stop the current sequence."""
        self._stop_event.set()

    # ---- Internals ----------------------------------------------------------

    def _log(self, *, level: str, msg: str, service: Optional[str] = None) -> None:
        log_entry = {
            "ts": time.time(),
            "level": level,
            "msg": msg,
        }
        if service:
            log_entry["service"] = service
        with self._lock:
            self._events.append(log_entry)

    def _run_sequence(self) -> None:
        with self._lock:
            self._state = _STATE_RUNNING
            self._running = True
            self._error_message = None

        try:
            for idx, step in enumerate(self._steps):
                if self._stop_event.is_set():
                    self._log(level="INFO", msg="Sequence stopped by user")
                    break

                with self._lock:
                    self._current_step_index = idx

                service = step.get("service", "?")
                params = step.get("params", {})
                sub_steps = step.get("sub_steps", [])
                confirm_pos_rsp = step.get("confirm_positive_response", True)

                self._log(
                    level="INFO",
                    msg=f"Step [{idx+1}/{len(self._steps)}] {service}",
                    service=service,
                )

                success = self._execute_step(
                    service=service,
                    params=params,
                    sub_steps=sub_steps,
                    confirm_pos_rsp=confirm_pos_rsp,
                )
                if not success and confirm_pos_rsp:
                    self._log(
                        level="ERROR",
                        msg=f"Step {service} failed (POS response expected)",
                    )
                    with self._lock:
                        self._error_message = f"Step {service} failed"
                    break
                elif not success and not confirm_pos_rsp:
                    # Negative response *is* the expected outcome
                    self._log(
                        level="INFO",
                        msg=f"Step {service} returned negative response (expected)",
                        service=service,
                    )
                else:
                    self._log(
                        level="INFO",
                        msg=f"Step {service} OK",
                        service=service,
                    )

                self._step_delay(step)

            with self._lock:
                if self._error_message is None:
                    self._state = _STATE_COMPLETED
                else:
                    self._state = _STATE_ERROR
        except Exception as e:
            with self._lock:
                self._error_message = str(e)
                self._state = _STATE_ERROR
            self._log(level="ERROR", msg=f"Unhandled error: {e}")
        finally:
            with self._lock:
                self._running = False

    def _step_delay(self, step: dict) -> None:
        """Inject delay based on step attributes (endDelay, etc.)."""
        dly = float(step.get("params", {}).get("endDelay", "0") or 0)
        if dly > 0:
            self._log(level="INFO", msg=f"Waiting {dly}ms (end delay)")
            if self._stop_event.wait(timeout=dly / 1000.0):
                return

    def _execute_step(
        self,
        service: str,
        params: dict[str, str],
        sub_steps: list[dict],
        confirm_pos_rsp: bool,
    ) -> bool:
        """Execute a single step. Returns True if (positive=success) XOR (negative=expected)."""
        tx_pdu = self._build_pdu(service, params, sub_steps)
        if tx_pdu is None:
            self._log(level="INFO", msg=f"Skipping {service} (no TX PDU)", service=service)
            return True

        resp = self._send_and_receive(tx_pdu)
        return self._eval_response(resp, service, confirm_pos_rsp)

    def _send_and_receive(self, payload: bytearray) -> bytes:
        timeout_sec = self._p2_star_can_server_max / 1000.0
        self._isotp_send(self._request_id, payload)
        resp = self._isotp_receive(self._response_id, timeout_sec)
        return resp if resp is not None else b""

    def _eval_response(self, resp: bytes, service: str, confirm: bool) -> bool:
        if not resp:
            self._log(level="WARNING", msg=f"No response from ECU for {service}", service=service)
            return False

        from uds_core import is_positive_response, parse_negative_response

        # Determine the service ID
        service_id_map = {
            "diagnosticSessionControl": 0x10,
            "ecuReset": 0x11,
            "readDataByIdentifier": 0x22,
            "securityAccess": 0x27,
            "communicationControl": 0x28,
            "routineControl": 0x31,
            "requestDownload": 0x34,
            "transferData": 0x36,
            "requestTransferExit": 0x37,
            "testerPresent": 0x3E,
            "controlDTCSetting": 0x85,
        }
        sid = service_id_map.get(service, 0)

        if is_positive_response(resp, sid):
            return True
        else:
            nrc, desc = parse_negative_response(resp)
            if nrc == 0:
                self._log(level="ERROR", msg=f"Strange response: {resp.hex()}")
                return False

            self._log(
                level="INFO",
                msg=f"GOT NRC 0x{nrc:02X} ({desc}) for {service}",
                service=service,
            )
            # confirmPositiveResponse="no" means negative is expected
            return not confirm

    # ---- PDU builders -------------------------------------------------------

    @staticmethod
    def _to_int(val, default=0):
        """Safely convert hex string or int from XML param to int."""
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.startswith("0x"):
            return int(val, 16)
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _build_pdu(self, service: str, params: dict, sub_steps: list[dict]) -> Optional[bytearray]:
        """Dispatch to the appropriate builder from uds_core."""
        from uds_core import (
            build_diagnostic_session,
            build_ecu_reset,
            build_read_data_by_id,
            build_security_access_request_seed,
            build_security_access_send_key,
            build_communication_control,
            build_routine_control,
            build_request_download,
            build_transfer_data,
            build_request_transfer_exit,
            build_tester_present,
            build_control_dtc_setting,
        )

        svc = service

        if svc == "diagnosticSessionControl":
            session_typ = params.get("sessionType", "0x01")
            return build_diagnostic_session(int(session_typ, 16))

        if svc == "ecuReset":
            reset_mode = int(params.get("resetMode", "0"), 16)
            return build_ecu_reset(reset_mode)

        if svc == "readDataByIdentifier":
            did = int(params.get("did", "0xF187"), 16)
            return build_read_data_by_id(did)

        if svc == "securityAccess":
            if sub_steps:
                sub = sub_steps[0]
                sub_service = sub.get("service", "")
                if sub_service in ("requestSeed", "securityAccessRequestSeed"):
                    return build_security_access_request_seed()
                elif sub_service in ("sendKey", "securityAccessSendKey"):
                    key_hex = sub.get("params", {}).get("key", "")
                    key_bytes = bytes.fromhex(key_hex.replace("0x", ""))
                    return build_security_access_send_key(key_bytes)
            # If no sub_steps, default to request seed
            return build_security_access_request_seed()

        if svc == "communicationControl":
            ctrl_type = int(params.get("controlType", "0"), 16)
            comm_type = int(params.get("communicationType", "0"), 16)
            return build_communication_control(ctrl_type, comm_type)

        if svc == "routineControl":
            rc_type = params.get("routineControlType", "0")
            rc_hex = params.get("routineIdentifier", "0x")
            rid = int(rc_hex, 16)
            rc_val = int(rc_type, 16) if rc_type.startswith("0x") else int(rc_type)
            return build_routine_control(rc_val, rid)

        if svc == "requestDownload":
            dfi = int(params.get("dataFormatIdentifier", "0xA"), 16)
            alfi_str = params.get("addressAndLengthFormatIdentifier", "44")
            alfi_hex = int(alfi_str, 16) if isinstance(alfi_str, str) else alfi_str
            mem_addr = int(params.get("memoryAddress", "0x"), 16)
            mem_size = int(params.get("memorySize", "0x"), 16)
            return build_request_download(dfi, alfi_hex, memory_address=mem_addr, memory_size=mem_size)

        if svc == "transferData":
            data_hex = params.get("data", "")
            bs_hex = params.get("blockSequenceNumber", "0x01")
            bs = int(bs_hex, 16) if "0x" in str(bs_hex) else int(bs_hex)
            if data_hex:
                payload = bytes.fromhex(data_hex.replace("0x", ""))
            else:
                payload = b''
            return build_transfer_data(bs, payload)

        if svc == "requestTransferExit":
            return build_request_transfer_exit()

        if svc == "testerPresent":
            suppress = params.get("subFunction", "") == "0x80"
            return build_tester_present(suppress)

        if svc == "controlDTCSetting":
            dtc_type = int(params.get("dtcSettingType", "0"), 16)
            return build_control_dtc_setting(dtc_type)

        return None

    def status(self) -> dict:
        """Return a dict suitable for the JSON response (compatible API)."""
        return self.get_status_dict()

    def start(self, request_id: int, response_id: int):
        """Start the sequence (compatible API).
        request_id 및 response_id는 start_sequence로 전달됨.
        """
        return self.start_request(request_id, response_id)
    
    def start_request(self, request_id: int, response_id: int):
        """Start the sequence with request/response IDs."""
        self.start_sequence(
            request_id=request_id,
            response_id=response_id,
            stmin_tx=0x0A,
            p2_star_can_server_max=5.0,
        )
        return {"ok": True, "total_steps": len(self._steps)}

    def get_status_dict(self) -> dict:
        """Return a dict suitable for the JSON response."""
        with self._lock:
            return {
                "state": self._state,
                "running": self._running,
                "procedure_loaded": self._state in (_STATE_READY, _STATE_RUNNING),
                "xml_filename": os.path.basename(self._xml_path) if self._xml_path else None,
                "total_steps": len(self._steps),
                "current_step_index": self._current_step_index,
                "current_step_name": self.current_step_name,
                "events": self.events,
                "error": self._error_message,
            }

    def get_result_dict(self) -> dict:
        """Return step results in JSON-friendly format."""
        return {
            "total_steps": len(self._steps),
            "all_success": self._state == _STATE_COMPLETED,
            "error": self._error_message,
        }