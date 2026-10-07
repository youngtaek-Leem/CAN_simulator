"""CAN Simulator MCP server (Model Context Protocol).

Exposes the full backend feature set (~120 REST endpoints, see main.py) as
~40 MCP tools so any MCP-capable AI client can drive the simulator:
connect, DBC inspect, TX signals, replay, ISO-TP, test runner, UDS SWDL,
OTA tester, power supply, audio, syslog/canlog analysis, layouts.

Transport:
  * Streamable HTTP (remote/generic clients incl. web AI, Copilot):
    served by main.py at /mcp via _McpProxy ASGI routes
    (session manager entered in main.py lifespan) -> 42 tools
  * stdio (Claude Desktop / Claude Code local):  backend/mcp_stdio.py

Design notes (see docs/MCP_INTEGRATION.md):
  * No circular import: this module never imports main. Services are
    injected via bind_services() (called once by main.py after its
    globals are created; tests bind their own virtual-bus instances).
  * Thin wrappers: each tool mirrors its REST handler's guards
    (_require_running / connected / DBC loaded) and delegates to the
    same service object, so behavior is identical to the GUI path.
  * Destructive ops (power output, UDS flash, OTA run, SeedKey DLL load,
    server shutdown) require confirm=True (code-enforced, not prompt-based).
  * MCP has no file-upload primitive and cannot receive the WebSocket
    push stream: binary artifacts are passed as server-local `path`,
    live RX is polled via canlog_query(what=frames) / can_status().

Run standalone (dev/inspector):
  cd backend && .venv/bin/python mcp_server.py   # Streamable HTTP on :8001
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "MCP SDK가 설치되어 있지 않습니다: backend/.venv/bin/pip install -r requirements.txt "
        "(mcp>=1.12,<2 필요)"
    ) from exc

logger = logging.getLogger("cansim.mcp")

# streamable_http_path="/": 이 앱은 main.py의 FastAPI에 _McpProxy ASGI
# 라우트(/mcp, /mcp/{rest})로 임베드되므로 내부 경로는 "/"여야 한다
# (기본값 "/mcp"를 두면 /mcp/mcp 이중 prefix가 된다).
# 단독 실행(하단 __main__, :8001) 시에는 엔드포인트가 http://127.0.0.1:8001/ 이다.
mcp = FastMCP("can-simulator", streamable_http_path="/")

# ---- service binder (avoids circular import with main.py) -----------------


_SERVICES: dict[str, Any] = {}


def bind_services(**kwargs: Any) -> None:
    """Inject backend singletons. main.py calls this once; tests bind fakes.

    Expected keys: can_manager, dbc_service, tx_scheduler, replay_service,
    log_service, power_supply_service, audio_service, syslog_service,
    can_log_service, test_runner_service, seedkey_service,
    uds_download_manager, ota_tester_manager, syslog_upload_manager,
    settings (dict), run_state (dict), layout_dir (Path), base_dir (Path).
    """
    _SERVICES.update(kwargs)


def _svc(name: str) -> Any:
    try:
        return _SERVICES[name]
    except KeyError:
        raise RuntimeError(
            f"MCP 서비스 '{name}'가 바인딩되지 않았습니다 "
            "(main.py의 bind_services() 호출 또는 테스트 바인딩 필요)"
        )


# ---- guards (mirror main.py handler preconditions) ------------------------


def _require_running() -> None:
    if not _SERVICES.get("run_state", {"running": True})["running"]:
        raise RuntimeError("전체 송수신이 정지 상태입니다 (run_start 도구를 먼저 호출하세요)")


def _require_connected() -> None:
    if not _svc("can_manager").connected:
        raise RuntimeError("CAN bus is not connected (can_connect 도구를 먼저 호출하세요)")


def _require_dbc() -> None:
    if not _svc("dbc_service").loaded:
        raise RuntimeError("DBC가 로드되지 않았습니다 (artifact_load target=dbc를 먼저 호출하세요)")


def _require_confirm(confirm: bool, op: str) -> None:
    """파괴적 동작 코드 강제 게이트: Power·UDS 플래시·OTA·DLL·shutdown."""
    if confirm is not True:
        raise RuntimeError(
            f"파괴적 동작 '{op}'은(는) 사전 확인이 필요합니다. "
            "영향 범위를 확인한 뒤 confirm=True로 다시 호출하세요."
        )
    logger.warning("MCP destructive op confirmed: %s", op)


def _ok(extra: Optional[dict] = None, **kwargs: Any) -> dict:
    out: dict[str, Any] = {"ok": True}
    out.update(kwargs)
    if extra:
        out.update(extra)
    return out


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("텍스트 디코딩 실패 (utf-8/cp1252/latin-1 모두 실패)")


def _read_text_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"파일이 없습니다: {path}")
    return _decode_text(p.read_bytes())


# =====================================================================
# Tier 0 — 연결 / 상태
# =====================================================================


@mcp.tool()
def can_connect(
    interface: str = "virtual",
    channel: str = "test",
    bitrate: int = 500000,
    receive_own_messages: bool = True,
    fd: bool = False,
    data_bitrate: int = 2000000,
) -> dict:
    """CAN 버스에 연결한다. interface: virtual(하드웨어 불필요)|pcan|vector.
    channel 예: virtual=test, pcan=PCAN_USBBUS1, vector=0.
    fd=True면 CAN-FD + data_bitrate(1/2/4/5/8 Mbit/s). 대응: POST /api/connect"""
    _svc("log_service").stop()
    try:
        return _svc("can_manager").connect(
            interface, channel, bitrate, receive_own_messages,
            fd=fd, data_bitrate=data_bitrate,
        )
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def can_disconnect() -> dict:
    """CAN 버스 연결 해제 + 스케줄러/replay/runner 정지. 대응: POST /api/disconnect"""
    _svc("test_runner_service").stop()
    _svc("replay_service").stop()
    _svc("log_service").stop()
    _svc("tx_scheduler").stop()
    _svc("tx_scheduler").stop_auto()
    _svc("can_manager").disconnect()
    return can_status()


@mcp.tool()
def can_status() -> dict:
    """전체 상태 요약 (can/tx/replay/dbc/run/test_runner/uds/ota/power/audio/log).
    폴링용 — MCP는 WebSocket 푸시를 받을 수 없으므로 주기 조회로 대체한다.
    대응: GET /api/status"""
    return {
        "can": _svc("can_manager").status(),
        "tx": _svc("tx_scheduler").status(),
        "replay": _svc("replay_service").info(),
        "dbc": {
            "loaded": _svc("dbc_service").loaded,
            "filename": _svc("dbc_service").filename,
        },
        "settings": dict(_SERVICES.get("settings", {})),
        "run": dict(_SERVICES.get("run_state", {})),
        "test_runner": _svc("test_runner_service").summary(),
        "uds": _svc("uds_download_manager").all_status(tail=50),
        "ota_tester": _svc("ota_tester_manager").status(tail=100),
        "power": _svc("power_supply_service").info(),
        "audio": _svc("audio_service").info(),
        "log": _svc("log_service").status(),
    }


@mcp.tool()
def run_start() -> dict:
    """전역 Start — 깨끗한 재시작 (이전 auto 주기송신은 초기화됨). 대응: POST /api/run/start"""
    _svc("tx_scheduler").stop_auto()
    _SERVICES.get("run_state", {"running": True})["running"] = True
    _svc("tx_scheduler").set_paused(False)
    return can_status()


@mcp.tool()
def run_stop() -> dict:
    """전역 Stop — 주기송신·replay·runner·SWDL·OTA 전체 정지. 대응: POST /api/run/stop"""
    _SERVICES.get("run_state", {"running": True})["running"] = False
    _svc("tx_scheduler").set_paused(True)
    _svc("tx_scheduler").stop_auto()
    _svc("replay_service").stop()
    _svc("test_runner_service").stop()
    uds_mgr = _svc("uds_download_manager")
    for i in range(uds_mgr.NUM_SLOTS):
        mgr = uds_mgr.get_manager(i)
        if mgr.running:
            mgr.stop()
    if _svc("ota_tester_manager").running:
        _svc("ota_tester_manager").stop()
    return can_status()


# =====================================================================
# DBC
# =====================================================================


@mcp.tool()
def dbc_summary() -> dict:
    """로드된 DBC 요약 (메시지/신호 목록, TX/RX senders, Event/Periodic 분류).
    대응: GET /api/dbc"""
    return _svc("dbc_service").summary()


@mcp.tool()
def dbc_signal_info(message_name: str, signal_name: str) -> dict:
    """특정 신호의 상세 정보 (bit/범위/단위/VAL_ 선택지/sendType/initial).
    dbc_summary에서 message_name/signal_name을 먼저 확인하라."""
    dbc = _svc("dbc_service")
    summary = dbc.summary()
    for msg in summary.get("messages", []):
        if msg.get("name") == message_name:
            for sig in msg.get("signals", []):
                if sig.get("name") == signal_name:
                    info = dict(sig)
                    info["message"] = message_name
                    try:
                        info["send_type"] = dbc.signal_send_type(message_name, signal_name)
                    except Exception:
                        pass
                    return info
            raise RuntimeError(f"신호 없음: {message_name}.{signal_name}")
    raise RuntimeError(f"메시지 없음: {message_name}")


@mcp.tool()
def dbc_set_send_type(message_name: str, signal_name: str, send_type: str) -> dict:
    """신호의 Event/Periodic 분류를 수동 override (태그 자동판별보다 우선).
    send_type: event|periodic. 대응: POST /api/dbc/send-type"""
    if send_type not in ("event", "periodic"):
        raise RuntimeError("send_type은 event|periodic 중 하나여야 합니다")
    try:
        _svc("dbc_service").set_send_type_override(message_name, signal_name, send_type)
    except ValueError as exc:
        raise RuntimeError(str(exc))
    return dbc_summary()


@mcp.tool()
def dbc_message_initial(message_name: str) -> dict:
    """DBC 메시지의 초기값 페이로드 (GenSigStartValue, 없으면 Invalid 채움).
    대응: GET /api/dbc/messages/{name}/initial"""
    _require_dbc()
    dbc = _svc("dbc_service")
    try:
        message = dbc.get_message(message_name)
        data = dbc.encode_initial(message_name)
    except (ValueError, KeyError, AttributeError) as exc:
        raise RuntimeError(f"초기값을 구할 수 없습니다: {exc}")
    return {
        "message_name": message_name,
        "length": message.length,
        "is_fd": message.is_fd,
        "data_hex": data.hex(" ").upper(),
    }


# =====================================================================
# 아티팩트 로드 (업로드 엔드포인트 통합)
# =====================================================================


@mcp.tool()
def artifact_load(
    target: str,
    content: Optional[str] = None,
    path: Optional[str] = None,
    filename: Optional[str] = None,
    slot_index: int = 0,
    case_id: Optional[str] = None,
    label: Optional[str] = None,
    kind: str = "testBlock",
    order: int = 0,
) -> dict:
    """파일 아티팩트 로드 — 10종 업로드 API 통합.
    target: dbc|replay|testrunner_script|testrunner_functions|uds_xml|uds_binary|
      ota_xml|ota_binary|syslog_log|syslog_db|canlog
    텍스트 기반(dbc, testrunner_*, syslog_db, ota_xml)은 content 문자열 직접 전달 가능.
    바이너리(BLF/ASC는 텍스트, BLF 바이너리·BIN·DLL·WAV·로그 바이너리)는 서버 로컬 path 필수
    (MCP에 파일 업로드プリミ티브가 없으므로 운영자가 서버 PC에 파일을 둔다).
    ota_xml에는 case_id 필수. uds_*에는 slot_index(0~2) 사용."""
    if (content is None) == (path is None):
        raise RuntimeError("content와 path 중 정확히 하나를 지정하세요")
    text = content if content is not None else _read_text_file(path)
    fname = filename or (Path(path).name if path else "mcp_upload")
    if target == "dbc":
        try:
            return _svc("dbc_service").load_string(text, fname)
        except Exception as exc:
            raise RuntimeError(f"DBC parse error: {exc}")
    if target == "testrunner_script":
        return _svc("test_runner_service").load(text, fname)
    if target == "testrunner_functions":
        return _svc("test_runner_service").load_functions(text, fname)
    if target == "syslog_db":
        return _svc("syslog_service").load_db(text, fname)
    if target == "syslog_log":
        raw = text.encode("utf-8") if content is not None else Path(path).read_bytes()
        return _svc("syslog_service").load_log(raw, fname)
    if target == "canlog":
        if path is None:
            raise RuntimeError("canlog는 서버 로컬 path로만 로드할 수 있습니다 (바이너리 가능)")
        raw = Path(path).read_bytes()
        return _svc("can_log_service").load_log(raw, Path(path).name)
    if target == "replay":
        if path is None:
            raise RuntimeError("replay 로그는 서버 로컬 path로만 로드할 수 있습니다")
        return _svc("replay_service").load(path, Path(path).name)
    if target == "uds_xml":
        if path is None:
            raise RuntimeError("UDS XML은 서버 로컬 path로만 로드할 수 있습니다")
        mgr = _svc("uds_download_manager").get_manager(slot_index)
        return mgr.load_xml(path)
    if target == "uds_binary":
        if path is None:
            raise RuntimeError("UDS 바이너리는 서버 로컬 path로만 로드할 수 있습니다")
        mgr = _svc("uds_download_manager").get_manager(slot_index)
        return mgr.load_binary(path)
    if target == "ota_xml":
        if case_id is None:
            raise RuntimeError("ota_xml에는 case_id가 필수입니다")
        if path is None:
            if content is None:
                raise RuntimeError("ota_xml에는 content 또는 path가 필요합니다")
            tmp = _SERVICES.get("base_dir", Path(".")) / "uploads" / "ota_tester" / f"{case_id}_mcp.xml"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content, encoding="utf-8")
            path = str(tmp)
        try:
            return _svc("ota_tester_manager").add_case(
                case_id, label or case_id, kind, path, order, True
            )
        except Exception as exc:
            raise RuntimeError(f"XML 파싱 오류: {exc}")
    if target == "ota_binary":
        if case_id is None or path is None:
            raise RuntimeError("ota_binary에는 case_id와 서버 로컬 path가 필수입니다")
        try:
            return _svc("ota_tester_manager").set_case_binary(case_id, path)
        except Exception as exc:
            raise RuntimeError(f"BIN 파일 로드 오류: {exc}")
    raise RuntimeError(
        "target은 dbc|replay|testrunner_script|testrunner_functions|uds_xml|uds_binary|"
        "ota_xml|ota_binary|syslog_log|syslog_db|canlog 중 하나여야 합니다"
    )


# =====================================================================
# Tier 1 — TX
# =====================================================================


@mcp.tool()
def tx_signal(
    message_name: str,
    values: dict,
    values_alt: Optional[dict] = None,
    once: bool = False,
    random_signals: Optional[list] = None,
) -> dict:
    """DBC 신호값 전송. values 예: {"EngineSpeed": 2000}.
    Event 신호: 유효값 전송 + 30ms 후 invalid(비트최대값) 자동 전송, 같은 메시지의 다른
    신호는 매번 invalid로 강제됨. Periodic 신호: DBC 주기로 자동 주기송신 시작.
    once=True면 1회만 (TX박스 Send 버튼). 대응: POST /api/tx/signal"""
    _require_running()
    _require_connected()
    _require_dbc()
    try:
        return _svc("tx_scheduler").send_signal(
            message_name, values, values_alt, once, random_signals
        )
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def tx_signal_pulse(message_name: str, values: dict, mode: str = "invalid_first") -> dict:
    """Periodic 신호 전용 펄스. mode=invalid_first: Invalid 즉시+30ms 후 설정값.
    mode=zero_after: 설정값 즉시+30ms 후 raw 0. 대응: .../invalid_first·zero_after"""
    _require_running()
    _require_connected()
    _require_dbc()
    try:
        if mode == "invalid_first":
            return _svc("tx_scheduler").send_signal_invalid_first(message_name, values)
        if mode == "zero_after":
            return _svc("tx_scheduler").send_signal_zero_after(message_name, values)
        raise RuntimeError("mode는 invalid_first|zero_after 중 하나여야 합니다")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def tx_send_once(
    arbitration_id: int,
    data_hex: str = "",
    is_extended: bool = False,
    is_fd: bool = False,
    bitrate_switch: bool = False,
) -> dict:
    """Raw 1회 전송 (DBC 불필요). data_hex 예: "11 22 33". Classic 버스(FD 미연결)에서
    8바이트 초과 시 거부된다. 대응: POST /api/tx/send_once"""
    _require_running()
    _require_connected()
    try:
        data = bytes.fromhex(data_hex) if data_hex else b""
    except ValueError as exc:
        raise RuntimeError(f"잘못된 hex 데이터: {exc}")
    try:
        return _svc("tx_scheduler").send_once(
            arbitration_id, data, is_extended, is_fd, bitrate_switch, None
        )
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def tx_table_configure(entries: list) -> dict:
    """주기송신 테이블 전체 설정 (최대 20행, TX박스). 대응: POST /api/tx/configure"""
    try:
        return _svc("tx_scheduler").configure(entries)
    except (ValueError, KeyError) as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def tx_table_control(action: str) -> dict:
    """주기송신 테이블 start|stop. 대응: POST /api/tx/start·stop"""
    if action == "start":
        _require_running()
        _require_connected()
        return _svc("tx_scheduler").start()
    if action == "stop":
        return _svc("tx_scheduler").stop()
    raise RuntimeError("action은 start|stop 중 하나여야 합니다")


@mcp.tool()
def tx_row_control(
    action: str,
    key: str,
    message_name: Optional[str] = None,
    values: Optional[dict] = None,
    values_alt: Optional[dict] = None,
    period_ms: Optional[float] = None,
    data_hex: Optional[str] = None,
    arbitration_id: Optional[int] = None,
    is_extended: bool = False,
    is_fd: bool = False,
    bitrate_switch: bool = False,
) -> dict:
    """TX박스 단일 행의 주기송신 start|stop|update. 대응: POST /api/tx/row/*"""
    sched = _svc("tx_scheduler")
    try:
        if action == "start":
            _require_running()
            _require_connected()
            return sched.row_periodic_start(
                key, message_name, values, values_alt,
                period_ms if period_ms is not None else 100.0,
                data_hex, arbitration_id, is_extended, is_fd, bitrate_switch, None,
            )
        if action == "stop":
            return sched.row_periodic_stop(key)
        if action == "update":
            return sched.row_periodic_update(
                key, message_name, values, values_alt, period_ms, None
            )
        raise RuntimeError("action은 start|stop|update 중 하나여야 합니다")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def tx_periodic_all(action: str, rx_node: str = "") -> dict:
    """전체 Periodic 메시지 일괄 송신 enable|disable (RX 노드 제외).
    대응: POST /api/tx/periodic/enable_all·disable_all"""
    if action == "enable":
        _require_running()
        _require_connected()
        _require_dbc()
        try:
            return _svc("tx_scheduler").enable_all_periodic(rx_node)
        except Exception as exc:
            raise RuntimeError(str(exc))
    if action == "disable":
        return _svc("tx_scheduler").disable_all_periodic()
    raise RuntimeError("action은 enable|disable 중 하나여야 합니다")


@mcp.tool()
def tx_auto_stop(message_name: Optional[str] = None) -> dict:
    """자동 주기송신 항목 제거 (None=전체, 지정 시 해당 메시지만).
    대응: POST /api/tx/auto/stop"""
    return _svc("tx_scheduler").stop_auto(message_name)


@mcp.tool()
def tx_generator(
    action: str,
    message_name: str,
    signal_name: str,
    mode: Optional[str] = None,
    range_min: Optional[int] = None,
    range_max: Optional[int] = None,
    step: int = 1,
    period_ms: Optional[float] = None,
) -> dict:
    """값 생성기(Random/Range) 제어. action=set(생성기 설정: mode=fixed|random|range),
    send(생성값 1회 전송), stop(생성기 해제), invalid(Invalid 1회 전송),
    event_start(주기 Random+Event규칙 시작, period_ms 필요), event_stop(중지).
    대응: POST /api/tx/signal/generator·generate·invalid·event_periodic..."""
    _require_dbc()
    sched = _svc("tx_scheduler")
    try:
        if action == "set":
            if mode not in ("fixed", "random", "range"):
                raise RuntimeError("set에는 mode=fixed|random|range가 필요합니다")
            sched.set_value_generator(
                message_name, signal_name, mode, range_min, range_max, step
            )
            return _ok()
        if action == "send":
            _require_running()
            _require_connected()
            return sched.send_generated(message_name, signal_name)
        if action == "stop":
            return sched.stop_generated(message_name, signal_name)
        if action == "invalid":
            _require_running()
            _require_connected()
            return sched.send_invalid(message_name, signal_name)
        if action == "event_start":
            _require_running()
            _require_connected()
            if period_ms is None:
                raise RuntimeError("event_start에는 period_ms가 필요합니다")
            return sched.start_event_periodic(message_name, signal_name, period_ms)
        if action == "event_stop":
            return sched.stop_event_periodic(message_name, signal_name)
        raise RuntimeError("action은 set|send|stop|invalid|event_start|event_stop 중 하나여야 합니다")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc))


# =====================================================================
# ISO-TP
# =====================================================================


def _collect_isotp_response(
    can_manager: Any, isotp_mod: Any, tx_id: int, resp_id: int,
    is_extended_id: bool, resp_timeout_s: float,
    resp_fc_stmin: int, resp_fc_block_size: int, reader: Any,
) -> dict:
    """main.py _collect_responses port: NRC 0x78 pending 추적 포함 응답 수집."""
    import time as _time

    def _is_nrc78(payload: bytes) -> bool:
        return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78

    deadline = _time.perf_counter() + resp_timeout_s
    responses: list[bytes] = []
    error: Optional[str] = None
    for _ in range(100):
        remaining = deadline - _time.perf_counter()
        if remaining <= 0:
            break
        try:
            response = isotp_mod.receive(
                can_manager, resp_id, tx_id, timeout_s=remaining,
                is_extended_id=is_extended_id, fc_stmin=resp_fc_stmin,
                fc_block_size=resp_fc_block_size, reader=reader,
            )
        except isotp_mod.IsoTpError as exc:
            error = str(exc)
            break
        responses.append(response)
        if _is_nrc78(response):
            deadline = _time.perf_counter() + resp_timeout_s
            continue
        break
    if not responses:
        raise isotp_mod.IsoTpError(error or "응답 프레임을 기다리다 시간 초과되었습니다")
    out = {
        "response": responses[0].hex(" ").upper(),
        "responses": [r.hex(" ").upper() for r in responses],
    }
    if error:
        out["response_error"] = error
    return out


@mcp.tool()
def isotp_send(
    tx_id: int,
    fc_id: int,
    data_hex: str,
    is_extended_id: bool = False,
    fc_timeout_ms: int = 1000,
    max_wait_frames: int = 10,
    resp_id: Optional[int] = None,
    resp_timeout_ms: float = 2000.0,
    resp_fc_stmin: int = 0,
    resp_fc_block_size: int = 0,
) -> dict:
    """ISO-TP 전송 (7B 이하 SF, 이상 FF+FC+CF, 8B 패딩). data_hex 예: "10 03".
    resp_id 지정 시 응답 대기(NRC78 추적)까지 수행. 대응: POST /api/isotp/send"""
    import can
    import isotp_service

    _require_running()
    _require_connected()
    can_manager = _svc("can_manager")
    hex_str = data_hex.replace(" ", "").replace("\n", "").replace("\t", "")
    if len(hex_str) % 2 != 0:
        raise RuntimeError("데이터 hex 문자열의 길이가 홀수입니다")
    try:
        data = bytes.fromhex(hex_str)
    except ValueError as exc:
        raise RuntimeError(f"잘못된 hex 데이터: {exc}")
    if resp_id is None:
        try:
            return isotp_service.send(
                can_manager, tx_id, fc_id, data,
                is_extended_id=is_extended_id,
                fc_timeout_s=fc_timeout_ms / 1000.0,
                max_wait_frames=max_wait_frames,
            )
        except isotp_service.IsoTpError as exc:
            raise RuntimeError(str(exc))
    if can_manager.notifier is None:
        raise RuntimeError("CAN 버스가 연결되어 있지 않습니다")
    reader = can.BufferedReader()
    can_manager.notifier.add_listener(reader)
    try:
        try:
            result = isotp_service.send(
                can_manager, tx_id, fc_id, data,
                is_extended_id=is_extended_id,
                fc_timeout_s=fc_timeout_ms / 1000.0,
                max_wait_frames=max_wait_frames, reader=reader,
            )
        except isotp_service.IsoTpError as exc:
            raise RuntimeError(str(exc))
        try:
            result.update(_collect_isotp_response(
                can_manager, isotp_service, tx_id, resp_id,
                is_extended_id, resp_timeout_ms / 1000.0,
                resp_fc_stmin, resp_fc_block_size, reader,
            ))
        except isotp_service.IsoTpError as exc:
            result["response_error"] = str(exc)
        return result
    finally:
        can_manager.notifier.remove_listener(reader)


@mcp.tool()
def isotp_security_access(
    tx_id: int,
    fc_id: int,
    resp_id: int,
    is_extended_id: bool = False,
    fc_timeout_ms: int = 1000,
    resp_timeout_ms: int = 2000,
    seed_level: int = 17,
    key_level: int = 18,
) -> dict:
    """UDS 27 11 SeedKey 자동 인증 (27 seed→67 seed 수신→DLL/미로드시 dummy 키 생성→27 key).
    transcript 반환. 대응: POST /api/isotp/security-access"""
    import can
    import isotp_service
    from uds_core import generate_key as _dummy_generate_key

    _require_running()
    _require_connected()
    can_manager = _svc("can_manager")
    seedkey_service = _svc("seedkey_service")
    if can_manager.notifier is None:
        raise RuntimeError("CAN 버스가 연결되어 있지 않습니다")
    seed_req = bytes([0x27, seed_level & 0xFF])
    transcript: dict = {
        "seed_request": seed_req.hex(" ").upper(),
        "seed_level": seed_level, "key_level": key_level,
    }
    reader = can.BufferedReader()
    can_manager.notifier.add_listener(reader)
    try:
        try:
            isotp_service.send(
                can_manager, tx_id, fc_id, seed_req,
                is_extended_id=is_extended_id,
                fc_timeout_s=fc_timeout_ms / 1000.0, reader=reader,
            )
        except isotp_service.IsoTpError as exc:
            raise RuntimeError(f"Seed 요청 송신 실패: {exc}")
        seed_result: dict = {"sent": True}
        try:
            seed_result.update(_collect_isotp_response(
                can_manager, isotp_service, tx_id, resp_id,
                is_extended_id, resp_timeout_ms / 1000.0, 0, 0, reader,
            ))
        except isotp_service.IsoTpError as exc:
            raise RuntimeError(f"Seed 응답 수신 실패: {exc}")
        transcript["seed_responses"] = seed_result["responses"]
        seed_resp = bytes.fromhex(seed_result["response"].replace(" ", ""))
        if len(seed_resp) < 3 or seed_resp[0] != 0x67 or seed_resp[1] != (seed_level & 0xFF):
            raise RuntimeError(f"Seed 요청 거부/이상 응답: {seed_result['response']}")
        seed = bytes(seed_resp[2:])
        transcript["seed_hex"] = seed.hex(" ").upper()
        if seedkey_service.loaded:
            try:
                key = bytes(seedkey_service.generate_key(seed))
                transcript["key_source"] = "dll"
            except Exception as exc:
                key = bytes(_dummy_generate_key(seed))
                transcript["key_source"] = f"dummy (DLL 실패: {exc})"
        else:
            key = bytes(_dummy_generate_key(seed))
            transcript["key_source"] = "dummy (DLL 미로드)"
        transcript["key_hex"] = key.hex(" ").upper()
        key_req = bytes([0x27, key_level & 0xFF]) + key
        transcript["key_request"] = key_req.hex(" ").upper()
        try:
            isotp_service.send(
                can_manager, tx_id, fc_id, key_req,
                is_extended_id=is_extended_id,
                fc_timeout_s=fc_timeout_ms / 1000.0, reader=reader,
            )
        except isotp_service.IsoTpError as exc:
            raise RuntimeError(f"Key 송신 실패: {exc}")
        key_result: dict = {"sent": True}
        try:
            key_result.update(_collect_isotp_response(
                can_manager, isotp_service, tx_id, resp_id,
                is_extended_id, resp_timeout_ms / 1000.0, 0, 0, reader,
            ))
        except isotp_service.IsoTpError as exc:
            transcript["key_responses"] = []
            transcript["response_error"] = f"Key 응답 수신 실패: {exc}"
            return transcript
        transcript["key_responses"] = key_result["responses"]
        if "response_error" in key_result:
            transcript["response_error"] = key_result["response_error"]
        return transcript
    finally:
        can_manager.notifier.remove_listener(reader)


# =====================================================================
# Replay
# =====================================================================


@mcp.tool()
def replay_control(
    action: str,
    mode: str = "pass",
    frame_ids: Optional[list] = None,
) -> dict:
    """CAN 로그 replay 제어. action=start|stop|pause|resume.
    start 시 mode=pass(선택 ID만)|stop(선택 ID 제외), frame_ids 미지정=전체.
    대응: POST /api/replay/*"""
    replay = _svc("replay_service")
    if action == "start":
        _require_running()
        _require_connected()
        try:
            return replay.start(mode=mode, frame_ids=frame_ids)
        except Exception as exc:
            raise RuntimeError(str(exc))
    if action == "stop":
        return replay.stop()
    if action == "pause":
        return replay.pause()
    if action == "resume":
        return replay.resume()
    raise RuntimeError("action은 start|stop|pause|resume 중 하나여야 합니다")


# =====================================================================
# TestRunner
# =====================================================================


@mcp.tool()
def testrunner_control(action: str, function_name: Optional[str] = None) -> dict:
    """테스트 Sequence 실행기 제어. action=start|stop|pause|resume|start_function
    (start_function에는 function_name 필요). 대응: POST /api/testrunner/*"""
    runner = _svc("test_runner_service")
    try:
        if action == "start":
            _require_running()
            return runner.start()
        if action == "stop":
            return runner.stop()
        if action == "pause":
            return runner.pause()
        if action == "resume":
            return runner.resume()
        if action == "start_function":
            if not function_name:
                raise RuntimeError("start_function에는 function_name이 필요합니다")
            _require_running()
            return runner.start_function(function_name)
        raise RuntimeError("action은 start|stop|pause|resume|start_function 중 하나여야 합니다")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def testrunner_status() -> dict:
    """테스트 실행 상태 + 단계별 이벤트 로그. 폴링용. 대응: GET /api/testrunner/status"""
    return _svc("test_runner_service").status()


# =====================================================================
# UDS SWDL (start는 파괴적: confirm 필수)
# =====================================================================


@mcp.tool()
def uds_swdl_control(
    action: str,
    slot_index: int = 0,
    slot_indices: Optional[list] = None,
    selected_steps: Optional[list] = None,
    modified_params: Optional[dict] = None,
    step_service: Optional[str] = None,
    params: Optional[dict] = None,
    confirm: bool = False,
) -> dict:
    """CAN-SWDL 제어. action=start(⚠️펌웨어 플래시, confirm=True 필수)|
    stop|stop_all|set_params(step_service+params 필요). 대응: /api/udswdl/*"""
    mgr = _svc("uds_download_manager")
    if action == "start":
        _require_confirm(confirm, "uds_swdl start (ECU 펌웨어 플래시)")
        _require_connected()
        try:
            return {"results": mgr.start_all(
                slot_indices if slot_indices is not None else [slot_index],
                selected_steps=selected_steps, modified_params=modified_params,
            )}
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(str(exc))
    if action == "stop":
        return mgr.get_manager(slot_index).stop()
    if action == "stop_all":
        return {"results": mgr.stop_all(slot_indices if slot_indices is not None else [0, 1, 2])}
    if action == "set_params":
        if not step_service or not params:
            raise RuntimeError("set_params에는 step_service와 params가 필요합니다")
        m = mgr.get_manager(slot_index)
        for k, v in params.items():
            m.set_param(step_service, k, v)
        return m.status()
    raise RuntimeError("action은 start|stop|stop_all|set_params 중 하나여야 합니다")


@mcp.tool()
def uds_swdl_status(tail: Optional[int] = None) -> dict:
    """전 슬롯 SWDL 상태 (이벤트 tail). 폴링용. 대응: GET /api/udswdl/status"""
    return {"slots": _svc("uds_download_manager").all_status(tail=tail)}


@mcp.tool()
def uds_swdl_steps(slot_index: int = 0) -> list:
    """슬롯의 파싱된 절차 단계 목록. 대응: GET /api/udswdl/steps"""
    steps = _svc("uds_download_manager").get_manager(slot_index).get_procedure_steps()
    if steps is None:
        raise RuntimeError("XML이 로드되지 않았습니다 (artifact_load target=uds_xml)")
    return steps


# =====================================================================
# OTA Tester (start는 파괴적: confirm 필수)
# =====================================================================


@mcp.tool()
def ota_tester_control(
    action: str,
    request_id: Optional[int] = None,
    response_id: Optional[int] = None,
    confirm: bool = False,
) -> dict:
    """OTA Tester 실행 start(⚠️ confirm=True 필수)|stop. 대응: POST /api/ota_tester/*"""
    mgr = _svc("ota_tester_manager")
    if action == "start":
        _require_confirm(confirm, "ota_tester start (OTA 다운로드 실행)")
        _require_running()
        _require_connected()
        if request_id is None or response_id is None:
            raise RuntimeError("start에는 request_id와 response_id가 필요합니다")
        try:
            return mgr.start(request_id, response_id)
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(str(exc))
    if action == "stop":
        return mgr.stop()
    raise RuntimeError("action은 start|stop 중 하나여야 합니다")


@mcp.tool()
def ota_tester_status(tail: Optional[int] = None) -> dict:
    """OTA Tester 상태. 폴링용. 대응: GET /api/ota_tester/status"""
    return _svc("ota_tester_manager").status(tail=tail)


@mcp.tool()
def ota_case_manage(
    action: str,
    case_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    selected_steps: Optional[list] = None,
) -> dict:
    """OTA 케이스 관리. action=list|cases(케이스 목록+상태)|clear|
    set_all_enabled(enabled 필요)|enable(case_id+enabled 필요)|
    steps(case_id 필요)|selected_steps(case_id 필요, None=전체).
    대응: /api/ota_tester/case/*"""
    mgr = _svc("ota_tester_manager")
    if action in ("list", "cases"):
        return {"cases": mgr.cases, "status": mgr.status(tail=20)}
    if action == "clear":
        try:
            return mgr.clear_cases()
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
    if action == "set_all_enabled":
        if enabled is None:
            raise RuntimeError("set_all_enabled에는 enabled가 필요합니다")
        return mgr.set_all_enabled(enabled)
    if action == "enable":
        if case_id is None or enabled is None:
            raise RuntimeError("enable에는 case_id와 enabled가 필요합니다")
        try:
            return mgr.set_case_enabled(case_id, enabled)
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
    if action == "steps":
        if case_id is None:
            raise RuntimeError("steps에는 case_id가 필요합니다")
        try:
            return {"steps": mgr.get_case_steps(case_id)}
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
    if action == "selected_steps":
        if case_id is None:
            raise RuntimeError("selected_steps에는 case_id가 필요합니다")
        try:
            return mgr.set_case_selected_steps(case_id, selected_steps)
        except RuntimeError as exc:
            raise RuntimeError(str(exc))
    raise RuntimeError(
        "action은 list|clear|set_all_enabled|enable|steps|selected_steps 중 하나여야 합니다"
    )


# =====================================================================
# Power (제어는 파괴적: confirm 필수, 측정은 자유)
# =====================================================================


@mcp.tool()
def power_measure() -> dict:
    """전원 실시간 측정값 (읽기 전용, 안전). 대응: GET /api/power/measure"""
    try:
        return _svc("power_supply_service").measure()
    except Exception as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
def power_control(
    op: str,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    command: Optional[str] = None,
    on_voltage: Optional[float] = None,
    on_current: Optional[float] = None,
    on_s: Optional[float] = None,
    off_voltage: Optional[float] = None,
    off_current: Optional[float] = None,
    off_s: Optional[float] = None,
    low: Optional[float] = None,
    high: Optional[float] = None,
    leg_s: Optional[float] = None,
    confirm: bool = False,
) -> dict:
    """⚠️ 실제 전원 출력 변경 — confirm=True 필수.
    op=connect|disconnect(연결 관리, 출력 변경 없음 — confirm 불필요)|
    battery(voltage+current)|acc_ign(command: ACC_On|ACC_Off|IGN_On|IGN_Off|ACC_IGN_On|ACC_IGN_Off)|
    onoff_start(on_voltage/on_current/on_s/off_voltage/off_current/off_s)|onoff_stop|
    sweep_start(low/high/current/leg_s)|sweep_stop. 대응: /api/power/*"""
    ps = _svc("power_supply_service")
    if op == "connect":
        return ps.connect()
    if op == "disconnect":
        return ps.disconnect()
    _require_confirm(confirm, f"power {op} (실제 전원 출력 변경)")
    try:
        if op == "battery":
            if voltage is None or current is None:
                raise RuntimeError("battery에는 voltage와 current가 필요합니다")
            return ps.set_battery(voltage, current)
        if op == "acc_ign":
            if command is None:
                raise RuntimeError("acc_ign에는 command가 필요합니다")
            return ps.set_acc_ign(command)
        if op == "onoff_start":
            if None in (on_voltage, on_current, on_s, off_voltage, off_current, off_s):
                raise RuntimeError("onoff_start에는 on/off 전압·전류·시간 6개가 필요합니다")
            return ps.start_onoff_repeat(
                on_voltage, on_current, on_s, off_voltage, off_current, off_s
            )
        if op == "onoff_stop":
            return ps.stop_onoff_repeat()
        if op == "sweep_start":
            if None in (low, high, current, leg_s):
                raise RuntimeError("sweep_start에는 low/high/current/leg_s가 필요합니다")
            return ps.start_sweep(low, high, current, leg_s)
        if op == "sweep_stop":
            return ps.stop_sweep()
        raise RuntimeError(
            "op은 connect|disconnect|battery|acc_ign|onoff_start|onoff_stop|"
            "sweep_start|sweep_stop 중 하나여야 합니다"
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc))


# =====================================================================
# Audio
# =====================================================================


@mcp.tool()
def audio_probe(
    what: str = "info",
    from_ms: float = 0.0,
    to_ms: float = 1000.0,
    max_points: int = 300,
) -> dict:
    """오디오 조회. what=info(상태)|devices(입력장치 목록)|level(실시간 레벨)|
    waveform(파형 구간, from_ms/to_ms). 대응: GET /api/audio/*"""
    audio = _svc("audio_service")
    if what == "info":
        return audio.info()
    if what == "devices":
        return audio.refresh_devices()
    if what == "level":
        return audio.get_level()
    if what == "waveform":
        return audio.get_waveform(from_ms, to_ms, max_points)
    raise RuntimeError("what은 info|devices|level|waveform 중 하나여야 합니다")


@mcp.tool()
def audio_control(action: str, index: Optional[int] = None) -> dict:
    """오디오 제어. action=select_device(index 필요)|monitor_start|monitor_stop|
    record_start|record_stop. 대응: POST /api/audio/*"""
    from audio_service import generate_monitor_filename

    audio = _svc("audio_service")
    if action == "select_device":
        if index is None:
            raise RuntimeError("select_device에는 index가 필요합니다 (audio_probe devices 참조)")
        return audio.select_device(index)
    if action == "monitor_start":
        return audio.start_monitor()
    if action == "monitor_stop":
        return audio.stop_monitor()
    if action == "record_start":
        return audio.start_widget_recording(generate_monitor_filename())
    if action == "record_stop":
        return audio.stop_widget_recording()
    raise RuntimeError(
        "action은 select_device|monitor_start|monitor_stop|record_start|record_stop 중 하나여야 합니다"
    )


# =====================================================================
# SysLog / CanLog 분석 + 취득 + 일반 로그
# =====================================================================


@mcp.tool()
def syslog_query(
    what: str = "status",
    ids: Optional[list] = None,
    segments: Optional[list] = None,
) -> dict:
    """sysLog 분석 조회. what=status|timeline|ids(segments가 있으면 해당 구간만)|
    series(ids=[...] 필요). 대응: GET /api/syslog/*"""
    svc = _svc("syslog_service")
    if what == "status":
        return svc.status()
    if what == "timeline":
        return svc.timeline()
    if what == "ids":
        return {"ids": svc.list_ids(segments)}
    if what == "series":
        if not ids:
            raise RuntimeError("series에는 ids 목록이 필요합니다 (syslog_query ids 참조)")
        return {"series": svc.get_series([int(i) for i in ids])}
    raise RuntimeError("what은 status|timeline|ids|series 중 하나여야 합니다")


@mcp.tool()
def canlog_query(
    what: str = "status",
    keys: Optional[list] = None,
    messages: Optional[list] = None,
    x_min_ms: float = 0.0,
    x_max_ms: float = 0.0,
    limit: int = 500,
) -> dict:
    """CAN 로그 분석 조회 (MCP의 실시간 RX 폴링 대체 수단).
    what=status|timeline|signals|messages|frames(시간창+x_min/x_max_ms, messages 필터)|
    series(keys=["Msg.Sig"] 필요). 대응: GET /api/canlog/*"""
    svc = _svc("can_log_service")
    if what == "status":
        return svc.status()
    if what == "timeline":
        return svc.timeline()
    if what == "signals":
        return {"signals": svc.list_signals()}
    if what == "messages":
        return {"messages": svc.list_messages()}
    if what == "frames":
        return svc.get_frames(x_min_ms, x_max_ms, messages, limit)
    if what == "series":
        if not keys:
            raise RuntimeError("series에는 keys 목록이 필요합니다 (canlog_query signals 참조)")
        return {"series": svc.get_series(keys)}
    raise RuntimeError("what은 status|timeline|signals|messages|frames|series 중 하나여야 합니다")


@mcp.tool()
def syslog_upload_control(
    action: str,
    request_id: int = 0x6D1,
    response_id: int = 0x6B0,
    address: int = 0,
    security_enable: bool = True,
    filename: Optional[str] = None,
) -> dict:
    """sysLog 취득(UDS 메모리 업로드) 제어. action=start|stop|status|download(filename 필요 —
    저장된 바이너리 경로 반환, MCP는 파일 내용을 직접 못 받으므로 서버 경로로 확인).
    대응: /api/syslog_upload/*"""
    mgr = _svc("syslog_upload_manager")
    if action == "start":
        _require_running()
        _require_connected()
        try:
            return mgr.start(request_id, response_id, address, security_enable)
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(str(exc))
    if action == "stop":
        return mgr.stop()
    if action == "status":
        return mgr.status()
    if action == "download":
        if not filename:
            raise RuntimeError("download에는 filename이 필요합니다 (status의 saved file 참조)")
        try:
            p = mgr.saved_file_path(filename)
        except (ValueError, FileNotFoundError) as exc:
            raise RuntimeError(str(exc))
        return {"path": str(p), "size": p.stat().st_size}
    raise RuntimeError("action은 start|stop|status|download 중 하나여야 합니다")


@mcp.tool()
def log_control(action: str) -> dict:
    """CAN 수신 로깅 start|stop|status (BLF/ASC 저장). 대응: POST /api/log/*"""
    log = _svc("log_service")
    if action == "start":
        _require_connected()
        try:
            return log.start()
        except Exception as exc:
            raise RuntimeError(str(exc))
    if action == "stop":
        return log.stop()
    if action == "status":
        return log.status()
    raise RuntimeError("action은 start|stop|status 중 하나여야 합니다")


@mcp.tool()
def seedkey_status() -> dict:
    """SeedKey DLL 상태 조회 (Windows 전용, 비Windows는 dummy 키 사용).
    DLL 로드는 파괴적(네이티브 코드 실행)으로 seedkey_load(confirm 필요)를 사용.
    대응: GET /api/seedkey/status"""
    return _svc("seedkey_service").status()


@mcp.tool()
def seedkey_load(path: str, confirm: bool = False) -> dict:
    """⚠️ SeedKey DLL 로드 (네이티브 코드 실행 — confirm=True 필수, Windows 전용).
    path는 서버 로컬 DLL 경로. 대응: POST /api/seedkey/upload"""
    _require_confirm(confirm, "seedkey DLL load (네이티브 코드 실행)")
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"파일이 없습니다: {path}")
    return _svc("seedkey_service").load(str(p), p.name)


# =====================================================================
# Layout
# =====================================================================


def _layout_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    if not safe:
        raise RuntimeError("invalid layout name")
    layout_dir = Path(_SERVICES.get("layout_dir", "layouts"))
    return layout_dir / f"{safe}.json"


@mcp.tool()
def layout_control(action: str, name: Optional[str] = None, body: Optional[dict] = None) -> dict:
    """설정 저장/불러오기. action=list|get(name 필요)|save(name+body 필요)|delete(name 필요).
    대응: /api/layouts*. body는 pages+widgets+canConfig+dbc/스크립트 파일명 (AGENTS.md 관례)."""
    if action == "list":
        d = Path(_SERVICES.get("layout_dir", "layouts"))
        return {"layouts": sorted(p.stem for p in d.glob("*.json")) if d.exists() else []}
    if action == "get":
        if not name:
            raise RuntimeError("get에는 name이 필요합니다")
        p = _layout_path(name)
        if not p.exists():
            raise RuntimeError("layout not found")
        return json.loads(p.read_text(encoding="utf-8"))
    if action == "save":
        if not name or body is None:
            raise RuntimeError("save에는 name과 body가 필요합니다")
        p = _layout_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": name}
    if action == "delete":
        if not name:
            raise RuntimeError("delete에는 name이 필요합니다")
        p = _layout_path(name)
        if p.exists():
            p.unlink()
        return {"deleted": name}
    raise RuntimeError("action은 list|get|save|delete 중 하나여야 합니다")


# =====================================================================
# 서버 종료 (파괴적)
# =====================================================================


@mcp.tool()
def server_shutdown(confirm: bool = False) -> dict:
    """⚠️ 백엔드 서버 프로세스 종료 (confirm=True 필수).
    HTTP 모드에서는 uvicorn 전체가 내려가고, stdio 모드에서는 MCP 프로세스만 종료된다.
    대응: POST /api/shutdown"""
    _require_confirm(confirm, "server shutdown (백엔드 프로세스 종료)")

    def _cleanup_and_exit() -> None:
        time.sleep(0.3)
        logger.warning("shutdown requested via MCP server_shutdown")
        try:
            _svc("tx_scheduler").shutdown()
        except Exception:
            pass
        try:
            _svc("can_manager").disconnect()
        except Exception:
            pass
        try:
            _svc("power_supply_service").disconnect()
        except Exception:
            pass
        try:
            _svc("audio_service").shutdown()
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_cleanup_and_exit, daemon=True, name="mcp-shutdown").start()
    return _ok(message="서버를 종료합니다")


# ---- standalone dev entry (inspector / curl smoke test) -------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
    import uvicorn

    # Standalone: bind a minimal virtual-bus service set so the tools are
    # usable without the full main.py app (dev/inspector only).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from can_manager import CanManager
    from dbc_service import DbcService
    from tx_scheduler import TxScheduler
    from replay_service import ReplayService
    from log_service import LogService
    from power_supply_service import PowerSupplyService
    from audio_service import AudioService
    from syslog_service import SysLogService
    from can_log_service import CanLogService
    from test_runner_service import TestRunnerService
    from seedkey_client import SeedKeyService
    from uds_download_manager import MultiUdsDownloadManager
    from ota_tester_download_manager import OtaTesterDownloadManager
    from syslog_upload_manager import SysLogUploadManager
    import isotp_service as _isotp

    _base = Path(__file__).resolve().parent
    _can = CanManager()
    _dbc = DbcService()
    _tx = TxScheduler(_can, _dbc)
    _replay = ReplayService(_can)
    _seed = SeedKeyService()
    bind_services(
        can_manager=_can, dbc_service=_dbc, tx_scheduler=_tx,
        replay_service=_replay,
        log_service=LogService(_can, _base / "can_logs"),
        power_supply_service=PowerSupplyService(),
        audio_service=AudioService(
            _base / "uploads" / "testrunner_audio",
            _base / "uploads" / "testrunner_golden"),
        syslog_service=SysLogService(), can_log_service=CanLogService(_dbc),
        test_runner_service=TestRunnerService(
            _can, _dbc, _tx, _replay,
            _base / "uploads" / "testrunner_logs",
            _base / "testrunner_results"),
        seedkey_service=_seed,
        uds_download_manager=MultiUdsDownloadManager(
            _can, _isotp.send, _isotp.receive, _seed, log_dir=_base / "can_logs"),
        ota_tester_manager=OtaTesterDownloadManager(
            _can, _isotp.send, _isotp.receive, _seed, log_dir=_base / "can_logs"),
        syslog_upload_manager=SysLogUploadManager(
            _can, _isotp.send, _isotp.receive, _seed, log_dir=_base / "can_logs"),
        settings={"ws_flush_ms": 30}, run_state={"running": True},
        layout_dir=_base / "layouts", base_dir=_base,
    )
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="127.0.0.1", port=8001)
