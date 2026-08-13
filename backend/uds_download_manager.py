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

import can

from uds_core import (
    UdsError,
    SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED,
    ROUTINE_START, ROUTINE_STOP, ROUTINE_REQUEST_RESULTS,
    RESET_HARD, RESET_KEY_OFF_ON, RESET_SOFT,
    SECURITY_REQUEST_SEED, SECURITY_SEND_KEY,
    build_session_control,
    build_ecu_reset,
    build_security_access_request_seed,
    build_security_access_send_key,
    build_routine_control,
    build_request_download,
    build_transfer_data,
    build_transfer_exit,
    build_tester_present,
    parse_response,
    parse_request_download_response,
    generate_key,
    expects_no_response,
    suppressed_response_result,
)
from uds_xml_parser import parse_xml, UdsProcedure, UdsStep, UdsRule, find_step
from log_service import AutoCanLogger

logger = logging.getLogger(__name__)


def _is_timeout_error(exc: Exception) -> bool:
    """isotp_service.receive() reports every "no frame arrived" case (SF/FF
    wait, CF wait) via an IsoTpError whose message contains this phrase --
    used to distinguish a genuine timeout (the expected outcome for a
    suppress-bit request) from other ISO-TP-level errors (malformed frame,
    SN mismatch, etc.), which should still be surfaced as real failures even
    when the suppress bit is set."""
    return "시간 초과" in str(exc)

MAX_EVENTS = 500

