"""OTA Tester Download Manager.

Executes an ordered list of "cases" -- each parsed from its own GITAuto
xfrm:test-rule XML file (a "hook" or "testBlock" entry from a CLI test
package's Testcases/<id>/*.json manifest) -- one after another, skipping any
case the user has deselected.

Key differences from the CAN-SWDL UDS Download Manager (uds_download_manager.py):
- Input XML uses the xfrm:test-rule / xfrm:rule flat-list layout (parsed by
  uds_xml_parser.parse_test_rule_xml), not the phased processing-rule/
  activate-rule layout.
- A run is a *sequence of cases*, each with its own steps and (optionally)
  its own firmware binary -- rather than a single procedure + single binary.
- Each step may have confirmPositiveResponse="no", meaning an expected UDS
  negative response (any NRC) should be treated as success instead of error.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Callable, Any

from uds_core import (
    UdsError,
    build_diagnostic_session,
    build_ecu_reset,
    build_read_data_by_id,
    build_security_access_request_seed,
    build_security_access_send_key,
    build_communication_control,
    build_routine_control,
    build_request_download,
    build_transfer_data,
    build_transfer_exit,
    build_tester_present,
    build_control_dtc_setting,
    parse_response,
    parse_request_download_response,
    generate_key,
)

logger = logging.getLogger(__name__)

try:
    from uds_xml_parser import parse_test_rule_xml
except ImportError:
    parse_test_rule_xml = None  # type: ignore

MAX_EVENTS = 500

_STATE_IDLE = "IDLE"
_STATE_READY = "READY"
_STATE_RUNNING = "RUNNING"
_STATE_COMPLETED = "COMPLETED"
_STATE_ERROR = "ERROR"


def _to_int(val: Any, default: int = 0) -> int:
    """Safely convert a hex string ("0x..") or plain int/str from an XML
    attribute into an int."""
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.strip():
        try:
            return int(val, 16) if val.lower().startswith("0x") else int(val)
        except ValueError:
            logger.warning("_to_int: could not parse %r, falling back to %r", val, default)
            return default
    return default


def _extract_comm_timing(steps: list[dict]) -> Optional[dict]:
    """Look for a `startCommunication` step among a case's parsed steps and
    return its config params (p2CanServerMax / p2StarCanServerMax /
    NRC78Repetitiontimeout / stminTx / ...), or None if the case doesn't
    have one.

    None of the real xfrm:test-rule sample files this project has seen
    include such a step (unlike CAN-SWDL's xfrm:processing-rule schema,
    which nests it under startCommunication's first sub-step) -- OTA
    Tester's flat rule list has historically carried no timing config at
    all, which is why _p2_can_server_max/_p2_star_can_server_max default
    to fixed constants below. This is deliberately schema-tolerant (checks
    both the step's own params and its first sub-step's, matching either
    layout) so that if a future export ever does include one, it's picked
    up automatically instead of silently ignored."""
    for step in steps:
        if step.get("service") != "startCommunication":
            continue
        sub_steps = step.get("sub_steps") or []
        if sub_steps and sub_steps[0].get("params"):
            return sub_steps[0]["params"]
        return step.get("params") or {}
    return None


def iter_transfer_chunks(
    binary: bytes, seek_addr: int, write_size: int, block_size: int
):
    """Yield (block_sequence_number, offset, chunk_bytes) tuples covering
    binary[seek_addr : seek_addr + write_size], split into <= block_size
    pieces. block_sequence_number wraps 1..255 per ISO 14229-1. Pure/side
    effect free so it can be unit tested without any CAN transport."""
    end_offset = min(seek_addr + write_size, len(binary))
    seq_num = 1
    offset = seek_addr
    while offset < end_offset:
        chunk = binary[offset:min(offset + block_size, end_offset)]
        yield seq_num & 0xFF, offset, chunk
        offset += len(chunk)
        seq_num += 1


class OtaTesterDownloadManager:
    """Manage OTA Tester execution across an ordered list of cases."""

    def __init__(
        self,
        can_manager,
        isotp_send_fn: Callable[..., dict],
        isotp_receive_fn: Callable[..., bytes],
        seedkey_service=None,
    ):
        self._can = can_manager
        self._isotp_send = isotp_send_fn
        self._isotp_receive = isotp_receive_fn
        # Real vendor SeedKey DLL (see seedkey_client.py). None (or not
        # loaded) -> _execute_security_access() falls back to uds_core's
        # dummy generate_key().
        self._seedkey_service = seedkey_service

        self._lock = threading.RLock()
        self._state = _STATE_IDLE
        self._cases: list[dict] = []
        self._events: list[dict] = []
        self._error_message: Optional[str] = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._current_case_index = -1
        self._current_case: Optional[dict] = None
        self._current_step_index = -1
        self._download_block_size = 0x0C02
        # None = use the default below; an int overrides it for every step in
        # every case, set right before start() (mirrors uds_download_manager's
        # global_stmin_tx -- see main.py's ota_tester_start endpoint). Shared
        # with CAN-SWDL in the UI (frontend/src/widgets/UdsGlobalControls.tsx),
        # but each manager instance keeps its own copy of the value.
        self._global_stmin_tx: Optional[int] = None

        self._request_id = 0x18DA00F1
        self._response_id = 0x18DA00F1
        # Timing (ms), matching uds_download_manager's convention. Fixed
        # defaults, overridden in add_case() if a case's XML actually
        # defines a startCommunication config (see _extract_comm_timing) --
        # no real test-rule sample seen so far has one, but this makes the
        # values genuinely XML-derived whenever the data exists instead of
        # only ever using the hardcoded default.
        self._p2_can_server_max = 50.0
        self._p2_star_can_server_max = 5000.0

    # ---- Case management -----------------------------------------------

    @property
    def cases(self) -> list[dict]:
        with self._lock:
            return list(self._cases)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def _find_case(self, case_id: str) -> Optional[dict]:
        for c in self._cases:
            if c["id"] == case_id:
                return c
        return None

    def clear_cases(self) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("실행 중에는 케이스 목록을 초기화할 수 없습니다")
            self._cases = []
            self._state = _STATE_IDLE
            self._events = []
            self._error_message = None
            self._current_case_index = -1
            self._current_case = None
            self._current_step_index = -1
        return self.status()

    def add_case(
        self,
        case_id: str,
        label: str,
        kind: str,
        xml_path: str,
        order: int,
        enabled: bool = True,
    ) -> dict:
        """Parse one test-rule XML file and add/replace it as a case."""
        if parse_test_rule_xml is None:
            raise RuntimeError("XML 파서를 사용할 수 없습니다")
        steps = parse_test_rule_xml(xml_path)
        binary_hint = steps[0].get("binary_path") if steps else None

        comm_cfg = _extract_comm_timing(steps)
        if comm_cfg:
            p2 = _to_int(comm_cfg.get("p2CanServerMax"), int(self._p2_can_server_max))
            p2_star = _to_int(comm_cfg.get("p2StarCanServerMax"), int(self._p2_star_can_server_max))
            with self._lock:
                self._p2_can_server_max = float(p2)
                self._p2_star_can_server_max = float(p2_star)
            self._log(
                level="INFO",
                msg=f"통신 타이밍 설정 로드 ({label}): P2={self._p2_can_server_max:.0f}ms, "
                    f"P2*={self._p2_star_can_server_max:.0f}ms",
            )

        case = {
            "id": case_id,
            "label": label,
            "kind": kind,
            "order": order,
            "steps": steps,
            "binary_data": None,
            "binary_filename": None,
            "binary_path_hint": binary_hint or None,
            "enabled": enabled,
            # None = every step runs (default); [] = none; [i, ...] = only those
            # step indices -- same convention as uds_download_manager's
            # selected_steps, set via set_case_selected_steps().
            "selected_steps": None,
        }
        with self._lock:
            self._cases = [c for c in self._cases if c["id"] != case_id]
            self._cases.append(case)
            self._cases.sort(key=lambda c: c["order"])
            if self._state not in (_STATE_RUNNING,):
                self._state = _STATE_READY
            self._error_message = None
        self._log(level="INFO", msg=f"케이스 로드: {label} ({kind}), step {len(steps)}개")
        return self.status()

    def set_case_binary(self, case_id: str, path: str) -> dict:
        case = self._find_case(case_id)
        if case is None:
            raise RuntimeError(f"케이스를 찾을 수 없습니다: {case_id}")
        with open(path, "rb") as f:
            data = f.read()
        with self._lock:
            case["binary_data"] = data
            case["binary_filename"] = os.path.basename(path)
        self._log(level="INFO", msg=f"바이너리 로드: {os.path.basename(path)} ({len(data)} bytes) -> {case['label']}")
        return self.status()

    def set_case_enabled(self, case_id: str, enabled: bool) -> dict:
        case = self._find_case(case_id)
        if case is None:
            raise RuntimeError(f"케이스를 찾을 수 없습니다: {case_id}")
        with self._lock:
            case["enabled"] = enabled
        return self.status()

    def set_all_enabled(self, enabled: bool) -> dict:
        with self._lock:
            for c in self._cases:
                c["enabled"] = enabled
        return self.status()

    def get_case_steps(self, case_id: str) -> list[dict]:
        """Return the parsed steps for one case, for the per-command
        checklist UI (mirrors uds_download_manager.get_procedure_steps)."""
        case = self._find_case(case_id)
        if case is None:
            raise RuntimeError(f"케이스를 찾을 수 없습니다: {case_id}")
        result = []
        for s in case["steps"]:
            pdu_preview, pdu_note = self._preview_pdu(case, s)
            result.append({
                "service": s.get("service", "?"),
                "params": dict(s.get("params", {})),
                "sub_steps": [
                    {"service": sub.get("service", "?"), "params": dict(sub.get("params", {}))}
                    for sub in s.get("sub_steps", [])
                ],
                "confirm_positive_response": s.get("confirm_positive_response", True),
                "pdu_preview": pdu_preview,
                "pdu_note": pdu_note,
            })
        return result

    def _preview_pdu(self, case: dict, step: dict) -> tuple[Optional[str], Optional[str]]:
        """Best-effort PDU bytes for the checklist UI, built with the exact
        same param parsing as the real execution path -- so what's shown is
        guaranteed to match what will actually be sent for statically-known
        services. securityAccess and transferData depend on runtime state
        (a seed the ECU hasn't issued yet / the loaded binary's bytes), so
        those get an explanatory note instead of (or alongside) a preview."""
        service = step.get("service", "?")
        params = step.get("params", {})
        sub_steps = step.get("sub_steps", [])
        try:
            if service == "securityAccess":
                pdu = build_security_access_request_seed()
                return bytes(pdu).hex(" ").upper(), "Seed 요청 → 키 생성 → SendKey (실제 키는 실행 시 결정됨)"

            if service == "requestDownload":
                dfi = _to_int(params.get("dataFormatIdentifier", "0x00"))
                alfi = _to_int(params.get("addressAndLengthFormatIdentifier", "0x44"), 0x44)
                mem_addr = _to_int(params.get("memoryAddress", "0x00000000"))
                mem_size = _to_int(params.get("memorySize", "0x00000000"))
                pdu = build_request_download(dfi, alfi, mem_addr, mem_size)
                return bytes(pdu).hex(" ").upper(), None

            if service == "transferData":
                seek_addr = _to_int(params.get("seekAddress", "0x0200"), 0x0200)
                write_size = _to_int(params.get("writeSize", "0"))
                block_size = _to_int(params.get("maxNumberOfBlockLength", "0x0C02"), 0x0C02)
                binary = case.get("binary_data")
                if not binary or write_size == 0:
                    return None, "바이너리 미로드 -- 실행 시 로드된 BIN 파일에서 청크 전송"
                first_len = min(block_size, write_size, len(binary) - seek_addr) if seek_addr < len(binary) else 0
                first_chunk = binary[seek_addr:seek_addr + first_len] if first_len > 0 else b""
                # A single block can be thousands of bytes (maxNumberOfBlockLength) --
                # showing it in full would make the checklist unusable, so the
                # preview is truncated to a handful of leading bytes.
                PREVIEW_BYTES = 12
                shown = first_chunk[:PREVIEW_BYTES]
                pdu_hex = bytes(build_transfer_data(1, shown)).hex(" ").upper()
                if len(first_chunk) > PREVIEW_BYTES:
                    pdu_hex += " ..."
                total_blocks = (write_size + block_size - 1) // block_size
                note = (
                    f"seekAddress=0x{seek_addr:X}, 블록당 {block_size} bytes, "
                    f"총 {total_blocks}개 블록 전송 (첫 블록의 앞 {len(shown)} bytes만 표시)"
                )
                return pdu_hex, note

            if service == "requestTransferExit":
                return bytes(build_transfer_exit()).hex(" ").upper(), None

            pdu = self._build_pdu(service, params, sub_steps)
            if pdu is None:
                return None, None
            return bytes(pdu).hex(" ").upper(), None
        except Exception as exc:
            return None, f"미리보기 생성 실패: {exc}"

    def set_case_selected_steps(self, case_id: str, selected_steps: Optional[list[int]]) -> dict:
        """selected_steps: None = all steps run (default); [] = none;
        [i, ...] = only those step indices within this case."""
        case = self._find_case(case_id)
        if case is None:
            raise RuntimeError(f"케이스를 찾을 수 없습니다: {case_id}")
        with self._lock:
            case["selected_steps"] = list(selected_steps) if selected_steps is not None else None
        return self.status()

    # ---- Public run API ---------------------------------------------------

    def start(self, request_id: int, response_id: int) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("이미 실행 중입니다")
            if not self._cases:
                raise RuntimeError("로드된 케이스가 없습니다")
            if self._can.notifier is None:
                raise RuntimeError("CAN 버스가 연결되어 있지 않습니다")
            self._request_id = request_id
            self._response_id = response_id
            self._events = []
            self._error_message = None
            self._running = True
            self._state = _STATE_RUNNING
            self._current_case_index = -1
            self._current_case = None
            self._current_step_index = -1

        self._stop_event.clear()
        self._thread = threading.Thread(name="ota_tester", target=self._run_all_cases, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            self._running = False
            if self._state not in (_STATE_COMPLETED, _STATE_ERROR):
                self._state = _STATE_IDLE
        return self.status()

    def status(self) -> dict:
        return self.get_status_dict()

    def get_status_dict(self) -> dict:
        with self._lock:
            cases_info = [
                {
                    "id": c["id"],
                    "label": c["label"],
                    "kind": c["kind"],
                    "order": c["order"],
                    "enabled": c["enabled"],
                    "total_steps": len(c["steps"]),
                    "binary_loaded": c["binary_data"] is not None,
                    "binary_filename": c["binary_filename"],
                    "binary_path_hint": c.get("binary_path_hint"),
                    "selected_steps": c.get("selected_steps"),
                }
                for c in self._cases
            ]
            return {
                "state": self._state,
                "running": self._running,
                "cases": cases_info,
                "total_cases": len(self._cases),
                "current_case_index": self._current_case_index,
                "current_case_label": self._current_case["label"] if self._current_case else None,
                "current_step_index": self._current_step_index,
                "total_steps_in_case": len(self._current_case["steps"]) if self._current_case else 0,
                "events": self.events,
                "error": self._error_message,
            }

    @property
    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events[-MAX_EVENTS:])

    # ---- Internal: logging ------------------------------------------------

    def _log(self, *, level: str, msg: str, service: Optional[str] = None) -> None:
        entry: dict[str, Any] = {"ts": time.time(), "level": level, "msg": msg}
        if service:
            entry["service"] = service
        with self._lock:
            self._events.append(entry)
            if len(self._events) > MAX_EVENTS:
                del self._events[: len(self._events) - MAX_EVENTS]

    # ---- Internal: run loop ------------------------------------------------

    def _run_all_cases(self) -> None:
        try:
            with self._lock:
                enabled_cases = [c for c in self._cases if c["enabled"]]
            self._log(
                level="INFO",
                msg=f"========== OTA Tester 시작: {len(enabled_cases)}/{len(self._cases)} 케이스 ==========",
            )
            all_ok = True
            for case_idx, case in enumerate(enabled_cases):
                if self._stop_event.is_set():
                    self._log(level="INFO", msg="사용자에 의해 중단됨")
                    all_ok = False
                    break
                with self._lock:
                    self._current_case_index = case_idx
                    self._current_case = case
                    self._current_step_index = -1
                    self._download_block_size = 0x0C02
                self._log(
                    level="INFO",
                    msg=f"--- 케이스 [{case_idx + 1}/{len(enabled_cases)}] {case['label']} ({case['kind']}) ---",
                )
                if not self._run_case_steps(case):
                    all_ok = False
                    break

            with self._lock:
                if self._stop_event.is_set():
                    self._state = _STATE_IDLE
                elif all_ok:
                    self._state = _STATE_COMPLETED
                    self._log(level="INFO", msg="===== OTA Tester 완료 =====")
                else:
                    self._state = _STATE_ERROR
        except Exception as exc:
            logger.exception("OTA Tester 실행 실패")
            with self._lock:
                self._error_message = str(exc)
                self._state = _STATE_ERROR
            self._log(level="ERROR", msg=f"실행 실패: {exc}")
        finally:
            with self._lock:
                self._running = False

    def _run_case_steps(self, case: dict) -> bool:
        steps = case["steps"]
        selected_steps = case.get("selected_steps")  # None = all
        for idx, step in enumerate(steps):
            if self._stop_event.is_set():
                self._log(level="INFO", msg="사용자에 의해 중단됨")
                return False
            with self._lock:
                self._current_step_index = idx

            service = step.get("service", "?")

            if selected_steps is not None and idx not in selected_steps:
                self._log(level="INFO", msg=f"Step [{idx + 1}/{len(steps)}] {service} 건너뜀 (선택 안 됨)", service=service)
                continue

            params = step.get("params", {})
            sub_steps = step.get("sub_steps", [])
            confirm_pos_rsp = step.get("confirm_positive_response", True)

            self._log(level="INFO", msg=f"Step [{idx + 1}/{len(steps)}] {service}", service=service)

            ok = self._execute_step(case, service, params, sub_steps, confirm_pos_rsp)
            if not ok:
                with self._lock:
                    self._error_message = f"{case['label']}: {service} 실패"
                return False

            self._step_delay(step)
        return True

    def _step_delay(self, step: dict) -> None:
        dly = float(step.get("params", {}).get("endDelay", "0") or 0)
        if dly > 0:
            self._log(level="INFO", msg=f"Waiting {dly}ms (end delay)")
            self._stop_event.wait(timeout=dly / 1000.0)

    # ---- Internal: UDS transport -------------------------------------------

    def _is_extended(self) -> bool:
        return self._request_id > 0x7FF or self._response_id > 0x7FF

    def _get_fc_stmin(self) -> int:
        """Flow Control STmin: the shared global override if set (see
        ota_tester_start in main.py), else the default 0x0A."""
        if self._global_stmin_tx is not None:
            return self._global_stmin_tx
        return 0x0A

    def _uds_request_with_retry(
        self, request: bytes, timeout_s: float, label: str = "",
        max_retries: int = 3, retry_delay_s: float = 0.1,
    ) -> dict:
        """Send a UDS request once and receive the response, waiting again
        (without retransmitting) on NRC 0x78 (ResponsePending) -- per ISO
        14229-1 the client must NOT resend the request while the server is
        still processing it, it must just keep waiting for the eventual
        final response. Each post-0x78 wait uses the extended
        P2*Server_max timeout (`_p2_star_can_server_max`) instead of the
        original, usually much shorter, P2 timeout that was only meant to
        bound the FIRST reply. Returns the parsed positive response dict;
        raises UdsError on any other negative response, or once
        max_retries consecutive 0x78s are exceeded."""
        is_ext = self._is_extended()
        req_hex = request.hex(" ").upper() if isinstance(request, (bytes, bytearray)) else str(request)
        self._log(level="INFO", service="CAN_TX", msg=f"Tx CAN_ID=0x{self._request_id:03X} DATA=[{req_hex}] ({label})")
        self._isotp_send(
            self._can, self._request_id, self._response_id, request,
            is_extended_id=is_ext, fc_timeout_s=timeout_s,
        )
        pending_timeout_s = max(self._p2_star_can_server_max / 1000.0, timeout_s)
        for attempt in range(max_retries + 1):
            response = self._isotp_receive(
                self._can, self._response_id, self._request_id,
                timeout_s=timeout_s if attempt == 0 else pending_timeout_s,
                is_extended_id=is_ext, fc_stmin=self._get_fc_stmin(),
            )
            result = parse_response(response)
            if result["positive"]:
                return result
            if result["nrc"] == 0x78 and attempt < max_retries:
                self._log(level="WARN", msg=f"NRC 0x78 (ResponsePending) 대기 {attempt + 1}/{max_retries} (P2*={pending_timeout_s:.1f}s)")
                time.sleep(retry_delay_s)
                continue
            raise UdsError(f"UDS Negative Response ({label}): NRC=0x{result['nrc']:02X}", nrc=result["nrc"])
        raise UdsError(f"No response ({label})")

    # ---- Internal: single-step execution -----------------------------------

    def _execute_step(
        self, case: dict, service: str, params: dict, sub_steps: list[dict], confirm_pos_rsp: bool,
    ) -> bool:
        """Execute a single step. Returns True on success (including an
        expected negative response when confirm_pos_rsp is False)."""
        try:
            if service == "securityAccess":
                self._execute_security_access()
            elif service == "requestDownload":
                self._execute_request_download(params)
            elif service == "transferData":
                self._execute_transfer_data(case, params)
            elif service == "requestTransferExit":
                self._execute_transfer_exit()
            else:
                pdu = self._build_pdu(service, params, sub_steps)
                if pdu is None:
                    self._log(level="INFO", msg=f"Skipping {service} (no TX PDU)", service=service)
                    return True
                timeout_s = self._step_timeout(service)
                self._uds_request_with_retry(pdu, timeout_s, service)
            self._log(level="INFO", msg=f"Step {service} OK", service=service)
            return True
        except UdsError as exc:
            if not confirm_pos_rsp:
                self._log(level="INFO", msg=f"Step {service} 음성 응답 (예상됨): {exc}", service=service)
                return True
            self._log(level="ERROR", msg=f"Step {service} failed (POS response expected): {exc}", service=service)
            return False

    def _step_timeout(self, service: str) -> float:
        """RoutineControl / EcuReset can involve longer ECU-side processing
        (P2*); everything else uses the short P2 timeout, matching
        uds_download_manager's convention."""
        if service in ("routineControl", "ecuReset"):
            return max(self._p2_star_can_server_max / 1000.0, 2.0)
        return self._p2_can_server_max / 1000.0

    def _build_pdu(self, service: str, params: dict, sub_steps: list[dict]) -> Optional[bytearray]:
        """Dispatch to the appropriate builder for the "simple" services
        (securityAccess/requestDownload/transferData/requestTransferExit have
        their own dedicated _execute_* methods, called before this)."""
        if service == "diagnosticSessionControl":
            session_typ = params.get("diagnosticSessionType", "0x01")
            return build_diagnostic_session(_to_int(session_typ, 0x01))

        if service == "ecuReset":
            reset_mode = _to_int(params.get("resetMode", "0x01"), 0x01)
            return build_ecu_reset(reset_mode)

        if service == "readDataByIdentifier":
            did = _to_int(params.get("dataIdentifier", "0xF187"), 0xF187)
            return build_read_data_by_id(did)

        if service == "communicationControl":
            ctrl_type = _to_int(params.get("controlType", "0"))
            comm_type = _to_int(params.get("communicationType", "0"))
            return build_communication_control(ctrl_type, comm_type)

        if service == "routineControl":
            rc_type = _to_int(params.get("routineControlType", "0x01"), 0x01)
            rid = _to_int(params.get("routineIdentifier", "0x0000"))
            option_str = params.get("routineControlOptionRecord", "") or ""
            option_record = bytes.fromhex(option_str.replace("0x", "")) if option_str else b""
            return build_routine_control(rc_type, rid, option_record)

        if service == "testerPresent":
            suppress = params.get("subFunction", "") == "0x80"
            return build_tester_present(suppress)

        if service == "controlDTCSetting":
            dtc_type = _to_int(params.get("dtcSettingType", "0"))
            return build_control_dtc_setting(dtc_type)

        return None

    def _execute_security_access(self) -> None:
        """RequestSeed -> generate key (SeedKey DLL or dummy) -> SendKey.

        The test-rule XML schema doesn't enumerate requestSeed/sendKey as
        separate sub-steps (unlike CAN-SWDL's schema) -- a single
        <xfrm:securityAccess type="ask"> element implies the full handshake,
        so both UDS round-trips happen here as one logical step."""
        timeout_s = self._p2_can_server_max / 1000.0

        request = build_security_access_request_seed()
        result = self._uds_request_with_retry(request, timeout_s, "RequestSeed")

        # Positive response layout is SID(1) | securityAccessType(1) | seed(N)
        seed = bytes(result["data"][2:])
        self._log(level="INFO", msg=f"Seed 수신: {seed.hex()} ({len(seed)} bytes)")

        if self._seedkey_service is not None and getattr(self._seedkey_service, "loaded", False):
            try:
                key = self._seedkey_service.generate_key(seed)
            except Exception as exc:
                raise UdsError(f"SeedKey 키 생성 실패: {exc}")
            self._log(level="INFO", msg=f"키 생성 완료 (SeedKey DLL): {key.hex()}")
        else:
            key = generate_key(seed)
            self._log(level="WARN", msg=f"[더미 키] SeedKey DLL이 로드되지 않아 더미 키를 사용합니다: {key.hex()}")

        request = build_security_access_send_key(key)
        self._uds_request_with_retry(request, timeout_s, "SendKey")

    def _execute_request_download(self, params: dict) -> None:
        dfi = _to_int(params.get("dataFormatIdentifier", "0x00"))
        alfi = _to_int(params.get("addressAndLengthFormatIdentifier", "0x44"), 0x44)
        mem_addr = _to_int(params.get("memoryAddress", "0x00000000"))
        mem_size = _to_int(params.get("memorySize", "0x00000000"))

        request = build_request_download(dfi, alfi, mem_addr, mem_size)
        timeout_s = max(self._p2_star_can_server_max / 1000.0, 2.0)

        result = self._uds_request_with_retry(
            request, timeout_s,
            f"RequestDownload(addr=0x{mem_addr:08X}, size=0x{mem_size:08X})",
            max_retries=10, retry_delay_s=0.5,
        )
        download_info = parse_request_download_response(result["data"])
        max_block_length = download_info.get("max_length", 0)
        with self._lock:
            self._ecu_max_block_length = max_block_length
        self._log(level="INFO", msg=f"RequestDownload 성공: ECU maxBlockLength={max_block_length}")

    def _execute_transfer_data(self, case: dict, params: dict) -> None:
        """Send binary[seekAddress : seekAddress+writeSize] in blocks."""
        binary = case.get("binary_data")
        if binary is None:
            raise UdsError(f"{case['label']}: 바이너리 파일이 로드되지 않았습니다")

        seek_addr = _to_int(params.get("seekAddress", "0x0200"), 0x0200)
        write_size = _to_int(params.get("writeSize", "0"))
        xml_block_size = _to_int(params.get("maxNumberOfBlockLength", "0x0C02"), 0x0C02)

        ecu_max = getattr(self, "_ecu_max_block_length", 0)
        block_size = min(ecu_max, xml_block_size) if ecu_max else xml_block_size
        block_size = min(block_size, 4095)

        if seek_addr >= len(binary):
            raise UdsError(f"seekAddress(0x{seek_addr:X})가 BIN 파일 크기({len(binary)} bytes)를 초과")
        if write_size == 0:
            self._log(level="INFO", msg="TransferData 건너뜀: writeSize=0")
            return

        end_offset = min(seek_addr + write_size, len(binary))
        if seek_addr + write_size > len(binary):
            self._log(
                level="WARN",
                msg=f"seekAddress(0x{seek_addr:X}) + writeSize(0x{write_size:X})가 BIN 파일 크기를 초과하여 endOffset을 {len(binary)}로 제한",
            )

        total_size = end_offset - seek_addr
        timeout_s = max(self._p2_star_can_server_max / 1000.0, 2.0)

        self._log(
            level="INFO",
            msg=f"TransferData 시작: seekAddress=0x{seek_addr:X}, writeSize=0x{write_size:X}, "
                f"endOffset=0x{end_offset:X}, blockSize={block_size}",
        )

        last_seq = 0
        for seq_num, offset, chunk in iter_transfer_chunks(binary, seek_addr, write_size, block_size):
            if self._stop_event.is_set():
                raise UdsError("사용자에 의해 전송 중단됨")
            request = build_transfer_data(seq_num, chunk)
            self._uds_request_with_retry(
                request, timeout_s,
                f"TransferData(seq={seq_num}, offset=0x{offset:06X}, size={len(chunk)})",
                max_retries=10, retry_delay_s=0.5,
            )
            last_seq = seq_num
            bytes_sent = offset + len(chunk) - seek_addr
            pct = min(100.0, (bytes_sent / total_size) * 100.0)
            if seq_num % 10 == 0:
                self._log(level="INFO", msg=f"전송 진도: 0x{offset + len(chunk):X}/0x{end_offset:X} bytes ({pct:.1f}%)")

        self._log(level="INFO", msg=f"TransferData 완료: {last_seq} blocks, {total_size} bytes (0x{seek_addr:X} -> 0x{end_offset:X})")

    def _execute_transfer_exit(self) -> None:
        request = build_transfer_exit()
        timeout_s = max(self._p2_star_can_server_max / 1000.0, 2.0)
        self._uds_request_with_retry(request, timeout_s, "RequestTransferExit", max_retries=10, retry_delay_s=0.5)
