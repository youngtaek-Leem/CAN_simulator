# CAN Simulator — MCP(AI 연동) 통합 문서

> 작성일: 2026-10-07 / 상태: 구현 진행 중
> 목표: 현재 개발된 CAN simulator의 **모든 기능(REST 약 120개 엔드포인트)을 AI 클라이언트에서 사용할 수 있도록 MCP(Model Context Protocol)로 공개**한다.

## 1. 결정 사항 (사용자 확정)

| 항목 | 선택 |
|---|---|
| 전송 방식 | **둘 다 지원** — Streamable HTTP(원격/웹·Copilot 등 범용) + stdio(Claude Desktop/Code 로컬) |
| 공개 범위 | **전체 120개 API** — 연결·DBC·TX·Replay·ISO-TP·TestRunner·UDS SWDL·OTA·Power·Audio·SysLog·CanLog·Layout 전부 |
| 안전 제어 | **Power 제어·UDS 플래시·OTA만 사전확인 후 실행** — 해당 도구는 `confirm=True` 없으면 코드 레벨에서 거부 |
| 주 클라이언트 | 웹/Copilot 등 범용 → **원격 HTTP 필수** (`http://127.0.0.1:8000/mcp`) |

## 2. 아키텍처

```
[Claude Desktop / Claude Code] -- stdio ---------> backend/mcp_stdio.py
[웹 AI / Copilot / Inspector] -- HTTP ----------> FastAPI /mcp (Streamable HTTP)
                                                        |
                                              backend/mcp_server.py (FastMCP, 40여 tools)
                                                        |  직접 호출 (loopback REST 없음)
                                                        v
 main.py 전역 서비스 객체 재사용: can_manager, dbc_service, tx_scheduler,
 replay_service, test_runner_service, uds_download_manager, ota_tester_manager,
 power_supply_service, audio_service, syslog_service, can_log_service,
 log_service, seedkey_service, syslog_upload_manager
```

### 핵심 설계 결정

1. **라이브러리**: 공식 `mcp>=1.12,<2` Python SDK의 `FastMCP` (v2의 `MCPServer` 개명 전 안정 API).
   `requirements.txt`에 1줄 추가. (v2.x 설치 시 `mcp.server.fastmcp`가 없어 동작하지 않음을 확인 — `<2` 핀 필수)
2. **순환 import 회피**: `mcp_server.py`는 `main.py`를 import하지 않는다.
   대신 `bind_services(...)`로 서비스 객체를 주입받는 **바인더 패턴**.
   `main.py`가 전역 객체 생성 후 `bind_services()` 1회 호출. 테스트는 virtual 버스 인스턴스로 직접 바인딩.
3. **별도 프로세스 분리 안함**: uvicorn 프로세스 안에 MCP를 마운트해야 CAN 버스 상태·스케줄러를 공유한다.
   별도 MCP 프로세스는 상태 이중화 버그를 유발하므로 채택하지 않았다.
4. **120개 → 40여 개 도구로 병합**: 1:1 매핑 시 AI 컨텍스트 폭발. 도메인별 `action` 파라미터로 병합
   (예: `tx_table_control(action=start|stop)`, `replay_control(action=start|stop|pause|resume)`).
5. **파일 업로드의 MCP 변환**: MCP에는 파일 업로드プリミティブ가 없으므로 바이너리 아티팩트(BLF/ASC/XML/BIN/DLL/DBC)는
   **서버 로컬 경로(`path`)** 로 전달한다 (운영자가 서버 PC에 파일을 두면 AI가 경로를 넘김).
   텍스트 기반(DBC·스크립트 JSON·syslog DB)은 `content` 문자열 직접 전달도 지원.
6. **WebSocket 대체**: MCP는 WS 푸시를 받을 수 없으므로 수신 스트림은
   `canlog_query(what=frames)` 폴링 + `can_status()` 폴링으로 대체한다 (tool 설명에 명시).

## 3. 도구 목록 (Tier별)

### Tier 0 — 연결/상태 (확인 불필요, 5개)
| 도구 | 대응 REST |
|---|---|
| `can_connect` | `POST /api/connect` (iface/channel/bitrate/fd/data_bitrate) |
| `can_disconnect` | `POST /api/disconnect` |
| `can_status` | `GET /api/status` (전체 요약: can/tx/replay/dbc/run/uds/ota/power/audio/log) |
| `run_start` / `run_stop` | `POST /api/run/start·stop` (Stop은 auto 송신·replay·runner·SWDL·OTA 전체 정지) |

