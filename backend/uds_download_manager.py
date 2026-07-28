"""UDS Software Download Manager.

Orchestrates the complete software download sequence over CAN bus by
interpreting an XML procedure definition and driving the UDS services
via ISO-TP transport.

State machine:
  IDLE → LOADING → READY → RUNNING_PREP → RUNNING_DOWNLOAD → RUNNING_ACTIVATE
                                                              → COMPLETED → IDLE
                                                              → ERROR → IDLE
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from uds_core import (
    UdsError,
    SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED,
    ROUTINE_START, ROUTINE_STOP, ROUTINE_REQUEST_RESULTS,
    RESET_HARD, RESET_KEY_OFF_ON, RESET_SOFT,
    SECURITY_REQUEST_SEED, SECURITY_SEND_KEY,
    build_session_control,
    build_ecu_reset,
    build_seed_request,
    build_send_key,
    build_routine_control,
    build_request_download,
    build_transfer_data,
    build_transfer_exit,
    parse_response,
    parse_request_download_response,
    generate_key,
)
from uds_xml_parser import parse_xml, UdsProcedure, UdsStep, UdsRule, find_step

logger = logging.getLogger(__name__)

MAX_EVENTS = 500

# States
STATE_IDLE = "IDLE"
STATE_LOADING = "LOADING"
STATE_READY = "READY"
STATE_RUNNING_PREP = "RUNNING_PREP"
STATE_RUNNING_DOWNLOAD = "RUNNING_DOWNLOAD"
STATE_RUNNING_ACTIVATE = "RUNNING_ACTIVATE"
STATE_COMPLETED = "COMPLETED"
STATE_ERROR = "ERROR"


class UdsDownloadManager:
    """Manages UDS software download from XML procedure + BIN file."""

    def __init__(self, can_manager, isotp_send_fn, isotp_receive_fn):
        self._can = can_manager
        self._isotp_send = isotp_send_fn
        self._isotp_receive = isotp_receive_fn

        self._lock = threading.RLock()
        self._state = STATE_IDLE
        self._global_stmin_tx: Optional[int] = None  # None = use XML value, int = override
        self._procedure: Optional[UdsProcedure] = None
        self._binary_data: Optional[bytes] = None
        self._binary_path: Optional[str] = None
        self._xml_path: Optional[str] = None
        self._events: list[dict] = []
        self._progress: dict[str, Any] = {
            "current_step": "",
            "total_blocks": 0,
            "current_block": 0,
            "percent": 0.0,
            "phase": "",
        }
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._error_message: Optional[str] = None

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
    def procedure(self) -> Optional[UdsProcedure]:
        with self._lock:
            return self._procedure

    @property
    def binary_data(self) -> Optional[bytes]:
        with self._lock:
            return self._binary_data

    @property
    def xml_path(self) -> Optional[str]:
        with self._lock:
            return self._xml_path

    @property
    def binary_path(self) -> Optional[str]:
        with self._lock:
            return self._binary_path

    def load_xml(self, path: str) -> dict:
        """Load and parse an XML procedure file."""
        with self._lock:
            self._state = STATE_LOADING
        try:
            proc = parse_xml(path)
            with self._lock:
                self._procedure = proc
                self._xml_path = path
                self._state = STATE_READY
                self._events = []
                self._error_message = None

            self._log(
                level="INFO", msg=f"XML 프로시저 로드 완료: {os.path.basename(path)}"
            )
            self._log(
                level="INFO",
                msg=f"CAN 통신: TX=0x{proc.request_id:X}, RX=0x{proc.response_id:X}, P6={proc.p6_time}ms",
            )
            self._log(level="INFO", msg=f"ROM 정보: {proc.rom_info}")

            self._log(level="INFO", msg="=== Processing Rule (다운로드) ===")
            for phase_name, phase_steps in [
                ("준비", proc.processing_rule.preparation),
                ("다운로드", proc.processing_rule.unit),
                ("완료", proc.processing_rule.complete),
            ]:
                if phase_steps:
                    steps_desc = " → ".join(s.service for s in phase_steps)
                    self._log(level="INFO", msg=f"  [{phase_name}] {steps_desc}")
                    for s in phase_steps:
                        param_str = ", ".join(
                            f"{k}={v}" for k, v in s.params.items()
                        )
                        if param_str:
                            self._log(
                                level="INFO",
                                msg=f"    - {s.service}: {param_str}",
                            )
                        if s.sub_steps:
                            for sub in s.sub_steps:
                                sub_param_str = ", ".join(
                                    f"{k}={v}" for k, v in sub.params.items()
                                )
                                self._log(
                                    level="INFO",
                                    msg=f"      └ {sub.service}: {sub_param_str}",
                                )

            if proc.activate_rule.preparation or proc.activate_rule.unit or proc.activate_rule.complete:
                self._log(level="INFO", msg="=== Activate Rule (활성화) ===")
                for phase_name, phase_steps in [
                    ("준비", proc.activate_rule.preparation),
                    ("실행", proc.activate_rule.unit),
                    ("완료", proc.activate_rule.complete),
                ]:
                    if phase_steps:
                        steps_desc = " → ".join(s.service for s in phase_steps)
                        self._log(level="INFO", msg=f"  [{phase_name}] {steps_desc}")
                        for s in phase_steps:
                            param_str = ", ".join(
                                f"{k}={v}" for k, v in s.params.items()
                            )
                            if param_str:
                                self._log(
                                    level="INFO",
                                    msg=f"    - {s.service}: {param_str}",
                                )

            return self.status()
        except Exception as exc:
            with self._lock:
                self._state = STATE_ERROR
                self._error_message = str(exc)
            raise

    def load_binary(self, path: str) -> dict:
        """Load a BIN file for download."""
        try:
            with open(path, "rb") as f:
                data = f.read()
            with self._lock:
                self._binary_data = data
                self._binary_path = path
            return {"loaded": True, "size": len(data), "path": path}
        except Exception as exc:
            raise RuntimeError(f"BIN 파일 로드 실패: {exc}")

    def start(self, selected_steps: Optional[list[str]] = None, modified_params: Optional[dict[str, dict]] = None) -> dict:
        """Start the download procedure in a background thread.

        Args:
            selected_steps: list of step service names to execute (empty/null = all)
            modified_params: dict of step_name -> {param_key: new_value} overrides
        """
        with self._lock:
            if self._state not in (STATE_READY, STATE_COMPLETED, STATE_ERROR):
                raise RuntimeError(f"현재 상태에서 시작할 수 없습니다: {self._state}")
            if self._procedure is None:
                raise RuntimeError("XML 프로시저가 로드되지 않았습니다")
            if self._binary_data is None:
                raise RuntimeError("BIN 파일이 로드되지 않았습니다")
            if self._running:
                raise RuntimeError("이미 실행 중입니다")
            if self._can.notifier is None:
                raise RuntimeError("CAN 버스가 연결되어 있지 않습니다")

            self._events = []
            self._progress = {"current_step": "", "total_blocks": 0, "current_block": 0, "percent": 0.0, "phase": ""}
            self._error_message = None
            self._running = True
            self._selected_steps = selected_steps or []
            self._modified_params = modified_params or {}

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        """Request graceful stop of the download procedure."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                self._log(level="WARN", msg="다운로드 스레드가 5초 내에 종료되지 않았습니다. 강제 종료 중...")
        with self._lock:
            self._running = False
            if self._state not in (STATE_COMPLETED, STATE_ERROR):
                self._state = STATE_IDLE
        return self.status()

    def status(self) -> dict:
        """Full status snapshot."""
        with self._lock:
            return {
                "state": self._state,
                "running": self._running,
                "procedure_loaded": self._procedure is not None,
                "xml_filename": os.path.basename(self._xml_path) if self._xml_path else None,
                "binary_loaded": self._binary_data is not None,
                "binary_filename": os.path.basename(self._binary_path) if self._binary_path else None,
                "binary_size": len(self._binary_data) if self._binary_data else 0,
                "progress": dict(self._progress),
                "events": list(self._events),
                "error": self._error_message,
                "comm_info": {
                    "request_id": self._procedure.request_id if self._procedure else 0,
                    "response_id": self._procedure.response_id if self._procedure else 0,
                } if self._procedure else None,
            }

    def get_procedure_steps(self) -> Optional[list[dict]]:
        """Get parsed procedure steps for UI rendering."""
        proc = self._procedure
        if proc is None:
            return None

        steps: list[dict] = []
        for rule_name, rule in [
            ("processing", proc.processing_rule),
            ("activate", proc.activate_rule),
        ]:
            for phase_name, phase_steps in [
                ("preparation", rule.preparation),
                ("unit", rule.unit),
                ("complete", rule.complete),
            ]:
                for s in phase_steps:
                    # Include sub-steps as well
                    sub_steps_list = [
                        {"service": sub.service, "params": dict(sub.params)}
                        for sub in s.sub_steps
                    ] if s.sub_steps else []
                    steps.append({
                        "rule": rule_name,
                        "phase": phase_name,
                        "service": s.service,
                        "params": dict(s.params),
                        "sub_steps": sub_steps_list,
                    })

        return steps

    def set_param(self, step_service: str, param_key: str, param_value: str) -> None:
        """Update a single parameter value for a step."""
        proc = self._procedure
        if proc is None:
            raise RuntimeError("프로시저가 로드되지 않았습니다")

        modified_params = getattr(self, '_modified_params', {})
        if step_service not in modified_params:
            modified_params[step_service] = {}
        modified_params[step_service][param_key] = param_value
        self._modified_params = modified_params

    def _get_effective_params(self, step: UdsStep) -> dict:
        """Get effective parameters for a step, merging modified overrides."""
        base = dict(step.params)
        modified_params = getattr(self, '_modified_params', {})
        if step.service in modified_params:
            for k, v in modified_params[step.service].items():
                if v:  # non-empty override → 사용자가 수정한 값
                    base[k] = v
                # 빈 문자열이면 원래값 유지
        return base

    def _is_step_selected(self, step: UdsStep) -> bool:
        """Check if a step is selected (or if no selection -> all selected)."""
        selected_steps = getattr(self, '_selected_steps', [])
        if not selected_steps:
            return True  # All steps selected by default
        return step.service in selected_steps

    # ---- Internal: state management -----------------------------------------

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def _log(self, **fields: Any) -> None:
        entry = {"ts": time.time(), **fields}
        with self._lock:
            self._events.append(entry)
            if len(self._events) > MAX_EVENTS:
                del self._events[:len(self._events) - MAX_EVENTS]

    def _update_progress(self, **fields: Any) -> None:
        with self._lock:
            self._progress.update(fields)

    # ---- Internal: UDS transport --------------------------------------------

    def _get_fc_stmin(self) -> int:
        """Get the stmin value to use in Flow Control frames.
        
        Priority:
        1. global_stmin_tx override (if set)
        2. XML parsed stmin_tx value
        3. Default 0x00 (no delay)
        """
        global_override = getattr(self, '_global_stmin_tx', None)
        if global_override is not None:
            return global_override
        if self._procedure is not None:
            return self._procedure.stmin_tx
        return 0x00

    def _uds_request(self, request: bytes, timeout_s: float, label: str = "") -> dict:
        """Send a UDS request and receive the response."""
        proc = self._procedure
        if proc is None:
            raise RuntimeError("No procedure loaded")

        tx_id = proc.request_id
        rx_id = proc.response_id
        is_ext = (proc.request_id > 0x7FF) or (proc.response_id > 0x7FF)
        extended_id_attr = getattr(proc, "is_extended_id", None)
        if extended_id_attr is not None:
            is_ext = extended_id_attr
        fc_stmin = self._get_fc_stmin()

        try:
            self._isotp_send(
                tx_id, rx_id, request,
                is_extended_id=is_ext,
                fc_timeout_s=timeout_s,
            )
        except Exception as exc:
            raise UdsError(f"ISO-TP 송신 실패 ({label}): {exc}")

        try:
            response = self._isotp_receive(
                rx_id, tx_id,
                timeout_s=timeout_s,
                is_extended_id=is_ext,
                fc_stmin=fc_stmin,
            )
        except Exception as exc:
            raise UdsError(f"ISO-TP 수신 실패 ({label}): {exc}")

        result = parse_response(response)
        if not result["positive"]:
            raise UdsError(
                f"UDS Negative Response ({label}): SID=0x{result['sid']:02X}, NRC=0x{result['nrc']:02X}",
                nrc=result["nrc"],
            )
        return result

    def _uds_request_with_retry(
        self, request: bytes, timeout_s: float, label: str = "",
        max_retries: int = 3, retry_delay_s: float = 0.1,
    ) -> dict:
        """Send UDS request with NRC 0x78 (ResponsePending) retry logic."""
        for attempt in range(max_retries + 1):
            try:
                return self._uds_request(request, timeout_s, f"{label} (시도 {attempt + 1})")
            except UdsError as exc:
                if exc.nrc == 0x78 and attempt < max_retries:
                    self._log(level="WARN", msg=f"NRC 0x78 (ResponsePending) 재시도 {attempt + 1}/{max_retries}")
                    time.sleep(retry_delay_s)
                    continue
                raise

    # ---- Internal: Main execution loop ---------------------------------------

    def _run(self) -> None:
        try:
            self._run_procedure()
        except Exception as exc:
            logger.exception("UDS download failed")
            self._log(level="ERROR", msg=f"다운로드 실패: {exc}")
            self._error_message = str(exc)
            self._set_state(STATE_ERROR)
            try:
                self._run_error_recovery()
            except Exception:
                logger.exception("Error recovery also failed")
        finally:
            with self._lock:
                self._running = False

    def _run_procedure(self) -> None:
        proc = self._procedure
        if proc is None:
            raise RuntimeError("No procedure loaded")

        self._log(msg="=== 다운로드 시작 ===")
        self._log(msg=f"XML: {self._xml_path}, BIN: {self._binary_path} ({len(self._binary_data)} bytes)")

        # ---- Rule Preparation ----
        self._set_state(STATE_RUNNING_PREP)
        self._update_progress(phase="Preparation", current_step="Preparation phase")
        self._log(msg="--- Preparation Phase ---")
        self._run_steps(proc.processing_rule.preparation, "preparation")

        # ---- Rule Unit (Download) ----
        self._set_state(STATE_RUNNING_DOWNLOAD)
        self._update_progress(phase="Download", current_step="Download phase")
        self._log(msg="--- Download (Unit) Phase ---")
        self._run_steps(proc.processing_rule.unit, "unit")

        # ---- Rule Complete ----
        self._set_state(STATE_RUNNING_ACTIVATE)
        self._update_progress(phase="Complete", current_step="Complete phase")
        self._log(msg="--- Complete Phase ---")
        self._run_steps(proc.processing_rule.complete, "complete")

        # ---- Activate Rule ----
        self._log(msg="--- Activate Phase ---")
        self._run_steps(proc.activate_rule.preparation, "activate_prep")
        self._run_steps(proc.activate_rule.unit, "activate_unit")
        self._run_steps(proc.activate_rule.complete, "activate_complete")

        self._set_state(STATE_COMPLETED)
        self._update_progress(current_step="Complete", percent=100.0)
        self._log(msg="=== 다운로드 완료 ===")

    def _run_steps(self, steps: list[UdsStep], phase_name: str) -> None:
        """Execute a list of UDS steps, skipping unselected ones."""
        for step in steps:
            if self._stop_event.is_set():
                raise RuntimeError("사용자에 의해 중단됨")
            if not self._is_step_selected(step):
                self._log(level="INFO", msg=f"스킵 (선택 안됨): {step.service}")
                continue
            self._execute_step(step, phase_name)

    def _execute_step(self, step: UdsStep, phase_name: str) -> None:
        """Execute a single UDS step."""
        params = self._get_effective_params(step)
        svc = step.service
        self._update_progress(current_step=f"{svc} ({phase_name})")
        self._log(service=svc, msg=f"실행: {svc} {params}")

        if svc == "startCommunication":
            self._log(msg=f"CAN 통신 초기화 (STmin=0x{self._procedure.stmin_tx:02X}, "
                          f"P2={self._procedure.p2_can_server_max}ms)")

        elif svc == "stopCommunication":
            self._log(msg="CAN 통신 종료")

        elif svc == "diagnosticSessionControl":
            # Determine which session type(s) to send.
            # The frontend may pass _selectedSessionType_* override to choose
            # between diagnosticSessionType and background_diagnosticSessionType
            # when both are present in the XML.
            step_params = self._get_effective_params(step)

            # Collect all keys matching _sessionType_*
            selected_type_keys = {k for k in step_params if k.startswith("_sessionType_")}
            selected_session_type: Optional[str] = None
            if selected_type_keys:
                # Take the first match; the user selected which param to use
                selected_k = next(iter(selected_type_keys))
                selected_session_type = step_params.get(selected_k)

            # Session type params live on the parent step, not on sub-steps
            session_type_str = step_params.get("diagnosticSessionType", "0x01")
            bg_session_type_str = step_params.get("background_diagnosticSessionType", None)
            timeout_s = self._procedure.p2_can_server_max / 1000.0 if self._procedure else 0.05

            if selected_session_type is not None:
                # User selected a specific session type — only send that one
                chosen_val = step_params.get(selected_session_type, "")
                if chosen_val:
                    stype = int(chosen_val, 16) if isinstance(chosen_val, str) else chosen_val
                    request = build_session_control(stype)
                    label = f"SessionControl(0x{stype:02X})"
                    self._uds_request_with_retry(request, timeout_s, label)
                    self._log(msg=f"세션 전환 성공: 0x{stype:02X}")
                # If selected_session_type's value is empty, fall through to default
                else:
                    session_type = int(session_type_str, 16) if isinstance(session_type_str, str) else session_type_str
                    request = build_session_control(session_type)
                    self._uds_request_with_retry(request, timeout_s, f"SessionControl(0x{session_type:02X})")
                    self._log(msg=f"세션 전환 성공: 0x{session_type:02X}")

                    if bg_session_type_str:
                        bg_session = int(bg_session_type_str, 16) if isinstance(bg_session_type_str, str) else bg_session_type_str
                        request2 = build_session_control(bg_session)
                        self._uds_request_with_retry(request2, timeout_s, f"SessionControl(Background=0x{bg_session:02X})")
                        self._log(msg=f"백그라운드 세션 전환 성공: 0x{bg_session:02X}")
            else:
                # No explicit selection — send both if both exist (existing behavior)
                session_type = int(session_type_str, 16) if isinstance(session_type_str, str) else session_type_str
                request = build_session_control(session_type)
                self._uds_request_with_retry(request, timeout_s, f"SessionControl(0x{session_type:02X})")
                self._log(msg=f"세션 전환 성공: 0x{session_type:02X}")

                if bg_session_type_str:
                    bg_session = int(bg_session_type_str, 16) if isinstance(bg_session_type_str, str) else bg_session_type_str
                    request2 = build_session_control(bg_session)
                    self._uds_request_with_retry(request2, timeout_s, f"SessionControl(Background=0x{bg_session:02X})")
                    self._log(msg=f"백그라운드 세션 전환 성공: 0x{bg_session:02X}")

        elif svc == "securityAccess":
            self._execute_security_access(step, phase_name)

        elif svc == "routineControl":
            ctrl_type_str = params.get("routineControlType", "0x01")
            routine_id_str = params.get("routineIdentifier", "0x0000")
            option_record_str = params.get("routineControlOptionRecord", "")
            ctrl_type = int(ctrl_type_str, 16) if isinstance(ctrl_type_str, str) else ctrl_type_str
            routine_id = int(routine_id_str, 16) if isinstance(routine_id_str, str) else routine_id_str
            option_record = bytes.fromhex(option_record_str[2:]) if option_record_str.startswith("0x") else b""

            request = build_routine_control(ctrl_type, routine_id, option_record)
            timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

            self._uds_request_with_retry(request, timeout_s,
                                          f"RoutineControl(0x{routine_id:04X}, type={ctrl_type})",
                                          max_retries=10, retry_delay_s=0.5)
            self._log(msg=f"루틴 제어 성공: ID=0x{routine_id:04X}, type={ctrl_type}")

        elif svc == "requestDownload":
            self._execute_request_download(step)

        elif svc == "transferData":
            self._execute_transfer_data(step)

        elif svc == "requestTransferExit":
            self._execute_transfer_exit(step)

        elif svc == "ecuReset":
            reset_mode_str = params.get("resetMode", "0x01")
            reset_timeout_str = params.get("resetTimeout", "60")
            reset_mode = int(reset_mode_str, 16) if isinstance(reset_mode_str, str) else reset_mode_str
            reset_timeout = int(reset_timeout_str)

            request = build_ecu_reset(reset_mode)
            timeout_s = max(float(reset_timeout), 5.0)

            try:
                self._uds_request(request, timeout_s, f"ECUReset(0x{reset_mode:02X})")
            except UdsError:
                pass
            self._log(msg=f"ECU 리셋 명령 전송: mode=0x{reset_mode:02X}")

        elif svc == "controlDTCSetting":
            self._log(msg=f"DTC 설정 (로깅): {params}")

        elif svc == "communicationControl":
            self._log(msg=f"통신 제어 (로깅): {params}")

        elif svc == "readDataByIdentifier":
            self._log(msg=f"DID 읽기 (로깅): {params}")

        elif svc == "complete\u0110ecision":
            self._log(msg=f"완료 판정 (향후): {params}")

        else:
            self._log(level="WARN", msg=f"알 수 없는 서비스: {svc}")

    def _execute_security_access(self, step: UdsStep, phase_name: str) -> None:
        """Execute SecurityAccess sequence: RequestSeed → GenerateKey → SendKey."""
        params = self._get_effective_params(step)
        algorithm_str = params.get("algorithm", "0x00")
        algorithm = int(algorithm_str, 16) if isinstance(algorithm_str, str) else algorithm_str

        seed_step = find_step(step.sub_steps, "requestSeed")
        key_step = find_step(step.sub_steps, "sendKey")

        if seed_step is None or key_step is None:
            self._log(level="WARN", msg="SecurityAccess: requestSeed/sendKey not found")
            return

        access_mode_seed = int(seed_step.params.get("accessMode", "0x11"), 16)
        access_mode_key = int(key_step.params.get("accessMode", "0x12"), 16)

        timeout_s = self._procedure.p2_can_server_max / 1000.0 if self._procedure else 0.05

        request = build_seed_request(access_mode_seed)
        self._log(msg=f"Seed 요청 (algorithm=0x{algorithm:02X}, mode=0x{access_mode_seed:02X})")
        result = self._uds_request_with_retry(request, timeout_s, "RequestSeed")
        seed = bytes(result["data"][1:])
        self._log(msg=f"Seed 수신: {seed.hex()} ({len(seed)} bytes)")

        key = generate_key(seed)
        self._log(msg=f"키 생성 완료: {key.hex()}")

        request = build_send_key(access_mode_key, key)
        self._uds_request_with_retry(request, timeout_s, "SendKey")
        self._log(msg="보안 액세스 성공 (Key accepted)")

    def _execute_request_download(self, step: UdsStep) -> None:
        """Execute RequestDownload."""
        params = self._get_effective_params(step)
        dfi = int(params.get("dataFormatIdentifier", "0x00"), 16)
        alfi = int(params.get("addressAndLengthFormatIdentifier", "0x44"), 16)
        mem_addr = int(params.get("memoryAddress", "0x00000000"), 16)
        mem_size = int(params.get("memorySize", "0x00000000"), 16)

        request = build_request_download(dfi, alfi, mem_addr, mem_size)
        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        result = self._uds_request_with_retry(
            request, timeout_s,
            f"RequestDownload(addr=0x{mem_addr:08X}, size=0x{mem_size:08X})",
            max_retries=10, retry_delay_s=0.5,
        )

        download_info = parse_request_download_response(
            bytes([0x74, alfi]) + result["data"]
        )
        max_block_length = download_info.get("max_length", 0)

        xml_block_str = params.get("maxNumberOfBlockLength", "0x0C02")
        xml_block_size = int(xml_block_str, 16) if isinstance(xml_block_str, str) else xml_block_str

        if max_block_length > 0 and max_block_length < xml_block_size:
            block_size = max_block_length
        else:
            block_size = xml_block_size

        effective_block_size = min(block_size, 4095)

        self._log(msg=f"RequestDownload 성공: blockSize={effective_block_size}")

        self._download_block_size = effective_block_size
        self._download_memory_addr = mem_addr
        self._download_memory_size = mem_size

    def _execute_transfer_data(self, step: UdsStep) -> None:
        """Execute TransferData: send binary in blocks."""
        binary = self._binary_data
        if binary is None:
            raise RuntimeError("BIN 데이터가 없습니다")

        params = self._get_effective_params(step)
        block_size = getattr(self, '_download_block_size', 0x0C02)

        seek_addr_str = params.get("seekAddress", "0x0200")
        seek_addr = int(seek_addr_str, 16) if isinstance(seek_addr_str, str) else seek_addr_str

        write_size_str = params.get("writeSize", "0")
        write_size = int(write_size_str, 16) if isinstance(write_size_str, str) else int(write_size_str)

        if seek_addr >= len(binary):
            raise RuntimeError(f"seekAddress(0x{seek_addr:X})가 BIN 파일 크기({len(binary)} bytes)를 초과")

        if seek_addr + write_size > len(binary):
            self._log(level="WARN", msg=f"seekAddress(0x{seek_addr:X}) + writeSize(0x{write_size:X})가 BIN 파일 크기를 초과하여 endOffset을 {len(binary)}로 제한")

        if write_size == 0:
            self._log(msg=f"TransferData 건너뜀: writeSize=0")
            self._update_progress(total_blocks=0, current_block=0, percent=100.0)
            return

        end_offset = min(seek_addr + write_size, len(binary))
        total_size = end_offset - seek_addr
        total_blocks = (total_size + block_size - 1) // block_size
        self._update_progress(total_blocks=total_blocks, current_block=0, percent=0.0)

        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        seq_num = 1
        offset = seek_addr

        self._log(msg=f"TransferData 시작: seekAddress=0x{seek_addr:X}, writeSize=0x{write_size:X}, "
                      f"endOffset=0x{end_offset:X}, blockSize={block_size}, totalBlocks={total_blocks}")

        while offset < end_offset:
            if self._stop_event.is_set():
                raise RuntimeError("사용자에 의해 전송 중단됨")

            chunk = binary[offset:min(offset + block_size, end_offset)]
            request = build_transfer_data(seq_num & 0xFF, chunk)

            try:
                self._uds_request_with_retry(
                    request, timeout_s,
                    f"TransferData(seq={seq_num}, offset=0x{offset:06X}, size={len(chunk)})",
                    max_retries=10, retry_delay_s=0.5,
                )
            except UdsError as exc:
                self._log(level="ERROR", msg=f"TransferData 블록 {seq_num} 실패: {exc}")
                raise

            offset += len(chunk)
            seq_num += 1

            bytes_sent = offset - seek_addr
            pct = min(100.0, (bytes_sent / total_size) * 100.0)
            self._update_progress(
                current_block=seq_num - 1,
                percent=round(pct, 1),
            )

            if seq_num % 10 == 0:
                self._log(msg=f"전송 진도: 0x{offset:X}/0x{end_offset:X} bytes ({pct:.1f}%)")

        self._log(msg=f"TransferData 완료: {total_blocks} blocks, {total_size} bytes (0x{seek_addr:X} → 0x{end_offset:X})")

    def _execute_transfer_exit(self, step: UdsStep) -> None:
        """Execute RequestTransferExit."""
        request = build_transfer_exit()
        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        self._uds_request_with_retry(request, timeout_s, "RequestTransferExit", max_retries=10, retry_delay_s=0.5)
        self._log(msg="전송 종료 성공")

    def _run_error_recovery(self) -> None:
        """Execute error-rule if present."""
        proc = self._procedure
        if proc is None:
            return
        if not proc.error_rule.preparation and not proc.error_rule.unit and not proc.error_rule.complete:
            self._log(msg="에러 복구 규칙 없음")
            return

        self._log(msg="--- 에러 복구 절차 실행 ---")
        try:
            self._run_steps(proc.error_rule.preparation, "error_prep")
            self._run_steps(proc.error_rule.unit, "error_unit")
            self._run_steps(proc.error_rule.complete, "error_complete")
            self._log(msg="에러 복구 완료")
        except Exception as exc:
            self._log(level="ERROR", msg=f"에러 복구 실패: {exc}")


