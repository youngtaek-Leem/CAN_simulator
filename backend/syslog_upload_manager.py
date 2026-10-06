"""sysLog 취득(UDS Upload) Manager.

ECU의 시스템 로그 메모리를 UDS로 읽어내 바이너리로 저장한다. 고정 시퀀스
(CAN-SWDL/OTA의 다운로드 방향과 반대인 업로드 방향):

  10 03            -> 50 03                  (세션 전환)
  22 F1 20        -> 62 F1 20 + 4B 크기      (로그 메모리 크기, big-endian)
  [27 11 -> 67 11 + seed -> 27 12 + key -> 67 12]  (Security Enable 시만)
  35 00 44 <addr> <size> -> 75 .. maxBlockLen (RequestUpload)
  36 ZZ            -> 76 ZZ + 최대 1024B     (TransferData 반복)
  37               -> 77                     (RequestTransferExit)

수신 데이터는 <log_dir>/syslog_upload_<ts>.bin 으로 저장하고, 상태에
파일명/크기를 담아 프론트의 다운로드 버튼이 내려받는다.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import can

from uds_core import (
    UdsError,
    build_session_control,
    build_read_data_by_id,
    build_security_access_request_seed,
    build_security_access_send_key,
    build_request_upload,
    build_request_transfer_exit,
    build_tester_present,
    parse_response,
    parse_request_upload_response,
    parse_memory_size_did,
    generate_key,
)

logger = logging.getLogger(__name__)


def _is_timeout_error(exc: Exception) -> bool:
    return "시간 초과" in str(exc)


MAX_EVENTS = 500

# TransferData 수신 중 세션 유지용 suppress TesterPresent (2초 간격, 기능주소)
TESTER_PRESENT_INTERVAL_S = 2.0
FUNCTIONAL_REQUEST_ID = 0x7DF

# TransferData 블록 재전송 (NRC 응답·타임아웃·seq 불일치 시 동일 seq 재요청)
TRANSFER_BLOCK_MAX_RETRIES = 3
TRANSFER_BLOCK_RETRY_DELAY_S = 0.5

# 타임아웃 (초)
REQ_TIMEOUT_S = 2.0
PENDING_TIMEOUT_S = 5.0
BLOCK_TIMEOUT_S = 10.0

# 안전 상한: DID가 비정상적으로 큰 값을 돌려줘도 이 이상은 읽지 않는다
MAX_UPLOAD_BYTES = 16 * 1024 * 1024

STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_COMPLETED = "COMPLETED"
STATE_ERROR = "ERROR"

# OTA/CAN-SWDL과 동일한 기본 SecurityAccess 레벨 (27 11 / 27 12)
SECURITY_ACCESS_MODE_SEED = 0x11
SECURITY_ACCESS_MODE_KEY = 0x12


class SysLogUploadManager:
    """Manage UDS sysLog memory upload into a .bin file."""

    def __init__(
        self,
        can_manager,
        isotp_send_fn,
        isotp_receive_fn,
        seedkey_service=None,
        log_dir: Optional[Path] = None,
    ):
        self._can = can_manager
        self._isotp_send = isotp_send_fn
        self._isotp_receive = isotp_receive_fn
        self._seedkey_service = seedkey_service
        self._log_dir = Path(log_dir) if log_dir is not None else Path("uploads")

        self._lock = threading.RLock()
        self._state = STATE_IDLE
        self._events: list[dict] = []
        self._error_message: Optional[str] = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._request_id = 0x6D1
        self._response_id = 0x6B0
        self._address = 0x00000000
        self._saved_filename: Optional[str] = None
        self._saved_size = 0
        self._progress: dict[str, Any] = {
            "phase": "",
            "total_bytes": 0,
            "received_bytes": 0,
            "percent": 0.0,
            "current_block": 0,
            "total_blocks": 0,
        }
        # 실행 동안 공유하는 단일 CAN 리스너 (블록마다 생성/제거 시 생기는
        # 수신 공백 + 지터를 없앤다 -- 다운로드 매니저와 동일 패턴)
        self._reader: Optional[can.BufferedReader] = None

    # ---- Public API ---------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(
        self,
        request_id: int = 0x6D1,
        response_id: int = 0x6B0,
        address: int = 0x00000000,
        security_enable: bool = True,
    ) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("이미 실행 중입니다")
            if self._can.notifier is None:
                raise RuntimeError("CAN 버스가 연결되어 있지 않습니다")
            self._request_id = request_id
            self._response_id = response_id
            self._address = address
            self._security_enable = security_enable
            self._events = []
            self._error_message = None
            self._saved_filename = None
            self._saved_size = 0
            self._progress = {
                "phase": "", "total_bytes": 0, "received_bytes": 0,
                "percent": 0.0, "current_block": 0, "total_blocks": 0,
            }
            self._running = True
            self._state = STATE_RUNNING

        self._stop_event.clear()
        self._thread = threading.Thread(name="syslog_upload", target=self._run, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            self._running = False
            if self._state not in (STATE_COMPLETED, STATE_ERROR):
                self._state = STATE_IDLE
        return self.status()

    def status(self, tail: int | None = None) -> dict:
        with self._lock:
            events = self._events[-tail:] if tail is not None else self._events[-MAX_EVENTS:]
            return {
                "state": self._state,
                "running": self._running,
                "request_id": self._request_id,
                "response_id": self._response_id,
                "security_enable": getattr(self, "_security_enable", True),
                "progress": dict(self._progress),
                "saved_filename": self._saved_filename,
                "saved_size": self._saved_size,
                "events": list(events),
                "error": self._error_message,
            }

    def saved_file_path(self, filename: str) -> Path:
        """다운로드용 저장 파일 경로 (파일명 검증 포함)."""
        safe = Path(filename).name
        if not safe or safe != filename or not safe.startswith("syslog_upload_"):
            raise ValueError(f"잘못된 파일명: {filename}")
        path = self._log_dir / safe
        if not path.is_file():
            raise FileNotFoundError(f"파일 없음: {filename}")
        return path

    # ---- Internal: logging --------------------------------------------

    def _log(self, *, level: str, msg: str, service: Optional[str] = None) -> None:
        entry: dict[str, Any] = {"ts": time.time(), "level": level, "msg": msg}
        if service:
            entry["service"] = service
        with self._lock:
            self._events.append(entry)
            if len(self._events) > MAX_EVENTS:
                del self._events[: len(self._events) - MAX_EVENTS]

    def _update_progress(self, **fields: Any) -> None:
        with self._lock:
            self._progress.update(fields)

    # ---- Internal: UDS transport --------------------------------------

    def _is_extended(self) -> bool:
        return self._request_id > 0x7FF or self._response_id > 0x7FF

    def _uds_exchange(
        self, request: bytes, timeout_s: float, label: str = "",
        pending_timeout_s: float = PENDING_TIMEOUT_S,
    ) -> bytes:
        """UDS 요청 1건을 보내고 positive 페이로드를 돌려받는다.

        NRC 0x78 (ResponsePending)이 오면 재전송 없이 연장 대기한다
        (ISO 14229-1 -- 처리 중인 서버에 요청을 다시 보내면 안 된다).
        다른 NRC면 UdsError(nrc=...)로 실패한다."""
        is_ext = self._is_extended()
        req_hex = request.hex(" ").upper()
        self._log(level="INFO", service="CAN_TX",
                  msg=f"Tx CAN_ID=0x{self._request_id:03X} DATA=[{req_hex}] ({label})")
        if self._can.notifier is None or self._reader is None:
            raise UdsError(f"ISO-TP 송신 실패 ({label}): CAN 버스가 연결되어 있지 않습니다")
        try:
            self._isotp_send(
                self._can, self._request_id, self._response_id, request,
                is_extended_id=is_ext, fc_timeout_s=timeout_s, reader=self._reader,
                stop_event=self._stop_event,
            )
        except Exception as exc:
            raise UdsError(f"ISO-TP 송신 실패 ({label}): {exc}")

        attempt = 0
        while True:
            try:
                response = self._isotp_receive(
                    self._can, self._response_id, self._request_id,
                    timeout_s=timeout_s if attempt == 0 else pending_timeout_s,
                    is_extended_id=is_ext,
                    fc_stmin=0x00, fc_block_size=0,
                    reader=self._reader,
                    stop_event=self._stop_event,
                )
            except Exception as exc:
                raise UdsError(f"ISO-TP 수신 실패 ({label}): {exc}")
            result = parse_response(response)
            if result["positive"]:
                return bytes(result["data"])
            if result["nrc"] == 0x78:
                self._log(level="WARN", msg=f"NRC 0x78 (ResponsePending) 대기 중 (P2*={pending_timeout_s:.1f}s)")
                if self._stop_event.wait(0.1):
                    raise UdsError(f"ISO-TP 수신 실패 ({label}): 사용자에 의해 중단됨")
                attempt += 1
                continue
            raise UdsError(
                f"UDS Negative Response ({label}): NRC=0x{result['nrc']:02X}",
                nrc=result["nrc"],
            )

    def _send_tester_present(self) -> None:
        """2초 keep-alive (suppress, 기능주소 -- 응답을 기다리지 않는다)."""
        request = build_tester_present(suppress_pos_rsp=True)
        try:
            self._isotp_send(self._can, FUNCTIONAL_REQUEST_ID, FUNCTIONAL_REQUEST_ID,
                             request, is_extended_id=False)
        except Exception as exc:
            self._log(level="WARN", msg=f"TesterPresent 전송 실패(무시): {exc}")

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise UdsError("사용자에 의해 중단됨")

    # ---- Internal: run -------------------------------------------------

    def _run(self) -> None:
        reader = can.BufferedReader()
        self._can.notifier.add_listener(reader)
        self._reader = reader
        try:
            self._run_upload()
        except Exception as exc:
            logger.exception("sysLog upload failed")
            self._log(level="ERROR", msg=f"취득 실패: {exc}")
            with self._lock:
                self._error_message = str(exc)
                self._state = STATE_ERROR
        finally:
            self._reader = None
            try:
                self._can.notifier.remove_listener(reader)
            except Exception:
                pass
            with self._lock:
                self._running = False
            try:
                self._can.clear_rx()
            except Exception:
                pass

    def _run_upload(self) -> None:
        self._log(level="INFO", msg="========== sysLog 취득 시작 ==========")
        self._log(level="INFO", msg=f"CAN 통신: TX=0x{self._request_id:X}, RX=0x{self._response_id:X}, addr=0x{self._address:08X}")

        # 1. 세션 전환 (10 03)
        self._update_progress(phase="세션 전환")
        resp = self._uds_exchange(build_session_control(0x03), REQ_TIMEOUT_S, "SessionControl(0x03)")
        if not resp or resp[0] != 0x50:
            raise UdsError(f"세션 전환 응답 이상: {resp.hex(' ').upper() if resp else '(없음)'}")
        self._log(level="INFO", msg="세션 전환 성공 (10 03 -> 50 03)")

        # 2. 로그 메모리 크기 (22 F1 20)
        self._update_progress(phase="크기 조회")
        resp = self._uds_exchange(build_read_data_by_id(0xF120), REQ_TIMEOUT_S, "ReadData(F1 20)")
        total_size = parse_memory_size_did(resp)
        if total_size <= 0:
            raise UdsError(f"메모리 크기 응답 이상: {resp.hex(' ').upper()}")
        if total_size > MAX_UPLOAD_BYTES:
            raise UdsError(f"메모리 크기 과다({total_size} bytes, 상한 {MAX_UPLOAD_BYTES}) -- 중단")
        self._log(level="INFO", msg=f"로그 메모리 크기: {total_size} bytes (0x{total_size:X})")
        self._update_progress(total_bytes=total_size)

        # 3. SecurityAccess (체크 시만)
        if getattr(self, "_security_enable", True):
            self._execute_security_access()
        else:
            self._log(level="INFO", msg="Security 미사용 (체크 해제) -- ASK 생략")

        # 4. RequestUpload (35 00 44 <addr> <size>)
        self._update_progress(phase="업로드 요청")
        resp = self._uds_exchange(
            build_request_upload(0x00, 0x44, self._address, total_size),
            REQ_TIMEOUT_S, "RequestUpload",
        )
        info = parse_request_upload_response(resp)
        if info["max_length"] <= 2:
            raise UdsError(f"RequestUpload 응답 이상: {resp.hex(' ').upper()}")
        # maxNumberOfBlockLength = SID(1) + seq(1) + data
        data_len = info["max_length"] - 2
        total_blocks = (total_size + data_len - 1) // data_len
        self._log(level="INFO",
                  msg=f"RequestUpload 성공: maxBlockLength={info['max_length']} "
                      f"(블록당 {data_len}B, 총 {total_blocks}블록)")
        self._update_progress(total_blocks=total_blocks)

        # 5. TransferData 반복 (36 ZZ -> 76 ZZ + data)
        self._update_progress(phase="수신 중")
        received = bytearray()
        last_tp_ts = time.time()
        seq = 1
        while len(received) < total_size:
            self._check_stop()
            now = time.time()
            if now - last_tp_ts >= TESTER_PRESENT_INTERVAL_S:
                self._send_tester_present()
                last_tp_ts = now
            seq_byte = seq & 0xFF
            request = bytes([0x36, seq_byte])
            label = f"TransferData(seq={seq_byte}, {len(received)}/{total_size})"
            for retry in range(TRANSFER_BLOCK_MAX_RETRIES + 1):
                try:
                    resp = self._uds_exchange(request, BLOCK_TIMEOUT_S, label)
                    break
                except UdsError as exc:
                    if self._stop_event.is_set():
                        raise UdsError("사용자에 의해 전송 중단됨")
                    retryable = exc.nrc != 0 or _is_timeout_error(exc)
                    if not retryable or retry >= TRANSFER_BLOCK_MAX_RETRIES:
                        raise
                    reason = f"NRC=0x{exc.nrc:02X}" if exc.nrc != 0 else "응답 시간초과"
                    self._log(level="WARN", msg=f"블록 {seq_byte} {reason}, 재요청 {retry + 1}/{TRANSFER_BLOCK_MAX_RETRIES}")
                    if self._stop_event.wait(timeout=TRANSFER_BLOCK_RETRY_DELAY_S):
                        raise UdsError("사용자에 의해 전송 중단됨")
            if len(resp) < 2 or resp[0] != 0x76 or resp[1] != seq_byte:
                raise UdsError(f"TransferData 응답 이상(seq={seq_byte}): {resp.hex(' ').upper()[:60]}")
            chunk = bytes(resp[2:])
            if not chunk:
                raise UdsError(f"TransferData 빈 데이터(seq={seq_byte})")
            received.extend(chunk[: total_size - len(received)])
            seq += 1
            pct = min(100.0, len(received) / total_size * 100.0)
            self._update_progress(current_block=seq - 1, received_bytes=len(received),
                                  percent=round(pct, 1))
            if (seq - 1) % 50 == 0:
                self._log(level="INFO", msg=f"수신 진도: {len(received)}/{total_size} bytes ({pct:.1f}%)")

        # 6. RequestTransferExit (37 -> 77)
        self._update_progress(phase="전송 종료")
        resp = self._uds_exchange(build_request_transfer_exit(), REQ_TIMEOUT_S, "RequestTransferExit")
        if not resp or resp[0] != 0x77:
            raise UdsError(f"전송 종료 응답 이상: {resp.hex(' ').upper() if resp else '(없음)'}")

        # 7. 저장
        self._update_progress(phase="저장")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        filename = f"syslog_upload_{time.strftime('%Y%m%d_%H%M%S')}.bin"
        path = self._log_dir / filename
        path.write_bytes(bytes(received))
        with self._lock:
            self._saved_filename = filename
            self._saved_size = len(received)
            self._state = STATE_COMPLETED
        self._update_progress(percent=100.0)
        self._log(level="INFO", msg=f"===== 취득 완료: {filename} ({len(received)} bytes) =====")

    def _execute_security_access(self) -> None:
        """27 11 -> seed -> 키 생성(DLL/dummy) -> 27 12."""
        self._update_progress(phase="보안 인증")
        resp = self._uds_exchange(
            build_security_access_request_seed(SECURITY_ACCESS_MODE_SEED),
            REQ_TIMEOUT_S, "RequestSeed",
        )
        if len(resp) < 3 or resp[0] != 0x67 or resp[1] != SECURITY_ACCESS_MODE_SEED:
            raise UdsError(f"Seed 응답 이상: {resp.hex(' ').upper()}")
        seed = bytes(resp[2:])
        self._log(level="INFO", msg=f"Seed 수신: {seed.hex()} ({len(seed)} bytes)")

        if self._seedkey_service is not None and getattr(self._seedkey_service, "loaded", False):
            try:
                key = bytes(self._seedkey_service.generate_key(seed))
            except Exception as exc:
                raise UdsError(f"SeedKey 키 생성 실패: {exc}")
            self._log(level="INFO", msg=f"키 생성 완료 (SeedKey DLL): {key.hex()}")
        else:
            key = bytes(generate_key(seed))
            self._log(level="WARN", msg=f"[더미 키] SeedKey DLL 미로드: {key.hex()}")

        resp = self._uds_exchange(
            build_security_access_send_key(key, SECURITY_ACCESS_MODE_KEY),
            REQ_TIMEOUT_S, "SendKey",
        )
        if len(resp) < 2 or resp[0] != 0x67 or resp[1] != SECURITY_ACCESS_MODE_KEY:
            raise UdsError(f"Key 응답 이상(NRC 가능): {resp.hex(' ').upper()}")
        self._log(level="INFO", msg="보안 인증 성공 (Key accepted)")
