# CAN Simulator — 웹 기반 CAN 통신 평가 환경

브라우저에서 GUI를 자유롭게 구성해 CAN 통신을 평가하는 도구입니다.
로컬 PC에서 실행되는 파이썬 백엔드가 USB-CAN 어댑터(PCAN / Vector CANcase)로
물리 CAN 통신을 수행하고, 웹 프론트엔드는 REST + WebSocket으로 백엔드와 통신합니다.

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
4. 레이아웃 이름 입력 후 **저장** / **불러오기**로 구성 재사용.

## 신호 송신 규칙

- **Periodic** 신호: 설정된 주기(DBC GenMsgCycleTime)로 계속 송신.
- **Event** 신호: 유효값 송신 → **30ms 후 invalid 값**(신호 비트로 표현 가능한
  최대값, 예: 4bit → 0xF) 송신.
- 판별: DBC의 GenSigSendType → GenMsgSendType 순으로 자동 판별, 위젯 설정에서 수동 변경 가능.

## CAN-FD

virtual / PCAN / Vector 모두 CAN-FD(최대 64바이트, 선택적 bitrate switch)를 지원합니다.

- **연결**: 상단 바에서 인터페이스 선택 옆의 **FD** 체크박스를 켜면 데이터 위상
  비트레이트(1/2/4/5/8 Mbit/s) 선택지가 나타납니다. 연결 후에는 변경할 수 없으니
  다시 연결해야 합니다.
- **DBC 신호**: DBC 메시지에 Vector 표준 속성 `VFrameFormat`이 `StandardCAN_FD` /
  `ExtendedCAN_FD`로 설정되어 있으면 자동으로 FD 메시지로 인식됩니다. 샘플 DBC의
  `FdSensorData`(32바이트, 20ms 주기)가 예시입니다. 이 신호를 위젯으로 조작하면
  자동으로 FD + bitrate switch 프레임이 나갑니다.