# ---- Multi-slot manager ---------------------------------------------------


class MultiUdsDownloadManager:
    """Manages 3 independent UDS download slots (file 01/02/03)."""

    NUM_SLOTS = 3

    def __init__(self, can_manager, isotp_send_fn, isotp_receive_fn):
        self._managers = [
            UdsDownloadManager(can_manager, isotp_send_fn, isotp_receive_fn)
            for _ in range(self.NUM_SLOTS)
        ]

    def get_manager(self, slot_index: int) -> UdsDownloadManager:
        if not 0 <= slot_index < self.NUM_SLOTS:
            raise ValueError(f"공대 슬롯 index: {slot_index}")
        return self._managers[slot_index]

    def all_status(self) -> list[dict]:
        """Return status for all 3 slots."""
        return [m.status() for m in self._managers]

    def all_steps(self) -> list[Optional[list[dict]]]:
        """Return procedure steps for all 3 slots."""
        return [m.get_procedure_steps() for m in self._managers]

    def start_all(self, slot_indices: list[int],
                   selected_steps: Optional[list[str]] = None,
                   modified_params: Optional[dict[str, dict]] = None) -> list[dict]:
        """Start specified slots in sequence (0→1→2)."""
        results: list[dict] = []
        # Sort by slot index
        for idx in sorted(slot_indices):
            mgr = self._managers[idx]
            try:
                result = mgr.start(selected_steps=selected_steps, modified_params=modified_params)
                results.append({"slot_index": idx, "status": result, "success": True})
            except Exception as exc:
                results.append({"slot_index": idx, "error": str(exc), "success": False})
                break
        return results

    def stop_all(self, slot_indices: list[int]) -> list[dict]:
        """Stop specified slots."""
        results = []
        for idx in slot_indices:
            mgr = self._managers[idx]
            try:
                result = mgr.stop()
                results.append({"slot_index": idx, "status": result, "success": True})
            except Exception as exc:
                results.append({"slot_index": idx, "error": str(exc), "success": False})
        return results