### Tier 1 — DBC·TX·ISO-TP·Replay (확인 불필요)
| 도구 | 대응 REST |
|---|---|
| `dbc_summary` / `dbc_signal_info` / `dbc_set_send_type` / `dbc_message_initial` | `GET /api/dbc`, summary 가공, `POST /api/dbc/send-type`, `GET /api/dbc/messages/{name}/initial` |
| `artifact_load` | `POST */upload` 10종 통합 (target=dbc\|replay\|testrunner_script\|testrunner_functions\|uds_xml\|uds_binary\|ota_xml\|ota_binary\|syslog_log\|syslog_db\|canlog, content 또는 path) |
| `tx_signal` | `POST /api/tx/signal` (Event=유효값+30ms invalid, Periodic=주기 자동송신) |
| `tx_signal_pulse` | `.../invalid_first` / `.../zero_after` (mode 파라미터) |
| `tx_send_once` | `POST /api/tx/send_once` (raw ID+hex, FD/BRS 지원) |
| `tx_table_configure` / `tx_table_control` / `tx_row_control` | `/api/tx/configure`, `/start·stop`, `/row/start·stop·update` |
| `tx_periodic_all` | `/api/tx/periodic/enable_all·disable_all` |
| `tx_auto_stop` | `POST /api/tx/auto/stop` |
| `tx_generator` | `/api/tx/signal/generator·generate·invalid·event_periodic...` + stop 계열 (action 분기) |
| `isotp_send` | `POST /api/isotp/send` (SF/FF·FC·CF, 선택적 응답 대기) |
| `isotp_security_access` | `POST /api/isotp/security-access` (27 11→seed→DLL/dummy 키→27 12) |
| `replay_control` | `/api/replay/start·stop·pause·resume` (Pass/Stop 필터 포함) |

### Tier 2 — 분석·테스트·로그 (확인 불필요)
| 도구 | 대응 REST |
|---|---|
| `testrunner_control` / `testrunner_status` | `/api/testrunner/start·stop·pause·resume`, `/functions/start`, `/api/testrunner/status` |
| `uds_swdl_control` / `uds_swdl_status` / `uds_swdl_steps` | `/api/udswdl/start·stop·stop_all`, `/status`, `/steps` + `PUT /step_params` |
| `ota_tester_control` / `ota_tester_status` / `ota_case_manage` | `/api/ota_tester/start·stop·status`, case enable/steps/clear |
| `power_measure` (읽기) | `GET /api/power/measure`, `/api/power/status` |
| `audio_probe` / `audio_control` | `GET /api/audio/*`, monitor/record start·stop, device 선택 |
| `syslog_query` / `canlog_query` | `/api/syslog/*`, `/api/canlog/*` (ids/series/timeline/frames) |
| `syslog_upload_control` / `syslog_upload_status` | `/api/syslog_upload/start·stop·status·download` |
| `log_control` | `/api/log/start·stop` + `log_service.status` |
| `layout_control` | `/api/layouts*` (list/get/save/delete) |
| `seedkey_status` | `GET /api/seedkey/status` (조회만, Windows 전용 DLL) |

### Tier 3 — 파괴적 (⚠️ `confirm=True` 코드 강제, 4개)
| 도구 | 대응 REST | 비고 |
|---|---|---|
| `power_control` | `/api/power/battery·acc_ign·onoff·​sweep` | 실제 전원 출력 변경 |
| `uds_swdl_control(action=start)` | `/api/udswdl/start` | ECU 펌웨어 플래시 |
| `ota_tester_control(action=start)` | `/api/ota_tester/start` | OTA 다운로드 실행 |
| `seedkey_load` | `POST /api/seedkey/upload` | Windows DLL 로드 |
| `server_shutdown` | `POST /api/shutdown` | 프로세스 종료 |

`confirm` 게이트는 `_require_confirm()` 1곳에서 강제한다 — 프롬프트 의존이 아니라
`confirm is not True`이면 호출 자체를 `RuntimeError`로 거부하고, 실행 전후 `diag_log` 성격의 로그를 남긴다.

## 4. 파일 변경 목록

| 파일 | 변경 |
|---|---|
| `backend/mcp_server.py` | **신규** — FastMCP + 42 tools + 바인더 + 가드/게이트 (약 1000줄) |
| `backend/mcp_stdio.py` | **신규** — stdio shim (약 70줄, 단독 virtual 바인딩) |
| `backend/main.py` | 세션매니저 lifespan + `/mcp` ASGI 프록시 + `bind_services()` (약 80줄) |
| `backend/requirements.txt` | `mcp>=1.12,<2` 1줄 |
| `backend/tests/test_mcp_server.py` | **신규** — virtual 버스 기반 17 테스트 |
| `docs/MCP_INTEGRATION.md` | 본 문서 |
| `README.md` | MCP 접속 섹션 추가 |

## 5. AI 클라이언트 접속 방법

**Streamable HTTP (범용)**: 서버 실행 후 `http://127.0.0.1:8000/mcp` 로 MCP 클라이언트 연결.
Inspector 검증: `npx @modelcontextprotocol/inspector` → Transport `Streamable HTTP` → URL 입력.

**stdio (Claude Desktop/Code)** — `claude_desktop_config.json` 또는 `.mcp.json`:
```json
{
  "mcpServers": {
    "can-simulator": {
      "command": "/abs/path/CAN_simulator/backend/.venv/bin/python",
      "args": ["/abs/path/CAN_simulator/backend/mcp_stdio.py"]
    }
  }
}
```
주의: stdio 모드는 같은 프로세스에서 CAN 하드웨어 상태를 공유하지 않는다
(별도 프로세스). 하드웨어 제어는 HTTP 모드(uvicorn 내장)를 권장하고,
stdio는 DBC 파싱·스크립트 생성 등 비상태성 작업에 적합하다 — tool 설명에도 명시.

## 6. 검증 방법

* `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
  (virtual 버스 — 하드웨어 불필요. confirm 게이트 거부 케이스 포함)
* 전체 회귀: `.venv/bin/python -m pytest tests/`
* Inspector로 `/mcp` 도구 목록·호출 스모크 테스트.
