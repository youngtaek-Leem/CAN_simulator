# CAN Simulator — 웹 기반 CAN 통신 평가 환경

브라우저에서 GUI를 자유롭게 구성해 CAN 통신을 평가하는 도구입니다.
로컬 PC에서 실행되는 파이썬 백엔드가 USB-CAN 어댑터(PCAN / Vector CANcase)로
물리 CAN 통신을 수행하고, 웹 프론트엔드는 REST + WebSocket으로 백엔드와 통신합니다.
CAN 신호 송수신뿐 아니라 UDS 진단/SW 다운로드(SWDL), 테스트 시나리오 자동화,
전원(파워서플라이)·오디오 계측까지 하나의 웹 GUI에서 다룹니다.

## 실행 방법

### 1. 백엔드 (필수)

```bash
cd backend
python3 -m venv .venv                # 최초 1회
.venv/bin/pip install -r requirements.txt   # 최초 1회
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Windows에서는 `backend\run_windows.bat`를 더블클릭하면 됩니다 (최초 실행 시 venv 생성과
의존성 설치까지 자동 수행). 수동으로 하려면:

```bat
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --host 127.0.0.1 --port 8000
```

Windows로 폴더를 복사할 때 `.venv`, `node_modules`, `__pycache__`는 제외하고 복사한다
(플랫폼 종속 — Windows에서 새로 생성해야 함). `frontend/dist`는 반드시 포함한다.

`requirements.txt`에는 CAN 송수신에 필요한 핵심 의존성(fastapi/uvicorn/python-can/cantools)
외에, 테스트 시나리오 실행기의 Power/Audio 스텝과 전원·오디오 위젯에 쓰이는 선택적
의존성(pyvisa, sounddevice, scipy, numpy, librosa, scikit-learn)과 xlsx 스크립트 변환용
openpyxl도 함께 포함되어 있다. 실제 파워서플라이(VISA)나 오디오 장치가 없어도 해당 기능은
"연결 안 됨/미초기화" 상태로 우아하게 저하될 뿐, 나머지 CAN 기능에는 영향이 없다.

### 2. 프론트엔드

- **일반 사용**: `frontend/dist`가 빌드되어 있으면 백엔드가 정적으로 서빙합니다.
  브라우저에서 <http://127.0.0.1:8000> 접속.
- **개발 모드**: `cd frontend && npm install && npm run dev` 후 <http://127.0.0.1:5173> 접속.
- **빌드**: `cd frontend && npm run build`

### 3. 기본 사용 순서

1. 상단 바에서 인터페이스(Virtual / PCAN / Vector), 채널, 비트레이트 선택 후 **연결**.
   - 하드웨어 없이 시험하려면 **Virtual** + 임의 채널명(예: `ch0`).
2. **DBC 업로드** (샘플: `samples/sample.dbc`).
3. **+ 위젯 추가**로 컴포넌트 배치. 편집 모드에서 타이틀바 드래그로 이동,
   모서리로 크기 조절, ⚙로 신호 할당.
4. 상단 바 "⋯ 더보기" 드롭다운(CAN 설정/DBC 업로드/Function Script/설정저장·불러오기)에서
   레이아웃 이름 입력 후 **저장** / **불러오기**로 구성 재사용.
5. 위젯 캔버스는 여러 **페이지(탭)** 로 나눌 수 있다 — 편집 모드에서 탭 바의 "+ 페이지"로
   추가, ✎로 이름 변경, ✕로 삭제(최소 1개는 유지). 다른 탭의 위젯도 계속 송수신된다.

## 신호 송신 규칙

- **Periodic** 신호: 설정된 주기(DBC GenMsgCycleTime)로 계속 송신.
- **Event** 신호: 유효값 송신 → **30ms 후 invalid 값**(신호 비트로 표현 가능한
  최대값, 예: 4bit → 0xF) 송신. 같은 메시지 안의 다른 신호가 함께 있으면 그 신호들도
  이력과 무관하게 매번 invalid로 나간다(설정 중인 신호 하나만 valid) — 영구 상태는
  오염되지 않으므로 이후 그 신호를 다시 설정하면 정상 값부터 시작한다.
- **판별 기준**: DBC 메시지 코멘트(`CM_ BO_`)의 맨 앞 `[태그]`를 본다 — `[P]`/`[PE]`는
  Periodic, 그 외 태그(`[EC]`/`[EW]`/`[TP]` 등)나 태그가 없는 메시지는 Event로 분류한다.
  위젯 설정 UI에서 신호 단위로 수동 변경(override)하면 이 판별보다 우선한다.
- **Periodic 신호 버튼의 valid/invalid 토글**: 버튼·멀티 버튼 셀·Random 버튼이 Periodic
  신호에 바인딩되어 있으면 클릭할 때마다 "설정값 계속 송신" ↔ "invalid 값 계속 송신"이
  토글된다(Event 신호는 토글 없이 기존 클릭 동작 그대로).

## CAN-FD

virtual / PCAN / Vector 모두 CAN-FD(최대 64바이트, 선택적 bitrate switch)를 지원합니다.

- **연결**: 상단 바 "⋯ 더보기" → CAN 설정에서 인터페이스 선택 옆의 **FD** 체크박스를 켜면
  데이터 위상 비트레이트(1/2/4/5/8 Mbit/s) 선택지가 나타납니다. 연결 후에는 변경할 수
  없으니 다시 연결해야 합니다.
- **DBC 신호**: DBC 메시지에 Vector 표준 속성 `VFrameFormat`이 `StandardCAN_FD` /
  `ExtendedCAN_FD`로 설정되어 있으면 자동으로 FD 메시지로 인식됩니다. 샘플 DBC의
  `FdSensorData`(32바이트, 20ms 주기)가 예시입니다. 이 신호를 위젯으로 조작하면
  자동으로 FD + bitrate switch 프레임이 나갑니다.
- **수동 FD 프레임**: CAN 메시지 전송 박스에서 DBC를 연동하지 않은(raw ID) 행에는
  **F**(FD)/**B**(bitrate switch) 체크박스가 있어 임의의 FD 프레임을 구성할 수 있습니다.
- **표시**: CAN 메시지 표시창에 FD 프레임은 ID 옆에 `FD` 또는 `FD+BRS` 배지로 표시됩니다.
- **연결 설정이 항상 우선**: 실제로 나가는 프레임의 FD 여부는 최종적으로 연결(HS-CAN
  classic / CAN-FD) 설정을 따른다. HS-CAN(classic)으로 연결한 상태에서는 TX 행의
  F 체크박스나 DBC 메시지의 FD 속성이 켜져 있어도 항상 classic 프레임으로 강제 전송된다
  (실제 하드웨어에서 classic 연결로 FD 프레임을 내보내려다 드라이버 오류·프레임 손상이
  나는 것을 막기 위한 안전장치, `backend/can_manager.py::CanManager.send()`).
- **주의**: FD를 켜지 않고 연결한 버스(classic CAN)에서 8바이트를 초과하는 프레임을
  보내려 하면 400 에러로 거부됩니다.

### 설정값을 바꿔야 할 수 있는 곳

| 설정 | 위치 | 기본값 | 언제 바꾸나 |
|---|---|---|---|
| 데이터 위상 비트레이트 | 상단 바 더보기 → CAN 설정, FD 체크박스 옆 select | 2 Mbit/s | 실제 네트워크의 데이터 위상 속도에 맞춰 선택 (연결 전에만 변경 가능) |
| PCAN FD 클럭/샘플포인트 | [backend/can_manager.py](backend/can_manager.py)의 `FD_CLOCK_HZ`(80MHz), `FD_SAMPLE_POINT`, `FD_DATA_SAMPLE_POINT`(각 80%) | 80MHz / 80% / 80% | 실제 PCAN-FD 어댑터가 이 조합으로 링크가 안 붙으면(비트 타이밍 불일치) 값을 조정. python-can의 `can.BitTimingFd.from_sample_point()`가 이 값들로 BRP/TSEG/SJW를 계산함 |
| Vector FD tseg/sjw | [backend/can_manager.py](backend/can_manager.py) `connect()` 내 `kwargs["fd"]`/`kwargs["data_bitrate"]` 설정부 | python-can 기본값 (sjw_abr=2, tseg1_abr=6 등) | 특수한 타이밍이 필요하면 `can.Bus(...)` 호출에 `sjw_abr`, `tseg1_abr` 등을 직접 추가 |
| DBC에서 메시지를 FD로 표시 | DBC 파일의 `BA_ "VFrameFormat" BO_ <id> 14;` (Standard) 또는 `15`(Extended) | — | Vector DBC 편집기(CANdb++)나 텍스트 편집으로 직접 설정 — cantools가 이 속성으로 `is_fd`를 판별함 |
| 최대 페이로드 판정 기준 | [backend/can_manager.py](backend/can_manager.py) `MAX_CLASSIC_DATA_LEN`(8) | 8 | 통상 바꿀 필요 없음 (CAN 2.0 표준값) |

## GUI 컴포넌트

위젯은 "+ 위젯 추가" 메뉴에서 추가한다. 크기 조절과 이동은 편집 모드와 무관하게 항상
가능하고, 편집 모드는 ⚙(설정)/✕(삭제) 버튼 노출에만 영향을 준다.

### 표시

| 컴포넌트 | 기능 |
|---|---|
| CAN 메시지 표시창 | 수신 메시지 ID별 실시간 표시(고정/스크롤 모드, 일시중지 후 최근 1분 스크롤, 수신 Time(ms) 컬럼). DBC로 디코딩된 행은 클릭 시 신호 목록이 펼쳐짐 |
| 수신 CAN 신호 표시창 | RX로 분류된 메시지의 신호를 이름/값/단위로 나열 표시 |
| 텍스트 표시창 | 할당된 신호의 디코딩 값 + 단위 표시 |
| CAN 신호 그래프 | 신호마다 독립된 미니 차트를 세로로 쌓는 계단형(step) 시계열 차트. 차트별 X/Y 독립 확대·축소(휠)·팬(드래그), 공유 롤링 윈도우(±5초 버튼) |

### 입력/컨트롤 (단일)

| 컴포넌트 | 기능 |
|---|---|
| 버튼 | 클릭 시 설정 값 송신(Periodic 신호는 클릭마다 valid/invalid 토글) |
| 체크박스 | ON/OFF 값 송신 |
| 드롭다운 | DBC VAL_ 테이블 선택지 송신 |
| 슬라이더 | 연속 값 송신(신호 min/max 자동 반영, 화살표 키로도 조작) |
| 입력 박스 | 물리값을 직접 입력해 즉시 송신 |
| Random 버튼 | 신호에 Random(전체 bit 범위) 또는 Range(지정 min/max/step 순환) 모드로 값을 생성해 송신. Periodic은 매 주기 새 값, Event는 클릭마다 새 값 |
| Function 버튼 | Function Script(FUNC 블록 마스터 스크립트)에서 고른 함수 하나를 클릭 시 실행 |

### 입력/컨트롤 (멀티 — 격자)

버튼/체크박스/드롭다운/슬라이더/입력 박스/Function 버튼/Random 버튼을 격자(최대 10x10)로
모은 위젯. 각 셀이 독립적으로 신호(또는 함수)를 할당한다.

| 컴포넌트 |
|---|
| 멀티 버튼 / 멀티 체크박스 / 멀티 드롭다운 / 멀티 슬라이더 / 멀티 입력 박스 |
| 멀티 Function 버튼 |
| 멀티 Random 버튼 |

### 송신/재생 도구

| 컴포넌트 | 기능 |
|---|---|
| CAN 메시지 전송 박스 | 최대 20개 메시지 등록(ID/주기/데이터, raw 또는 DBC 연동), Start/Stop, 행별 F/B(FD/BRS) 체크박스 |
| ISO-TP 메시지 전송 | ID+데이터(hex)만 입력하면 8바이트 초과 시 자동으로 FF/CF로 분할, Flow Control(FC ID/타임아웃/BS/STmin) 대기·준수 |
| CAN 로그 Replay 박스 | .blf/.asc 로드, 메시지 선택 Pass/Stop 필터, Replay Start/Stop |

## UDS 진단 / SW 다운로드(SWDL)

ISO-TP(ISO 15765-2) 전송 위에서 UDS(ISO 14229) 진단 서비스를 구동하는 두 위젯이 있다.
공통 백엔드: `backend/uds_core.py`(PDU 빌더/파서), `backend/isotp_service.py`(전송 계층),
`backend/seedkey_client.py`(SecurityAccess용 실제 키 계산).

| 컴포넌트 | 기능 |
|---|---|
| CAN-SWDL | 벤더 XML 절차 정의(진단 세션 전환 → SecurityAccess → RoutineControl → RequestDownload → seekAddress/writeSize 기반 청크 TransferData → TransferExit)를 그대로 해석해 순차 실행하는 SW 다운로드 위젯. 스텝 단위 체크박스로 일부만 선택 실행 가능 |
| OTA Tester | 폴더 선택(`webkitdirectory`) 한 번으로 `CLI/cli_config.json` → 테스트케이스 매니페스트 → hook/testBlock XML·bin을 모두 찾아 케이스 리스트로 구성, 케이스별 체크리스트로 선택 실행. `VehicleInfo/vehicleInfo.json`에서 Req/Resp ID를 자동 채움 |

- **SecurityAccess(0x27)**: `HKMC_AdvancedSeedKey_*.dll`(Windows 전용)을 업로드하면 실제
  Seed→Key 계산을 수행하고, DLL이 없거나 비-Windows 환경이면 더미(0) 키로 대체해 흐름
  검증은 계속 가능하다. STmin 오버라이드 + SeedKey DLL 업로드 UI는
  `frontend/src/widgets/UdsGlobalControls.tsx`로 두 위젯이 공유한다.
- 두 위젯 모두 폴더 탐색·XML/JSON 파싱은 브라우저(JS)에서 수행하고(브라우저가 로컬 절대
  경로를 서버에 넘길 수 없다는 제약 때문), 백엔드는 업로드된 파일 내용만 다룬다.
  Windows 경로(`\`)와 상대 경로 세그먼트를 정규화해 폴더 구조가 바뀌어도 안전하게 매칭한다.

## 테스트 자동화 (테스트 Sequence 실행기)

`backend/test_runner_service.py`가 JSON 스텝 스크립트를 순서대로 실행하는 시나리오
러너다. 스텝 타입: `ID`(케이스 경계+반복 횟수), `delay`, `CANReq`/`CANEv`(신호 1회 전송,
Event/Periodic 규칙은 `tx_scheduler`와 동일하게 적용), `CANResp`(타임아웃 내 기대값 수신
판정), `CANlogReplay`(.blf/.asc 재생, DBC 노드 기준 자기 TX 제외 필터), `Power`(ACC/IGN
전원 제어), `Audio`(StartREC/StartRECtime/StartRECref/StopREC/compWAV/saveAsGolden),
`Loop`(중첩 `{"type":"loop","cycle":N,"steps":[...]}` 및 구버전 `id`/`gotoid` 평면 스캔
방식 모두 지원). 실행 로그와 케이스별 pass/fail 결과가 실시간으로 위젯에 표시된다.

- **Function Test**: `FUNC` 블록(`{"type":"FUNC","name":"...", "Cycle":1}`)으로 구성된
  "Function Script"를 상단 바에서 별도로 업로드하면, Function 버튼/멀티 Function 버튼
  위젯이 그중 하나를 골라 클릭 시 그 함수의 스텝만 실행한다. 실행 엔진·로그는 테스트
  시나리오 실행기와 완전히 공유하며, 일반 시나리오와 Function 실행은 같은 CAN 버스/스레드를
  쓰므로 상호 배타적으로 동작한다.
- **엑셀 스크립트 변환**: `backend/xlsx_to_script.py`(CAN Test Script Editor 형식의 .xlsx →
  실행기 JSON, 업로드 API에서 `.xlsx` 확장자를 자동 인식) / `backend/dbc_to_script_editor.py`
  (DBC → 스텝 입력용 .xlsx 생성, 신호명 검색·드롭다운 포함).
- Power/Audio 스텝은 실제 장비가 없으면 해당 스텝만 우아하게 Fail 처리되고 나머지 CAN
  스텝은 계속 진행된다(CAN 부분만으로도 하드웨어 없이 전체 검증 가능).

## 오디오 신호 모니터

선택한 오디오 입력 장치(채널 수만큼 자동으로 미니 차트를 세로 분리)의 실시간 파형을
GraphWidget과 동일한 방식(휠 줌, 드래그 팬, 축 독립)으로 보여주는 위젯이다.

- **Start**(파형만 표시, 저장 안 함) / **Record**(파형 + WAV 저장) 버튼이 분리되어 있고,
  Record는 이미 열려 있는 모니터 스트림을 재오픈 없이 그 자리에서 녹음으로 업그레이드한다.
- 백엔드가 채널별로 30초 순환 버퍼에 원본 샘플을 보관하고, 프론트가 현재 보고 있는
  구간만 픽셀 컬럼 단위로 다운샘플해 요청한다(`GET /api/audio/waveform`) — 줌인하면
  사실상 원본 파형, 줌아웃하면 min/max 엔벨로프.
- 녹음 파일은 30분 단위로 자동 분할되고, 파형 X축은 녹음 시작을 0초로 하는 경과초로
  표시된다.
- 테스트 시나리오 실행기의 Audio 스텝(`compWAV`)은 MFCC+DTW, 대역 제한 FFT 상관계수,
  상호상관, RMS/ZCR/스펙트럴 센트로이드 등 다중 지표로 녹음 파형을 케이스별 golden(기준)
  WAV와 비교해 통과 임계값을 판정한다(`backend/audio_service.py`).

## 전원 컨트롤

PyVISA/SCPI 파워서플라이를 직접 제어하는 위젯(`backend/power_supply_service.py`).

| 기능 | 설명 |
|---|---|
| 연결/해제 | VISA 리소스에 연결. pyvisa 미설치나 장비 미연결 시 `initialized=False`로 우아하게 저하 |
| 배터리 전압/전류 | 값 입력 후 OK로 `APPLy {v},{i}` 명령 전송(read-back 없음 — 마지막으로 보낸 명령값만 표시) |
| ACC / IGN 토글 | 전원 상태 비트마스크로 각각 On/Off 전환 |
| 자동 On/Off 반복 | 배터리 전압을 설정한 On값 ↔ 0V로 On시간/Off시간 간격마다 계속 전환 |
| 전압 Up/Down 스윕 | Low ↔ High 삼각파(편도 시간 입력, 전류는 스윕 내내 고정) |

자동 On/Off 반복과 전압 스윕은 같은 전압 채널을 다루므로 동시 실행은 서로 거부한다.

## 성능 설계

- 백엔드는 수신 프레임을 묶어서(WebSocket, 기본 30ms 단위) 전송.
- 프론트는 수신 데이터를 DOM에 직접 반영하지 않고 객체 store에 누적한 뒤
  `requestAnimationFrame`으로 **초당 10~60회만 UI 갱신** (⚙ 설정에서 변경).
- Windows에서 백엔드 실행 시 시스템 타이머 해상도를 1ms로 고정(timeBeginPeriod).

## 테스트

```bash
cd backend
.venv/bin/python -m pytest tests/          # 219개 테스트 (virtual 버스 기반, 하드웨어 불필요)
```

Power/Audio 관련 테스트는 실제 VISA 장비·오디오 장치가 없는 환경에서도 "미연결/우아한
저하" 경로를 검증하도록 작성되어 있어 CI/개발 환경에서 그대로 통과한다.

샘플 데이터: `samples/sample.dbc`(CAN-FD 메시지 `FdSensorData` 포함), `samples/sample.asc`,
`samples/sample.blf` (재생성: `backend/.venv/bin/python samples/make_sample_logs.py`)

## 디렉터리 구조

```
backend/
  main.py                          FastAPI 앱 + REST/WebSocket 엔드포인트
  can_manager.py                   CAN 버스 연결/송수신 (virtual/PCAN/Vector, classic+FD)
  dbc_service.py                   DBC 파싱, 신호 인코딩/디코딩, Event/Periodic 판별
  tx_scheduler.py                  주기/이벤트 송신 스케줄러, 값 생성기(Random/Range)
  isotp_service.py                 ISO-TP(ISO 15765-2) 송신
  uds_core.py                      UDS(ISO 14229) PDU 빌더/파서
  uds_download_manager.py          CAN-SWDL: XML 절차 기반 SW 다운로드 상태머신
  uds_xml_parser.py                CAN-SWDL용 XML 절차 정의 파서
  seedkey_client.py                HKMC Advanced SeedKey DLL 클라이언트(SecurityAccess 키 계산)
  ota_tester_download_manager.py   OTA Tester: 케이스 리스트 기반 SW 다운로드 실행기
  test_runner_service.py           테스트 시나리오 실행기(CAN/Power/Audio 스텝, Function Test)
  replay_service.py                .blf/.asc 로그 재생
  log_service.py                   수신 로그 기록
  power_supply_service.py          PyVISA/SCPI 파워서플라이 제어(배터리/ACC/IGN/자동모드)
  audio_service.py                 오디오 녹음/실시간 파형/다중 지표 WAV 비교
  xlsx_to_script.py                CAN Test Script Editor .xlsx → 실행기 JSON 변환
  dbc_to_script_editor.py          DBC → 테스트 스크립트 입력용 .xlsx 생성
  timer_util.py                    Windows 타이머 해상도 고정(winmm timeBeginPeriod)
  tests/                           pytest 스위트 (virtual 버스 기반, 하드웨어 불필요)
frontend/   React + TypeScript + react-grid-layout GUI 빌더
samples/    샘플 DBC / BLF / ASC / 테스트 스크립트(JSON) / xlsx 변환 스크립트
```

## 하드웨어 참고

- **PCAN**: PEAK PCAN 드라이버 설치 필요. 채널명 예: `PCAN_USBBUS1`.
- **Vector CANcase**: Vector XL 드라이버 필요 — **Windows 전용**. 채널 예: `0`.
- macOS/Linux에서는 Virtual 인터페이스로 개발·시험 가능.
- **파워서플라이**: PyVISA로 접근 가능한 SCPI 장비(VISA 리소스 문자열로 연결). 미연결
  시 전원 관련 기능은 모두 "연결 안 됨"으로 우아하게 저하된다.
- **오디오**: sounddevice가 열 수 있는 입력 채널이 있는 장치만 오디오 위젯/테스트 러너의
  장치 목록에 나타난다.
- **SeedKey DLL**: `HKMC_AdvancedSeedKey_Win32.dll`/`HKMC_AdvancedSeedKey_x64.dll`은
  Windows PE 바이너리라 Windows에서만 로드된다. 다른 OS에서는 더미 키로 대체된다.
