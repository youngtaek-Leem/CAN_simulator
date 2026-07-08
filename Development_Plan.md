# CAN 통신 평가 환경 개발 계획

> 작성일: 2026-07-03 / 상태: **구현 완료** (2026-07-04, 전 단계 검증 통과 — 실행 방법은 README.md 참조)

## 1. 컨텍스트

브라우저는 하드웨어에 직접 접근할 수 없으므로, **로컬 PC에서 실행되는 파이썬 백엔드 서버**가 USB-CAN 어댑터(PCAN, Vector CANcase)로 물리 CAN 통신을 수행하고, **웹 프론트엔드**는 백엔드와 REST + WebSocket으로 통신하는 구조로 개발한다. 사용자는 웹 화면에서 컴포넌트를 자유롭게 배치·크기조절하여 자신만의 CAN 평가 GUI를 구성하고, DBC로 파싱된 신호를 컴포넌트에 할당해 송수신한다.

## 2. 기술 스택

| 영역 | 선택 | 근거 |
|---|---|---|
| 백엔드 | Python 3.11+, FastAPI + uvicorn | WebSocket/REST 동시 지원, 비동기 처리 |
| CAN 드라이버 | python-can (`pcan`, `vector`, `virtual` 인터페이스) | PCAN·CANcase 모두 공식 지원, BLF/ASC 리더 내장 |
| DBC 파싱 | cantools | 신호 인코딩/디코딩, DBC 속성(GenMsgSendType 등) 접근 가능 |
| 프론트엔드 | React + TypeScript + Vite | 컴포넌트 기반 GUI 빌더 구현에 적합 |
| 레이아웃 | react-grid-layout | 드래그 배치 + 크기 조절 기본 제공 |

## 3. 아키텍처

```
[브라우저: React GUI 빌더]
   │  REST (설정: DBC 업로드, 채널 연결, 레이아웃 저장)
   │  WebSocket (실시간: CAN 수신 스트림, 송신 명령, replay 상태)
   ▼
[FastAPI 백엔드 (로컬 PC)]
   ├─ can_manager: 버스 연결/해제, 수신 리스너 (pcan / vector / virtual)
   ├─ dbc_service: DBC 파싱, 신호 인코딩/디코딩, Event/Periodic 속성 판별
   ├─ tx_scheduler: 주기 송신(최대 20개) + Event 신호 30ms invalid 처리
   ├─ replay_service: BLF/ASC 로드, Tx Pass/Stop 필터, 타임스탬프 재생
   └─ timer_util: Windows에서 timeBeginPeriod(1ms) 적용 (macOS/Linux는 no-op)
```

- **수신 경로**: python-can Notifier(수신 스레드) → asyncio 큐 → WebSocket으로 배치 전송. 백엔드에서도 20~50ms 단위로 메시지를 묶어 전송해 브라우저 부하를 줄인다.
- **송신 규칙**:
  - Periodic 신호: 설정 주기로 계속 송신 (전용 스레드 스케줄러, 1ms 타이머 해상도).
  - Event 신호: 유효값 송신 → 30ms 후 invalid 값(해당 신호 비트로 표현 가능한 최대값, 예: 8bit → 0xFF) 송신.
- **프론트 렌더링 최적화(사양 명시)**: WebSocket 수신 데이터는 DOM을 직접 만들지 않고 in-memory store에만 반영 → `requestAnimationFrame` 기반 throttle로 초당 10~60회만 UI 갱신. 갱신율은 설정 메뉴에서 변경 가능.

## 4. 디렉터리 구조

```
CAN_simulator/
├── backend/
│   ├── main.py              # FastAPI 앱, WebSocket/REST 라우팅
│   ├── can_manager.py       # 버스 연결, 수신 Notifier
│   ├── dbc_service.py       # DBC 파싱, 인코딩/디코딩
│   ├── tx_scheduler.py      # Periodic/Event 송신 스케줄러
│   ├── replay_service.py    # BLF/ASC replay
│   ├── timer_util.py        # Windows 1ms 타이머 해상도
│   ├── requirements.txt
│   └── tests/               # pytest (virtual 버스 기반)
├── frontend/
│   ├── src/
│   │   ├── components/      # 8종 위젯
│   │   ├── store/           # CAN 데이터 store + rAF throttle 렌더러
│   │   ├── layout/          # react-grid-layout 기반 GUI 빌더
│   │   └── api/             # REST/WebSocket 클라이언트
│   └── package.json
├── Requirement.md           # 확정 사양 반영해 업데이트
└── CLAUDE.md
```

## 5. GUI 컴포넌트 (8종, 모두 크기 조절 가능)