- **수동 FD 프레임**: CAN 메시지 전송 박스에서 DBC를 연동하지 않은(raw ID) 행에는
  **F**(FD)/**B**(bitrate switch) 체크박스가 있어 임의의 FD 프레임을 구성할 수 있습니다.
- **표시**: CAN 메시지 표시창에 FD 프레임은 ID 옆에 `FD` 또는 `FD+BRS` 배지로 표시됩니다.
- **주의**: FD를 켜지 않고 연결한 버스(classic CAN)에서 8바이트를 초과하는 프레임을
  보내려 하면 400 에러로 거부됩니다 — 하드웨어에 잘못된 classic 프레임이 나가는 것을
  막기 위한 안전장치입니다.

### 설정값을 바꿔야 할 수 있는 곳

| 설정 | 위치 | 기본값 | 언제 바꾸나 |
|---|---|---|---|
| 데이터 위상 비트레이트 | 상단 바 FD 체크박스 옆 select | 2 Mbit/s | 실제 네트워크의 데이터 위상 속도에 맞춰 선택 (연결 전에만 변경 가능) |
| PCAN FD 클럭/샘플포인트 | [backend/can_manager.py](backend/can_manager.py)의 `FD_CLOCK_HZ`(80MHz), `FD_SAMPLE_POINT`, `FD_DATA_SAMPLE_POINT`(각 80%) | 80MHz / 80% / 80% | 실제 PCAN-FD 어댑터가 이 조합으로 링크가 안 붙으면(비트 타이밍 불일치) 값을 조정. python-can의 `can.BitTimingFd.from_sample_point()`가 이 값들로 BRP/TSEG/SJW를 계산함 |
| Vector FD tseg/sjw | [backend/can_manager.py](backend/can_manager.py) `connect()` 내 `kwargs["fd"]`/`kwargs["data_bitrate"]` 설정부 | python-can 기본값 (sjw_abr=2, tseg1_abr=6 등) | 특수한 타이밍이 필요하면 `can.Bus(...)` 호출에 `sjw_abr`, `tseg1_abr` 등을 직접 추가 |
| DBC에서 메시지를 FD로 표시 | DBC 파일의 `BA_ "VFrameFormat" BO_ <id> 14;` (Standard) 또는 `15`(Extended) | — | Vector DBC 편집기(CANdb++)나 텍스트 편집으로 직접 설정 — cantools가 이 속성으로 `is_fd`를 판별함 |
| 최대 페이로드 판정 기준 | [backend/can_manager.py](backend/can_manager.py) `MAX_CLASSIC_DATA_LEN`(8) | 8 | 통상 바꿀 필요 없음 (CAN 2.0 표준값) |

## GUI 컴포넌트 (12종)

| 컴포넌트 | 기능 |
|---|---|
| CAN 메시지 표시창 | 수신 메시지 ID별 실시간 표시 (DBC 이름, 주기, 카운트, 클릭 시 신호 상세 펼침) |
| 텍스트 표시창 | 할당된 신호의 디코딩 값 + 단위 표시 |
| 버튼 | 클릭 시 설정 값 송신 (포커스 후 Space로도 동작) |
| 체크박스 | ON/OFF 값 송신 (포커스 후 Space로도 동작) |
| 드롭다운 | DBC VAL_ 테이블 선택지 송신 |
| 슬라이더 | 연속 값 송신 (신호 min/max 자동 반영, 화살표 키로도 조작 가능) |
| 멀티 버튼 | 버튼을 격자(가로x세로, 최대 10x10)로 모은 위젯. 셀마다 독립적으로 신호 할당 |
| 멀티 체크박스 | 체크박스를 격자로 모은 위젯. 셀마다 독립적으로 신호 할당 |
| CAN 메시지 전송 박스 | 최대 20개 메시지 등록(ID/주기/데이터), Start/Stop |
| ISO-TP 메시지 전송 | ID+데이터(hex)만 입력하면 8바이트 초과 시 자동으로 FF/CF로 분할, Flow Control 대기·준수 |
| CAN 로그 Replay 박스 | .blf/.asc 로드, 메시지 선택 Pass/Stop 필터, Replay Start/Stop |
| CAN 신호 그래프 | 신호마다 독립된 미니 차트를 세로로 쌓아 표시하는 시계열 차트. 차트별 X/Y축 독립 확대·축소(휠)·팬(드래그), 점+연결선. X축(시간) 눈금은 맨 아래 차트에만 표시 |

신호를 선택하는 모든 화면(위젯 바인딩, TX 박스, Replay 필터, 그래프)에서 메시지 목록은
이름순으로 정렬되며, TX/RX/전체 필터 버튼으로 목록을 좁힐 수 있다. 상단 바에서 실제
DUT(실기) 노드를 "RX 노드"로 지정하면, 그 노드가 DBC 송신자로 등록된 메시지는 RX(시뮬레이터
수신)로, 나머지는 TX(시뮬레이터가 대신 송신)로 분류된다.

## 성능 설계

- 백엔드는 수신 프레임을 묶어서(WebSocket, 기본 30ms 단위) 전송.
- 프론트는 수신 데이터를 DOM에 직접 반영하지 않고 객체 store에 누적한 뒤
  `requestAnimationFrame`으로 **초당 10~60회만 UI 갱신** (⚙ 설정에서 변경).
- Windows에서 백엔드 실행 시 시스템 타이머 해상도를 1ms로 고정(timeBeginPeriod).

## 테스트

```bash
cd backend
.venv/bin/python -m pytest tests/          # 44개 테스트 (virtual 버스 기반, 하드웨어 불필요)
```

샘플 데이터: `samples/sample.dbc`(CAN-FD 메시지 `FdSensorData` 포함), `samples/sample.asc`,
`samples/sample.blf` (재생성: `backend/.venv/bin/python samples/make_sample_logs.py`)

## 디렉터리 구조

```
backend/    FastAPI 서버 (can_manager, dbc_service, tx_scheduler, replay_service)
frontend/   React + TypeScript + react-grid-layout GUI 빌더
samples/    샘플 DBC / BLF / ASC
```

## 하드웨어 참고

- **PCAN**: PEAK PCAN 드라이버 설치 필요. 채널명 예: `PCAN_USBBUS1`.
- **Vector CANcase**: Vector XL 드라이버 필요 — **Windows 전용**. 채널 예: `0`.
- macOS/Linux에서는 Virtual 인터페이스로 개발·시험 가능.