# TransferData keep-alive: send a suppressed TesterPresent [3E 80] at least
# this often while a block transfer is in progress, so the ECU's S3 session
# timer doesn't trip during a long binary transfer.
TESTER_PRESENT_INTERVAL_S = 2.0

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

    def __init__(self, can_manager, isotp_send_fn, isotp_receive_fn, seedkey_service=None,
                 log_dir: Optional[Path] = None, slot_label: str = "slot"):
        self._can = can_manager
        self._isotp_send = isotp_send_fn
        self._isotp_receive = isotp_receive_fn
        # Real vendor SeedKey DLL (see seedkey_client.py). None (or not
        # loaded) -> _execute_security_access() falls back to uds_core's
        # dummy generate_key().
        self._seedkey_service = seedkey_service
        # Auto ASCII CAN log for this slot's whole run (Start through
        # Complete/Error, including error-rule recovery) -- None when the
        # caller didn't provide a log_dir (e.g. tests), which makes every
        # _auto_logger call below a no-op via the `is not None` guards.
        self._auto_logger = AutoCanLogger(can_manager, log_dir) if log_dir is not None else None
        self._slot_label = slot_label

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
            # Global step index (matches get_procedure_steps()' flat list) of
            # the step currently executing, or -1 when none is (idle/between
            # runs). Only set for the main procedure's own steps -- error-rule
            # recovery runs its own separate, shorter step list (see
            # _run_error_recovery) whose indices would otherwise collide with
            # unrelated main-procedure steps of the same number.
            "current_step_idx": -1,
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

    def start(self, selected_steps: Optional[list[int]] = None, modified_params: Optional[dict[str, dict]] = None) -> dict:
        """Start the download procedure in a background thread.

        Args:
            selected_steps: list of step indices to execute (empty/null = all)
            modified_params: dict of step_name -> {param_key: new_value} overrides

        The selected_steps and modified_params are captured as immutable copies
        at ``start()`` time and passed into the worker thread so a subsequent
        ``start()`` call (or a param mutation via ``set_param``) cannot perturb
        this in-flight run -- the worker reads the captured copies, not the
        live ``self._selected_steps``/``self._modified_params`` attributes.
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
            self._progress = {"current_step": "", "total_blocks": 0, "current_block": 0, "percent": 0.0, "phase": "", "current_step_idx": -1}
            self._error_message = None
            self._running = True
            # Capture immutable copies for the worker thread. We also keep
            # (overwriting) the live attributes for backward-compatible reads
            # by other code paths (e.g. set_param), but the worker uses the
            # captured locals -- never the live attributes -- so concurrent
            # start()/set_param() calls cannot race the in-flight run.
            captured_selected_steps = list(selected_steps) if selected_steps is not None else None
            captured_modified_params = {svc: dict(params) for svc, params in (modified_params or {}).items()}
            self._selected_steps = captured_selected_steps
            self._modified_params = captured_modified_params

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(captured_selected_steps, captured_modified_params),
            daemon=True,
        )
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        """Request graceful stop of the download procedure."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                self._log(level="WARN", msg="다운로드 스레드가 5초 내에 종료되지 않았습니다. 백그라운드에서 계속 실행 중입니다.")
                # Thread is still actually running -- leave _running/_state
                # untouched so a concurrent start() keeps being refused
                # instead of spinning up a second worker thread that would
                # race the still-live one over shared state (self._events,
                # the CAN bus, the ECU session). _run()'s own finally block
                # resets _running once this thread genuinely finishes.
                return self.status()
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
        """Update a single parameter value for a step.

        Note: this mutates the live ``self._modified_params`` which is read by
        the *next* ``start()`` call's capture. A currently running worker is
        immune because it operates on its own captured copy (see ``start()``).
        """
        proc = self._procedure
        if proc is None:
            raise RuntimeError("프로시저가 로드되지 않았습니다")

        modified_params = getattr(self, '_modified_params', {})
        if step_service not in modified_params:
            modified_params[step_service] = {}
        modified_params[step_service][param_key] = param_value
        self._modified_params = modified_params

    def _get_effective_params(self, step: UdsStep, modified_params: Optional[dict[str, dict]] = None) -> dict:
        """Get effective parameters for a step, merging modified overrides.

        ``modified_params`` is the immutable copy captured at ``start()`` time
        and threaded through the worker. When provided it takes precedence over
        the live ``self._modified_params`` so the in-flight run is isolated
        from concurrent ``start()``/``set_param()`` mutations.
        """
        base = dict(step.params)
        mp = modified_params if modified_params is not None else getattr(self, '_modified_params', {})
        if step.service in mp:
            for k, v in mp[step.service].items():
                if v:  # non-empty override → 사용자가 수정한 값
                    base[k] = v
                # 빈 문자열이면 원래값 유지
        return base

    def _is_step_selected(self, step_idx: int, step: UdsStep, selected_steps: Optional[list[int]] = None) -> bool:
        """Check if a step is selected by its index.

        - None: all steps selected (default)
        - []: no steps selected
        - [idx1, idx2, ...]: only selected step indices

        ``selected_steps`` is the immutable copy captured at ``start()`` time.
        When provided it takes precedence over ``self._selected_steps`` so a
        concurrent ``start()`` cannot change the selection of a running worker.
        """
        ss = selected_steps if selected_steps is not None else getattr(self, '_selected_steps', None)
        if ss is None:
            return True  # No selection → all by default
        return step_idx in ss

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

        # Log raw CAN frame before sending
        req_hex = request.hex(" ").upper() if isinstance(request, (bytes, bytearray)) else str(request)
        self._log(
            level="INFO",
            service="CAN_TX",
            msg=f"Tx CAN_ID=0x{tx_id:03X} DATA=[{req_hex}] ({label})"
        )

        if self._can.notifier is None:
            raise UdsError(f"ISO-TP 송신 실패 ({label}): CAN 버스가 연결되어 있지 않습니다")
        # Registered *before* sending, not after -- an ECU that answers
        # unusually fast (observed: 4.688ms after a TransferData request)
        # can have its response on the bus before a listener created only
        # after send() returns gets a chance to see it, since python-can's
        # Notifier only delivers to listeners registered at the moment a
        # frame arrives. Passing the same reader into both send() (for any
        # Flow Control it needs while sending a multi-frame request) and
        # receive() keeps it registered across the whole exchange with no
        # gap in between. See isotp_service.send()/receive()'s own
        # docstrings.
        reader = can.BufferedReader()
        self._can.notifier.add_listener(reader)
        try:
            try:
                self._isotp_send(
                    self._can, tx_id, rx_id, request,
                    is_extended_id=is_ext,
                    fc_timeout_s=timeout_s,
                    reader=reader,
                )
            except Exception as exc:
                raise UdsError(f"ISO-TP 송신 실패 ({label}): {exc}")

            try:
                response = self._isotp_receive(
                    self._can, rx_id, tx_id,
                    timeout_s=timeout_s,
                    is_extended_id=is_ext,
                    fc_stmin=fc_stmin,
                    reader=reader,
                )
            except Exception as exc:
                # Suppress Positive Response bit set -> the server is required
                # to send nothing at all, so a plain timeout here is the
                # correct, expected outcome, not a failure. A non-timeout error
                # (malformed frame, etc.) still fails regardless of the bit.
                if expects_no_response(request) and _is_timeout_error(exc):
                    self._log(level="INFO", msg=f"응답 없음(suppressPosRspMsgIndicationBit) — 정상 ({label})")
                    return suppressed_response_result(request)
                raise UdsError(f"ISO-TP 수신 실패 ({label}): {exc}")
        finally:
            self._can.notifier.remove_listener(reader)

        result = parse_response(response)
        if not result["positive"]:
            raise UdsError(
                f"UDS Negative Response ({label}): SID=0x{result['sid']:02X}, NRC=0x{result['nrc']:02X}",
                nrc=result["nrc"],
            )
        return result

    def _uds_request_with_retry(
        self, request: bytes, timeout_s: float, label: str = "",
        max_retries: Optional[int] = None, retry_delay_s: float = 0.1,
    ) -> dict:
        """Send a UDS request once and receive the response, waiting again
        (without retransmitting) on NRC 0x78 (ResponsePending) -- per ISO
        14229-1 the client must NOT resend the request while the server is
        still processing it, it must just keep waiting for the eventual
        final response. Each post-0x78 wait uses the extended
        P2*Server_max timeout (`proc.p2_star_can_server_max`) instead of
        the original, usually much shorter, P2 timeout that was only meant
        to bound the FIRST reply.

        ``max_retries=None`` (the default) means unlimited: the client keeps
        waiting as long as the ECU keeps sending 0x78, with no attempt-count
        ceiling -- per ISO 14229-1 there is no cap on the number of pending
        responses, only on how long each individual wait may take (bounded
        by P2*Server_max on each attempt). Pass an explicit integer to cap
        it where that's actually wanted.

        One CAN listener is kept registered across the send *and* the
        entire retry sequence (passed into `_isotp_send()` and every
        `_isotp_receive()` call as `reader`) -- registering it only after
        send() returns, or tearing it down and recreating it between
        retries, both leave a gap during which nothing is registered to
        catch an arriving frame. If the ECU's real final response happens
        to land in one of those gaps (right after sending, right after an
        NRC 0x78, or during the retry_delay_s sleep), it's gone for good
        and the next attempt times out waiting for a frame that already
        went by -- this produced the exact "timed out anyway" symptom
        despite the 0x78 retry logic itself being correct, and also a
        response arriving unusually fast (observed: 4.688ms after a
        TransferData request) being missed entirely. See
        isotp_service.send()/receive()'s own `reader` docstrings.

        Deliberately does not delegate to `_uds_request()` (which both
        sends and receives) -- that method is still used as-is by callers
        that want a single send+receive with no pending-retry handling
        (e.g. ECUReset)."""
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

        req_hex = request.hex(" ").upper() if isinstance(request, (bytes, bytearray)) else str(request)
        self._log(level="INFO", service="CAN_TX", msg=f"Tx CAN_ID=0x{tx_id:03X} DATA=[{req_hex}] ({label})")

        if self._can.notifier is None:
            raise UdsError(f"ISO-TP 송신 실패 ({label}): CAN 버스가 연결되어 있지 않습니다")
        # Registered *before* sending -- see _uds_request()'s matching
        # comment and isotp_service.send()'s docstring; the fast-ECU-
        # response race this fixes applies here identically.
        reader = can.BufferedReader()
        self._can.notifier.add_listener(reader)
        try:
            try:
                self._isotp_send(self._can, tx_id, rx_id, request, is_extended_id=is_ext, fc_timeout_s=timeout_s, reader=reader)
            except Exception as exc:
                raise UdsError(f"ISO-TP 송신 실패 ({label}): {exc}")

            pending_timeout_s = max(proc.p2_star_can_server_max / 1000.0, timeout_s)
            attempt = 0
            while True:
                try:
                    response = self._isotp_receive(
                        self._can, rx_id, tx_id,
                        timeout_s=timeout_s if attempt == 0 else pending_timeout_s,
                        is_extended_id=is_ext,
                        fc_stmin=fc_stmin,
                        reader=reader,
                    )
                except Exception as exc:
                    # Suppress Positive Response bit set -> no response at
                    # all is the correct, expected outcome on a plain
                    # timeout, not a failure (see _uds_request()'s matching
                    # comment). A non-timeout error still fails regardless.
                    if expects_no_response(request) and _is_timeout_error(exc):
                        self._log(level="INFO", msg=f"응답 없음(suppressPosRspMsgIndicationBit) — 정상 ({label})")
                        return suppressed_response_result(request)
                    raise UdsError(f"ISO-TP 수신 실패 ({label}): {exc}")

                result = parse_response(response)
                if result["positive"]:
                    return result
                if result["nrc"] == 0x78 and (max_retries is None or attempt < max_retries):
                    limit_str = "무제한" if max_retries is None else str(max_retries)
                    self._log(
                        level="WARN",
                        msg=f"NRC 0x78 (ResponsePending) 대기 {attempt + 1}/{limit_str} (P2*={pending_timeout_s:.1f}s)",
                    )
                    time.sleep(retry_delay_s)
                    attempt += 1
                    continue
                raise UdsError(
                    f"UDS Negative Response ({label}): SID=0x{result['sid']:02X}, NRC=0x{result['nrc']:02X}",
                    nrc=result["nrc"],
                )
        finally:
            self._can.notifier.remove_listener(reader)

    # ---- Internal: Main execution loop ---------------------------------------

    def _run(self, selected_steps: Optional[list[int]], modified_params: Optional[dict[str, dict]]) -> None:
        # Thread entry point. selected_steps and modified_params are the
        # immutable copies captured at start() time.
        if self._auto_logger is not None:
            label = Path(self._xml_path).stem if self._xml_path else self._slot_label
            self._auto_logger.start(label)
        try:
            self._run_procedure(selected_steps, modified_params)
        except Exception as exc:
            logger.exception("UDS download failed")
            self._log(level="ERROR", msg=f"다운로드 실패: {exc}")
            self._error_message = str(exc)
            self._set_state(STATE_ERROR)
            try:
                self._run_error_recovery(modified_params)
            except Exception:
                logger.exception("Error recovery also failed")
        finally:
            with self._lock:
                self._running = False
            if self._auto_logger is not None:
                self._auto_logger.stop(success=(self._state == STATE_COMPLETED))

    def _run_procedure(self, selected_steps: Optional[list[int]], modified_params: Optional[dict[str, dict]]) -> None:
        proc = self._procedure
        if proc is None:
            raise RuntimeError("No procedure loaded")

        self._log(level="INFO", msg="========== 다운로드 시작 ==========")
        self._log(level="INFO", msg=f"DEBUG: XML={self._xml_path}, BIN={self._binary_path} ({len(self._binary_data)} bytes)")
        self._log(level="INFO", msg=f"DEBUG: selected_steps={selected_steps} (None=all, []=none, [int,...]=partial)")

        # Build a flat list of all steps (consistent with get_procedure_steps)
        all_phases = [
            ("preparation", proc.processing_rule.preparation),
            ("unit", proc.processing_rule.unit),
            ("complete", proc.processing_rule.complete),
            ("activate_prep", proc.activate_rule.preparation),
            ("activate_unit", proc.activate_rule.unit),
            ("activate_complete", proc.activate_rule.complete),
        ]

        # Debug: dump full step list with global indices
        global_idx = 0
        step_index_map = []
        for phase_name, phase_steps in all_phases:
            for step in phase_steps:
                selected = self._is_step_selected(global_idx, step, selected_steps)
                step_index_map.append(f"  [{global_idx}] {phase_name}: {step.service} → {'✓ SELECTED' if selected else '✗ SKIP'}")
                global_idx += 1
        self._log(level="INFO", msg="DEBUG: 전체 스텝 리스트:")
        for line in step_index_map:
            self._log(level="INFO", msg=line)

        # Execute phases with global indexing
        global_step_idx = 0
        for phase_name, phase_steps in all_phases:
            self._log(level="INFO", msg=f"DEBUG: --- {phase_name} Phase (시작 idx={global_step_idx}) ---")
            global_step_idx = self._run_steps(
                phase_steps, phase_name, global_step_idx, selected_steps, modified_params
            )

        self._set_state(STATE_COMPLETED)
        self._update_progress(current_step="Complete", percent=100.0, current_step_idx=-1)
        self._log(level="INFO", msg="===== 다운로드 완료 =====")

    def _run_steps(self, steps: list[UdsStep], phase_name: str, start_idx: int = 0,
                   selected_steps: Optional[list[int]] = None,
                   modified_params: Optional[dict[str, dict]] = None,
                   force_all: bool = False) -> int:
        """Execute a list of UDS steps, skipping unselected ones.

        Args:
            steps: list of UDS steps for this phase
            phase_name: phase label for logging
            start_idx: global step index offset for this phase
            selected_steps: immutable captured selection (None=all, []=none)
            modified_params: immutable captured param overrides (or None)
            force_all: run every step regardless of selected_steps. Needed for
                error-rule recovery, whose own step list is indexed from 0
                independently of the main procedure's -- reusing the main
                run's index-based selected_steps there would match unrelated
                steps by coincidental index overlap (selected_steps=None
                would fall back to the live self._selected_steps instead of
                meaning "all", so it can't be used to express this either).

        Returns:
            next global step index after this phase
        """
        step_idx = start_idx
        for step in steps:
            if self._stop_event.is_set():
                raise RuntimeError("사용자에 의해 중단됨")
            selected = True if force_all else self._is_step_selected(step_idx, step, selected_steps)
            self._log(level="INFO", msg=f"DEBUG: step[{step_idx}] {step.service} → {'SELECTED' if selected else 'SKIPPED'}")
            if not selected:
                step_idx += 1
                continue
            # force_all is only ever True for error-rule recovery (see this
            # method's docstring) -- its step list has its own separate,
            # shorter index space, so reporting its step_idx here would
            # highlight an unrelated main-procedure step of the same number
            # in the UI checklist.
            if not force_all:
                self._update_progress(current_step_idx=step_idx)
            self._execute_step(step, phase_name, modified_params, step_idx)
            step_idx += 1
        return step_idx

    def _execute_step(self, step: UdsStep, phase_name: str, modified_params: Optional[dict[str, dict]] = None,
                       step_idx: int = -1) -> None:
        """Execute a single UDS step."""
        params = self._get_effective_params(step, modified_params)
        svc = step.service
        self._update_progress(current_step=f"{svc} ({phase_name})")
        self._log(level="INFO", service=svc, msg=f"실행: {svc} {params}")

        if svc == "startCommunication":
            self._log(level="INFO", msg=f"CAN 통신 초기화 (STmin=0x{self._procedure.stmin_tx:02X}, "
                          f"P2={self._procedure.p2_can_server_max}ms)")

        elif svc == "stopCommunication":
            self._log(level="INFO", msg="CAN 통신 종료")

        elif svc == "diagnosticSessionControl":
            # Determine which session type(s) to send.
            # The frontend may pass a _sessionType_<step_idx> override to
            # choose between diagnosticSessionType and
            # background_diagnosticSessionType when both are present in the
            # XML. modified_params is keyed by *service name* only, so when a
            # procedure has more than one diagnosticSessionControl step, every
            # one of their _sessionType_* overrides ends up merged into every
            # occurrence's step_params (see _get_effective_params) -- scanning
            # for "any" key with that prefix (previous code) could pick up
            # another step's override instead of this step's own, silently
            # falling through to the "no selection" branch (which resends
            # *both* the main and background session) whenever that other
            # step's value didn't happen to match a key on this step. Keying
            # directly on this step's own global index sidesteps the
            # collision entirely.
            step_params = self._get_effective_params(step, modified_params)
            selected_session_type = step_params.get(f"_sessionType_{step_idx}")

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
                    self._log(level="INFO", msg=f"세션 전환 성공: 0x{stype:02X}")
                # If selected_session_type's value is empty, fall through to default
                else:
                    session_type = int(session_type_str, 16) if isinstance(session_type_str, str) else session_type_str
                    request = build_session_control(session_type)
                    self._uds_request_with_retry(request, timeout_s, f"SessionControl(0x{session_type:02X})")
                    self._log(level="INFO", msg=f"세션 전환 성공: 0x{session_type:02X}")

                    if bg_session_type_str:
                        bg_session = int(bg_session_type_str, 16) if isinstance(bg_session_type_str, str) else bg_session_type_str
                        request2 = build_session_control(bg_session)
                        self._uds_request_with_retry(request2, timeout_s, f"SessionControl(Background=0x{bg_session:02X})")
                        self._log(level="INFO", msg=f"백그라운드 세션 전환 성공: 0x{bg_session:02X}")
            else:
                # No explicit selection — send both if both exist (existing behavior)
                session_type = int(session_type_str, 16) if isinstance(session_type_str, str) else session_type_str
                request = build_session_control(session_type)
                self._uds_request_with_retry(request, timeout_s, f"SessionControl(0x{session_type:02X})")
                self._log(level="INFO", msg=f"세션 전환 성공: 0x{session_type:02X}")

                if bg_session_type_str:
                    bg_session = int(bg_session_type_str, 16) if isinstance(bg_session_type_str, str) else bg_session_type_str
                    request2 = build_session_control(bg_session)
                    self._uds_request_with_retry(request2, timeout_s, f"SessionControl(Background=0x{bg_session:02X})")
                    self._log(level="INFO", msg=f"백그라운드 세션 전환 성공: 0x{bg_session:02X}")

        elif svc == "securityAccess":
            self._execute_security_access(step, phase_name, modified_params)

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
                                          retry_delay_s=0.5)
            self._log(level="INFO", msg=f"루틴 제어 성공: ID=0x{routine_id:04X}, type={ctrl_type}")

        elif svc == "requestDownload":
            self._execute_request_download(step, modified_params)

        elif svc == "transferData":
            self._execute_transfer_data(step, modified_params)

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
            self._log(level="INFO", msg=f"ECU 리셋 명령 전송: mode=0x{reset_mode:02X}")

        elif svc == "controlDTCSetting":
            self._log(level="INFO", msg=f"DTC 설정 (로깅): {params}")

        elif svc == "communicationControl":
            self._log(level="INFO", msg=f"통신 제어 (로깅): {params}")

        elif svc == "readDataByIdentifier":
            self._log(level="INFO", msg=f"DID 읽기 (로깅): {params}")

        elif svc == "completeDecision":
            self._log(level="INFO", msg=f"완료 판정 (향후): {params}")

        else:
            self._log(level="WARN", msg=f"알 수 없는 서비스: {svc}")

    def _execute_security_access(self, step: UdsStep, phase_name: str, modified_params: Optional[dict[str, dict]] = None) -> None:
        """Execute SecurityAccess sequence: RequestSeed → GenerateKey → SendKey."""
        params = self._get_effective_params(step, modified_params)
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

        request = build_security_access_request_seed(access_mode_seed)
        self._log(level="INFO", msg=f"Seed 요청 (algorithm=0x{algorithm:02X}, mode=0x{access_mode_seed:02X})")
        result = self._uds_request_with_retry(request, timeout_s, "RequestSeed")

        # Positive response layout is SID(1) | securityAccessType(1) | seed(N) --
        # skip both header bytes, not just the SID, or the seed passed to
        # generate_key() is off by one (starts with the echoed access type).
        seed = bytes(result["data"][2:])
        self._log(level="INFO", msg=f"Seed 수신: {seed.hex()} ({len(seed)} bytes)")

        if self._seedkey_service is not None and self._seedkey_service.loaded:
            try:
                key = self._seedkey_service.generate_key(seed)
            except Exception as exc:
                raise UdsError(f"SeedKey 키 생성 실패: {exc}")
            self._log(level="INFO", msg=f"키 생성 완료 (SeedKey DLL): {key.hex()}")
        else:
            key = generate_key(seed)
            self._log(level="WARN", msg=f"[더미 키] SeedKey DLL이 로드되지 않아 더미 키를 사용합니다: {key.hex()}")

        request = build_security_access_send_key(key, access_mode_key)
        self._uds_request_with_retry(request, timeout_s, "SendKey")
        self._log(level="INFO", msg="보안 액세스 성공 (Key accepted)")

    def _execute_request_download(self, step: UdsStep, modified_params: Optional[dict[str, dict]] = None) -> None:
        """Execute RequestDownload."""
        params = self._get_effective_params(step, modified_params)
        dfi = int(params.get("dataFormatIdentifier", "0x00"), 16)
        alfi = int(params.get("addressAndLengthFormatIdentifier", "0x44"), 16)
        mem_addr = int(params.get("memoryAddress", "0x00000000"), 16)
        mem_size = int(params.get("memorySize", "0x00000000"), 16)

        request = build_request_download(dfi, alfi, mem_addr, mem_size)
        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        result = self._uds_request_with_retry(
            request, timeout_s,
            f"RequestDownload(addr=0x{mem_addr:08X}, size=0x{mem_size:08X})",
            retry_delay_s=0.5,
        )

        # result["data"] is already the full positive response (SID 0x74 +
        # the ECU's own lengthFormatIdentifier byte + maxNumberOfBlockLength);
        # it must be passed through as-is, not prefixed with a second
        # synthetic 0x74/alfi header, or parsing reads the wrong bytes as the
        # length field.
        download_info = parse_request_download_response(result["data"])
        max_block_length = download_info.get("max_length", 0)

        xml_block_str = params.get("maxNumberOfBlockLength", "0x0C02")
        xml_block_size = int(xml_block_str, 16) if isinstance(xml_block_str, str) else xml_block_str

        if max_block_length > 0 and max_block_length < xml_block_size:
            block_size = max_block_length
        else:
            block_size = xml_block_size

        effective_block_size = min(block_size, 4095)

        self._log(level="INFO", msg=f"RequestDownload 성공: blockSize={effective_block_size}")

        self._download_block_size = effective_block_size
        self._download_memory_addr = mem_addr
        self._download_memory_size = mem_size

    def _send_tester_present(self) -> None:
        """Fire-and-forget suppressed TesterPresent [3E 80] -- always a
        Single Frame, so isotp_service.send() returns immediately without
        waiting for anything, and the suppress bit means the ECU shouldn't
        answer at all. A send failure here must never abort the actual
        transfer it's protecting, so it's logged and swallowed."""
        proc = self._procedure
        if proc is None:
            return
        tx_id = proc.request_id
        rx_id = proc.response_id
        is_ext = (proc.request_id > 0x7FF) or (proc.response_id > 0x7FF)
        request = build_tester_present(suppress_pos_rsp=True)
        try:
            self._isotp_send(self._can, tx_id, rx_id, request, is_extended_id=is_ext)
            self._log(level="INFO", service="CAN_TX",
                      msg=f"Tx CAN_ID=0x{tx_id:03X} DATA=[{request.hex(' ').upper()}] (TesterPresent keep-alive)")
        except Exception as exc:
            self._log(level="WARN", msg=f"TesterPresent 전송 실패(무시): {exc}")

    def _execute_transfer_data(self, step: UdsStep, modified_params: Optional[dict[str, dict]] = None) -> None:
        """Execute TransferData: send binary in blocks."""
        binary = self._binary_data
        if binary is None:
            raise RuntimeError("BIN 데이터가 없습니다")

        params = self._get_effective_params(step, modified_params)
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
            self._log(level="INFO", msg="TransferData 건너뜀: writeSize=0")
            self._update_progress(total_blocks=0, current_block=0, percent=100.0)
            return

        end_offset = min(seek_addr + write_size, len(binary))
        total_size = end_offset - seek_addr
        total_blocks = (total_size + block_size - 1) // block_size
        self._update_progress(total_blocks=total_blocks, current_block=0, percent=0.0)

        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        seq_num = 1
        offset = seek_addr
        # A large binary can take long enough between diagnostic requests
        # that the ECU's own S3 session timer (no traffic for ~5s = drop
        # back to the default session) trips mid-transfer -- a suppressed
        # TesterPresent every 2s keeps the session alive without waiting for
        # (or expecting) a response.
        last_tester_present_ts = time.time()

        self._log(level="INFO", msg=f"TransferData 시작: seekAddress=0x{seek_addr:X}, writeSize=0x{write_size:X}, "
                      f"endOffset=0x{end_offset:X}, blockSize={block_size}, totalBlocks={total_blocks}")

        while offset < end_offset:
            if self._stop_event.is_set():
                raise RuntimeError("사용자에 의해 전송 중단됨")

            now = time.time()
            if now - last_tester_present_ts >= TESTER_PRESENT_INTERVAL_S:
                self._send_tester_present()
                last_tester_present_ts = now

            chunk = binary[offset:min(offset + block_size, end_offset)]
            request = build_transfer_data(seq_num & 0xFF, chunk)

            try:
                self._uds_request_with_retry(
                    request, timeout_s,
                    f"TransferData(seq={seq_num}, offset=0x{offset:06X}, size={len(chunk)})",
                    retry_delay_s=0.5,
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
                self._log(level="INFO", msg=f"전송 진도: 0x{offset:X}/0x{end_offset:X} bytes ({pct:.1f}%)")

        self._log(level="INFO", msg=f"TransferData 완료: {total_blocks} blocks, {total_size} bytes (0x{seek_addr:X} → 0x{end_offset:X})")

    def _execute_transfer_exit(self, step: UdsStep) -> None:
        """Execute RequestTransferExit."""
        request = build_transfer_exit()
        timeout_s = max(self._procedure.p2_star_can_server_max / 1000.0, 2.0) if self._procedure else 2.0

        self._uds_request_with_retry(request, timeout_s, "RequestTransferExit", retry_delay_s=0.5)
        self._log(level="INFO", msg="전송 종료 성공")

    def _run_error_recovery(self, modified_params: Optional[dict[str, dict]] = None) -> None:
        """Execute error-rule if present, running every one of its steps.

        The step checklist in the UI only ever lists the main processing/
        activate rules (see get_procedure_steps()) -- there is no checkbox
        for error-rule steps, so there is no user selection to apply here.
        Previously this reused the main run's ``selected_steps``, but that
        index list is sized for the (much longer) main procedure's flat step
        list; against the error-rule's own, separate, shorter step list the
        same numbers pointed at unrelated steps by coincidence, so recovery
        would run the wrong services (or skip real ones) depending on what
        had been checked for the main run.
        """
        proc = self._procedure
        if proc is None:
            return
        if not proc.error_rule.preparation and not proc.error_rule.unit and not proc.error_rule.complete:
            self._log(level="INFO", msg="에러 복구 규칙 없음")
            return

        self._log(level="INFO", msg="--- 에러 복구 절차 실행 ---")
        try:
            idx = 0
            idx = self._run_steps(proc.error_rule.preparation, "error_prep", idx, None, modified_params, force_all=True)
            idx = self._run_steps(proc.error_rule.unit, "error_unit", idx, None, modified_params, force_all=True)
            idx = self._run_steps(proc.error_rule.complete, "error_complete", idx, None, modified_params, force_all=True)
            self._log(level="INFO", msg="에러 복구 완료")
        except Exception as exc:
            self._log(level="ERROR", msg=f"에러 복구 실패: {exc}")


# ---- Multi-slot manager ---------------------------------------------------


class MultiUdsDownloadManager:
    """Manages 3 independent UDS download slots (file 01/02/03)."""

    NUM_SLOTS = 3

    def __init__(self, can_manager, isotp_send_fn, isotp_receive_fn, seedkey_service=None,
                 log_dir: Optional[Path] = None):
        self._managers = [
            UdsDownloadManager(can_manager, isotp_send_fn, isotp_receive_fn, seedkey_service,
                                log_dir=log_dir, slot_label=f"slot{i + 1}")
            for i in range(self.NUM_SLOTS)
        ]

    def get_manager(self, slot_index: int) -> UdsDownloadManager:
        if not 0 <= slot_index < self.NUM_SLOTS:
            raise ValueError(f"잘못된 슬롯 index: {slot_index}")
        return self._managers[slot_index]

    def all_status(self) -> list[dict]:
        """Return status for all 3 slots."""
        return [m.status() for m in self._managers]

    def all_steps(self) -> list[Optional[list[dict]]]:
        """Return procedure steps for all 3 slots."""
        return [m.get_procedure_steps() for m in self._managers]

    def start_all(self, slot_indices: list[int],
                   per_slot_selected_steps: Optional[dict[int, Optional[list[int]]]] = None,
                   per_slot_modified_params: Optional[dict[int, Optional[dict[str, dict]]]] = None,
                   selected_steps: Optional[list[int]] = None,
                   modified_params: Optional[dict[str, dict]] = None) -> list[dict]:
        """Start specified slots sequentially: slot N+1 only starts once slot N
        has stopped running (COMPLETED/ERROR), never in parallel.

        Per-slot overrides (``per_slot_selected_steps``/``per_slot_modified_params``)
        take precedence over the shared ``selected_steps``/``modified_params``.
        Missing per-slot entries fall back to the shared value, and a missing
        shared value means ``None`` (use XML defaults / no overrides).

        The first slot is started synchronously so its immediate result
        (success or failure) is reflected in the returned list, matching the
        previous single-call behavior. Remaining slots are started one at a
        time from a background orchestrator thread as their turn comes --
        each waits for the slot ahead of it to stop running before starting.
        A failure to start one of them is written to that slot's own event
        log (visible in the TextDisplay) and stops the rest of the sequence,
        the same "stop on first failure" behavior as before.
        """
        ordered = sorted(slot_indices)
        results: list[dict] = []
        if not ordered:
            return results

        def resolve(idx: int) -> tuple[Optional[list[int]], Optional[dict[str, dict]]]:
            slot_selected = (
                per_slot_selected_steps.get(idx) if per_slot_selected_steps is not None else None
            )
            if slot_selected is None:
                slot_selected = selected_steps
            slot_params = (
                per_slot_modified_params.get(idx) if per_slot_modified_params is not None else None
            )
            if slot_params is None:
                slot_params = modified_params
            return slot_selected, slot_params

        first_idx = ordered[0]
        first_mgr = self._managers[first_idx]
        first_selected, first_params = resolve(first_idx)
        try:
            result = first_mgr.start(selected_steps=first_selected, modified_params=first_params)
            results.append({"slot_index": first_idx, "status": result, "success": True})
        except Exception as exc:
            results.append({"slot_index": first_idx, "error": str(exc), "success": False})
            return results

        remaining = ordered[1:]
        for idx in remaining:
            results.append({"slot_index": idx, "status": "queued", "success": None})

        if remaining:
            def _run_sequence() -> None:
                prev_mgr = first_mgr
                for idx in remaining:
                    while prev_mgr.running:
                        time.sleep(0.05)
                    mgr = self._managers[idx]
                    slot_selected, slot_params = resolve(idx)
                    try:
                        mgr.start(selected_steps=slot_selected, modified_params=slot_params)
                    except Exception as exc:
                        mgr._log(level="ERROR", msg=f"슬롯 시작 실패로 순차 실행 중단: {exc}")
                        return
                    prev_mgr = mgr

            threading.Thread(target=_run_sequence, daemon=True).start()

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