1. **CAN 메시지 표시창** — 수신 메시지를 ID별 최신값 갱신형 그리드로 표시 (raw + DBC 디코딩 값)
2. **텍스트 표시창** — 할당된 신호의 디코딩 값을 텍스트로 표시
3. **버튼** — 클릭 시 할당된 신호 값 송신 (Event 속성이면 30ms invalid 규칙 적용)
4. **체크박스** — on/off 값을 신호에 매핑해 송신
5. **드롭다운** — 선택지→신호 값 매핑 송신
6. **슬라이더** — 연속 값 송신
7. **CAN 메시지 전송 박스** — 메시지 추가(최대 20개), ID/주기/데이터 설정, Start/Stop 버튼
8. **CAN 로그 replay 박스** — BLF/ASC 파일 로드, Tx Pass/Stop 필터, Replay Start/Stop 버튼

공통 사항:
- 편집 모드(배치/크기 조절/신호 할당) ↔ 실행 모드 전환.
- 신호 할당은 DBC 파싱 결과를 메시지→신호 트리로 보여주고 선택하는 방식.
- 레이아웃 + 신호 할당 정보는 JSON으로 저장/불러오기.

## 6. 구현 단계 (각 단계 검증 후 다음 단계 진행)

| 단계 | 내용 | 검증 방법 |
|---|---|---|
| 1 | 스캐폴딩 + 백엔드 코어: FastAPI 앱, virtual CAN 버스 연결/수신, WebSocket 스트림 | pytest로 virtual 버스 송수신 확인 |
| 2 | DBC 서비스: 업로드/파싱 API, 신호 인코딩/디코딩, Event/Periodic 판별 | 샘플 DBC 단위 테스트 |
| 2-1 | 테스트용 샘플 파일 제작: Vector 포맷 샘플 DBC (Periodic/Event 메시지, 1~16bit 신호, VAL_ 테이블, GenMsgSendType·GenMsgCycleTime·GenSigSendType 속성 포함) + replay용 샘플 BLF/ASC 로그 생성 (`samples/` 폴더) | cantools 파싱 성공 + python-can 리더로 로드 확인 |
| 3 | 송신 스케줄러: 최대 20개 주기 송신, Event 30ms invalid 규칙, Start/Stop | virtual 버스에서 주기·타이밍 테스트 |
| 4 | 로그 replay: BLF/ASC 리더, Tx 필터, 재생 제어 | 샘플 로그 파일로 테스트 |
| 5 | 프론트 코어: Vite+React, WebSocket 연결, store + rAF throttle (10~60Hz 설정 메뉴) | 고빈도 수신 시 브라우저 안정성 확인 |
| 6 | GUI 빌더 + 8종 컴포넌트: 배치/리사이즈, 신호 할당 UI, 레이아웃 저장/불러오기 | 브라우저에서 시나리오 수동 확인 |
| 7 | 통합 검증 + 문서화: virtual 버스 E2E, Requirement.md 확정 사양 반영, 실행 방법 문서화 | 송신→수신→표시 전체 흐름 확인 |

## 7. 검증 방법

- **백엔드**: pytest + python-can `virtual` 인터페이스 — 하드웨어 없이 전 기능 테스트 가능.
- **프론트**: 빌드 + 브라우저에서 고빈도(50ms 이하 주기, 수십 개 메시지) 수신 시나리오 확인.
- **실기 검증 (PCAN/CANcase)**: Windows PC + 실제 어댑터 필요 → 개발 완료 후 사용자 환경에서 최종 확인 요청.

## 8. 사양 확인 요청 사항

아래 제안대로 진행할 예정이며, 수정이 필요하면 알려주세요.

1. **Event/Periodic 판별 기준**: DBC 표준 속성(GenMsgSendType / GenSigSendType)에서 자동으로 읽되, UI에서 수동 변경도 가능하게 구현. DBC에 해당 속성이 없는 경우를 대비.
2. **가상 CAN 버스 모드 추가 (사양 외 제안)**: 하드웨어 없이 개발·테스트 가능하도록 `virtual` 인터페이스 선택지를 채널 설정에 포함.
3. **레이아웃 저장/불러오기 추가 (사양 외 제안)**: 디자인한 GUI를 JSON으로 저장/복원.
4. **CANcase 제약**: Vector 장비는 Windows 전용 XL 드라이버가 필요하므로 실기 동작은 Windows에서만 가능. 현재 개발 머신이 macOS이므로 개발·테스트는 virtual 버스로 진행하고, Windows 타이머 최적화(timeBeginPeriod 1ms)는 OS 감지 후 적용.
5. **invalid 값 정의 확인**: "신호에 할당된 비트로 표현할 수 있는 가장 큰 값"(예: 4bit → 0xF)으로 구현. 사양 원문의 "nvalid"는 "invalid"의 오타로 해석함.
