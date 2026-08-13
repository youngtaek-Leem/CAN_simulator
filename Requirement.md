기반 CAN 통신 평가 환경을 개발 하려고 한다. 

웹 기반 앱(브라우저)은 하드웨어 직접 접근이 제한되므로, 로컬 PC에서 실행되는 파이썬 백엔드 서버(API)와 통신하고, 
백엔드 서버가 USB-CAN 어댑터를 통해 물리적으로 CAN 통신을 수행하도록 설계해야 한다.

사용하려는 CAN 장비는 P-CAN 과 CAN-CASE 이다.

웹기반에 아래와 같은 콤퍼넌트를 이융해 자유롭게 GUI 를 디자인 할수 있는 환경이 필요 하다.
각 콤퍼넌트의 크기 조절이 가능해야 한다. 
1. Can message display 창.
2. Text message display 창. 
3. 버튼
4. 체크박스
5. 드랍다운박스
6. 슬라이드
7. CAN 메세지 전송 박스
    1. 전송 하기 원하는 CAN message 를 추가하는 메뉴가 필요.(최대 20개 메세지 추가 할수 있게)
    2. 위 에 추가한 메세지에 대해 메세지 ID, 발송 주기, message 값 설정 하는 메뉴가 필요.
    3. 메세지 전송을 start/stop 하는 버튼이 필요.
8. CAN log replay 박스
    1. 파일 불러 오기 기능( 가능하면 *.blf, *.asc 모두 지원가능하게)
    2. Tx 메세지 Pass , Stop 필터.
    3. Replay Start, stop 버튼.

이 컴퍼넌트에 CAN 신호를 할당해 원하는 값을 전송할 수 있도록 구현한다.  
CAN 신호 할당을 위해서 CAN 데이터를 쉽게 보기 위하여 DBC 를 이용하여 파싱된 데이터를 보여주어야 한다. 
신호 값을 전송할 때는 아래 규칙을 따른다. 
1. CAN 신호의 속성이 Event 속성이면 유효한 값을 전송 하고 30ms 후에 nvalid 값(CAN 신호에 할당된 비트로 표현할 수 있는 가장큰 값)을 전송한다. 
2. CAN 신호의 속성이 Periodic 속성이면 유효한 값을 설정된 주기에 따라서 계속 보낸다. 

가능하면 백엔드 실행 시 윈도우 시스템 타이머 해상도를 1 ms로 고정해 주는 추가 최적화 라이브러리(win-precision-timer등)를 함께 적용해서 안정적으로 동작하도록 구현한다. 

짧은 주기(50ms이하)로 수십 개의 메세지가 송신,수신 하더라도 웹브라우저가 안정 적으로 동작 하도록 개발한다.  
이것을 구현하기 위해 JavaScript 단에서 데이터를 받을 때마다 곧바로 DOM 엘리먼트를 생성하지 말고, 객체 형태로 상시 업데이트를 수행한 뒤 requestAnimationFrame을 이용해 UI를 초당 10~60회만 갱신(Throttle)하도록 최적화 한다. 
이 갱신 값은 쉽게 변경할 수 있게 설정 메뉴로 구성한다. 

---

## 확정 사양 (개발 중 구체화된 사항, 2026-07-04)

1. Event/Periodic 판별 기준 (2026-07-11 변경): 메시지 코멘트(`CM_ BO_`)의 맨 앞 "[태그]"를
   기준으로 판별한다 — `[P]`, `[PE]` 태그는 Periodic, 그 외 태그(`[EC]`, `[EW]`, `[TP]` 등)나
   태그가 아예 없는 메시지(예: `NM_*` 네트워크 매니지먼트 프레임)는 모두 Event로 분류한다.
   위젯 설정 UI에서 신호 단위로 수동 변경(override)하면 이 판별보다 우선한다.
   (최초에는 DBC 속성 GenSigSendType/GenMsgSendType 기반으로 판별했으나, `OnChangeWithRepetition`
   같은 값이 이벤트/주기 어느 쪽에도 명확히 안 걸리고 속성이 없는 메시지의 최종 fallback이
   무조건 periodic이 되는 버그가 있어 변경 — 아래 26번 항목 참고.)
2. invalid 값 정의: 신호에 할당된 비트로 표현할 수 있는 가장 큰 raw 값 (예: 4bit → 0xF, 8bit → 0xFF).
   invalid 값은 신호 상태에 저장하지 않으며, 이후 같은 메시지 송신 시 마지막 유효값으로 복귀한다.
3. 가상 CAN 버스(virtual) 모드 추가: 하드웨어 없이 개발·테스트 가능 (python-can virtual 인터페이스).
4. 레이아웃 저장/불러오기 추가: 위젯 배치 + 신호 할당을 JSON으로 백엔드에 저장하고 복원한다.
5. Periodic 신호를 위젯(슬라이더 등)으로 조작하면 해당 메시지가 DBC GenMsgCycleTime 주기로
   자동 주기송신을 시작한다 (기본 100ms, 중지 API 제공).
6. Replay 필터 정의 (2026-07-04 변경): Pass/Stop 옆의 "메시지 선택" 메뉴에서 DBC 메시지를
   1~N개 선택해 필터에 적용한다. 메뉴는 DBC가 로드된 경우에만 활성화된다.
   - Pass 필터: 선택한 메시지만 재생
   - Stop 필터: 선택한 메시지를 제외하고 재생
   - 선택 없음: 전체 재생
7. 백엔드 수신 스트림은 WebSocket으로 묶음 전송(기본 30ms, 설정 가능)하여 브라우저 부하를 줄인다.
8. Vector CANcase는 Windows 전용 XL 드라이버가 필요하므로 실기 검증은 Windows에서 수행한다.
   Windows 타이머 1ms 고정은 winmm timeBeginPeriod로 구현(OS 자동 감지, 타 OS는 no-op).
9. 전역 Start/Stop (2026-07-04 추가): 상단 바 "CAN Simulator" 오른쪽의 Start/Stop 버튼으로
   전체 메시지 송수신을 제어한다. 정지 상태에서는 주기 송신·자동 송신·replay·신호 전송이 모두
   차단되고 수신 스트림도 표시되지 않는다. 편집 모드에서는 이 버튼이 비활성화되며 송수신이
   자동으로 정지된다.
10. 위젯 z-순서 (2026-07-04 추가): 클릭(선택)한 위젯이 겹친 위젯들 위로 올라온다.
11. 자동 정렬 메뉴 (2026-07-04 추가): "자동 정렬" 메뉴에서 바둑판(좌→우 행 채움) 또는
    계단식(대각선) 배치를 선택하면 전체 위젯이 자동 정렬된다. 위젯 크기는 유지된다.
12. CAN 메시지 표시창 표시 모드 (2026-07-04 추가):
    - 고정 모드: 동일 ID는 한 줄에 고정하고 최신 값으로 갱신 (기존 방식)
    - 스크롤 모드: 모든 수신 메시지를 시간순으로 스크롤 표시 (가상 스크롤로 대량 프레임 처리)
    - 일시중지 버튼: 누르면 화면이 동결되고 최근 1분 이내 수신된 모든 메시지를 스크롤로
      확인할 수 있다 (버퍼: 60초 / 최대 30,000 프레임). 재개하면 실시간 표시로 복귀.
    - 수신 타임 표시: 전역 Start를 누른 뒤 첫 수신 프레임을 0ms 기준으로 Time(ms) 컬럼에
      표시한다. Clear 버튼과 전역 재시작 시 기준이 리셋된다.
13. CAN-FD 지원 (2026-07-05 추가): virtual/PCAN/Vector 모두 CAN-FD(최대 64바이트,
    선택적 bitrate switch)를 지원한다.
    - 연결 설정: 상단 바 인터페이스 선택 옆 "FD" 체크박스 + 데이터 위상 비트레이트
      선택(1/2/4/5/8 Mbit/s). virtual은 FD 유무만, PCAN/Vector는 데이터 비트레이트도 사용.
    - DBC 신호: DBC의 VFrameFormat 속성(…CAN_FD)으로 FD 메시지를 자동 인식하고,
      해당 메시지의 신호를 위젯으로 조작하면 자동으로 FD+BRS 프레임을 송신한다.
    - TX 박스: DBC 미연동(raw ID) 행에는 F(FD)/B(bitrate switch) 체크박스를 두어
      수동으로도 FD 프레임을 구성할 수 있다.
    - CAN 메시지 표시창: FD 프레임에는 "FD" 또는 "FD+BRS" 배지가 ID 옆에 표시된다.
    - Classic 버스(FD 미활성화) 연결 상태에서 8바이트 초과 페이로드를 보내면
      백엔드가 400 에러로 거부한다 (하드웨어에 잘못된 classic 프레임이 나가는 것을 방지).
    - PCAN의 FD 비트 타이밍은 `backend/can_manager.py`의 FD_CLOCK_HZ(80MHz)/
      FD_SAMPLE_POINT/FD_DATA_SAMPLE_POINT(각 80%) 상수로 계산한다. 실제 어댑터의
      클럭이나 샘플포인트가 다르면 이 상수를 수정하거나 connect() 파라미터로 노출해야 한다.
14. CAN 메시지 표시창 — 신호 상세 보기 (2026-07-08 추가): 고정 모드에서 DBC로 디코딩된
    메시지 행 앞에 화살표(▸/▾)가 표시되며, 행을 클릭하면 그 아래에 해당 메시지에 포함된
    신호 목록(이름/값/단위)이 펼쳐진다. 값은 이미 스케일·VAL_ 라벨이 적용된 표시용 값을
    그대로 사용하며, 펼친 상태에서도 실시간(스로틀 갱신)으로 값이 갱신된다. 신호 정렬 순서는
    DBC 파일에 정의된 순서를 그대로 따른다(비트 위치 재정렬 없음). DBC 미매칭 ID는 화살표가
    없고 클릭해도 펼쳐지지 않는다. 스크롤 모드에는 적용하지 않는다(요청 범위 아님).
15. 위젯 위치·크기 변경 (2026-07-08 변경): 위젯 드래그(제목 표시줄) 이동과 리사이즈는
    편집 모드와 무관하게 항상 가능하다. 편집 모드는 위젯 설정(⚙)/삭제(✕) 버튼 노출과
    전역 송수신 자동 정지에만 영향을 준다.
16. 멀티 버튼 / 멀티 체크박스 위젯 (2026-07-08 추가): 버튼·체크박스를 격자로 모아 놓은
    위젯. 위젯 설정(⚙)에서 가로(열)·세로(행) 개수를 지정한다(기본 4x3, 최대 10x10).
    각 셀은 독립적으로 CAN 신호를 할당한다 — 편집 모드에서 셀 우측 상단의 작은 ⚙로
    셀 전용 설정(메시지/신호/라벨/전송값 또는 ON·OFF값)을 연다. 버튼 셀은 클릭 시 지정한
    값을 전송(Event/Periodic 규칙 그대로 적용), 체크박스 셀은 체크/해제 시 ON/OFF 값을
    전송한다. 신호 미할당 셀은 비활성화되고 `#번호`로 표시된다. 셀 설정은 위젯 옵션
    (JSON의 `cells` 배열)에 저장되어 레이아웃 저장/불러오기 시 함께 보존된다.
17. 멀티 버튼/체크박스 최소 가로 크기 제거 (2026-07-09 변경): 최소 너비(minW)를 12칸 그리드
    기준 1칸으로 낮춰 가로로 자유롭게 줄일 수 있다. 위젯의 리사이즈 최소 크기(minW/minH)는
    저장된 레이아웃 값이 아니라 항상 `frontend/src/widgets/registry.tsx`의 현재 값을
    실시간으로 따르므로(App.tsx `effectiveLayout`), 이전에 저장된 레이아웃을 불러와도
    최신 제한이 즉시 적용된다.
18. ISO-TP(ISO 15765-2) 메시지 전송 위젯 (2026-07-09 추가): 8바이트를 초과하는 데이터를
    ID와 데이터를 그대로 입력하면 자동으로 TP 프레임으로 분할해 전송한다.
    - 입력: TX ID(hex), FC ID(hex, 8바이트 초과 시에만 필요), FC 타임아웃(ms), 확장 ID
      여부, 데이터(hex, 공백 허용) — ID와 데이터 입력창은 분리되어 있다.
    - 7바이트 이하: Single Frame으로 즉시 전송 (Flow Control 불필요).
    - 8바이트 이상(최대 4095바이트): First Frame 전송 → 지정한 FC ID로 수신측의
      Flow Control(FC) 프레임을 대기(타임아웃 있음) → FC의 Flow Status(CTS/WAIT/Overflow),
      Block Size(BS), STmin을 그대로 따라 Consecutive Frame을 블록 단위로 전송한다.
      표준(ISO 15765-2) 준수 방식으로, FC를 무시하고 고정 간격으로 밀어내는 방식은
      채택하지 않았다(사용자 확인, 2026-07-09).
    - 모든 프레임은 8바이트로 패딩(0x00)한다. 백엔드: `backend/isotp_service.py`
      (`send()` 함수, 수신·재조립은 구현하지 않음 — 송신 전용).
    - 전역 Start/Stop 및 버스 연결 상태를 따른다(다른 송신 기능과 동일).
19. 위젯 키보드 조작 (2026-07-09 추가, 2026-07-09 수정): 최초 구현은 네이티브
    `<button>`/`<input type=checkbox>`/`<input type=range>` 요소의 브라우저 기본 동작에
    의존했으나, 사용자 실사용 확인 결과 Space·화살표 키가 동작하지 않는 문제가 보고되어
    네이티브 동작에 의존하지 않는 명시적 `onKeyDown` 핸들러로 다시 구현했다. 버튼(단일/
    멀티 셀)은 Space/Enter, 체크박스(단일/멀티 셀)는 Space, 슬라이더는 ArrowLeft/Right/Up/
    Down(step 단위 증감)·Home(최소)·End(최대)에서 각각 `preventDefault()`로 네이티브 기본
    동작을 취소하고 동일한 전송 로직을 직접 호출한다(중복 전송 방지). 브라우저에서 실제
    dispatchEvent(keydown)로 재검증 완료 — 체크박스 토글, 슬라이더 값 변경 모두 정상 동작
    확인 (`frontend/src/widgets/controls.tsx`, `frontend/src/widgets/MultiControls.tsx`).
20. 신호 선택 메시지 리스트 정렬 및 RX/TX 분리 (2026-07-09 추가, 2026-07-09 확장,
    2026-07-09 버그 수정, 2026-07-09 기준 노드 재정의): 위젯 설정에서 CAN 메시지를 고를
    때(위젯 바인딩, 멀티 셀, TX 박스, Replay 필터, 그래프 위젯 공통) 항상 이름 알파벳순으로
    정렬된다. 최초에는 optgroup으로 시각적 구분만 했으나, 위 5곳 모두에 전체/TX/RX 토글
    버튼(`MessageFilter` 컴포넌트)을 추가해 클릭 시 목록 자체를 해당 그룹만 남도록 실제로
    필터링하도록 확장했다(전체=optgroup 2개 그룹, TX/RX=평평한 단일 목록). **버그**: 확장
    직후 5곳 모두에서 기준 노드 미설정 시 전체/TX/RX 버튼이 전부 비활성화되어 클릭해도
    아무 반응이 없었다(기준 노드는 기본값이 미설정 상태라 사실상 항상 재현됨) — 사용자가
    "필터 버튼이 선택되지 않는다"고 보고해 발견. `disabled` prop을 5곳 모두와
    `MessageFilter` 컴포넌트 정의에서 제거해 수정.
    **기준 노드 재정의(2026-07-09)**: 최초 설계는 "TX 노드"(시뮬레이터 자신을 대표하는
    노드 — 그 노드가 보내는 메시지 = TX)였으나, 이 도구의 실제 사용 시나리오(PCAN/CANcase로
    실제 DUT 하드웨어 한 대와 연결하고 나머지 모든 ECU를 시뮬레이터가 대신함)에는 반대
    방향이 맞다는 사용자 피드백("AMP_FD를 Rx 노드로 설정 후 이것에 맞게 분류")에 따라
    "RX 노드"(실제 DUT 노드 — 그 노드가 DBC상 송신자로 등록된 메시지는 시뮬레이터가
    "수신"하므로 RX, 나머지 전부는 시뮬레이터가 다른 모든 ECU를 대신해 "송신"해야 하므로
    TX)로 개념을 뒤집었다. 상단 바 DBC 로드 시 "RX 노드" 선택 드롭다운이 나타나며, 선택은
    브라우저에 저장되어(`localStorage` 키 `can-sim.rx-node`, 이전 `can-sim.tx-node`에서
    이름 변경 — 기존 저장값은 마이그레이션하지 않음) 새로고침해도 유지된다. RX 노드
    미설정 시에는 어떤 메시지도 확실히 RX로 판단할 근거가 없으므로 전체가 TX로 표시된다
    (이전 TX 노드 방식의 미설정 기본값 RX와 반대). 백엔드 `dbc_service.summary()`가
    `nodes`(전체 노드 목록)와 메시지별 `senders`를 노출하며, 프론트 `groupedMessages()`
    (`frontend/src/store/appContext.ts`)와 `canStore.getRxNode()`/`setRxNode()`
    (`frontend/src/store/canStore.ts`)가 분류 로직을 담당한다. AMP_HS_260201.dbc(노드
    "AMP")로 실 데이터 검증: RX 노드="AMP" 설정 시 RX 23개/TX 112개(전체 135개)로 정상
    분류됨을 확인했다.
21. CAN 신호 그래프 위젯 (2026-07-09 추가, 2026-07-09 재구성 1차: 가로 배치, 2026-07-09
    재구성 2차: 세로 배치): Canvas 2D 기반 시계열 차트. 최초에는 위젯 하나의 캔버스 한
    장에 여러 신호를 겹쳐 그렸으나, 신호마다 값 범위가 크게 달라 Y축을 공유하기 어렵다는
    문제로 신호를 추가할 때마다 독립된 미니 차트로 분리했다. 처음에는 가로로 나란히
    배치했다가, 사용자 요청으로 세로로 쌓는 방식(`.graph-charts-col`, `flex-direction:
    column`)으로 다시 변경했다 — 모든 미니 차트가 같은 시간축을 공유하므로 X축(시간)
    눈금 라벨은 맨 아래(목록의 마지막) 차트에서만 그리고, 나머지 차트는 세로 격자선만
    그리고 숫자 라벨은 생략해 반복을 줄인다(`SignalChart`의 `showXAxis` prop, 목록에서
    마지막 원소일 때만 true; 각자 자체 canvas·X/Y 뷰 상태·확대축소·팬 상태는 여전히
    독립적으로 가짐). 신호 추가는 위젯 상단의 "+ 신호 추가" 버튼(편집 모드)으로 메시지·
    신호를 선택한다(메시지 선택 시 TX/RX/전체 필터 버튼 포함). 각 샘플은 점으로 표시되고
    연속된 점은 선으로 연결된다. 미니 차트마다 X축·Y축을 독립적으로 확대·축소한다: 플롯
    영역 위 휠은 X·Y 동시 확대, 아래쪽 X축 라벨 영역 위 휠은 X만, 왼쪽 Y축 라벨 영역 위
    휠은 Y만 확대·축소되며, 커서 위치를 기준점으로 확대된다(줌해도 커서 아래 데이터
    지점이 고정됨). 플롯 영역을 드래그하면 팬(이동)된다. 차트별 헤더의 "⟲"으로 해당
    차트만 자동 맞춤 상태로 되돌리고, 편집 모드의 "✕"으로 해당 신호만 제거한다. 신호
    데이터는 그래프에 실제로 추가된 신호에 한해서만 기록되며(`canStore.watchSignal`/
    `unwatchSignal`로 참조 카운트 관리), 신호당 최대 10,000개 샘플을 보관한다. 실 데이터로
    2개 신호를 동시에 추가해 세로 배치, 마지막 차트에만 X축 라벨 표시, 각기 다른 Y축
    자동 범위, 독립적인 확대축소가 모두 정상 동작함을 확인했다. 프론트:
    `frontend/src/widgets/GraphWidget.tsx`.
22. 그래프 X축 롤링 윈도우 + +/- 확대축소 버튼, 슬라이더 최대값 비트 기반 fallback,
    선택형(VAL_) 신호 그래프 미표시 버그 (2026-07-10 추가/수정):
    - **X축 롤링 윈도우**: 기존에는 X축이 수신된 전체 히스토리의 min~max로 자동 맞춤되어,
      시간이 지날수록 오래된 데이터까지 포함하며 화면상 그래프가 계속 압축되어 보이는
      문제가 있었다. 기본 동작을 "현재 시각 기준 최근 10초" 롤링 윈도우로 변경했다
      (`canStore.nowMs()` — 백엔드와 프런트가 로컬 1대에서 같이 도는 도구라는 전제로
      `Date.now()`와 `timeBase`만으로 벽시계 기준 "현재" 위치를 계산, 신호 갱신이 뜸해도
      창이 계속 앞으로 흐름). 이 윈도우 크기(기본 10.0초, 500ms~5분, 배율 1.5배)는
      위젯 하나에 여러 신호가 있어도 전부 동일하게 적용되도록 `GraphWidget`(부모)의
      상태로 관리하며, 상단 툴바에 −/+ 버튼 1쌍만 두고 각 신호(`SignalChart`)는 이 값을
      prop으로만 받는다(처음엔 차트마다 개별 −/+ 버튼을 뒀었는데, 사용자가 "모든
      그래프에 동일하게 적용되도록 상단에 1개만" 요청해 통합). 개별 차트의 "⟲"은
      그 차트의 수동 팬/줌(휠·드래그) 상태만 초기화하고 공유 윈도우 크기는 건드리지
      않는다. 마우스 휠로 개별 차트의 X축을 확대·축소하면 그 차트만 커서 기준 절대
      확대(프리즈)로 전환되며(Y축과 동일한 방식), 공유 롤링 윈도우 크기에는 영향을
      주지 않는다 — 오직 상단 −/+ 버튼만 모든 차트의 라이브 윈도우 크기를 바꾼다.
      Y축 자동범위 계산도 전체 히스토리가 아니라 현재 보이는 X 구간의 샘플만
      사용하도록 함께 수정했다(오래된 이상치가 현재 화면의 스케일을 망치지 않도록).
      브라우저에서 신호 2개를 추가하고 상단 + 버튼을 눌러 두 차트의 `xWindowMs`
      prop이 동일하게 바뀜을 React 파이버로 직접 확인했고, 값을 3초 간격으로 8회
      전송해 오래된 값(t=0~9초)이 10초 창 밖으로 스크롤되어 사라지고 최근 값만
      보임을 확인했다.
    - **슬라이더 최대값**: 위젯에서 신호를 슬라이더에 바인딩할 때 최대값 입력의 기본값이
      DBC에 `maximum`이 없으면 무조건 100으로 고정되어 있었다. 이제 DBC가 `maximum`을
      선언하지 않은 경우 신호의 비트 폭에서 표현 가능한 최댓값(부호 없음: `2^length-1`,
      부호 있음: `2^(length-1)-1`, 각각 `scale`/`offset` 적용)으로 자동 설정된다
      (`signalBitMax()` in `frontend/src/store/appContext.ts`). 백엔드
      `dbc_service.summary()`가 신호별 `is_signed`를 새로 노출한다. 3bit/6bit 신호로
      각각 7, 63이 정확히 계산됨을 확인했다.
    - **선택형(VAL_) 신호가 그래프에 안 보이는 버그**: 백엔드가 VAL_ 테이블이 있는 신호는
      원시 숫자가 아니라 사람이 읽는 라벨 문자열(예: "Off"/"TakeOverReq")로 디코딩해
      보내는데, `canStore.ingestFrames()`가 `typeof value === 'number'`인 경우에만
      시계열 히스토리에 기록해 문자열로 오는 선택형 신호(`Warn_Sound_FCW` 등)는 조용히
      드롭되고 있었다 — 사용자가 "신호는 뜨는데 그래프에는 안 보인다"고 보고해 발견.
      DBC가 로드될 때 `canStore.setDbc()`로 신호별 라벨→원시값 역방향 조회 테이블을
      만들어두고(`choiceReverse`), 값이 문자열로 오면 이 테이블로 원시 숫자를 복원해
      기록하도록 수정했다. 다른 위젯(텍스트 표시, 신호 상세 등)은 문자열 그대로 표시하는
      기존 동작 그대로 유지되며 영향 없음. `Warn_Sound_FCW`(3bit, VAL_ 0~7)로 값을
      바꿔가며 전송해 그래프에 계단형 변화가 정상적으로 그려짐을 확인했다.
23. Windows 실행 시 백엔드 API/WebSocket 연결 실패 버그 (2026-07-10 발견/수정): Windows에서
    `run_windows.bat`로 실행 후 브라우저에서 화면은 정상 표시되지만 "서버 끊김(재시도
    중)"이 계속 뜨고 DBC 업로드 시 "Failed to fetch"가 발생한다는 사용자 보고로 발견.
    원인: `frontend/src/api/client.ts`의 백엔드 URL 결정 로직이 개발 서버 포트(5173)
    여부만으로 분기했는데, 이 세션 중 macOS 개발 환경의 포트 충돌(5173→5174)을 우회하려고
    만든 `frontend/.env.local`(`VITE_BACKEND_URL=http://127.0.0.1:8000`)이 Vite 빌드
    시점에 `import.meta.env.VITE_BACKEND_URL`로 그대로 번들에 박혀, 커밋된 프로덕션
    번들(`frontend/dist`)에 `http://127.0.0.1:8000`이 하드코딩되어 있었다. 이 때문에
    사용자가 브라우저에서 `http://127.0.0.1:8000`이 아닌 다른 주소(예: `localhost:8000`,
    LAN IP, 포트 변경 시)로 접속하면 API/WebSocket 요청이 실제 서빙 origin과 다른 곳으로
    나가 CORS 차단·연결 실패가 발생했다. 수정: `import.meta.env.DEV`(Vite가 제공하는
    "개발 서버로 실행 중인지" 플래그)로 분기해, 프로덕션 빌드에서는 `VITE_BACKEND_URL`
    같은 개발자 로컬 환경변수가 무엇이든 상관없이 항상 상대 경로(`BASE = ''`)를 쓰도록
    강제했다 — 프로덕션은 항상 `backend/main.py`의 `StaticFiles` 마운트로 프런트와
    API가 같은 FastAPI 프로세스·같은 origin에서 서빙되므로, 상대 경로가 접속 호스트명에
    관계없이 항상 올바르게 동작한다. 개발 서버(`vite dev`)에서만 `VITE_BACKEND_URL`
    오버라이드(없으면 `http://127.0.0.1:8000` 기본값)를 사용해 별도 포트의 벡엔드를
    가리킨다. WebSocket URL 조립도 `||` 단락 평가 버그(빈 문자열 fallback이 사실상
    항상 죽은 코드였음)를 `? :` 삼항으로 고쳐 의도대로 동작하게 했다. 빌드 후
    `dist/assets/index-*.js`에 `127.0.0.1:8000` 문자열이 더 이상 존재하지 않음을
    `grep`으로 확인했고, 개발 서버에서도 정상 연결(서버 연결됨)됨을 재확인했다.
24. 전역 Stop/Start 정지 불완전, 위젯 삭제 후에도 신호 전송 지속, 그래프가 Stop 중에도
    계속 스크롤되는 문제, 그래프 확대/축소 배율 → 고정 5초 단위 변경 (2026-07-10):
    - **Stop이 모든 것을 완전히 멈추지 않음**: `run/stop`은 스케줄러를 일시정지(`_paused`)
      시켜 그 순간부터는 아무것도 새로 전송되지 않았지만, 위젯에서 만든 주기 신호
      자동 송신 항목(`_auto_entries`, 버튼/슬라이더 등으로 한 번이라도 periodic 신호를
      보내면 생성되어 그 메시지의 사이클타임마다 계속 재전송됨)은 지워지지 않고 그대로
      남아 있었다. 그래서 Stop 후 아무 조작 없이 다시 Start만 눌러도 예전에 만졌던
      위젯의 신호가 사용자도 모르게 즉시 재전송을 재개했다 — "Stop은 완전히 멈춘 상태,
      Start는 초기화 후 재시작"을 원한다는 사용자 요구와 어긋남. `run/stop`과 `run/start`
      양쪽에서 `tx_scheduler.stop_auto()`로 auto 항목을 전부 비우도록 수정했다
      (`backend/main.py`). Stop 직후 `auto_entries`가 즉시 빈 배열이 됨을, Start 후에도
      계속 빈 상태로 유지되며(이전에 만든 항목이 되살아나지 않음) 위젯을 다시 조작해야만
      새로 생긴다는 것을 `/api/status`로 직접 확인했다.
    - **위젯 삭제 후에도 신호가 계속 전송됨**: 위젯을 지워도 프런트는 위젯 목록에서만
      제거할 뿐 백엔드의 `_auto_entries`는 몰랐으므로, 위젯이 화면에서 사라진 뒤에도
      해당 메시지가 계속 주기 전송되고 있었다. `App.tsx`의 `removeWidget`이 삭제되는
      위젯의 바인딩(단일 바인딩 위젯은 `config.binding`, 멀티 버튼/체크박스는 셀별
      `binding`)에서 사용하던 메시지 이름을 모으고, 삭제 후 남은 위젯 중 같은 메시지를
      쓰는 것이 하나도 없으면 `POST /api/tx/auto/stop`으로 그 메시지의 자동 송신을
      끈다(같은 메시지를 다른 위젯이 아직 쓰고 있으면 유지). 사용자가 보고한 정확한
      재현 순서(위젯 신호 할당 → Start → 신호 전송 확인 → Stop → 위젯 삭제 → Start)를
      그대로 재현해 `auto_entries`가 끝까지 빈 상태임을 확인했다.
    - **그래프가 Stop 중에도 계속 스크롤됨**: 롤링 윈도우의 "현재 시각" 기준점
      (`canStore.nowMs()`)이 `Date.now()`를 그대로 썼기 때문에, 전역 Stop으로 데이터
      수신이 멎어도 벽시계 시간은 계속 흘러 그래프가 계속 스크롤되는 것처럼 보였다.
      `ingestStatus()`가 running true→false 전환 시점의 `nowMs()` 값을 얼려두고
      (`frozenNowMs`), Stop 상태인 동안 `nowMs()`가 그 값을 그대로 반환하도록 수정했다
      (Start 시 다시 null로 풀리고 `resetTimeBase()`로 새 타임라인이 시작됨). 브라우저에서
      Stop 직후와 4초 뒤의 그래프 X축 라벨이 완전히 동일함을 스크린샷으로 확인했다
      (Run 중에는 같은 시간 동안 라벨이 실제로 진행됨을 대조 확인).
    - **그래프 확대/축소를 5초 단위로 변경**: 위젯 상단의 +/- 버튼이 기존에는 배율(1.5배)
      방식이었는데, 클릭당 정확히 ±5초씩 창 크기가 바뀌도록 변경했다
      (`frontend/src/widgets/GraphWidget.tsx`의 `X_WINDOW_STEP_MS = 5000`, 덧셈 방식).
      10.0s에서 "+" 클릭 시 5.0s, 이어서 "−" 두 번 클릭 시 15.0s가 됨을 확인했다.
25. 그래프 Y축 자동 확대·축소 시 최소값이 0 미만(음수)으로 내려가지 않게 함 (2026-07-11):
    자동 맞춤 시 `yMin = lo - pad`(데이터 최솟값에서 10% 여백을 뺀 값)를 그대로 썼는데, 값이
    0에 가까운 신호(예: 대부분 0~1인 워닝 플래그)는 `lo=0`이어도 패딩 때문에 Y축 최소값이
    음수로 내려가 보였다. `yMin = Math.max(0, lo - pad)`로 클램프해 자동 맞춤 Y축 최소값이
    항상 0 이상이 되도록 수정했다(`frontend/src/widgets/GraphWidget.tsx`). 수동 팬/줌(휠·드래그)
    으로 사용자가 직접 음수 영역까지 내려보는 것은 그대로 허용된다. 값 0~1을 오가는 신호로
    테스트해 자동 맞춤 Y축 최소값이 정확히 0.00으로 고정됨을(패딩 적용 전이면 -0.1이 됐을
    상황) 스크린샷으로 확인했다.
26. Event/Periodic 판별을 메시지 코멘트 "[태그]" 기반으로 전면 변경 (2026-07-11): 사용자가
    `samples/AMP_FD_260501.dbc`를 직접 확인해 달라고 요청 — 이 DBC(및 같은 팀이 작성한
    DBC들)는 메시지 코멘트 맨 앞에 `[P]`(Periodic), `[PE]`(Periodic and On Event),
    `[EC]`(On Event and On Change), `[EW]`(On Event and On Write), `[TP]`(Transport
    Protocol) 같은 태그를 붙여 송신 방식을 문서화하는 관례가 있음을 확인했다(전체 120개
    메시지 중 106개에 태그, 나머지 14개는 전부 `NM_*` 네트워크 매니지먼트 프레임으로 코멘트
    자체가 없음). 이 태그는 DBC의 `GenMsgSendType` 속성과 100% 일치했다(`[P]`/`[PE]` ↔
    `GenMsgSendType=Cyclic`, 나머지 ↔ 속성 미설정). 사용자가 "`[P]`/`[PE]`만 Periodic, 나머지는
    전부 Event"로 이 태그를 직접 파싱해 판별하도록 지시해 `dbc_service.py`의
    `_signal_send_type()`을 전면 교체했다 — 기존 `GenSigSendType`/`GenMsgSendType` 속성 기반
    로직(및 `EVENT_TYPES`/`PERIODIC_TYPES` 매핑 테이블)을 제거하고, `message.comment`의 앞부분
    `[TAG]`를 정규식으로 뽑아 `{"P","PE"}`에 속하면 periodic, 아니면(태그가 다르거나 코멘트가
    아예 없으면) event로 판별하는 `_message_send_type()`으로 교체했다. 신호 단위 수동
    override(`set_send_type_override`)는 그대로 최우선 순위 유지. **버그 수정 겸함**: 기존
    로직은 `GenSigSendType`이 `EVENT_TYPES`/`PERIODIC_TYPES` 어느 쪽에도 정확히 안 걸리거나
    (예: `OnChangeWithRepetition`) `GenMsgSendType`이 미설정인 경우 최종 fallback이 무조건
    `"periodic"`이어서, `[EC]`/`[EW]`/`[TP]` 태그가 붙은(= 원래 Event여야 하는) 메시지의 신호들이
    실제로는 Periodic으로 잘못 분류되고 있었다(예: 메시지 1144 `CLU_WelcomeStartReq`,
    `GenSigSendType=OnChangeWithRepetition`). `samples/sample.dbc`의 `CM_ BO_` 코멘트에도
    같은 태그 컨벤션을 반영(`[P]`/`[EC]`)해 테스트와 실제 동작을 일치시켰다. 검증: DBC 전체
    786개 신호에 대해 태그로 계산한 기대값과 `signal_send_type()` 실제 출력을 전수 대조해
    불일치 0건 확인(periodic 77개 메시지, event 43개 메시지), `CLU_WelcomeStartReq`가
    이제 정확히 "event"로 나옴을 확인, 백엔드 테스트 45개(신규 1개 포함) 통과.
27. 그래프 선을 계단형(step)으로 변경 (2026-07-11): 기존에는 연속된 두 샘플 (x1,y1)→(x2,y2)를
    직선으로 바로 이어서, 값이 바뀌는 구간에서 마치 값이 서서히 변해가는 것처럼(대각선) 보였다.
    CAN 신호는 다음 샘플이 올 때까지 이전 값을 그대로 유지하는 성격이므로, 수평선(x1,y1)→
    (x2,y1) 후 수직선(x2,y1)→(x2,y2)로 잇는 step-after 방식으로 변경했다
    (`frontend/src/widgets/GraphWidget.tsx`의 선 그리기 루프). 브라우저에서 값을 0→3→1→2→0→3로
    바꿔가며 전송해 모든 구간이 수평/수직선만으로(대각선 없이) 계단형으로 그려짐을
    스크린샷으로 확인했다.
28. 색상 토큰 통합 + 상단 메뉴(topbar) "더보기" 드롭다운 (2026-07-14, 사용자 승인 완료): 색상
    조절이 쉬운 GUI를 만들어 달라는 요청으로 시작. 사용자 확인: 테마 전환 UI는 만들지 않고
    색상만 변수로 통합, `.control-widget`(Windows 스타일 버튼/체크박스/슬라이더 스킨)의 파란
    계열 색상도 `--accent`와 연동, topbar는 사용 빈도 낮은 그룹을 드롭다운으로 접는 구조적
    개선까지 진행.
    - **색상 변수 통합**(`frontend/src/styles.css`): `:root`에 `--bg-deep`, `--panel-3`,
      `--text-on-accent`, `--accent-hover`, `--accent-active`, `--primary`(=
      `var(--accent-active)`), `--danger`, `--banner-bg`, `--fd-color`를 추가하고, 기존에
      하드코딩돼 있던 배너/뱃지/그래프 헤더/신호 상세 배경/테스트 결과 색 등 약 15곳을 모두
      변수 참조로 교체했다. `.control-widget`의 Windows-blue(`#0078d7`/`#429ce3`/`#005499`)는
      역할별로 `var(--accent)`/`var(--accent-hover)`/`var(--accent-active)`로 교체해 이제
      앱의 accent 색을 바꾸면 이 버튼/체크박스/슬라이더의 강조색도 함께 바뀐다. 버튼/체크박스
      배경·테두리 등 순수 Windows 네이티브 회색 팔레트(`#e1e1e1`, `#adadad`, `#cccccc` 등)는
      요청 범위 밖이라 그대로 유지했다(의도적으로 고정된 하드웨어 패널 느낌).
    - **상단바 "⋯ 더보기" 드롭다운**(`frontend/src/App.tsx` `TopBar`): 사용 빈도가 낮은
      "함수 마스터 스크립트 업로드" 그룹과 "레이아웃 이름/저장/불러오기" 그룹을 우측 상단
      "⋯ 더보기" 버튼 뒤 드롭다운 패널(`.topbar-more-panel`)로 옮겼다. 버스 연결/DBC
      업로드/위젯 추가/편집 모드/자동 정렬/⚙ 설정은 그대로 상시 노출된다. 바깥 클릭 시
      닫히도록 `document`에 `mousedown` 리스너를 붙였다(드롭다운 열려 있을 때만 등록·해제).
    - 검증: `npm run build`(tsc+vite) 통과, 브라우저에서 실제 확인 — topbar가 줄바꿈 없이
      한 줄로 유지됨, "더보기" 클릭 시 두 그룹이 드롭다운에 나타남, 바깥 클릭 시 닫힘,
      `getComputedStyle`로 신규 변수 값이 의도대로 해석됨(`--primary`가 `--accent-active`를
      따라 `#1d4ed8`로 해석되는 것 포함) 확인. `grep`으로 `styles.css`에 남은 하드코딩 hex가
      `:root` 정의부와 의도적으로 유지한 Windows 중립 회색뿐임을 확인했다.
29. 상단바 "더보기" 드롭다운에 CAN 설정/DBC 업로드까지 통합 (2026-07-14): 28번 항목에서
    "더보기"로 옮긴 함수 스크립트/레이아웃에 더해, 사용자 요청으로 "CAN 설정"(인터페이스·
    채널·비트레이트·FD·연결) 그룹과 "DBC 업로드"(파일 업로드+RX 노드 선택) 그룹도 더보기
    드롭다운으로 옮겼다. 상시 노출 그룹은 이제 로고+Start/Stop, 위젯 추가/편집 모드/자동
    정렬, ⚙ 설정뿐이다. 드롭다운 내부는 "CAN 설정"/"DBC 업로드"/"FUNCTION SCRIPT"/
    "설정저장/불러오기"(최초 "레이아웃" → "설정 저장 불러오기" → 같은 날 최종
    "설정저장/불러오기"로 사용자 요청에 따라 순차 개명)
    4개 섹션으로 구분하고 구분선(`.topbar-more-section` 경계선)을 넣었다(`frontend/src/
    App.tsx`, CSS는 `.topbar-more-section`/`.topbar-more-heading` 추가).
    "함수 마스터 스크립트" 라벨/알림 메시지를 "Function Script"로 전면 변경했다. 연결 상태
    (`connected`)에 따른 각 입력의 disabled 로직, FD 체크 시 데이터 비트레이트 셀렉트
    노출 등 기존 동작은 변경 없이 그대로 이전했다. 검증: `npm run build` 통과, 브라우저에서
    더보기 드롭다운 내 FD 체크박스 토글 시 드롭다운이 닫히지 않고 데이터 비트레이트 셀렉트가
    바로 나타남을 확인, 콘솔 에러 없음.
30. Event 신호 미설정 형제 invalid 처리, 멀티 드롭다운/슬라이더, 신호 검색 입력, 멀티 페이지
    탭 (2026-07-15, 사용자 승인 완료 — 4개 모듈 개발 완료):
    - **Event 메시지의 다른 신호 invalid 처리** (`backend/dbc_service.py`): Event 신호를
      전송할 때, 같은 메시지에 있는 다른 신호가 raw 0으로(또는 이전에 보낸 값을 계속
      "기억"해서) 나가던 버그를 수정했다. **최초 구현(당일 동일 세션 내, 사용자 확인 후
      재수정)**: "한 번도 설정된 적 없는 신호만" invalid로 치환하고, 이미 한 번이라도
      설정된 적 있는 신호는 그 마지막 값을 계속 기억해서 재사용하는 `_touched` 이력 방식으로
      만들었으나, 사용자가 "이벤트 신호를 보낼 때 Valid(설정값) 하나만 실제 값이고, 같은
      메시지의 다른 신호는 이력과 무관하게 **항상** Invalid여야 한다"고 정정 — 최종 구현은
      이력(`_touched`)을 전부 제거하고, `encode_with_values`에서 지금 막 설정 중인 신호가
      하나라도 event 송신속성이면 그 호출에서 `values`에 없는 나머지 신호 전부를 각자의
      invalid 값(`(1<<length)-1`)으로 매번 무조건 치환한다. `encode_invalid`(30ms 후
      후속 프레임)도 마찬가지로 메시지의 모든 신호를 예외 없이 invalid로 인코딩하도록
      단순화했다(`_signal_state` 조회 자체가 불필요해짐). 두 경우 모두 치환은 **전송용
      프레임에만** 적용되고 영구 상태(`_signal_state`)는 오염되지 않으므로, 이후 실제로
      다른 신호를 설정하면 깨끗한 값부터 시작한다. Periodic 메시지(invalid 개념 없음,
      설정 중인 신호가 전부 periodic인 호출)는 영향받지 않고 기존처럼 상태가 누적된다.
      검증: 신규 pytest(`test_event_send_forces_other_signals_invalid_every_time` —
      한 신호를 설정한 직후 다른 신호를 설정해도 앞서 설정한 신호가 다시 invalid로
      나가는지, 영구 상태는 두 값 모두 정상 유지되는지 확인) 및 기존
      `test_invalid_value_encoding`을 새 사양에 맞게 갱신(형제 신호가 이제 마지막 값이
      아니라 invalid로 나가는지 확인), `test_untouched_periodic_sibling_stays_zero_not_invalid`
      포함 백엔드 전체 106개 테스트 통과.
    - **공용 신호 검색 입력 컴포넌트** (`frontend/src/widgets/MessageOptions.tsx`의
      `SignalPicker`): 기존 "메시지 선택 → 그 메시지의 신호 선택" 2단 select 방식과 별개로,
      신호 이름 일부를 입력하면 그 문자열을 포함하는 모든 신호(전체 메시지 대상)가
      "신호명 — 메시지명" 형태로 나열되고 클릭하면 메시지+신호가 한 번에 선택되는 검색
      입력을 추가했다. 기존 방식과 공존하며 같은 `binding` 상태를 공유한다. 이 컴포넌트로
      기존에 각자 따로 구현돼 있던 3곳 — `WidgetFrame.tsx`의 `ConfigModal`, `MultiControls.tsx`의
      `CellEditModal`, `GraphWidget.tsx`의 `AddSeriesModal`(기존엔 `messageName`/`signalName`
      개별 state였던 것을 `SignalBinding` 하나로 리팩터) — 을 통일했다. 검증: 브라우저에서
      "Speed" 검색 시 `EngineSpeed`(EngineData)/`Speed`(VehicleSpeed) 둘 다 나열되고 선택 시
      메시지·신호 select가 함께 갱신됨을 3곳 모두에서 확인, 콘솔 에러 없음.
    - **멀티 드롭다운 / 멀티 슬라이더 위젯** (`frontend/src/widgets/MultiControls.tsx`):
      기존 멀티 버튼/멀티 체크박스와 동일한 그리드 인프라(`getGrid`/`useCellUpdater`/
      `.multi-grid`)를 재사용해 `MultiDropdownWidget`(셀마다 독립적으로 신호의 VAL_
      선택지를 드롭다운으로 전송, 단일 `DropdownWidget`과 동일 로직)과
      `MultiSliderWidget`(셀마다 독립적으로 100ms 스로틀 전송, 단일 `SliderWidget`과 동일
      로직)을 추가했다. `MultiCell`에 슬라이더 전용 물리값 필드 `sliderMin`/`sliderMax`/
      `sliderStep`을 신설(기존 `rangeMin`/`rangeMax`/`step`은 Random 모드의 raw 값 전용이라
      의미가 달라 재사용하지 않음). `CellEditModal`의 `kind`에 `'dropdown'`/`'slider'` 추가,
      `WidgetFrame.tsx`의 행/열 개수 설정 노출 조건과 `registry.tsx`에도 등록. 검증: 브라우저에서
      `DriverCommand.TurnSignal`(VAL_ 선택지)을 멀티 드롭다운 셀에 할당해 "Left" 선택 시
      TX 2(valid+30ms invalid, Event 규칙)를 확인했고, `EngineData.EngineSpeed`(periodic)를
      멀티 슬라이더 셀에 할당해 2000rpm으로 이동 시 주기 자동 송신(+auto 1)이 걸리는 것을
      확인, 콘솔 에러 없음.
    - **멀티 페이지 탭** (`frontend/src/App.tsx`): 위젯 캔버스가 페이지 하나뿐이던 것을,
      `widgets`/`layout` 평면 배열을 `pages: {id, name, widgets, layout}[]` + `activePageId`
      구조로 리팩터해 상단바 아래 탭 바(`PageTabs`)로 여러 페이지에 위젯을 나눠 배치할 수
      있게 했다. `addWidget`/`updateWidget`/`arrange`/`effectiveLayout`은 활성 페이지에만
      스코프하되, `removeWidget`의 "다른 위젯이 같은 메시지를 아직 쓰는지" 체크는 숨겨진
      다른 탭의 위젯도 여전히 그 신호를 쓰고 있을 수 있으므로 전체 페이지를 훑도록 했다.
      캔버스는 `GridLayout` 인스턴스 하나만 유지하고 활성 페이지 데이터만 먹인다(탭 전환 시
      다른 페이지 위젯은 언마운트 — 백엔드 송수신은 프론트 렌더링과 무관하게 계속 동작하므로
      주기 신호는 탭을 벗어나도 안 끊기고, `GraphWidget`처럼 언마운트 시 `unwatchSignal`하는
      위젯만 탭을 벗어나면 기록이 멈춤). 편집 모드에서만 페이지 이름 변경(✎)·삭제(✕, 마지막
      1개는 삭제 불가)·추가(+ 페이지) 컨트롤이 보인다. 레이아웃 저장 형식을
      `{layout, widgets}` → `{pages: Page[]}`로 확장하되, `pages` 키가 없는 기존 저장
      파일은 불러올 때 자동으로 단일 페이지로 감싸 하위 호환을 보장한다(백엔드
      `backend/main.py`의 레이아웃 저장 API는 스키마 검증 없이 JSON을 그대로 저장/반환하므로
      백엔드 변경은 불필요했다). 검증: 브라우저에서 페이지 추가/이름변경(Sensors)/삭제(마지막
      1개는 삭제 버튼 사라짐, 삭제 시 활성 탭 자동 전환) 확인, 각 페이지에 다른 위젯을 넣고
      탭 전환해도 서로 섞이지 않음을 확인, 기존 레거시 단일 페이지 레이아웃("default")을
      불러왔을 때 "Page 1" 하나로 정상 마이그레이션됨을 확인, 새로 만든 2페이지 레이아웃을
      저장 후 다시 불러와 두 페이지와 각각의 위젯이 그대로 복원됨을 확인, 콘솔 에러 없음.
    - `npm run build`(tsc+vite) 전 모듈 공통으로 통과, 백엔드 전체 106개 pytest 통과(모듈 1
      외에는 백엔드 변경 없음, 회귀 없음 재확인). `.claude/launch.json`에 `backend`(uvicorn)
      실행 설정을 추가해 브라우저 검증 시 백엔드도 함께 띄울 수 있게 했다.
31. Event 신호 invalid 처리 정정 — 이력(remember) 방식 제거 (2026-07-15, 30번 항목의 사용자
    피드백 반영): 30번 항목 최초 구현은 "한 번도 설정된 적 없는 신호만" invalid로 치환하고
    이미 설정된 적 있는 신호는 마지막 값을 계속 "기억"해서 재사용했는데, 사용자가 "이벤트
    신호를 보낼 때 Valid(설정값)는 그 신호 하나뿐이고, 같은 메시지의 다른 신호는 이력과
    무관하게 항상 Invalid여야 한다"고 정정했다. `backend/dbc_service.py`에서 이력 추적용
    `_touched` 필드를 완전히 제거하고, `encode_with_values`는 지금 설정 중인 신호가 하나라도
    event 속성이면 그 호출에서 값이 주어지지 않은 나머지 신호 전부를 매번 무조건 각자의
    invalid 값으로 치환하도록 단순화했다. `encode_invalid`(30ms 후 후속 프레임)도 메시지의
    모든 신호(호출 대상 신호 포함)를 예외 없이 invalid로 인코딩하도록 단순화해 `_signal_state`
    조회 자체가 불필요해졌다. 두 경우 모두 치환은 전송 프레임에만 적용되고 영구 상태는
    오염되지 않는다. Periodic 전용 전송(설정 중인 신호가 전부 periodic)은 영향 없음. 검증:
    기존 `test_invalid_value_encoding`을 새 사양대로 갱신(형제 신호가 마지막 값이 아니라
    invalid로 나가는지)하고, `test_event_send_forces_other_signals_invalid_every_time`을
    신규 추가(직전에 설정한 신호도 다음 이벤트 전송 시 다시 invalid로 나가는지, 영구 상태는
    두 값 모두 정상 유지되는지)해 백엔드 전체 106개 테스트 통과.
32. 레이아웃 저장 시 DBC/Function Script **파일명만** 기록, 없으면 에러 표시 (2026-07-15,
    최초 구현 후 같은 날 사용자 피드백으로 재수정): 최초 구현은 "설정 저장할 때 DBC 파일과
    Function Script(json) 파일도 같이 저장해라"를 "파일 내용 전체를 레이아웃 JSON에 임베드"로
    해석해 `DbcService.raw_text`/`raw()`, `TestRunnerService._functions_raw`/`functions_raw()`,
    `GET /api/dbc/raw`/`GET /api/testrunner/functions/raw` 엔드포인트를 추가하고
    `{filename, content}` 전체를 저장 후 불러오기 시 자동 재업로드하는 방식으로 만들었으나,
    사용자가 "의도는 CAN 설정값 저장이었다. DBC/JSON은 각자 로컬에 이미 갖고 있으니 파일
    내용을 전부 저장할 필요 없이 파일명만 저장하고, 로컬에 그 파일이 없으면 에러 메시지를
    표시하라"고 정정했다 — 브라우저의 `<input type=file>`은 보안상 선택된 파일의 전체 경로를
    노출하지 않고 파일명만 제공하므로(임의 로컬 경로를 스크립트가 읽는 것은 애초에 불가능),
    "경로 저장 후 자동으로 가져오기"는 기술적으로 불가능하고 "파일명만 기록해 현재 로드
    상태와 대조"가 유일하게 가능한 구현이다. 최초 구현에서 추가했던 raw-content 관련 백엔드
    코드(`raw_text`/`raw()`/`_functions_raw`/`functions_raw()`/두 GET 엔드포인트/관련 테스트
    `test_dbc_and_function_script_raw_endpoints`)와 프론트 `api.getDbcRaw`/
    `getFunctionScriptRaw`, `loadLayout`의 File 재구성·자동 업로드 로직을 전부 제거했다.
    최종 구현: `saveLayout`은 현재 로드된 DBC/Function Script의 **파일명만**
    (`dbc.filename`, `canStore.status.test_runner.functions.filename`)
    `{filename}` 형태로 레이아웃 JSON에 저장(로드 안 돼 있으면 생략). `loadLayout`은 위젯
    페이지·CAN 설정을 복원한 뒤, 저장된 `dbc.filename`/`functionScript.filename`이 **현재
    로드돼 있는** DBC/Function Script 파일명과 다르거나 없으면
    `레이아웃 "…" 불러옴 — DBC(sample.dbc) 파일이 로드되어 있지 않습니다. 직접 업로드하세요.`
    형태의 에러를 배너로 표시한다(자동 업로드 시도 없음 — 각자 자기 로컬 파일을 직접
    업로드해야 함). `SavedFile` 타입도 `{filename, content}` → `{filename}`으로 단순화.
    검증: 백엔드 108→107개 테스트(raw 엔드포인트 테스트 제거) 통과, `npm run build` 통과.
    브라우저에서 DBC 업로드 후 저장 → 저장된 JSON 파일에 `content` 없이 `filename`만
    있음을 직접 확인 → 백엔드 완전 재시작(DBC 미로드로 리셋) 후 그 레이아웃을 불러와
    정확히 `DBC(sample.dbc) 파일이 로드되어 있지 않습니다` 배너가 뜨는 것을 확인, DBC를
    다시 업로드한 뒤 같은 레이아웃을 불러오면 `loadLayout`이 정상 실행(레이아웃 이름이
    올바르게 갱신)됨을 확인.
33. CAN 설정 저장/불러오기, 그래프 순서 변경, Y축 정수화, Random 범위 지정 (2026-07-15,
    사용자 승인 완료 — 4개 모듈 개발 완료):
    - **CAN 설정값 저장/불러오기** (`frontend/src/App.tsx`): `iface`/`channel`/`bitrate`/`fd`/
      `dataBitrate`가 `TopBar` 내부 로컬 state였던 것을 `App` 레벨 `canConfig` state로
      끌어올려, "설정저장/불러오기"에 `canConfig`를 항상 포함하도록 했다. 불러오기 시
      값만 복원하고 **실제 연결은 자동으로 하지 않는다**(연결은 부수효과 있는 동작이라
      사용자가 "연결" 버튼을 직접 눌러야 함). `canConfig` 키가 없는 기존 레이아웃은 현재
      값 유지(하위 호환). 검증: PCAN/1000kbit/FD로 값을 바꿔 저장 → Virtual로 되돌린 뒤
      불러오기로 PCAN/1000kbit/FD가 정확히 복원되고 자동 연결은 되지 않음을 브라우저에서
      확인.
    - **CAN 신호 그래프 순서 변경** (`frontend/src/widgets/GraphWidget.tsx`): 위젯 내부에
      쌓인 미니 차트(신호별)들의 순서를 바꿀 수 있도록 `moveSeries(index, dir)`를 추가하고,
      각 차트 헤더에 편집 모드 전용 "▲"/"▼" 아이콘 버튼을 추가했다(첫/마지막 차트는 해당
      방향 버튼 비활성화). 맨 아래 차트만 X축 라벨을 그리는 기존 로직은 배열 순서 기준이라
      별도 처리 없이 순서 변경에 자동으로 따라간다. 검증: 신호 2개 추가 후 ▼ 클릭으로
      순서와 X축 라벨 위치가 함께 바뀜을 브라우저에서 확인.
    - **Y축 정수 표시**: `fmt(v)`(Y축 눈금 전용 포맷 함수)를 `Math.round(v).toString()`으로
      단순화해 소수점 없이 정수만 표시하도록 했다(데이터/자동맞춤 계산 자체는 float 유지,
      표시 문자열만 정수화). 검증: 그래프 Y축에 소수점이 전혀 안 보임을 스크린샷으로 확인.
    - **Random 버튼 "Random" 모드에 범위 지정 지원** (`backend/tx_scheduler.py`
      `set_value_generator`): 기존엔 `mode="random"`이 항상 전체 bit 범위에서만 뽑았는데,
      `range` 모드와 동일한 클램핑 로직을 적용해 `range_min`/`range_max`가 주어지면 그 범위
      안에서, 없으면 기존처럼 전체 bit 범위에서 뽑도록 확장했다(`step`은 random과 무관해
      무시). 프론트(`WidgetFrame.tsx`, `MultiControls.tsx`)는 이미 `mode`와 무관하게
      `rangeMin`/`rangeMax`를 백엔드로 전달하고 있어 최소값/최대값 입력 UI를
      `{mode === 'range'}` 조건에서 빼내 Random/Range 두 모드 모두에서 보이도록만
      수정(step 입력만 Range 모드 전용으로 유지), 두 모드 모두 값모드 표시 라벨에 범위가
      지정돼 있으면 `Random 2~5`처럼 보이도록 개선. Range(순차 순환) 기능 자체는 변경
      없음. 검증: 신규 pytest `test_generator_random_respects_range` 포함 백엔드 108개
      테스트 통과. 브라우저에서 `DriverCommand.TurnSignal`(raw 0~15)을 Random 모드 +
      최소2/최대5로 설정 후 5회 클릭한 `raw_value` 응답이 각각 3,2,3,3,5로 모두 2~5
      범위 안에서만 나옴을 네트워크 요청으로 직접 확인.
    - `npm run build`(tsc+vite) 통과, 콘솔 에러 없음.

## Automation 시나리오 러너 통합 계획 (2026-07-11, 사용자 승인 완료 — Phase 1/2 개발 완료)

`Automation/AppTest.py`(Tkinter 데스크톱 앱)와 `Automation/test_script_Rev01.json`을 분석해
CAN_simulator 웹 앱에 통합하는 계획. AppTest.py는 JSON에 나열된 스텝을 순서대로 실행하는
인터프리터로, 스텝 타입은 `ID`(케이스 경계+반복횟수), `Power`(전원 On/Off), `delay`,
`CANReq`(신호 1회 전송), `CANEv`(전송 후 30ms 뒤 invalid 자동 전송 — CAN_simulator의 기존
Event 규칙과 동일), `CANResp`(timeout 내 기대값 수신 대기/판정), `CANlogReplay`(.blf 재생,
자기 자신의 TX ID 제외 필터 있음), `Audio`(StartREC/StopREC/compWAV — sounddevice 녹음 +
상호상관 기반 파형 비교), `AP`(RMS 측정 등 자리표시자), `Loop`(id/gotoid 텍스트 라벨을
스캔해 그 구간을 반복하는 수동 goto 방식). 실행 결과는 `{step, Signal, status}` 로그로
누적되어 타임스탬프 JSON 파일로 저장된다.

### 단계 구분
- **Phase 1 (이번에 개발, 승인 완료)**: CAN 관련 스텝만 — `ID`/`CANReq`/`CANEv`/`delay`/
  `CANResp`/`CANlogReplay`/`Loop`. 하드웨어 의존이 없어 virtual 버스로 전 과정 검증 가능.
- **Phase 2 (추후 별도 승인 후 진행)**: `Power`/`Audio`. 사용자 확인: "python 코드에 있는
  Power와 Audio 동작은 이미 검증이 끝난 코드이므로 그 방식대로 진행" — PyVISA SCPI
  전원 제어, sounddevice 녹음 메커니즘은 AppTest.py 방식을 그대로 이식한다. 단, WAV
  비교 알고리즘은 `Automation/compareWAV_MFCC.py`의 다중 지표(MFCC+DTW, 대역 제한 FFT
  상관계수, RMS/ZCR/스펙트럴 센트로이드)로 교체하고, 사이클끼리 비교하던 기존 방식 대신
  케이스별로 저장한 고정 기준(golden) WAV와 비교 + 통과 임계값을 설정 가능하게 개선한다
  (사용자 승인: "MFCC 다중지표로 개선").

### Phase 1 모듈 분해 — 개발 완료 (2026-07-11, 검증 통과)

| 모듈 | 책임 | 인터페이스 | 의존 | 검증 방법 | 상태 |
|---|---|---|---|---|---|
| `backend/test_runner_service.py` | JSON 시나리오 파싱 + CAN 스텝 순차 실행 + 케이스별 pass/fail 결과 생성 | `load(text, filename)`, `start()`/`stop()`(백그라운드 스레드), `summary()`(경량), `status()`(전체: events+results) | `dbc_service`, `can_manager`(신규 `add_listener`/`remove_listener`), `tx_scheduler`, `replay_service` | pytest 13개(`tests/test_test_runner_service.py`): 신구 Loop 파싱, `_type` 비활성 블록 스킵, raw hex→scaled 값 변환, 멀티 시그널 CANReq, CANResp pass/timeout, Loop 반복 횟수 정확성, 종료 시 auto_entries 클리어, 결과 파일 저장, stop() 중단, CANlogReplay(+ 송신 노드 제외 필터) | **통과** |
| `backend/main.py` API 확장 | 스크립트/로그파일 업로드·시작·중지 REST, 경량 상태를 `/api/status`에 포함 | `POST /api/testrunner/upload`, `/logfile/upload`, `/start`, `/stop`, `GET /api/testrunner/status` | test_runner_service | pytest 2개(`tests/test_api.py`): 업로드→시작→완료까지 REST 왕복, 연결 안 된 상태에서 거부됨, 중간에 stop() | **통과** |
| `frontend/src/widgets/TestRunnerBox.tsx` | JSON/로그파일 업로드 UI, 시작/중지, 스텝별 실시간 로그, 케이스별 pass/fail 배지 | 경량 상태는 `canStore.status.test_runner`(WS), 상세 로그·결과는 400ms 주기로 `GET /api/testrunner/status` 폴링 | WidgetFrame, canStore, api client | 브라우저에서 실제 업로드(fetch로 직접 재현)→Start→로그/결과 실시간 확인 | **통과** |

백엔드 60개 테스트(신규 15개 포함) 통과, `tsc`/`npm run build` 통과. 브라우저에서 실제
`EngineData.EngineSpeed`를 CANReq로 보내고 CANResp로 같은 값을 확인하는 시나리오를
업로드→실행해 "케이스 1 · 반복 1 · ✅OK"와 스텝별 로그(`[CANReq] EngineData → Sent`,
`[CANResp] EngineData EngineSpeed → OK`)가 실시간으로 표시됨을 확인했다.

### 통합 중 확정/구현된 개선 사항
1. **Loop 문법**: 신규 스크립트는 중첩 구조 `{"type":"loop","cycle":3,"steps":[...]}`를
   쓰고, 기존 `id`/`gotoid` 평면 스캔 방식 JSON도 자동 감지해 그대로 파싱하는 구버전
   호환 파서를 병행 지원한다(`parse_script()`/`_parse_step_list()`).
2. **CAN 연결 재사용**: AppTest.py는 채널·비트타이밍이 하드코딩된 별도 Vector 버스
   인스턴스를 새로 열지만, CAN_simulator는 상단 바에 이미 PCAN/Vector/virtual 연결
   UI가 있으므로 이를 그대로 재사용해 이중 연결을 피했다.
3. **CANReq/CANEv를 동일하게 처리**: `tx_scheduler.send_signal()`이 이미 DBC의 `[TAG]`
   기반 분류로 신호별 Event(30ms invalid)/Periodic 규칙을 정확히 적용하므로, AppTest.py처럼
   CANEv에서 수동으로 30ms 뒤 invalid를 다시 보내는 별도 로직이 필요 없다 — CANReq와
   CANEv를 완전히 동일하게 처리한다. Periodic 신호는 기존 위젯과 동일하게 auto_entries로
   계속 재전송되다가, 시나리오 실행이 끝나면(정상 종료·중단 모두) `tx_scheduler.stop_auto()`
   로 정리된다 — 전역 Start/Stop의 auto_entries 클리어와 동일한 패턴.
4. **원시값(raw) 기준 처리**: JSON의 `Value`는 물리값이 아니라 원시 16진수 비트 패턴이므로,
   CANReq/CANEv는 `raw*scale+offset`으로 물리값 변환 후 전송하고, CANResp는 `decode_raw()`
   (스케일·VAL_ 라벨 없이 원시값만 디코딩하는 신규 메서드)로 비교해 신호의 scale이나
   선택형 여부와 무관하게 항상 정확히 비교되도록 했다 — AppTest.py 원본은 이 변환이 없어
   scale≠1인 신호에서는 값이 어긋날 수 있는 잠재 버그가 있었다.
5. **CANlogReplay 제외 필터를 DBC 노드 기반으로**: AppTest.py의 하드코딩된 16진 ID
   제외 목록 대신, 스텝에 `"excludeSenders": ["AMP_FD"]`처럼 DBC 노드 이름을 적어주면
   `message.senders`를 통해 자동으로 frame_id를 찾아 제외한다 — 포터블하고 DBC가
   바뀌어도 그대로 재사용 가능.
6. **결과 리포트**: 로컬 JSON 파일 저장은 유지하되, 브라우저에서 케이스별 pass/fail과
   스텝별 로그를 실시간으로 바로 확인할 수 있게 했다(Phase 2에서 오디오 파형 비교
   그래프까지 확장 검토).

### Phase 2 모듈 분해 — 개발 완료 (2026-07-11, 검증 통과)

사용자 지시: "AppTest.py 코드에 있는 Power와 Audio 동작은 이미 검증이 끝난 코드이니 실수
없이 integration 하면 잘 동작할 것이다" — SCPI 전원 제어 비트마스크와 sounddevice
녹음 메커니즘은 AppTest.py 원본 그대로 이식했다(값 하나도 바꾸지 않음). 개선한 부분은
사전 승인된 두 가지(WAV 비교 알고리즘, CANlogReplay 제외 필터)뿐이다.

| 모듈 | 책임 | 인터페이스 | 의존 | 검증 방법 | 상태 |
|---|---|---|---|---|---|
| `backend/power_supply_service.py` | PyVISA SCPI로 ACC/IGN 전원 비트마스크 제어(AppTest.py `PowerSupply` 그대로 이식) | `connect()`/`disconnect()`/`info()`/`set_power(block)` | 없음(pyvisa 선택적 의존 — 미설치/무장비 시 `initialized=False`로 우아하게 저하) | pytest 5개(`tests/test_power_supply_service.py`): 초기 미연결 상태, 무장비 연결 시 저하, 미연결 시 제어 거부, AppTest.py와 동일한 비트마스크 전이 로직, BATT 커맨드 | **통과** |
| `backend/audio_service.py` | 녹음(AppTest.py `Audio` 그대로 이식) + 다중 지표 WAV 비교(`compareWAV_MFCC.py` 이식: MFCC+DTW, 전체/대역제한 FFT 상관계수, 상호상관, RMS/ZCR/스펙트럴센트로이드) + golden 기준 WAV 저장/비교 | `start()`/`stop()`, `compare(rec_path, golden_name, threshold)`, `save_as_golden()`, `list_devices()`/`select_device()` | 없음(sounddevice/librosa/scikit-learn 선택적 의존) | pytest 9개(`tests/test_audio_service.py`): 무장비 시 장치목록 조회 안전, 장치 미선택 시 녹음 거부, 비교 대상 파일 없음 처리, 동일 신호 비교 시 통과, 무음 vs 톤 비교 시 실패, 7개 지표 모두 반환, golden 저장/원본 없음 처리 | **통과** |
| `backend/test_runner_service.py` 확장 | 스텝 타입 `Power`/`Audio`(StartREC/StartRECtime/StartRECref/StopREC/compWAV/saveAsGolden) 실행, 서비스 미연결 시 해당 스텝만 Fail 처리하고 나머지 CAN 스텝은 계속 진행 | 생성자에 `power_service`/`audio_service` 선택적 주입 | power_supply_service, audio_service | pytest 5개 추가(`tests/test_test_runner_service.py`): Fake 서비스로 Power 스텝 호출 확인, 서비스 없을 때 우아한 실패, 녹음→비교 전체 시퀀스, golden 필드 누락 시 실패, saveAsGolden | **통과** |
| `backend/main.py` API 확장 | 전원/오디오 연결·상태·장치선택 REST, golden WAV 업로드 | `POST /api/power/connect`, `/disconnect`, `GET /api/power/status`, `GET /api/audio/devices`, `POST /api/audio/device`, `GET /api/audio/status`, `POST /api/testrunner/golden/upload` | power_supply_service, audio_service | pytest 3개(`tests/test_api.py`): 무장비 시 전원 API 우아한 저하, 오디오 장치 조회·선택, golden WAV 업로드(+ 비-wav 확장자 거부) | **통과** |
| `frontend/src/widgets/TestRunnerBox.tsx` 확장 | 전원 연결/해제 토글 버튼+상태, 오디오 장치 드롭다운+새로고침, golden WAV 업로드, 녹음 중 표시 | `canStore.status.power`/`.audio`(WS), `api.powerConnect/Disconnect`, `api.audioDevices/SelectDevice`, `api.uploadTestGolden` | WidgetFrame, canStore, api client | 브라우저 실제 확인(가상 버스+sample.dbc 연결 후): 전원 연결 클릭 시 실제 VISA 에러 메시지가 툴팁에 표시, 오디오 장치 드롭다운에 실제 맥 마이크 3개 나열 및 선택 시 `device_index` 반영, Power+Audio 스텝이 섞인 시나리오 실행 시 무장비 상태에서도 각 스텝이 우아하게 Fail 기록되며 나머지 CAN 스텝은 정상 진행되는 것을 실행 로그에서 확인 | **통과** |

백엔드 81개 테스트(Phase 2 신규 17개 포함) 통과, `tsc -b --noEmit`/`npm run build` 통과.
브라우저에서 가상 버스+sample.dbc로 Power(ACC_On)+CANReq(EngineSpeed)+Audio(StartRECtime+
compWAV)가 섞인 스크립트를 업로드해 실행: 파워서플라이 미연결 시
"[Power] ACC_On → 실패: 파워서플라이가 연결되어 있지 않습니다"가 기록된 채로 다음
CANReq 스텝은 정상 전송(`[CANReq] EngineData → Sent`)되었고, 실제 마이크로 녹음을
시도했을 때는(장치 선택 후) sounddevice가 반환한 실제 채널 오류(`Invalid number of
channels`)까지 그대로 로그에 노출되며 스크립트가 중단되지 않고 compWAV까지 진행되어
"비교할 녹음 파일 없음"으로 우아하게 종료 → 케이스 전체는 Fail로 정확히 집계됨을 확인했다.
이는 하드웨어 없는 개발 환경에서 CAN 부분만 정상 검증되고, Power/Audio 하드웨어를 실제
연결하면 동일 코드 경로로 그대로 동작하도록 설계된 대로임을 보여준다.

### 미결정 사항
- Phase 1/2 모두 개발 완료. 실제 파워서플라이(SCPI)·오디오 녹음 장비·DUT를 연결한
  end-to-end 실기 검증은 아직 없음 — 하드웨어 준비되는 대로 진행 필요.
- CANlogReplay용 .blf/.asc 파일은 `POST /api/testrunner/logfile/upload`로 개별
  업로드해야 한다(스크립트 JSON과 로그 파일을 한 번에 묶어 올리는 기능은 아직 없음) —
  실제 사용해보고 불편하면 개선.
- 오디오 비교 임계값(`threshold`, 기본 0.8)의 실제 경보음 대비 최적값은 실기 검증 후
  조정 필요 — 현재는 합성 사인파 테스트로만 검증됨(MFCC 지표가 아주 단순한 순수
  단일주파수 톤끼리는 구분력이 약할 수 있음을 확인했으나, 실제 경보음은 배음 구조가
  풍부해 이 한계의 영향이 제한적일 것으로 예상).
- **`Automation/AppTest.py` 삭제 예정(사용자 확인, 2026-07-11): 실기 검증 완료 후 삭제.**
  기능적으로는 `_process_block`이 실행하는 모든 스텝 타입(CANReq/CANEv/CANlogReplay/
  delay/CANResp/Power/AP/Audio)이 이미 포팅 완료됐고(빈 스텁이던 `CheckResult01`
  제외), Power/Audio 로직은 원본 그대로 이식했다. 하지만 실기(파워서플라이·마이크·DUT)
  로 end-to-end 검증되기 전까지는 원본과 비교할 기준선으로 보존한다. 실기 검증이
  통과하면 이 파일(및 `test_script_Rev01.json` 원본이 필요했던 이유)을 삭제해도 된다.

## Function Test 기능 (2026-07-11, 사용자 승인 완료 — 개발 완료, 검증 통과)

`FUNC` 블록(`{"type":"FUNC","name":"PowerTest","Cycle":1}`)으로 구성된 마스터 스크립트를
한 번 로드하고, 여러 "Function Button" 위젯이 각각 하나의 `FUNC.name`을 골라 클릭 시
그 함수의 스텝만 실행한다. 스텝 실행 엔진·로그·결과는 기존 "테스트 시나리오 실행기"
(test_runner_service.py)와 완전히 공유한다 — 별도 실행기를 새로 만들지 않는다.

### 범위
- 포함: 마스터 스크립트 업로드(상단 툴바, DBC 업로드와 동일한 패턴), FUNC 파싱(기존
  ID 파싱과 동일하게 신규 중첩 Loop 문법 지원, `_type:"FUNC"`로 비활성화 가능), Function
  Button 위젯(기존 "버튼" 위젯과 동일한 외형 — `.big-btn`), 클릭 시 해당 함수만 실행,
  실행 로그/결과는 기존 "테스트 시나리오 실행기" 위젯에 그대로 표시(별도 로그 UI 없음,
  사용자 확인: "실행로그는 테스트 시나리오 실행기에 출력해라").
- 제외: Function Button 자체에 결과 배지 표시(사용자 확인: 불필요), 마스터 스크립트와
  일반 시나리오의 동시 실행 — 같은 CAN 버스/스레드를 쓰므로 상호 배타적으로 동작(하나
  실행 중엔 다른 쪽 시작 요청이 거부됨, 기존 `_running` 가드 재사용).

### 모듈 분해

| 모듈 | 책임 | 인터페이스 | 검증 방법 | 상태 |
|---|---|---|---|---|
| `backend/test_runner_service.py` 확장 | FUNC 블록 파싱(`parse_functions()`, 기존 Case/Loop 파싱 로직 재사용), 별도 슬롯(`self._functions`)에 저장, 이름으로 단건 실행(`start_function(name)`) — 기존 `_run()`의 케이스 반복 로직을 `_run_case()`로 추출해 전체 실행과 단건 실행이 공유 | `load_functions(text, filename)`, `start_function(name)`, `summary()`에 `functions: {loaded, filename, names}` 추가 | pytest 7개(`tests/test_test_runner_service.py`): FUNC 파싱(신구 Loop 포함), `_type:"FUNC"` 비활성 처리, 이름으로 단건 실행 시 해당 케이스만 동작·나머지 함수 미실행, 존재하지 않는 이름 요청 시 에러, 시나리오⇄함수 양방향 상호배타 거부, `summary()`의 functions 필드 | **통과** |
| `backend/main.py` API 확장 | 마스터 스크립트 업로드/실행 REST | `POST /api/testrunner/functions/upload`, `POST /api/testrunner/functions/start`(`{name}`) | pytest 1개(`tests/test_api.py`): 업로드→이름 목록 확인→실행→`/api/testrunner/status`에 로그 반영→존재하지 않는 이름 400 확인 | **통과** |
| `frontend` 툴바 확장 | DBC 업로드와 동일한 패턴으로 마스터 스크립트 업로드 컨트롤 추가 | `api.uploadFunctionScript(file)`, `canStore.status.test_runner.functions` | 브라우저 확인 | **통과** |
| `frontend/src/widgets/FunctionButtonWidget.tsx`(신규) | 기존 `ButtonWidget`과 동일한 외형, 클릭 시 `config.options.funcName`에 해당하는 함수 실행 | `api.functionStart(name)` | 브라우저 확인 | **통과** |
| `WidgetFrame.tsx` 설정 모달 확장 | `functionButton` 타입일 때 로드된 함수 이름 드롭다운(신호 바인딩 대신) | `draft.options.funcName` | 브라우저 확인 | **통과** |

백엔드 89개 테스트(신규 8개) 통과, `tsc -b --noEmit`/`npm run build` 통과. 브라우저에서
가상 버스+sample.dbc 연결 후 `Temp_req.json`(6개 FUNC: PowerTest/PDWTest03/AudioMode0x15/
Welcome0x01/TickTok/WarnTest)을 상단 툴바로 업로드 → Function 버튼 위젯 설정 모달의
드롭다운에 6개 이름이 정확히 나열됨을 확인 → `PowerTest`를 골라 저장 → 버튼 라벨이
"PowerTest"로 바뀜(기존 버튼 위젯과 동일한 외형) → 클릭 시 PowerTest의 6개 Power 스텝만
1000ms 간격으로 실행되고(타임스탬프로 실제 페이싱 확인) 파워서플라이 미연결로 각 스텝이
우아하게 실패, 케이스 결과 `PowerTest · 반복 1 · ❌Fail`이 찍혔으며, 다른 FUNC(PDWTest03
등)의 스텝은 전혀 실행되지 않음을 확인했다. 실행 로그/결과는 Function Button 위젯이 아닌
"테스트 시나리오 실행기" 위젯에 그대로 표시됨을 확인(요구사항대로 별도 로그 UI 없음).

### 가정/미결정
- FUNC 이름 중복 시 첫 번째 항목 우선(마스터 파일에 중복 없음 확인됨, 문제되면 추후 조정) —
  실사용 중 중복이 발생하면 재검토.
- CANlogReplay용 로그 파일은 기존 "테스트 시나리오 실행기" 위젯의 업로드 버튼을 그대로
  재사용(같은 디렉터리 공유, 신규 UI 불필요).

### 후속 개선 (2026-07-11, 사용자 요청 — 개발 완료, 검증 통과)
1. **Function 버튼 실행 중 시각 표시**: 자신이 트리거한 함수가 실행 중일 때 버튼 색을
   회색(`#8c8c8c`, 흰 글씨)으로 바꾸고, 종료되면 기존 색으로 돌아온다. 다른 함수/시나리오가
   실행 중이라 그냥 비활성화된 버튼(연회색 `#cccccc`, 기존 `:disabled` 스타일)과 시각적으로
   구분된다. 이를 위해 `test_runner_service`에 현재 실행 중인 케이스/함수 이름을 추적하는
   `self._running_case`를 추가하고 `summary()`에 `running_case` 필드로 노출했다
   (`_run_case()` 시작 시 설정, `_run()` 종료 시 초기화). 프론트는
   `running_case === funcName`일 때만 `.func-running` CSS 클래스를 적용.
   - 검증: pytest(`test_running_case_tracks_active_function`) — 함수 실행 중
     `running_case`가 해당 이름과 일치, 종료 후 `None`으로 복귀. 브라우저에서 "PowerTest"
     버튼 클릭 후 즉시 스크린샷 → 진회색·흰 글씨로 바뀜 확인, 6초 실행 완료 후 스크린샷 →
     원래 색으로 복귀 확인.
2. **마스터 스크립트에 FUNC 블록이 하나도 없으면 에러**: 일반 ID 기반 시나리오 JSON을
   실수로 "함수 마스터 스크립트" 업로드에 올리면(FUNC 블록이 전혀 없음) 이전에는 조용히
   "0개 기능"으로 로드됐다 — 이제 `load_functions()`가 `ValueError`를 던지고
   `POST /api/testrunner/functions/upload`가 400과 함께 "FUNC 블록이 없습니다 -- 함수
   마스터 스크립트가 아닙니다" 메시지를 반환한다. 실패한 업로드는 기존에 로드돼 있던
   함수 목록을 덮어쓰지 않는다.
   - 검증: pytest(`test_load_functions_without_func_blocks_raises`,
     `test_testrunner_functions_upload_rejects_script_without_func_blocks`) — ID 기반
     스크립트를 함수 업로드에 넣으면 예외/400, 기존 정상 로드분은 그대로 유지됨. 실제
     curl로 `/api/testrunner/functions/upload`에 FUNC 없는 JSON을 올려 400 + 에러
     메시지 확인.

백엔드 92개 테스트(추가 3개) 통과, `tsc -b --noEmit`/`npm run build` 통과.

## 값 범위 제한 + Random 버튼 + Function 멀티버튼 (2026-07-11, 사용자 승인 완료 — 개발 완료, 검증 통과)

### 목표/범위
1. 버튼/체크박스 위젯 설정의 "전송 값" 입력을 signal bit 범위(물리값 단위, 기존
   `signalBitMax` 활용 + 신규 `signalBitMin`)로 제한(HTML min/max + 클램프).
2. 신규 위젯 "Random 버튼": signal에 Random(항상 전체 bit 범위 랜덤) 또는 Range(사용자
   지정 raw min/max/step으로 순차 순환) 모드를 지정. Periodic 신호는 매 주기 자동으로
   새 값을 전송(백엔드 스케줄러가 매 tick 직전 값을 재생성), Event 신호는 클릭할 때마다
   새 값을 전송.
3. 신규 위젯 "Function 멀티버튼": 기존 멀티버튼과 동일한 grid 구조를 재사용하되, 각 셀이
   CAN 신호 대신 FUNC 이름을 트리거.

핵심 설계: Periodic 신호는 프론트 개입 없이 백엔드 스케줄러(`tx_scheduler.py`)가 계속
재전송하므로, "매 주기 새 값"을 만족하려면 값 생성 로직이 백엔드에 있어야 한다 —
신호별 "값 생성기"를 스케줄러에 등록해두고 매 periodic tick 직전에 새 값을 계산해 DBC
raw 상태에 주입한 뒤 인코딩한다.

### 모듈 분해

| 모듈 | 책임 | 인터페이스 | 검증 방법 | 상태 |
|---|---|---|---|---|
| `backend/dbc_service.py` | `set_raw_signal_value(message_name, signal_name, raw_value)` 추가 | 신규 메서드 | pytest | **통과** |
| `backend/tx_scheduler.py` | `set_value_generator(msg, sig, mode, range_min, range_max, step)`(fixed/random/range), 매 periodic tick 전 생성기 호출해 raw 상태 갱신, `send_generated(msg, sig)`(1회 생성+즉시 송신+event/periodic 후속규칙, 기존 `send_signal`과 로직 공유) | 신규 메서드 3개 | pytest 7개(`tests/test_tx_scheduler.py`): random은 매번 bit 범위 내 값, range는 step만큼 순환 후 wrap, range가 bit 범위를 벗어나면 clamp, periodic 연속 프레임이 서로 다른 값, event는 auto-resend 없이 클릭시(valid+invalid 2프레임)만, 미등록 generator 호출시 에러, `mode="fixed"`로 해제 | **통과** |
| `backend/main.py` | 값 생성기 등록/1회생성 REST | `POST /api/tx/signal/generator`, `POST /api/tx/signal/generate` | pytest 1개(`tests/test_api.py`): 등록→생성→해제 후 재호출 시 400 | **통과** |
| `frontend` `WidgetFrame.tsx` | 버튼/체크박스 전송값 물리값 min/max 적용+클램프(DBC 자체 min/max 우선, 없으면 bit 범위로 폴백 — Slider 위젯과 동일 컨벤션), `randomButton` 타입 설정 UI(모드+range면 raw min/max/step, bit 범위로 clamp) | `signalBitMin`/`signalRawBounds` 신규 | 브라우저 확인 | **통과** |
| `frontend/src/widgets/RandomButtonWidget.tsx`(신규) | `.big-btn` 재사용, mount/설정저장 시 서버에 generator 등록, 클릭 시 `sendGenerated` 호출 | `api.setValueGenerator`, `api.sendGenerated` | 브라우저 확인 | **통과** |
| `frontend/src/widgets/MultiControls.tsx` 확장 | 기존 멀티버튼 grid 재사용한 `FunctionMultiButtonWidget`, 셀에 `funcName` 필드, 클릭 시 `functionStart`, 실행 중 셀은 기존 `func-running` 스타일 | `MultiCell.funcName` | 브라우저 확인 | **통과** |

백엔드 100개 테스트(신규 8개) 통과, `tsc -b --noEmit`/`npm run build` 통과. 브라우저에서
가상 버스+sample.dbc로 확인:
1. 버튼 위젯에 `EngineSpeed`(scale 0.25, DBC 선언 범위 없음) 바인딩 → "범위: 0 ~ 16383.75"
   힌트 표시, 99999 입력 시 16383.75로, -500 입력 시 0으로 클램프됨을 확인.
2. 체크박스에 `TurnSignal`(4bit이지만 DBC가 `[0|14]`로 선언, 15는 안 씀) 바인딩 →
   "범위: 0 ~ 14" 힌트(4비트 최대 15가 아니라 DBC 선언값 14 우선 적용 확인 — 최초 구현에서
   bit 범위만 쓰던 버그를 이 과정에서 발견해 수정), 50 입력 시 14로 클램프 확인.
3. Random 버튼을 `EngineSpeed`(periodic)에 Random 모드로 연결 → 클릭 1회 후 CAN 메시지
   표시창에서 프레임 데이터가 매 10ms 틱마다 계속 바뀜을 확인(예: `85 38`→`A9 8D`),
   클릭 이후 추가 조작 없이 자동으로 계속 새 값이 나감을 확인.
4. Random 버튼을 `TurnSignal`(event)에 Range(min=2, max=8, step=2)로 연결 → 클릭할
   때마다 2→4→6→8→2→4로 정확히 순환하고, 클릭 사이에는 추가 프레임이 전혀 나가지
   않음(event는 auto-resend 없음)을 `/api/tx/signal/generate` 직접 호출로 확인.
5. Function 멀티버튼 위젯을 추가 → 12칸 그리드 렌더링, 셀 설정에서 함수 마스터
   스크립트의 6개 함수명이 드롭다운에 나열됨 확인 → 한 셀에 "TickTok" 할당 → 클릭 시
   그 셀만 진회색(`func-running`)으로 바뀌고 나머지 11칸은 비활성화됨을 확인,
   `test_runner_service` 이벤트 로그에 `case: TickTok`만 기록되고 다른 함수는 전혀
   실행되지 않음을 확인.

### 가정 (질문으로 확정)
- Range 모드 min/max/step은 raw bit 값(스케일 미적용) 직접 입력, signal bit 범위로 clamp.
- Random 모드는 항상 전체 bit 범위(사용자 지정 불가).

## Periodic 신호 버튼 Valid/Invalid 토글 + Random 멀티버튼 (2026-07-12, 사용자 승인 완료 — 개발 완료, 검증 통과)

### 목표/범위
1. 위젯-버튼: 바인딩된 신호가 Periodic일 때 클릭이 valid→invalid→valid 2단 토글로 동작.
   1회차: 설정된 신호값 전송 시작(기존 동작). 2회차: invalid 값(할당 bit의 최댓값) 지속
   전송. 3회차: 다시 설정된 신호값. Event 신호는 기존 동작 그대로(토글 없음).
2. 위젯-멀티버튼: 각 셀에 동일한 토글 로직 적용(셀의 바인딩 신호가 Periodic일 때만).
3. 위젯-Random 버튼: 바인딩된 신호가 Periodic일 때 클릭이 generating→invalid→generating
   토글로 동작. invalid 전환 시 등록된 값 생성기를 해제(그렇지 않으면 다음 tick에 랜덤값이
   덮어씀)하고 invalid를 지속 전송, 다시 클릭하면 생성기를 재등록하고 즉시 1회 생성 전송.
4. 신규 위젯 "Random 멀티버튼": 기존 멀티버튼 grid를 재사용하되 각 셀이 자체 신호
   바인딩 + Random/Range 모드(+range면 min/max/step)를 가지며, 위 3번과 동일한 토글 동작.

핵심 설계: Periodic 신호는 스케줄러가 `dbc_service`의 raw 상태를 계속 재인코딩해 보내므로,
"invalid 지속 전송"도 Random/Range 값 생성기와 같은 방식으로 구현한다 — raw 상태에
invalid 값(`(1<<bit)-1`)을 주입해두면 이후 매 tick마다 그 값이 계속 나간다.

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `backend/tx_scheduler.py` | `send_invalid(message_name, signal_name)` 추가 — raw 상태에 invalid 값 주입 후 즉시 송신, periodic이면 auto_entry 유지(계속 invalid 전송), 등록된 값 생성기가 있으면 먼저 해제 | pytest 3개(`tests/test_tx_scheduler.py`): periodic tick마다 invalid 지속, 등록된 random 생성기가 다음 tick에 값을 덮어쓰지 않음, `send_signal()`로 다시 유효값을 보내면 정상 복구 | **통과** |
| `backend/main.py` | `POST /api/tx/signal/invalid` | pytest 1개(`tests/test_api.py`) | **통과** |
| `frontend/src/widgets/controls.tsx`(`ButtonWidget`) | 바인딩 신호가 Periodic일 때만 클릭이 valid/invalid 로컬 토글로 동작, Event는 기존 동작 유지 | 브라우저 확인 | **통과** |
| `frontend/src/widgets/MultiControls.tsx`(`MultiButtonWidget`) | 셀별 동일 토글 로직(셀 신호가 Periodic일 때만) | 브라우저 확인 | **통과** |
| `frontend/src/widgets/RandomButtonWidget.tsx` | Periodic일 때 토글: 생성기 해제+invalid 지속 전송 ↔ 생성기 재등록+`sendGenerated` 재개 | 브라우저 확인 | **통과** |
| `frontend/src/widgets/MultiControls.tsx`(`RandomMultiButtonWidget`, 신규) | 셀별 신호 바인딩+Random/Range 모드+토글, 그리드는 기존 멀티버튼 재사용 | 브라우저 확인 | **통과** |

백엔드 104개 테스트(신규 4개) 통과, `tsc -b --noEmit`/`npm run build` 통과.

**버그 발견 및 수정**: 최초 구현에서 프론트 토글 상태를 단일 boolean(`willSendInvalid`)으로
표현했는데, 이 값이 "다음 클릭이 보낼 것"과 "지금 활성 상태인 것"을 혼동시켜 **첫 클릭
직후부터 라벨이 잘못 INVALID로 표시되는 버그**가 있었다(브라우저 실제 클릭 테스트에서
발견). `pending`(다음 클릭이 보낼 값) / `lastSent`(마지막으로 실제 보낸 값, 클릭 전엔
`null`) 두 개의 상태로 분리해 4개 위젯(`ButtonWidget`/`MultiButtonWidget`/
`RandomButtonWidget`/`RandomMultiButtonWidget`) 모두 동일하게 수정하고 재검증했다.

브라우저에서 가상 버스+sample.dbc로 확인:
- 버튼(`VehicleSpeed.Speed=80`, periodic): 클릭1 → 라벨 그대로("Speed = 80"), 클릭2 →
  "Speed = INVALID", 클릭3 → 다시 "Speed = 80"으로 정확히 순환.
- 멀티버튼 셀(`EngineData.EngineTemp=50`, periodic): 동일하게 클릭1(라벨 유지)→
  클릭2("EngineTemp = INVALID") 확인.
- Random 버튼(`EngineData.EngineSpeed`, periodic, Random 모드): 클릭1 → 라벨
  "[Random]" 유지 + CAN 메시지 표시창에서 매 tick 값이 계속 바뀜 확인, 클릭2 → 라벨
  "= INVALID" + 프레임 데이터가 `FF FF`(0xFFFF)로 고정되어 계속 나감을 확인(생성기가
  실제로 해제됨), 클릭3 → 라벨이 다시 "[Random]"으로 복귀.
- Random 멀티버튼: 그리드 렌더링 확인, 셀 설정 모달에서 신호 바인딩 + Random/Range
  모드 선택 + Range 모드일 때 raw 최소/최대/step 입력 필드가 정확히 나타남을 확인
  (단건 클릭 사이클은 위 Random 버튼과 동일 코드 경로이므로 라벨 표시만 확인).

## 실기 검증 현황

- **Vector CANcase — HS-CAN(classic CAN): 검증 완료 (2026-07-06, 사용자 확인).** 이상 없음.
- CAN-FD(PCAN/CANcase 공통), PCAN classic: 아직 미검증 — virtual 버스로만 확인된 상태.
- CANcase로 CAN-FD 실기 테스트 예정 (2026-07-06, 사용자 계획). Vector Hardware Config에서
  채널을 "CANalyzer"에 할당해야 하는 점, FD 체크박스 + 데이터 비트레이트 선택 UI를
  참고 (README.md "CAN-FD" 절).


## "오디오 신호 모니터" 위젯 (2026-07-30, 사용자 승인 완료 — 개발 완료, 검증 통과)

### 목표/범위
- 선택된 오디오 입력 장치(CH3/CH4, 기존 `DEFAULT_CHANNELS`)의 실시간 Peak/RMS 레벨 미터 +
  최근 5초 min/max 스크롤 파형을 보여주는 신규 위젯.
- 테스트 러너의 녹음 기능과 오디오 스트림을 공유 — 장치당 스트림은 하나만 열 수 있으므로,
  테스트 러너가 녹음 중일 때도 모니터 위젯이 같은 스트림에서 레벨을 읽어온다.
- 제외: 정밀 오실로스코프급 고해상도 파형, 새 오디오 장치/포맷 지원.

### 핵심 설계
`AudioService`의 콜백을 통합: 스트림이 열려 있으면 항상(누가 열었든) 채널별 Peak/RMS를
계산해 최근 히스토리를 메모리에 유지하고, `_wav_name`이 설정된 동안만 원본 오디오를
`_audio_data`에 버퍼링(기존 녹음 동작 그대로). `_stream_owner`("recording"|"monitor")로
누가 스트림을 열었는지 추적해서: 모니터의 Stop이 진행 중인 녹음을 끄지 못하게 하고,
녹음 시작 시 이미 열려있는 모니터 전용 스트림이 있으면 새로 열지 않고 그 자리에서
업그레이드(같은 스트림에 파일명만 설정)한다.

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `backend/audio_service.py`: `_ChannelLevelTracker` | 콜백 청크별 Peak/RMS 계산, 50ms 버킷으로 다운샘플된 min/max/rms 히스토리(5초, ~100포인트) 유지 | pytest 4개: peak/rms 계산, 빈 청크 무시, 버킷 경계에서 히스토리 추가, reset 동작 | **통과** |
| `backend/audio_service.py`: `start`/`stop`/`start_monitor`/`stop_monitor`/`get_level` | 스트림 소유권 추적, 녹음↔모니터 스트림 공유/업그레이드, 소유권 없는 쪽의 정지 요청 거부 | pytest 6개: 기본 상태, 장치 미선택 시 거부, 미활성 시 stop_monitor no-op, 녹음 중 stop_monitor 거부(스트림 안 건드림 확인), start()가 기존 모니터 스트림을 제자리 업그레이드, 이미 스트림 있을 때 start() 거부 | **통과** |
| `backend/main.py` | `POST /api/audio/monitor/start`, `POST /api/audio/monitor/stop`, `GET /api/audio/level` | pytest 1개(`test_audio_monitor_endpoints`): 장치 미선택 시 시작 거부, 미활성 시 stop no-op, level 기본 shape 확인 | **통과** |
| `frontend/src/widgets/AudioMonitorWidget.tsx` (신규) | Start/Stop 버튼, 채널별 레벨 바(Peak/RMS, 색상 구간), 최근 히스토리 canvas 스크롤 그래프, 100ms 폴링 | 브라우저 확인 | **통과** |
| `frontend/src/widgets/registry.tsx`, `types.ts`, `api/client.ts` | 위젯 타입 등록, `AudioLevel` 등 타입, API 클라이언트 3개 | `tsc -b --noEmit` | **통과** |

백엔드 125개 테스트(신규 11개) 통과, 프론트 `tsc -b --noEmit` 통과.

**실기 검증 제약**: 이 개발 환경(macOS)의 내장/iPhone 마이크는 1채널뿐이라, 이 기능이
전제하는 2채널(`DEFAULT_CHANNELS=[1,2]`) 오디오 인터페이스로 실제 스트림을 열 수 없다
(`PaErrorCode -9998: Invalid number of channels`) — 이는 기존 녹음 기능도 동일하게 겪는
이 환경의 하드웨어 제약이며, 신규 코드의 결함이 아니다. 브라우저에서 위젯 렌더링, Start
클릭 시 이 에러가 위젯에 정확히 표시되는 것, CH1/CH2 0% 기본 상태는 확인했다. 스트림
공유/소유권 로직(모니터 Stop이 녹음을 못 끄는 것, 녹음이 모니터 스트림을 업그레이드하는
것)은 pytest로 철저히 검증했으나, 실제 2채널 오디오 인터페이스가 연결된 환경에서의
end-to-end 브라우저 검증은 아직 하지 않았다.

### 버그 수정 (2026-07-31): 채널 수 하드코딩으로 인한 PaErrorCode -9998

사용자가 오디오 장치 선택 후 Start를 누르면 위 "실기 검증 제약"에 적었던 바로 그 에러
(`Error opening InputStream: Invalid number of channels [PaErrorCode -9998]`)가 실제로
재현됨을 보고. `_open_stream()`이 선택된 장치의 실제 입력 채널 수와 무관하게 항상
`channels=len(DEFAULT_CHANNELS)`(=2)로 스트림을 열려고 시도한 것이 원인 — 1채널 마이크
(MacBook Pro 마이크 등)에서는 항상 실패했다.

**수정**: `choose_channel_count(max_input_channels)` 순수 함수를 추가해
`min(len(DEFAULT_CHANNELS), 실제_장치_입력채널수)`로 캡핑. 입력 채널이 0인 장치(예:
스피커를 잘못 선택)는 명확한 에러로 거부. 레벨 트래커 배열도 실제 연 채널 수에 맞춰
재생성되므로, 1채널 장치에서는 CH1만 표시된다 (기존 실기용 2채널 인터페이스는 그대로
2채널 유지, 동작 변화 없음).

**검증**: pytest 3개 추가(경계값 캡핑, 1채널로 축소, 0채널 처리) — 백엔드 128개 전체 통과.
그리고 이번엔 **진짜 하드웨어로 end-to-end 확인**: MacBook Pro 마이크(1채널) 선택 →
`/api/audio/monitor/start` 성공(`{"ok":true}`, 이전엔 실패) → `/api/audio/level`이 실시간
룸 노이즈를 반영하는 실제 peak/rms/history 값을 반환 → 브라우저에서 위젯이 CH1 레벨 바
하나만 정확히 표시하고 실시간으로 값이 변하는 것 확인 → Stop으로 정상 종료 확인. 이로써
위 "실기 검증 제약" 항목의 마지막 미검증 사항(실제 오디오 인터페이스로 열기)이 최소
1채널 케이스에 대해서는 해소되었다. 실제 2채널 이상 다중 채널 인터페이스에서의 검증은
여전히 남아있다.

## "오디오 신호 모니터" 업그레이드 (2026-07-31, 사용자 승인 완료 — 개발 완료, 실기 검증 통과)

### 목표/범위
1. 레벨 미터(Peak/RMS)만 보여주던 것을 실시간 파형으로 업그레이드.
2. GraphWidget(CAN 신호 그래프)과 동일한 X/Y 축 독립 확대·축소(휠 줌, 축 감지)/드래그 팬.
3. 입력 채널이 2개면 GraphWidget처럼 채널별로 별도 미니 차트를 세로로 분리.
4. 오디오 장치 드롭다운에는 입력 채널(`channels > 0`)이 있는 장치만 표시.
5. Start(파형만, 저장 안 함) / Record(파형 + WAV 저장) 버튼 분리. Record는 이미 Start로 열려있는
   모니터 스트림을 재오픈 없이 그 자리에서 녹음으로 업그레이드.

### 핵심 설계: 실제 파형 데이터 전송
기존 50ms 버킷 min/max 요약(레벨 미터 트렌드용)은 실제 파형 확대에 쓰기엔 해상도가 너무
낮아(440Hz 톤 한 주기 ~2.3ms) 전면 재설계:
- 백엔드가 채널별로 원본 샘플을 30초 순환 버퍼(`RAW_BUFFER_SECONDS`)로 보관
  (`_ChannelLevelTracker._raw_chunks`, 콜백 청크 단위로 저장, epoch time 기준).
- 프론트가 현재 보고 있는 시간 구간(epoch ms, `Date.now()`와 동일 단위 — 로컬 머신이라
  프론트/백엔드 시계가 그대로 맞음)을 `GET /api/audio/waveform?from_ms&to_ms&max_points`로
  요청하면, 백엔드가 그 구간을 `max_points`(캔버스 폭)만큼 픽셀 컬럼당 min/max로 다운샘플
  (`waveform_slice()`)해서 반환 — 줌아웃 시 여러 샘플이 한 컬럼에 뭉쳐 엔벨로프로, 줌인 시
  컬럼당 ~1샘플이라 사실상 원본 파형.
- 각 채널 차트가 독립적으로 자기 뷰 범위만큼 폴링(60ms 간격) — GraphWidget은 클라이언트가
  전체 히스토리를 들고 필터링만 하면 되지만, 오디오는 원본이 너무 많아 "보고 있는 만큼만"
  서버에서 받아오는 구조로 차이가 있음.
- 소유권 모델 확장: `_stream_owner`에 `"widget_record"` 추가(테스트 러너의 `"recording"`과
  구분) — 위젯의 Record/Stop이 테스트 러너의 녹음을 절대 건드리지 않도록.

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `_ChannelLevelTracker` 재설계 | 버킷 히스토리 제거, 원본 샘플 순환 버퍼 + `waveform_slice()` 픽셀 컬럼 디시메이션 | pytest 9개: peak/rms, 빈 청크, 30초 트리밍, reset, waveform_slice 5종(빈 상태/잘못된 range/컬럼 배치 정확성/줌아웃 병합/range 밖 청크 무시) | **통과** |
| `AudioService.get_waveform`, `start_widget_recording`, `stop_widget_recording` | 파형 조회 API, 위젯 전용 녹음 소유권(`widget_record`) 관리 | pytest 5개: 스트림 없을 때 빈 응답, 모니터 스트림 업그레이드, 다른 소유자일 때 거부, stop이 다른 소유자 스트림 안 건드림 | **통과** |
| `backend/main.py`: `/api/audio/waveform`, `/api/audio/record/start`, `/api/audio/record/stop` | REST 엔드포인트, 로그 스팸 필터에 waveform 추가 | pytest 1개(`test_audio_waveform_and_record_endpoints`) | **통과** |
| `frontend/src/widgets/AudioMonitorWidget.tsx` 전면 재작성 | `WaveformChart`(GraphWidget의 SignalChart와 동일한 줌/팬 구조), Start/Record/Stop 분리, 입력 장치 필터 | 브라우저 + 실기 확인 | **통과** |

백엔드 139개 테스트(신규 12개) 통과, `tsc -b --noEmit` 통과.

**실기 검증 (MacBook Pro 마이크, 1채널)**: Start 클릭 → 실시간 파형 캔버스에 실제 그려지는
파형 확인(픽셀 데이터 검사로 배경 아닌 픽셀 28%, 1초 간격으로 픽셀 합이 계속 변해 실시간
갱신 확인) → 휠 줌/드래그 팬 이벤트 디스패치 시 에러 없이 처리 확인 → 리셋 버튼 동작 확인
→ Record 클릭 시 기존 모니터 스트림이 재오픈 없이 녹음으로 업그레이드됨(`owner:
widget_record`) 확인 → Stop 클릭 시 실제 WAV 파일(1.69MB, 845824 프레임)이 디스크에
저장되고 위젯에 "저장됨: ..." 메시지 표시 확인 → 장치 드롭다운에 입력 채널 있는 장치
2개만 표시(출력 전용 "MacBook Pro 스피커" 제외) 확인.

**미검증**: 실제 2채널 이상 오디오 인터페이스에서 채널별 차트 분리가 화면에 나란히 잘
나오는지(코드상 `channels.map()`으로 자동 분리되지만, 이 환경은 1채널 장치뿐이라 실제
2개 차트가 동시에 렌더링되는 시각적 확인은 못함). 줌/팬 동작은 GraphWidget과 동일한
로직을 그대로 재사용했고 이벤트 디스패치로 에러 없음은 확인했지만, 실제 마우스 드래그로
파형이 시각적으로 올바르게 이동/확대되는지는 이 자동화 환경의 드래그 시뮬레이션 한계로
완전히 확인하지 못했다.

## "OTA Tester" 위젯 폴더 기반 실행 업그레이드 (2026-08-01, 개발 완료 — 검증 통과)

### 목표/범위
기존 OTA Tester 위젯(단일 XML 업로드 + 무조건 전체 실행)을 CAN-SWDL과 유사하게 동작하도록
업그레이드. XML 파싱 스키마만 다르고(`parse_test_rule_xml`, 기존 그대로 재사용 가능하다고
조사 결과 확인) 나머지 흐름은 CAN-SWDL과 대등하게 맞춘다.

1. 폴더 선택 메뉴 구성 → 폴더 선택.
2. 폴더 내부 `CLI/cli_config.json`(파일명 고정) 파싱.
3. `cli_config.json`의 `testcases[].id`로 `Testcases/<id>/*.json`(확장자로 탐색, 파일명은 testcaseName) 매니페스트 파싱.
4. 매니페스트의 `hooks[]`/`testBlocks[]` 각각의 `id`/`fileName`으로 같은 폴더의 XML을 찾아 순서대로(훅 먼저, 그 다음 testBlock, 각 리스트는 JSON 순서) 테스트 케이스 구성.
5. 케이스는 전체 선택 또는 일부만 선택해 선택된 것만 실행(체크리스트 UI).
6. 다운로드 시 XML의 `seekAddress`(예: `0x0200`)부터 `writeSize`만큼 해당 케이스의 `*.bin` 파일을 청크 전송.

추가로 사용자 요청: **폴더 선택 방식이 윈도우에서도 정상 동작**해야 하고, **CAN-SWDL의 기존
폴더 선택 구현도 같은 관점에서 재검토**할 것.

### 핵심 설계
- **폴더 선택은 브라우저가 실제 파일시스템 경로를 백엔드에 넘길 수 없다는 제약** 때문에(보안상
  차단됨) CAN-SWDL의 기존 "📁 폴더 선택"(`webkitdirectory`) 패턴을 그대로 따른다: 브라우저가
  전체 폴더 트리를 `File[]`로 읽고, **JS에서 `cli_config.json`/매니페스트 JSON을 직접 파싱**해
  각 hook/testBlock의 XML(및 XML 안의 `binaryPath`로 참조된 bin)을 찾아 백엔드로 업로드.
  백엔드는 파일 내용(XML 구조 파싱, UDS 전송)만 담당하고 폴더 탐색은 하지 않는다.
- **Windows 경로 안전성**: `webkitRelativePath`와 XML에 박힌 `binaryPath` 문자열 양쪽 모두
  `\`→`/` 정규화 후, **선택한 루트 폴더 이름 세그먼트를 양쪽에서 각각 제거**하고 나머지
  suffix로 매칭(`stripRootSegment`). 이렇게 하면 (a) OS별 구분자 차이, (b) 루트 폴더가
  복사/재압축으로 이름이 바뀌는 경우에도 안전하다. 실제 참고 데이터의 `project.json`이
  `"26-07-27-16-13-40\\project.json"`처럼 백슬래시를 섞어 쓰는 것으로 이 필요성을 확인했다.
  **CAN-SWDL 재검토 결과**: 기존 구현은 애초에 `webkitRelativePath`를 아예 쓰지 않고 파일명
  (`f.name`, basename)만으로 매칭하며 XML 안의 `romInfo` 경로도 이미 백슬래시 정규화를 하고
  있어 — 별도 수정 없이 이미 Windows-safe함을 확인.
- **매니저 재작성**: 단일 XML/단일 진행 상태였던 `OtaTesterDownloadManager`를 **순서가 있는
  케이스 리스트**(`hook`/`testBlock`, 각자 독립적인 steps + 선택적 binary_data) 구조로 전면
  재작성. 케이스 단위로 `enabled` 토글, 전체 선택/해제, 순차 실행(하나 끝나면 다음), 비활성
  케이스는 완전히 건너뜀.
- **CAN-SWDL의 `_execute_transfer_data`(seekAddress/writeSize 기반 청크 전송, ECU 응답의
  maxBlockLength 클램핑, NRC 0x78 재시도)를 포팅**해 새 매니저에 이식.
- **기존 버그 수정** (조사 중 발견, "CAN-SWDL과 유사한 동작"이라는 요구사항의 일부로 판단해 포함):
  - `main.py`의 `load_xml(path, filename)` 2-인자 호출 — 실제 메서드는 1개만 받아 항상
    TypeError였음 (새 케이스 기반 엔드포인트로 대체하며 자연히 해소).
  - `_send_and_receive`의 TX-ONLY 하드코딩(실제 응답을 안 받고 무조건 성공 처리) — CAN-SWDL에서
    이미 고친 것과 같은 패턴, 동일하게 실제 수신으로 복원.
  - 타임아웃 단위 버그: `p2_star_can_server_max`가 "5000ms" 주석과 달리 5.0으로 초기화되어
    `/1000.0` 계산 시 실제로는 5ms 타임아웃이 되어버림 — CAN-SWDL과 동일하게 ms 단위로 통일.
  - `is_extended_id=False` 하드코딩 — 기본 Req/Resp ID(`0x18DA00F1`)가 29비트 확장 ID인데도
    표준 ID로 전송하고 있었음 — CAN-SWDL처럼 `request_id/response_id > 0x7FF`로 판정.
  - PDU 파라미터 이름 불일치(실제 참고 XML과 대조해 발견): `diagnosticSessionControl`이
    `sessionType`을 읽고 있었으나 실제 XML 속성명은 `diagnosticSessionType`; `readDataByIdentifier`가
    `did`를 읽고 있었으나 실제는 `dataIdentifier`; `routineControl`이 `routineControlOptionRecord`를
    아예 읽지 않아 옵션 레코드가 빠짐(이번 세션 초반 CAN-SWDL에서 고친 것과 같은 종류의 버그).
  - `securityAccess`: 참고 XML 스키마는 CAN-SWDL과 달리 `requestSeed`/`sendKey` 하위 스텝을
    따로 두지 않고 `<xfrm:securityAccess type="ask">` 하나로 표현 — 기존 코드는 이를 인식하지
    못해 항상 requestSeed만 보내고 sendKey는 보낸 적이 없었음. CAN-SWDL과 동일하게 Seed 요청 →
    (SeedKey DLL 또는 더미) 키 생성 → SendKey 전체 핸드셰이크를 한 스텝으로 수행하도록 재작성.
  - `uds_core.build_diagnostic_session`의 `VALID_SESSIONS={0x01,0x02,0x03,0x7F}` 제한 —
    참고 XML의 VersionCheck 훅이 제조사 특화 세션 타입 `0x81`을 사용해 ValueError로 즉시 실패.
    한 바이트 범위(`0x00~0xFF`) 검증으로 완화(ECU가 실제 수락 여부의 최종 판정자).

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `backend/ota_tester_download_manager.py` 전면 재작성 | 케이스 리스트 관리(add/clear/enable), 순차 실행, seekAddress 청크 전송, 실제 UDS 요청/응답(재시도 포함), securityAccess 실핸드셰이크 | pytest 15개(신규): PDU 파라미터명 회귀 3종, `iter_transfer_chunks` 순수함수 3종, 실제 페이크 ECU로 다중 케이스 순차 실행/비활성 케이스 스킵/TX-ONLY 제거 회귀(진짜 negative response 감지)/confirmPositiveResponse=no 처리/케이스 교체/시작 전제조건 | **통과** |
| `backend/uds_core.py` `build_diagnostic_session` 검증 완화 | 제조사 특화 세션 타입(0x81 등) 허용 | 위 매니저 테스트에서 간접 검증(VersionCheck 훅 실행 성공) | **통과** |
| `backend/main.py` 신규 엔드포인트 | `case/xml_upload`, `case/binary_upload`, `case/enable`, `cases/set_all_enabled`, `cases/clear`; `seedkey_service`를 매니저 생성자에 연결 | pytest 1개(`test_ota_tester_case_endpoints_wiring`, HTTP 레벨 wiring 스모크) + 전체 회귀 | **통과** |
| `frontend/src/widgets/OtaTesterWidget.tsx` 전면 재작성 | 폴더 선택 → JS에서 cli_config/매니페스트 파싱 → XML/bin 해석 후 업로드, 체크리스트(전체선택/해제 포함), 케이스별 진행률 | tsc 클린 + 실제 참고 데이터로 브라우저 확인(아래) | **통과** |

`tsc -b`/`vite build`/`oxlint` 모두 클린. 백엔드 전체 155개 테스트 통과(신규 16개: 매니저
15개 + API wiring 1개).

**실물 참고 데이터 검증**: 실제 `reference/26-07-27-16-13-40/` 폴더(진짜 OEM XML/JSON/펌웨어
bin 포함)를 대상으로 두 단계로 확인했다.
1. 프론트엔드의 폴더 해석 로직(`stripRootSegment`/`findBySuffix`/`findByPrefixAndExt`)을
   Node로 그대로 재현해 실제 파일 19개를 대상으로 실행 — `CLI/cli_config.json` 탐색, 1개
   testcase의 매니페스트 탐색, 훅 1개 + testBlock 4개 전부 XML 매칭, 각 XML의 `binaryPath`로
   실제 bin 파일(1.5MB/24MB/5.2MB) 해석까지 5개 케이스 전부 정확히 성공. 백슬래시 혼합
   경로(Windows 스타일)도 별도 합성 테스트로 정규화 확인.
2. 백엔드를 재기동(기존 실행 중이던 프로세스가 이번 세션 수정 전 코드였음을 확인하고 재시작)
   한 뒤, 실제 VersionCheck 훅 XML + Unit1 testBlock XML + 그 실제 bin 파일(1599240 bytes)을
   신규 엔드포인트로 업로드 → 브라우저에서 위젯을 열어 상태가 실제로 반영되는지 확인:
   상태 "준비", 체크리스트에 "hook VersionCheck 3 steps"/"testBlock Unit1 7 steps ✓ BIN"
   정확히 표시, 이벤트 로그에 실제 로드 메시지("바이너리 로드: ..._RomData01.bin (1599240
   bytes) -> Unit1") 정상 출력, Start 버튼 활성화 확인.

**미검증 (투명하게 명시)**: 실제 CAN 하드웨어(또는 인프로세스 fake ECU 하니스)로 Start →
전체 시퀀스(세션전환→보안접근→루틴제어→다운로드요청→청크전송→전송종료) 왕복 통신까지의
전 구간을 이 자동화 환경에서 직접 눌러 확인하지는 못했다 — 대신 매니저 레벨에서 주입 가능한
페이크 송수신 콜백으로 정확히 동일한 프로토콜 로직(15개 테스트)을 검증했고, 이는 CAN-SWDL
자신도 현재 이 수준의 매니저 단위 테스트조차 없는 것과 비교하면 오히려 더 두터운 커버리지다.
브라우저 네이티브 폴더 선택 다이얼로그 자체는 이 자동화 도구가 구동할 수 없어(OS 네이티브
다이얼로그), 대신 동일한 해석 로직을 Node로 재현해 실제 데이터로 검증했다.

### 후속 보완 (2026-08-01, 같은 날 추가 요청 — 개발 완료, 검증 통과)

사용자 추가 요청 2건:
1. `VehicleInfo/vehicleInfo.json`의 `communicationInfo.settings.requestID`/`responseID`에 따라
   Req/Resp ID를 자동 설정.
2. CAN-SWDL처럼 XML에 정의된 진단 명령어를 모두 위젯에 표시하고, 명령어(스텝) 단위로
   체크박스를 만들어 선택된 것만 실행.

**구현**:
- 폴더 선택 시 프론트가 `VehicleInfo/vehicleInfo.json`도 함께 찾아 파싱, `requestID`/`responseID`
  (예: `"0x00000783"`)의 `0x` 접두어만 벗겨 Req ID/Resp ID 입력창에 채운다(수동 수정은 계속 가능).
  파일이 없거나 파싱 실패 시 기존 값을 유지하고 경고만 남긴다.
- 매니저에 케이스별 `selected_steps`(None=전체, []=없음, [i,...]=해당 인덱스만) 필드 추가,
  `_run_case_steps`가 이를 참조해 미선택 스텝을 건너뛴다(CAN-SWDL의 `_is_step_selected`와 동일한
  관례). 신규 엔드포인트 `GET /api/ota_tester/case/steps`(케이스의 스텝 목록, CAN-SWDL의
  `/api/udswdl/steps`에 대응), `PUT /api/ota_tester/case/selected_steps`.
- 위젯: 각 케이스 행에 "▶/▼" 펼침 버튼 추가, 펼치면 그 케이스의 실제 진단 명령어들이
  (`SERVICE_DISPLAY_NAMES`로 한글 표시) 스텝별 체크박스로 나열되고 케이스 내 전체선택/해제도
  가능. 헤더의 "N/M steps" 표시도 선택된 개수를 반영하도록 갱신.

### 모듈 분해 (후속)

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `ota_tester_download_manager.py`: `get_case_steps`, `set_case_selected_steps`, `_run_case_steps` 스킵 로직 | 케이스별 스텝 조회/선택, 실행 시 미선택 스텝 skip | pytest 4개 추가(스텝 조회, 선택 스킵 회귀, 빈 선택=전체 스킵, 존재하지 않는 케이스 에러) | **통과** |
| `backend/main.py`: `case/steps`(GET), `case/selected_steps`(PUT) | HTTP wiring | pytest 1개 추가(`test_ota_tester_case_steps_and_selected_steps_endpoints`) | **통과** |
| `OtaTesterWidget.tsx`: vehicleInfo.json 파싱, 스텝 체크리스트 UI | Req/Resp ID 자동 채움, 케이스별 펼침/스텝 체크박스 | tsc/build/lint 클린 + 실제 참고 데이터로 브라우저 확인(아래) | **통과** |

`tsc -b`/`vite build`/`oxlint` 클린. 백엔드 전체 160개 테스트 통과(이번 추가분 5개 포함).

**실물 참고 데이터 검증**:
- Node로 `stripRootSegment`/파일 인덱스 로직을 재현해 실제 `VehicleInfo/vehicleInfo.json`을
  찾고 파싱 → `requestID "0x00000783"` → `783`(hex) / `responseID "0x0000078B"` → `78B`(hex)로
  정확히 변환됨을 확인.
- 백엔드를 재기동한 뒤 실제 VersionCheck 훅 XML을 업로드하고 `/api/ota_tester/case/steps`를
  호출 → 실제 3개 스텝(진단 세션 전환 0x81/confirmPositiveResponse=no, DID 읽기 0xF187, DID
  읽기 0xF1B1)이 그대로 반환됨을 확인. 브라우저에서 위젯을 열어 "▶" 펼침 버튼을 눌러
  체크리스트가 "1 진단 세션 전환 (음성 응답 예상) / 2 DID 읽기 / 3 DID 읽기"로 정확히 렌더링됨을
  확인. `selected_steps` 토글(`[1,2]`로 설정 → 케이스 상태에 반영 → `null`로 복원)도 curl로
  왕복 확인.

### 후속 보완 2 (2026-08-01, 같은 날 추가 요청 — 개발 완료, 검증 통과)

사용자 요청: 각 진단 항목에 실제 진단 명령어/파라미터를 XML에서 파싱해 보여줄 것
(예시: "루틴제어" → `[31 01 FF 00 F1 B1]`).

**구현**:
- `get_case_steps()`가 각 스텝마다 `pdu_preview`(실제 실행 경로와 동일한 `_build_pdu`/전용
  디스패처로 만든 PDU를 hex 문자열로)와 `pdu_note`(런타임 의존적인 항목에 대한 설명)를 함께
  반환하도록 확장. 표시되는 바이트가 실행 시 실제로 전송되는 바이트와 반드시 일치하도록,
  미리보기 전용 로직을 새로 만들지 않고 실행 경로가 쓰는 것과 같은 빌더 함수를 그대로 재사용.
  - `securityAccess`: 실제 키는 ECU가 준 seed에 의해 실행 시점에 결정되므로, Seed 요청 PDU
    (`27 01`)만 보여주고 "Seed 요청 → 키 생성 → SendKey (실제 키는 실행 시 결정됨)" 설명 추가.
  - `transferData`: `maxNumberOfBlockLength`가 수천 바이트일 수 있어(예: `0x0C02`=3074) 첫
    블록을 통째로 보여주면 체크리스트가 못 쓸 정도로 길어짐 — 앞 12바이트만 보이고 "..."로
    자르고, 블록 크기/총 블록 수를 note로 별도 표기. (개발 중 이 트렁케이션 없이 구현했다가
    실제 참고 데이터로 확인하는 과정에서 3000바이트가 그대로 렌더링되는 문제를 발견해 즉시
    수정 — 회귀 테스트로 고정.)
  - 나머지(진단세션전환/ecuReset/DID읽기/통신제어/루틴제어/다운로드요청/전송종료요청/
    testerPresent/DTC설정)는 XML 파라미터만으로 전체 PDU가 고정되므로 그대로 표시.
- 위젯: 스텝 체크박스 옆에 `[31 01 FF 00 F1 B1]` 형식으로 모노스페이스 표시, note가 있으면
  그 아래 작은 글씨로 병기.

### 모듈 분해 (후속 2)

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `ota_tester_download_manager.py`: `_preview_pdu` | 실제 실행 경로와 동일한 빌더로 스텝별 PDU 미리보기 생성 | pytest 6개 추가(루틴제어 사용자 예시 정확히 일치, requestDownload/diagnosticSessionControl 등 정적 서비스, securityAccess/transferData의 런타임 의존 note, 대용량 블록 트렁케이션 회귀) | **통과** |
| `OtaTesterWidget.tsx` 스텝 행 | `[XX XX ...]` 모노스페이스 미리보기 + note 표시 | tsc/build/lint 클린 + 실제 참고 데이터로 브라우저 확인 | **통과** |

`tsc -b`/`vite build`/`oxlint` 클린. 백엔드 전체 166개 테스트 통과.

**실물 참고 데이터 검증**: 백엔드 재기동 후 실제 Unit1 testBlock XML + 그 실제 bin 파일을
업로드해 `/api/ota_tester/case/steps` 응답과 브라우저 위젯 렌더링을 대조 — 7개 스텝 모두
정확: `진단 세션 전환 [10 02]`, `보안 액세스 [27 01]`(런타임 note 포함), **`루틴 제어
[31 01 FF 00 F1 B1]`(사용자가 제시한 예시와 완전히 일치)**, `다운로드 요청 [34 0A 44 00 44 E0
00 00 18 65 08]`, `데이터 전송 [36 01 ... 12bytes ...]`(블록당 3074 bytes, 총 521개 블록 note
포함, 트렁케이션 확인), `전송 종료 요청 [37]`, `루틴 제어 [31 01 02 00]`.

### 후속 보완 3 (2026-08-01, 같은 날 추가 요청 — 개발 완료, 검증 통과)

사용자 요청: OTA Tester 위젯에도 CAN-SWDL과 동일한 STmin 설정 메뉴 + SeedKey(ASK) DLL 로딩
메뉴를 구성하고, 두 위젯이 이를 공용으로 사용할 것.

**구현**:
- 새 공유 컴포넌트 `frontend/src/widgets/UdsGlobalControls.tsx`: STmin 체크박스+입력값 +
  SeedKey DLL 업로드 UI를 하나로 묶어 CAN-SWDL/OTA Tester 양쪽에서 그대로 import해서 쓴다.
  CAN-SWDL에 있던 기존 JSX/상태를 이 컴포넌트로 옮기고, CAN-SWDL 쪽도 이 컴포넌트를 쓰도록
  교체(중복 제거 + 진짜 공용화 동시 달성).
- STmin 값 자체가 "공용"이 되도록 `canStore.ts`에 `getGlobalStminEnabled/setGlobalStminEnabled`,
  `getGlobalStminTx/setGlobalStminTx`를 추가(localStorage 영속, 기존 `fps`/`rxNode` 패턴과 동일).
  한 위젯에서 체크박스나 값을 바꾸면 다른 위젯에도 즉시 반영됨(같은 store를 구독).
- SeedKey DLL은 이미 백엔드에 단일 공용 서비스(`seedkey_service`)로 있어서 별도 상태 공유가
  필요 없음 — OTA Tester 매니저 생성 시점에 이미 CAN-SWDL과 같은 인스턴스를 주입해뒀던 걸
  그대로 활용, 새 UI만 추가.
- OTA Tester 백엔드: `_global_stmin_tx` 오버라이드 필드 + `_get_fc_stmin()`을 CAN-SWDL과 같은
  방식(오버라이드 있으면 그 값, 없으면 기본값)으로 추가. `POST /api/ota_tester/start`에
  `global_stmin_tx` 필드 추가, 있으면 시작 전 매니저에 세팅(CAN-SWDL의 `udswdl_start`가 슬롯
  매니저들에 `_global_stmin_tx`를 세팅하는 것과 동일한 패턴).

### 모듈 분해 (후속 3)

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `UdsGlobalControls.tsx` (신규) | STmin+SeedKey UI를 두 위젯이 공유, `getGlobalStminOverride()` 헬퍼 | tsc/build/lint 클린 + 브라우저에서 두 위젯 동시 렌더 후 canStore 공유 확인 | **통과** |
| `canStore.ts`: `getGlobalStminEnabled/Tx` | STmin 값 자체를 두 위젯 간 공유하는 단일 소스 | 브라우저에서 실측(아래) | **통과** |
| `ota_tester_download_manager.py`: `_global_stmin_tx`, `_get_fc_stmin()` | STmin 오버라이드를 실제 ISO-TP 수신(fc_stmin)에 반영 | pytest 3개 추가(기본값, 오버라이드 적용, `_uds_request_with_retry`가 실제로 `isotp_receive`의 `fc_stmin` 인자에 오버라이드 값을 전달하는지 회귀) | **통과** |
| `main.py`: `OtaTesterStartRequest.global_stmin_tx` | HTTP wiring | 전체 회귀 스위트로 확인 | **통과** |

`tsc -b`/`vite build`/`oxlint` 클린(기존에 이미 있던 `only-export-components` 경고가 이 신규
파일에도 동일하게 뜨는데, 프로젝트에 이미 존재하는 패턴(`controls.tsx`)과 같은 종류라 그대로 둠).
백엔드 전체 169개 테스트 통과.

**브라우저 실측 검증**: CAN-SWDL과 OTA Tester 위젯을 동시에 캔버스에 올린 뒤, 한쪽에서 STmin
체크박스를 켜고 값을 입력 → DOM을 직접 조회해 두 위젯의 체크박스 상태(`true`/`true`)와 입력값
(`"0A2A"`/`"0A2A"`)이 완전히 동일함을 확인 — canStore를 통한 공유가 실제로 동작함을 실측으로
증명. SeedKey DLL 업로드 UI도 두 위젯에 동일하게 렌더링됨을 확인("SeedKey DLL 업로드" /
"미로드 (더미 키 사용)"). 이 자동화 환경의 드래그 시뮬레이션 한계로 위젯을 겹치지 않게
재배치하지는 못해 값 대조는 DOM 조회로 했다(위젯 리스트/체크박스 클릭 등 실제 UI 조작 자체는
정상 동작).

## "오디오 신호 모니터" 소폭 개선: 30분 단위 파일 분할 + 경과초 X축 (2026-08-02, 개발 완료 — 실기 검증 통과)

### 요구사항
1. 레코딩 시 파일명을 날짜/시간으로 자동 설정(이미 구현되어 있었음) + **30분 단위로 파일을
   끊어서 저장**(신규).
2. 파형 그래프 X축을 초 단위 시간으로 표시하고, **녹음을 시작하면 0초부터 시작**하도록 표시.

### 구현
- **30분 세그먼트 분할**: `AudioService`에 백그라운드 타이머 스레드(`_rotation_loop`, 5초 간격
  점검)를 추가. 위젯의 Record(`owner="widget_record"`)가 30분(`SEGMENT_DURATION_S`) 이상
  진행되면 `_rotate_segment()`가 현재까지 버퍼된 오디오를 현재 파일명으로 저장하고, 같은
  포맷(`monitor_YYYYmmdd_HHMMSS.wav`)의 새 파일명으로 이어서 계속 녹음 — **InputStream 자체는
  끊기지 않아 오디오 공백이 없다**. 테스트 러너의 `start()/stop()` 녹음(골든 파일 비교용, 항상
  파일 하나로 완결되어야 함)은 이 분할 대상에서 명시적으로 제외.
- 오디오 콜백 스레드에서 `_audio_data`에 append하는 부분과, 로테이션/정지가 그 리스트를
  스왑/저장하는 부분 사이에 새 락(`_audio_data_lock`)을 추가해 레이스 없이 안전하게 분리.
- 파일명 생성 로직을 `main.py`에서 `audio_service.py`의 `generate_monitor_filename()`으로
  이동(로테이션도 같은 함수를 재사용하도록 단일화).
- **X축 경과초(0s 시작)**: `AudioService`가 녹음 시작 시각을 `_recording_started_at`(30분
  로테이션과 무관하게 녹음 전체 기간 동안 고정)으로 기록해 `/api/audio/level`에 노출.
  프론트(`WaveformChart`)는 자동/실시간 창 계산 시 `xMin = Math.max(xMin, recordingStartedAtMs)`로
  녹음 시작 이전으로는 창이 넘어가지 않도록 클램프하고, 눈금 라벨의 기준점을 (녹음 중이 아닐 때
  쓰던) "지금으로부터 몇 초 전"에서 "녹음 시작으로부터 몇 초 후"로 전환. 결과: 녹음 직후에는
  창이 0s에 고정된 채 오른쪽으로 자라나고, xWindowMs(기본 2초)를 채운 뒤부터는 기존처럼 자연스럽게
  스크롤.
- 부가: 30분 로테이션이 조용히 일어나지 않도록 `/api/audio/level`에 `current_filename`을 추가해
  위젯이 그 값이 바뀌는 시점을 감지해 활동 로그에 "오디오 녹음 구간 저장됨: ..." 한 줄을 남기도록
  구성(투명성 목적, 사용자가 명시 요청한 것은 아니지만 조용한 분할이 혼란을 줄 수 있어 최소한으로
  추가).

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `audio_service.py`: `_rotation_loop`/`_maybe_rotate_segment`/`_rotate_segment`, `generate_monitor_filename` | 30분 세그먼트 자동 분할, 로테이션 타이머 | pytest 7개(위젯 Record만 시작 시각 기록, Stop 시 초기화, 모니터 전용은 시작 시각 없음, 실제 파일 쓰기+새 파일명 시작, 30분 전엔 무동작, 30분 후 발동, 테스트 러너 녹음은 절대 분할 안 됨) | **통과** |
| `audio_service.py`: `get_level()`의 `recording_started_at`/`current_filename` | X축 0초 기준점 및 로테이션 감지용 필드 노출 | 위 7개 테스트에 포함 + 기존 `test_get_level_default_state` 갱신 | **통과** |
| `AudioMonitorWidget.tsx`: `WaveformChart`의 `recordingStartedAtMs` 클램프 + 눈금 기준점 전환 | 녹음 시작 시 X축 0초 고정, 이후 정상 스크롤 | tsc/build/lint 클린 + 실기 검증(아래) | **통과** |

`tsc -b`/`vite build`/`oxlint` 클린. 백엔드 전체 176개 테스트 통과(신규 7개).

**실기 검증 (MacBook Pro 마이크)**: 백엔드 재기동 후 실제 Record 클릭 →
`recording_started_at`/`current_filename`이 즉시 채워짐 확인(`monitor_20260802_063753.wav` 등
실제 날짜/시간 형식) → Stop 후 실제 WAV 파일이 디스크에 저장됨 확인, `recording_started_at`이
`null`로 초기화됨 확인. X축 0초 시작은 **네트워크 요청 레벨에서 정확한 수치로 증명**:
Record 직후 첫 `/api/audio/waveform` 요청들의 `from_ms`가 `recording_started_at * 1000`과
소수점까지 정확히 일치(`1785620418454.7148`)하며, `to_ms`만 점점 커지다가(0s 지점에 창이
고정된 채 오른쪽으로 자라남) 경과 시간이 `xWindowMs`(2초)를 넘어선 뒤부터 정상적인 폭 2초
스크롤 창으로 자연스럽게 전환되는 것을 실제 요청 로그로 확인.

**미검증**: 30분 세그먼트 분할 자체는 실시간으로 30분을 기다려 눈으로 확인하지는 못했다 —
대신 실제 WAV 파일 쓰기까지 포함한 매니저 레벨 pytest로 로테이션 로직을 검증했다(`_rotate_segment`가
진짜 파일을 디스크에 쓰고 새 파일명으로 전환하는 것까지 확인).

### 후속 보완 (2026-08-02, 같은 날 추가 요청 — 개발 완료, 실기 검증 통과)

사용자 요청: X축 가장 왼쪽을 항상 0s로 표시하고 좌측→우측으로 파형을 그릴 것, 그리고 이
표시 방식을 "Start"(모니터만)와 "Record"(녹음) 양쪽에서 동일하게 구현할 것.

**문제**: 직전 구현은 녹음 중일 때만 `recordingStartedAtMs`를 기준으로 0초를 표시하고,
모니터링만 할 때는 "지금으로부터 몇 초 전"(음수, 우측이 0) 방식을 썼다 — 두 모드의 표시
방식이 달랐고, 스크롤이 진행된 뒤에는 녹음 중에도 결국 "지금 기준" 표시로 되돌아갔다.

**수정**: 눈금 라벨의 기준점을 항상 **현재 보이는 창의 왼쪽 끝(`xMin`)**으로 통일
(`t - xMin`, 기존 `t - xMax` 또는 조건부 `recordingStartedAtMs` 대신). 이러면:
- 왼쪽 끝은 언제나 0으로 표시되고 시간은 우측으로 갈수록 증가 — Start/Record 구분 없이
  동일한 코드 경로.
- 녹음 시작 직후에는 (이전에 구현한) `xMin`을 `recordingStartedAtMs`로 클램프하는 로직이
  여전히 살아있어, 그 구간 동안은 "진짜 녹음 시작 후 경과초"와 100% 일치.
- 클램프가 더 이상 적용되지 않는 시점(경과 시간이 xWindowMs를 넘어선 뒤)부터는 "현재 보이는
  창의 왼쪽 = 0s"라는 동일한 규칙이 모니터링 모드와 마찬가지로 자연스럽게 이어진다.

**검증**: 실제 마이크로 두 모드 모두 캔버스를 직접 캡처(`canvas.toDataURL()`)해 눈금 라벨을
픽셀 단위로 확인 — Start 모드와 Record 모드 둘 다 "0ms, 667ms, 1.33s, 2.00s"로 완전히 동일하게
표시됨을 확인했다(스크린샷이 아니라 캔버스 자체를 이미지로 추출해 실제 렌더링된 숫자를 직접
읽은 것이라 신뢰도가 높다). tsc 클린, 백엔드 전체 176개 테스트 통과(로직 변경 없음, 프론트
전용 수정).

### 후속 보완 2 (2026-08-02, 같은 날 추가 요청 — 개발 완료, 실기 검증 통과)

사용자 피드백: "Record"할 때의 파형 출력(왼쪽=0s, 0에서 시작해 창이 채워짐)은 잘 됐는데,
"Start"(모니터만)일 때는 아직 이 방식이 아니었다 — 동일하게 고칠 것.

**원인**: x축 0초 기준(`recording_started_at`)을 실제 WAV 녹음이 시작될 때(`start()`/
`start_widget_recording()`)만 설정하고 있었다. Start(모니터 전용, `start_monitor()`)는 이
값을 전혀 설정하지 않아서, `xMin`이 `recordingStartedAtMs`로 클램프되지 않고 처음부터
"지금 - xWindowMs"로 계산되어 버렸다 — 즉 Start를 누른 순간에도 창이 0에서부터 자라나지
않고 바로 꽉 찬 폭으로 시작(대부분 빈 구간)했다.

**수정**: 이 앵커 개념을 "녹음이 시작된 시각"에서 **"현재 스트림(Start든 Record든)이 열린
시각"**으로 일반화했다.
- 필드명을 `_recording_started_at` → `_stream_started_at`으로 변경(의미가 넓어졌으므로).
- `_open_stream()`(monitor/recording/widget_record 셋 다 거치는 공통 경로)에서 스트림이
  실제로 열릴 때 **항상** 이 값을 설정 — Start와 Record가 정확히 같은 코드 경로를 타게 됨.
- CAN-SWDL의 SeedKey 패턴처럼 "이미 열린 스트림을 그 자리에서 업그레이드"하는 경로
  (모니터→녹음, 모니터→위젯 녹음)는 그 순간 앵커를 새로 리셋 — Record를 누르면 그 순간부터
  다시 0초 (모니터링 중이던 시간과 무관하게).
- 이미 스트림이 열려 있을 때의 `start_monitor()` "piggyback" 경로(예: 테스트 러너 녹음이
  이미 진행 중일 때 Start를 누른 경우)는 기존 앵커를 건드리지 않음 — 회귀 테스트로 고정.
- `get_level()` 응답 필드명도 `recording_started_at` → `stream_started_at`으로 변경, 프론트
  `AudioLevel` 타입과 `AudioMonitorWidget.tsx`도 동일하게 리네임(`recordingStartedAtMs` →
  `streamStartedAtMs`).

**검증**: pytest 6개 추가/수정(모니터 전용 스트림도 앵커가 설정됨, piggyback 시 앵커 유지,
stop_monitor 시 앵커 초기화, 기존 rotate/stop 테스트 리네임) — 백엔드 전체 178개 테스트 통과.
실기 검증: 실제 마이크로 Start를 누른 직후 `/api/audio/level`의 `stream_started_at`이 즉시
채워짐을 확인했고, 그 직후 첫 `/api/audio/waveform` 요청들의 `from_ms`가 `stream_started_at *
1000`과 소수점까지 정확히 일치(`1785625223104.1619`)하며 `to_ms`만 점점 커지는 것을
확인했다 — Record 모드에서 이미 검증했던 것과 완전히 동일한 동작.

### 후속 보완 3 (2026-08-02, 같은 날 추가 버그 리포트 — 수정 완료, 실기 검증 통과)

사용자 리포트: "x 축 시간 값이 변하지 않고 항상 고정되어 있다."

**원인**: 직전 수정("Start/Record 동일하게")에서 눈금 라벨 기준점을 `t - xMin`(현재 보이는
창의 왼쪽 끝)으로 바꿨는데, 실시간 스크롤 구간에서는 `xMin`이 매 프레임 `t`와 같은 속도로
같이 흘러가기 때문에 `t - xMin`이 **항상 상수**가 되어버렸다(예: 항상 "0ms, 667ms, 1.33s,
2.00s"로 고정 — 실제로는 절대 변하지 않음). "왼쪽=0s"라는 요구사항을 잘못 해석해서, 매
순간의 창 자체를 기준으로 라벨을 다시 정규화한 것이 문제였다.

**수정**: 눈금 기준점을 다시 **고정된 앵커**(`streamStartedAtMs`, Start/Record가 시작된
절대 시각)로 되돌렸다 — 단, 이번엔 백엔드가 Start/Record 양쪽에 이미 이 값을 채워주고
있으므로(후속 보완 2에서 구현) 별도 분기 없이도 자동으로 "Start와 Record 동일" 요구사항이
충족된다. 이 앵커가 고정되어 있으므로 `t - streamStartedAtMs`는 실제 벽시계 경과 시간과
함께 계속 증가한다 — 스트림 시작 직후에는 (기존 클램프 덕분에) 왼쪽 끝이 정확히 0s이고,
시간이 지나면서 좌우 라벨 모두 실제 경과초를 반영하며 계속 커진다.

**검증**: 실제 마이크로 Start 실행 후 5초 간격으로 캔버스를 두 번 스크린샷 — 첫 번째
"311.26s ~ 316.26s", 5초 뒤 "336.48s ~ 341.48s"로 라벨이 실제로 전진하는 것을 육안으로
확인했다(더 이상 고정되지 않음). 추가로 `/api/audio/level`의 `stream_started_at`과 실제
캡처된 `/api/audio/waveform` 요청들의 `from_ms`를 대조해 좌측 눈금 값이 경과초와 정확히
일치하며 시간에 따라 증가함을 수치로도 재확인했다. tsc/build/lint 클린, 백엔드 전체 178개
테스트 통과(이번 수정은 프론트 라벨 계산 로직만 변경, 백엔드/테스트 변경 없음).

## "전원 컨트롤" 위젯 (2026-08-04, 사용자 승인 완료 — 개발 완료, 검증 통과)

### 목표/범위
- 기존 `backend/power_supply_service.py`(PyVISA/SCPI 파워서플라이 제어, 테스트 러너의
  Power 스텝에서만 쓰이던 것)를 REST로 직접 노출하는 신규 독립 위젯.
- 기능: ① 전원 연결/해제, ② 배터리 전압+전류 입력 후 OK로 명령 전송, ③ ACC 토글
  스위치, ④ IGN 토글 스위치, ⑤ 자동 On/Off 반복(배터리 전압을 설정한 On값↔0V로 On시간/
  Off시간 간격으로 계속 전환), ⑥ 자동 전압 Up/Down 반복(Low↔High 삼각파, 편도 시간 입력,
  전류는 스윕 내내 고정).
- 사양 확정 과정에서 명확히 한 것(사용자 확인):
  - 5번은 ACC/IGN이 아니라 **배터리 전압**을 On(입력값)↔Off(0V/0A)로 토글하는 것.
  - 6번은 **삼각파**(Low→High→Low, 한 사이클 = 입력한 편도 시간 × 2), 톱니파 아님.
  - 2, 5, 6번 모두 전압+전류를 함께 입력(기존 `APPLy {voltage},{current}` SCPI 포맷 —
    테스트 러너 스크립트의 `{"command":"BATT","voltage":"14.4,5"}` 패턴과 동일한 개념).
- 제외: 실제 전압/전류 read-back(장비에 MEAS 계열 명령이 없어 측정값 조회 불가 — 위젯은
  마지막으로 보낸 명령값만 표시), 파워서플라이 출력 릴레이 자체의 On/Off(OUTP 명령은
  이 장비에서 검증된 적 없어 사용하지 않음 — ACC/IGN 디지털 비트와 배터리 전압 두 채널만
  다룸).

### 핵심 설계
- `_apply_battery(v, i)` 공통 헬퍼로 모든 전압 설정 경로(수동 OK, On/Off 반복, 스윕)가
  `APPLy {v},{i}`를 보내고 마지막 전압/전류를 추적(read-back이 없어 위젯 표시용으로 필요).
  기존 `set_power()`(테스트 러너 스크립트용, 문자열 그대로 전달)는 하위 호환을 위해
  변경하지 않고 그대로 둠 — 새 `set_battery()`/`set_acc_ign()`이 위젯 전용 진입점.
- On/Off 반복과 전압 스윕은 `tx_scheduler.py`/`audio_service.py`의 기존 "상시 백그라운드
  스레드 + 0.2초 틱" 패턴을 재사용(`_auto_loop`/`_auto_tick`). 둘 다 같은 전압 채널을
  다루므로 하나가 켜져 있으면 다른 하나의 시작을 거부(`start_onoff_repeat`/`start_sweep`).
  삼각파는 `elapsed % (leg_s*2)`로 위상 계산 — 위로/아래로 구간을 나눠 선형 보간.
- `_auto_tick(now=...)`에 시각을 주입할 수 있게 해서, 테스트가 실제로 sleep하지 않고도
  위상 전환/삼각파 계산을 결정론적으로 검증할 수 있게 함(`audio_service`의 회전 타이머
  테스트와 동일한 패턴).

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `backend/power_supply_service.py` | `set_battery`/`set_acc_ign`/`start·stop_onoff_repeat`/`start·stop_sweep`, 백그라운드 틱, `info()` 확장(acc/ign/battery_voltage/battery_current/onoff/sweep) | pytest 17개 신규(연결 안 됐을 때 거부, bit 디코딩, 시간 0 이하 거부, On/Off 위상 전환, 삼각파 4개 지점 값, 상호 배타, disconnect 시 자동모드 정지 등) | **통과** |
| `backend/main.py` | `POST /api/power/battery`, `/acc_ign`, `/onoff/start`,`/stop`, `/sweep/start`,`/stop` | pytest 1개(`test_power_control_widget_routes_degrade_gracefully_without_hardware`): 미연결 상태에서 전부 `ok:false`(정지 라우트는 `ok:true`), status 필드 존재 확인 | **통과** |
| `frontend/src/widgets/PowerControlWidget.tsx` (신규) | 6개 섹션 UI, `canStore.status.power` 폴링, 자동모드 중 수동 배터리 입력/반대쪽 자동모드 비활성화 | `tsc -b`/oxlint 클린, 브라우저로 미연결 상태 렌더링·비활성화 상태·스크롤 확인 | **통과** |
| `types.ts`/`api/client.ts`/`registry.tsx` | `PowerStatus` 확장(`PowerOnOffState`/`PowerSweepState`), `WidgetType`에 `powerControl` 추가, API 클라이언트 6개 함수 | `tsc -b` | **통과** |

백엔드 전체 204개 테스트 통과(신규 18개), 프론트 `tsc -b`/`oxlint`/`vite build` 클린.

**검증 제약**: 이 개발 환경에는 실제 파워서플라이(VISA 장비)가 연결돼 있지 않다(기존
`power_supply_service` 테스트도 동일 전제 — "no real VISA instrument attached in
CI/dev"). 백엔드 로직(SCPI 명령 시퀀스, 위상 전환, 삼각파 보간)은 가짜 VISA 인스턴트로
철저히 단위 검증했고, 프론트는 브라우저에서 연결 버튼 클릭 시 `pyvisa`가 없다는 에러가
정확히 표시되는 것과 미연결 상태에서의 입력/버튼 비활성화, 6개 섹션 전체 스크롤을
확인했다. 실제 하드웨어로 전압/ACC/IGN 명령이 물리적으로 잘 나가는지, On/Off 반복·삼각파
스윕이 실제 장비에서 의도대로 동작하는지는 실기 연결 후 별도 확인이 필요하다.

## "CAN-오디오 지연 확인" 위젯 (2026-08-06, 사용자 승인 완료 — Phase 1 개발 완료, 검증 통과)

### 목표/범위
CAN 신호 전송(트리거) 후 오디오 신호가 반응하기까지의 지연시간을 측정하고 싶다는 요청.
기존 "CAN 신호 그래프"(`GraphWidget`)와 "오디오 신호 모니터"(`AudioMonitorWidget`)를 한
화면에서 같은 시간축으로 겹쳐 보여주면 눈으로 델타를 읽을 수 있다는 아이디어였으나, 구현
전에 두 그래프가 애초에 같은 시간축(클럭 정합성)인지부터 리뷰했다.

- **Phase 1(이번 범위, 승인 완료)**: CAN 신호 그래프 + 오디오 파형을 하나의 위젯에서 같은
  절대 epoch ms 시간축으로 겹쳐 표시하고, 사용자가 휠 줌/드래그 팬으로 확대해 눈으로
  델타를 읽는다. 자동 델타 계산은 없음.
- **Phase 2(범위 밖, 설계 방향만 문서화)**: CAN 신호 트리거 시점 이후 오디오 레벨이
  임계값을 넘는 첫 지점을 백엔드가 자동 감지해 지연시간(ms)을 계산·누적 통계(평균/최소/
  최대/표준편차)로 표시. 오디오 원시 샘플은 이미 `_raw_chunks`(30초 순환 버퍼,
  `audio_service.py`의 `RAW_BUFFER_SECONDS`)에 있으므로 분석 엔드포인트만 추가하면 되어
  구현량 자체는 크지 않지만, 임계값 튜닝·오검출 처리에 실기 검증이 필요해 별도 승인 후
  진행한다.
- **CAN 인터페이스**: Vector/PCAN 둘 다 지원 대상.

### 오차 요인 리뷰 (실제 코드 근거)
1. **CAN 타임스탬프 출처별 정합성** — `can.Message.timestamp`(초)의 의미가 인터페이스마다
   다르다.
   - virtual: 수신/루프백 시 `time.time()`을 그대로 찍는다(python-can
     `interfaces/virtual.py`). 이미 epoch 정렬, 서브 ms 오차.
   - Vector CANcase: 연결 시 1회 `xlGetSyncTime`/`xlGetChannelTime`을
     `time.perf_counter()`와 상관시켜 `_time_offset`을 계산해두고 이후 모든 프레임에
     더한다(`interfaces/vector/canlib.py`). 하드웨어 타임스탬프 기반, 별도 조치 없이
     epoch 정렬됨.
   - PCAN: `uptime` 패키지가 설치돼 있어야 `boottimeEpoch`(부팅 시각의 epoch)를 디바이스
     타임스탬프(부팅 이후 경과 μs)에 더해 epoch로 만들어준다(`interfaces/pcan/pcan.py`).
     `uptime`이 없으면 `boottimeEpoch=0`이 되어 타임스탬프가 "기기 부팅 이후 상대값"에
     불과 — 오디오(epoch 기준)와 아예 다른 시간축이 되어 비교가 무의미해진다. 기존
     `backend/requirements.txt`에는 `uptime`이 없었다.
2. **오디오 타임스탬프의 체계적 지연 편향** — `AudioService`의 sounddevice 콜백은
   `now = time.time()`으로 콜백이 "도착한" 시각을 찍고 그 청크의 모든 샘플 시각을 여기서
   역산한다(`audio_service.py`). 콜백은 디바이스가 이미 버퍼링해 둔 블록을 넘겨준 뒤에야
   실행되므로, 실제 음향 이벤트는 이보다 "블록사이즈/샘플레이트 + OS 오디오 스택 지연"만큼
   먼저 일어났다 — 디바이스에 따라 대략 수~수십 ms(통상 10~30ms대)의 고정 편향이 오디오
   쪽에만 낀다. 청크 내부 샘플 간 상대 타이밍은 샘플레이트 기반이라 정확하다.
3. **네트워크/렌더링 지연은 저장된 좌표값을 오염시키지 않는다** — CAN 쪽은
   `signalHistory`에 백엔드 원시 `ts`를 그대로 저장하고(`canStore.ts`), 오디오 쪽은
   `GET /api/audio/waveform`이 epoch ms 구간을 그대로 질의해 원본 샘플을 내려준다
   (`audio_service.py` `get_waveform`). WS 30ms 배치, 프론트 10~60Hz 스로틀, 오디오
   60ms 폴링은 "화면에 지금 무엇이 그려지는지"에만 영향을 주고 히스토리 데이터 좌표
   자체는 오염시키지 않는다 — 오차의 지배 요인은 전송 지연이 아니라 타임스탬프 출처의
   정합성과 오디오 콜백 편향이다.
4. **예상 총 오차(개략치, 실기 확인 전)**: Vector/virtual + 오디오는 오디오 콜백 편향
   (대략 10~30ms대, 디바이스 의존)이 지배적, CAN 쪽은 서브 ms. PCAN은 `uptime` 없이는
   측정 불가, 설치해도 boottime 계산의 실측 정확도는 실기에서 별도 확인 필요. 완전한
   0 오차는 불가능하지만 대부분의 "CAN 트리거 → 경고음 시작" 지연(보통 수백 ms 이상)
   비교에는 실용적으로 문제없을 가능성이 높다. 수십 ms 이하 정밀 비교가 필요하면 CAN으로
   릴레이를 클릭시켜 그 소리를 같은 마이크로 잡는 방식 등으로 1회 보정 측정을 권장(자동
   보정 로직은 Phase 1에 넣지 않음).

### 모듈 분해

| 모듈 | 책임 | 검증 방법 | 상태 |
|---|---|---|---|
| `backend/requirements.txt` | `uptime` 패키지 추가(PCAN 타임스탬프 epoch 정렬용, python-can이 이미 자동으로 사용하는 선택적 의존성) | 설치 확인 | **통과** |
| `backend/can_manager.py` | `connect()`의 `interface=="pcan"` 분기에서 연결 성공 직후 `can.interfaces.pcan.pcan.boottimeEpoch != 0`을 읽어 `config["epoch_aligned"]`로 노출(virtual/vector는 항상 True) | pytest 1개 추가(`test_virtual_connection_reports_epoch_aligned_timestamps`), `/api/connect` 실제 호출로 `epoch_aligned:true` 응답 확인. PCAN 분기는 실기 필요 | **통과**(virtual만) |
| `frontend/src/widgets/CanAudioLatencyWidget.tsx`(신규) | CAN 신호(`SignalPicker` 재사용) + 오디오 채널 파형을 절대 epoch ms 공유 X축(`pt.ts*1000` vs 기존 `/api/audio/waveform`)으로 겹쳐 표시, 공유 휠줌/드래그팬, PCAN `epoch_aligned=false` 시 경고 배너 | `tsc -b`/`vite build`/`oxlint` 클린 | **통과**(빌드만, 아래 검증 제약 참고) |
| `types.ts`/`registry.tsx` | `WidgetType`에 `canAudioLatency` 추가, 라벨/기본크기 등록 | `tsc -b` | **통과** |

기존 `GraphWidget.tsx`/`AudioMonitorWidget.tsx`는 내부 차트 컴포넌트가 export되지 않고
로컬 상태에 강결합돼 있어 그대로 재사용할 수 없다. 이미 실기 검증을 통과한 두 위젯을
건드리면 회귀 리스크가 생기므로, 새 위젯 파일 하나로 완결시키고 기존 두 위젯은 수정하지
않는다(실제로 두 파일 모두 수정하지 않음, `git diff` 확인).

백엔드 전체 220개 테스트 통과(신규 1개), 프론트 `tsc -b`/`vite build`/`oxlint` 클린.

### 검증 제약
이 개발 환경에는 브라우저 자동화 도구(Playwright/chromium-cli 등)가 준비돼 있지 않아
(설치를 시도했으나 프로젝트 매니페스트에 없는 패키지의 임시 설치라 자동 승인되지 않음)
**브라우저에서 실제 클릭·줌/팬까지는 확인하지 못했다** — 타입체크/빌드/린트 통과와
코드 리뷰(캔버스 드로잉·줌/팬 수학을 이미 실기 검증된 `GraphWidget`/`AudioMonitorWidget`의
동일 로직에서 그대로 포팅)까지만 확인했다. 또한 이 개발 환경에는 실제 PCAN/Vector
하드웨어와 마이크로 CAN-오디오 동시 반응을 재현할 DUT가 없다. `epoch_aligned`는 virtual
경로만 자동화하고, PCAN에서 정렬이 실제로 되는지와 오디오 콜백 편향의 실측값(대략
10~30ms대로 예측)은 실기 확보 후 별도 확인이 필요하다. 사용자가 브라우저에서
CAN 신호 선택 → 오디오 장치 선택 → Start → 두 차트가 같은 시간축에서 그려지는지,
휠 줌/드래그 팬이 두 차트에 동시에 반영되는지를 최초 1회 직접 확인해줄 것을 권장한다.

### 버그 수정 (2026-08-06, 사용자 실사용 확인 — 위 "검증 제약"에서 우려했던 바로 그 종류의 문제)
사용자가 virtual CAN으로 실사용 확인 중 "Start로 측정 시작 후 Stop을 누르면 파형이
멈춰야 하는데 계속 스크롤되며 사라진다"고 보고. 원인: `CanSignalChart`/`AudioChannelChart`
둘 다 줌/팬으로 손대지 않은("라이브") 상태의 롤링 윈도우 오른쪽 끝(`xMax`)을 항상
`Date.now()`로 계산했다 — 위젯의 Stop은 오디오 스트림만 멈출 뿐 CAN 수신이나 벽시계는
멈추지 않으므로, Stop 이후에도 `xMax`가 계속 흘러 마지막으로 잡힌 데이터가 `xWindowMs`
(기본 10초) 밖으로 밀려나며 화면에서 사라진 것 — `AudioMonitorWidget.tsx`의
`WaveformChart`가 이미 `liveAnchorRef`/`frozenAnchorRef`로 풀어둔 것과 같은 종류의 문제를
이번엔 두 차트가 시간축을 공유하는데도 그 얼림(freeze) 로직 자체를 빠뜨렸던 것.

수정: 부모 `CanAudioLatencyWidget`에 공유 `nowAnchor()` 함수를 추가해 두 차트 모두
`Date.now()` 대신 이를 통해 윈도우 오른쪽 끝을 구하도록 변경했다. 오디오가 `active`이면
`Date.now()`를 그대로 따라가고, Stop으로 `active`가 꺼지는 순간 마지막 값에 고정(freeze)
된다 — CAN 차트도 같은 앵커를 쓰므로 두 차트가 함께 멈춘다. 단, 오디오를 한 번도 Start한
적 없는 상태(`audioEverActiveRef`)에서는 항상 라이브로 두어, 이 위젯의 Start/Stop과
무관하게 계속 흘러야 하는 CAN 차트가 오디오를 켜기 전부터 멈춰 보이는 일이 없게 했다.
`active` 값은 클로저가 아니라 ref(`activeRef`)로 참조해, 자식 컴포넌트가 과거 렌더의
`nowAnchor` 클로저를 들고 있어도 항상 최신 상태를 반영하도록 했다.

검증: `tsc -b`/`vite build`/`oxlint` 클린(백엔드 변경 없음, 프론트 전용 수정). 실제
브라우저 재확인은 사용자가 다음에 Start→Stop을 눌러 파형이 그 시점에 고정되는지 확인
필요(이 환경엔 여전히 브라우저 자동화 도구가 없음, 위 "검증 제약" 참고).

### 버그 수정 2건 (2026-08-06, 사용자 실사용 확인 4건 보고 — 위 "검증 제약"에서
우려했던 바로 그 종류의 문제, 브라우저 자동화 도구 부재로 코드 리뷰만으로 원인 특정)

사용자가 virtual CAN + 위젯 실사용 중 4가지를 보고: ① Start→Stop→줌 확인→다시 Start를
누르면 동작하지 않음, ② 파형이 왼쪽→오른쪽 시간 흐름인데 시간 눈금이 가장 왼쪽을 0으로
증가하는 형태가 아님, ③ 확대/축소 후 ⟲(리셋) 아이콘을 눌러도 최초 상태로 안 돌아감,
④ 상단 시간창 +/− 버튼을 눌러도 파형·시간축이 갱신 안 됨. 브라우저에서 직접 재현할 수
없어 코드를 정독해 원인을 특정했다 — 4건 모두 근본 원인은 두 가지로 수렴한다.

1. **X축 눈금이 "오른쪽 끝 기준 경과(음수)"였다** (②): 처음엔 "몇 초 전"을 쉽게 읽도록
   `t - xMax`(0 at 오른쪽, 나머지 음수)로 그렸는데, 사용자가 원한 건 "왼쪽=0, 오른쪽으로
   갈수록 증가"였다. `fmtXTick` 호출을 `t - xMin`으로 바꿔 왼쪽 끝이 항상 0이 되도록
   수정(`CanSignalChart`/`AudioChannelChart` 양쪽 draw effect). 데이터가 그려지는
   좌우 순서(왼쪽=과거, 오른쪽=현재) 자체는 원래도 맞았다 — 눈금 라벨만 잘못됐었다.
2. **한 번이라도 휠 줌/드래그 팬을 하면(`xViewRef.current`가 null이 아닌 고정값이 됨)
   그 뒤로 Start·+/−·⟲가 전부 무력화됐다** (①③④의 공통 원인): 세 기능 모두
   "`xViewRef.current.xMin === null`일 때만" 자신의 로직이 반영되도록 짜여 있었다 —
   즉 라이브(자동) 모드일 때만 동작하고, 사용자가 한 번이라도 확대/축소한 뒤(요청 ①의
   실사용 시나리오 자체가 "확대/축소로 확인 후"라 거의 항상 이 상태)에는 아무 반응이
   없어 보였다.
   - **Start (①)**: `start()`가 `xViewRef`를 건드리지 않아, Stop 전에 확대해둔 고정
     구간이 재시작 후에도 그대로 남아 새 데이터가 전혀 안 보였다. 오디오 스트림이
     비활성→활성으로 전환되는 순간(rising edge)을 감지해 자동으로 뷰를 라이브로
     되돌리는 effect를 추가했다(`prevActiveRef`로 전이만 감지, 이미 라이브인 동안
     사용자의 수동 줌/팬과 충돌하지 않음).
   - **+/− 시간창 버튼 (④)**: `zoomXWindow`가 `xWindowMs`(라이브 모드의 기본 창 크기)만
     바꿨는데, 고정 뷰에서는 그 값이 아예 안 쓰였다. 현재 뷰가 고정 상태면 오른쪽 끝을
     기준으로 `xViewRef.current`를 직접 리사이즈하도록 수정해, 라이브·고정 어느
     상태에서나 버튼이 항상 보이는 효과를 내도록 했다.
   - **⟲ 리셋 (③)**: 상단 툴바의 리셋은 공유 X만 초기화하고 각 차트의 로컬 Y축 줌은
     그대로 남겨 "절반만 리셋"됐다(Y축이 이전 확대 상태로 고정된 채라 새 데이터가
     찌그러지거나 잘려 보임). 상단 리셋·차트별 리셋 버튼 3개를 모두 `resetEverything()`
     하나로 통일해 공유 X + 양쪽 차트의 Y를 전부 자동 맞춤으로 되돌리도록 했다
     (`resetToken` state를 부모가 올리면 각 차트가 자신의 `yViewRef`를 지우는 effect로
     구독).
   - 부수적으로 발견: `CanSignalChart`의 "신호가 조용해도 창이 계속 흐르게" 하는
     `LIVE_TICK_MS`(200ms) 인터벌이 `notifyChange`를 의존성 배열에 넣고 있었는데,
     `notifyChange`는 부모가 렌더될 때마다 새로 만들어지는 클로저라 오디오 폴링(60ms)이
     돌고 있는 동안은 이 인터벌이 200ms를 채우기 전에 계속 해제·재등록되어 사실상 한 번도
     발화하지 못했다(오디오 채널이 있을 때는 오디오 쪽 폴링이 대신 리드로우를 유발해
     가려져 있었을 뿐). `notifyChange`를 의존성에서 빼(과거 클로저를 들고 있어도 결국
     같은 `setSharedVersion`을 호출하므로 안전) 인터벌이 실제로 살아남도록 고쳤다.

검증: `tsc -b`/`vite build`/`oxlint` 클린, 백엔드 전체 220개 테스트(무관, 회귀 없음
재확인). 브라우저 재확인은 여전히 사용자 몫 — 이 환경엔 브라우저 자동화 도구가 없다(위
"검증 제약" 참고). 사용자가 다음에 확인할 때 정확히 보고했던 4가지 재현 절차(Start→
확대/축소→Stop→다시 Start, +/− 버튼, ⟲ 버튼)를 그대로 다시 밟아 확인해 줄 것을 권장한다.

### 버그 수정: 위젯 사용 후 CAN Simulator 전역 Start가 먹통 되는 심각한 렉 (2026-08-06,
사용자 실사용 확인)

사용자가 위젯에서 파형 취득→분석을 마친 뒤 상단 바의 전역 "CAN Simulator" Start 버튼을
누르면 반응이 없고 "내부적으로 매우 긴 렉"이 걸린다고 보고. 원인: `AudioChannelChart`의
파형 폴링(`WAVEFORM_POLL_MS=60ms`)과 부모의 오디오 레벨 폴링(`LEVEL_POLL_MS=100ms`)이
둘 다 `setInterval`로 구현돼 있어, 이전 요청이 아직 응답하지 않았어도 다음 요청을
무조건 새로 쏘고 있었다. 이 위젯은 (Requirement.md의 이전 항목에서 이미 확인했듯)
Stop 이후에도 채널 목록이 비지 않아(`AudioService`가 `_level_trackers`를 Stop 시
비우지 않음) 폴링이 절대 멈추지 않고 위젯이 마운트돼 있는 한 계속 도는데,
`GET /api/audio/waveform`(`backend/audio_service.py`의 `get_waveform()`)은 최대
30초치 원시 오디오 청크를 스캔·디시메이션하는 실제 연산 비용이 있는 요청이다 — 위젯을
오래 켜둔 채로 있다가 이 처리가 60ms보다 살짝이라도 느려지는 순간부터, 밀린 요청 위에
또 새 요청이 계속 쌓이며 눈덩이처럼 불어난다.

**왜 하필 전역 Start가 먹통이 되는가**: `backend/main.py`를 확인한 결과
`/api/audio/waveform`과 `/api/run/start` 둘 다 `async def`가 아니라 동기 `def`
핸들러 — FastAPI/Starlette는 동기 핸들러를 공유 스레드풀에서 실행하므로, 밀린
`/api/audio/waveform` 요청 수백 개가 스레드풀 슬롯을 다 차지하면 그 뒤에 들어온
`/api/run/start` 요청도 같은 큐에서 자기 차례를 기다리게 된다 — 이것이 "Start를
눌러도 반응이 없고 렉이 걸린다"의 정확한 메커니즘.

수정: 두 폴링 모두 `setInterval` 대신 "요청이 끝난 뒤에만 다음 요청을 예약하는"
자기재스케줄 `setTimeout` 방식으로 변경(`AudioChannelChart`의 파형 폴링,
부모의 오디오 레벨 폴링). 채널당·용도당 항상 최대 1개의 요청만 동시에 떠 있도록
보장되므로, 백엔드가 아무리 느려져도 요청이 쌓이는 일 자체가 원천적으로 불가능해진다.
이 문제는 원래 있던 `AudioMonitorWidget.tsx`의 `WaveformChart`도 동일한 `setInterval`
패턴을 쓰고 있어 잠재적으로 같은 위험이 있으나(이번 작업 범위는 새 위젯으로 한정,
`AudioMonitorWidget.tsx`는 수정하지 않음), 사용자가 그쪽에서도 유사 증상을 보면 같은
원인일 가능성이 높다는 점을 기록해둔다.

검증: `tsc -b`/`vite build`/`oxlint` 클린, 백엔드 전체 220개 테스트 통과(무관, 회귀
없음). 실제로 장시간 방치 후 요청이 정말 쌓이지 않는지, 전역 Start가 즉시 반응하는지는
이 환경에 브라우저 자동화 도구가 없어 직접 재현·확인하지 못했다 — 사용자가 이전과 같은
방식(위젯 사용 후 한동안 열어두고 전역 Start)으로 재확인해줄 것을 권장한다.

### CAN periodic 신호 영향 점검 (2026-08-06, 사용자 요청 — 실측 후 백엔드 최적화 1건 적용)

사용자가 "위 렉 문제가 CAN periodic 메시지 송신에도 영향을 미치는 것 같다"고 추가로
요청해 점검했다.

**직접적인 연결고리는 없음**: 주기 송신은 `backend/tx_scheduler.py`의 전용 백그라운드
스레드(`_loop()`, `time.sleep(0.001)`로 ~1ms 틱)에서 완전히 인프로세스로 처리되고
FastAPI의 HTTP 요청 스레드풀을 전혀 거치지 않는다. 오디오 쪽 락(`_level_lock` 등)과
CAN 쪽 락(`CanManager._lock`, `TxScheduler._lock`)도 서로 다른 객체라 직접적인 락
경합은 없다.

**간접적인 연결고리(GIL)는 실제로 있고, 실측으로 확인됨**: CPython은 GIL 때문에 한
순간에 파이썬 바이트코드를 실행하는 스레드가 하나뿐이다. `audio_service.py`의
`waveform_slice()`(오디오 파형 요청마다 호출, 최대 30초치 원시 청크를 파이썬 레벨로
순회하며 numpy 연산)가 GIL을 오래 쥐고 있으면, 같은 시간에 `tx_scheduler`의 1ms 틱
루프가 GIL을 못 받아 지연될 수 있다 — 즉 "CAN periodic 메시지에도 영향을 미친다"는
사용자의 의심은 메커니즘상 타당하다. 실제로 측정해봤다(`_ChannelLevelTracker`에 합성
오디오 30초를 채운 뒤 `waveform_slice()` 1회 호출 시간):

| 콜백 블록사이즈(샘플) | 30초 전체 스캔 | 최근 10초(라이브 창) |
|---|---|---|
| 256 | ~90ms | ~31ms |
| 441 | ~59ms | ~21ms |
| 1024 | ~38ms | ~14ms |
| 2048 | ~28ms | ~11ms |

라이브(최근 10초) 요청 하나에도 10~30ms가 걸린다 — 바로 위 항목에서 고친 요청 폭주
버그가 없더라도, 이 위젯이 켜져 있는 한 매 폴링 주기(원래 60ms)마다 이 정도 시간을
GIL을 쥔 채로 쓰는 셈이라 `tx_scheduler`가 주기적으로 밀릴 여지가 실측으로도 확인된다.

**적용한 조치**:
1. **`waveform_slice()` 최적화**(`backend/audio_service.py`): 청크 목록이 시간순으로
   쌓이는(append-only) 점을 이용해 `bisect`로 `[from_s, to_s]`와 겹칠 수 없는 앞쪽
   청크를 건너뛰고, 뒤쪽은 `chunk_start > to_s`가 되는 순간 `break`하도록 바꿨다.
   실측 결과 "과거 시점을 확대해서 보는" 시나리오(뒤쪽에 청크가 많이 남아있는 경우)는
   유의미하게 빨라지지만, 가장 흔한 "라이브(최근 N초)" 요청은 애초 비용의 대부분이
   "관련 없는 청크를 건너뛰는 것"이 아니라 "관련 있는 청크 각각의 numpy 연산" 자체라
   개선폭이 작다(21ms→21ms 수준, 실측으로 확인) — 그래도 기존 동작과 100% 동일한
   결과를 내면서(기존 `test_waveform_slice_*` 전부 통과) 공짜로 얻는 이득이라 반영했다.
   더 큰 폭의 개선(청크별 파이썬 루프+`np.unique` 자체를 없애는 벡터화 재작성)은 이번
   범위를 넘는 더 큰 리스크의 변경이라 하지 않았다 — 필요하면 별도 승인 후 진행.
2. **`WAVEFORM_POLL_MS` 60→100ms**(`frontend/src/widgets/CanAudioLatencyWidget.tsx`):
   `LEVEL_POLL_MS`와 동일한 값으로 늘려, 이 위젯이 만드는 GIL 점유 빈도 자체를
   줄였다(사람 눈에는 여전히 실시간처럼 보이는 수준을 유지하면서). `AudioMonitorWidget.tsx`는
   건드리지 않았다(이번 범위 아님).

**참고로 원래 `AudioMonitorWidget.tsx`의 `WaveformChart`도 같은 `waveform_slice()`를
동일한 폴링 방식(원래 60ms)으로 호출**하므로, 그쪽 위젯을 켜둔 채로도 동일한 GIL 경합이
있을 수 있다 — 이번엔 `waveform_slice()` 자체(공용 함수)는 개선했지만
`AudioMonitorWidget.tsx`의 폴링 주기는 건드리지 않았다.

검증: `tests/test_audio_service.py` 44개 전부 통과(동작 동일성 확인), 백엔드 전체
220개 테스트 통과, 프론트 `tsc -b`/`vite build`/`oxlint` 클린. tx_scheduler의 실제
주기 송신 타이밍이 이 변경으로 얼마나 개선되는지는(예: 실기에서 주기 메시지의 실제
전송 간격 지터를 오실로스코프/CAN 애널라이저로 측정) 이 환경에서 직접 확인할 방법이
없다 — 실기로 오디오 위젯을 켜둔 채 주기 신호의 CAN 메시지 표시창 카운트/간격이
안정적인지 사용자가 확인해줄 것을 권장한다.

### 후속 요청 2건 (2026-08-06, 사용자 확인 — 위 수정이 잘 됐다고 확인 후 추가 요청)

1. **"CAN-오디오 지연 확인" 위젯의 시간창 +/− 스텝을 5초 고정 → 10% 비례로 변경**:
   `X_WINDOW_STEP_MS = 5_000`(고정 델타)를 `X_WINDOW_STEP_FACTOR = 1.1`(현재 창 크기의
   ±10%, 곱셈 방식)로 교체했다. 고정 초 단위 스텝은 창이 이미 좁을 때(예: 500ms)
   5초를 더하면 창 크기가 10배 이상 뛰는 반면 창이 넓을 때(예: 5분)는 티도 안 나는
   문제가 있어, 어느 확대 수준에서도 일관되게 "10% 만큼" 늘고 줄게 했다
   (`frontend/src/widgets/CanAudioLatencyWidget.tsx`).

2. **"Enable Msg" 버튼이 위젯의 개별 Periodic 신호 송신과 얽혀 있던 문제 분리**:
   상단바 "Enable Msg"는 DBC의 모든 Periodic 메시지를 일괄 On/Off 하는 버튼인데,
   그 On/Off 표시가 `tx.auto_entries.length > 0`(즉 "무엇이든 하나라도 주기 재전송
   중이면 켜짐")으로 계산되고 있었다. 그런데 위젯에서 Periodic 신호 하나만 건드려도
   그 메시지가 개별적으로 auto_entries에 등록되는 것은 원래 의도된 동작(확정 사양
   5번)이라, 사용자가 위젯만 썼을 뿐인데 "Enable Msg"가 눌린 것처럼 보이는 문제가
   있었다. `backend/tx_scheduler.py`에 `_enable_msg_armed`(Enable Msg로 켠 메시지
   이름만 추적하는 집합)를 추가하고, `status()`에 `periodic_enabled` 필드로 노출,
   신규 `disable_all_periodic()`(Enable Msg가 켠 것만 끄고 위젯이 개별적으로 켠
   건 건드리지 않음)을 추가했다. `stop_auto()`(전역 Start/Stop이 호출)는 전체
   클리어 시 이 집합도 같이 비우고, 단일 메시지 클리어 시(위젯 삭제 시 다른 위젯이
   안 쓰는 메시지 정리) 그 이름만 집합에서도 제거한다. 신규 엔드포인트
   `POST /api/tx/periodic/disable_all`. 프론트 `App.tsx`의 `periodicOn`은 이제
   `tx.periodic_enabled`를 읽고, 끌 때 `api.disableAllPeriodic()`을 호출한다(기존
   `api.txAutoStop()`은 위젯 삭제 시 개별 메시지 정리 용도로 그대로 유지).
   - 주의할 점(문서화): Enable Msg를 누르면 이미 위젯이 개별적으로 켜둔 메시지도
     포함해 "전체"를 다시 무장하므로(`enable_all_periodic()`은 DBC의 모든 Periodic
     메시지를 대상으로 함), 그 상태에서 Enable Msg를 다시 꺼서 전체를 멈추면 그
     위젯이 개별적으로 켰던 메시지도 같이 멈춘다 — Enable Msg 버튼의 "전체 On/Off"
     의미상 의도된 동작이다. 이번에 고친 것은 반대 방향(위젯 조작 → Enable Msg가
     저절로 눌린 것처럼 보이는 것)이다.

검증: `backend/tests/test_tx_scheduler.py`에 5개 신규 테스트(위젯 단독 조작 시
플래그 안 켜짐, enable_all이 플래그 켜고 disable_all이 그것만 끔, disable_all이
위젯이 독자적으로 켠 신호는 안 건드림, 전역 stop_auto가 플래그도 전체 리셋, 단일
메시지 stop_auto가 그 이름만 집합에서 제거), `test_api.py`에 REST 왕복 테스트 1개
추가. 백엔드 전체 226개 테스트 통과. 프론트 `tsc -b`/`vite build`/`oxlint` 클린.
브라우저에서 실제로 위젯 조작 시 "Enable Msg" 버튼 표시가 더 이상 자동으로 바뀌지
않는지는 이 환경에 브라우저 자동화 도구가 없어 직접 확인하지 못했다 — 사용자가
다음 실사용 시 확인해줄 것을 권장한다.

### Windows 실사용 버그 조사: 오디오 위젯 Start 후 CAN 전송 지연 + Stop 10여초
지연 + 전역 Stop 시 Failed to fetch (2026-08-10, 사용자 보고 — 로그 추가만 진행,
Windows 실기가 없어 코드 리뷰로 원인 후보를 특정하고 재현 시 확증할 진단 로그를
심음)

사용자가 Windows에서 "CAN-오디오 지연 확인" 위젯의 Start를 누른 후 CAN 신호 전송이
매우 느려지고, 위젯의 Stop을 눌러도 10여초 후에야 멈추며, 그 뒤 전체 시뮬레이터
Start/Stop 바의 Stop을 누르면 "Failed to fetch"가 뜬다고 보고. 이 개발 환경(macOS)엔
Windows도, 사용자와 같은 다채널 오디오 인터페이스도 없어 직접 재현할 수 없었다 --
코드 리뷰로 원인 후보를 좁히고, 재현 시 정확히 어디서 시간이 새는지 드러낼 진단
로그를 추가했다.

**가장 유력한 원인 후보 (기존에 이미 알고 있던, 아직 안 고친 버그와 정확히 일치)**:
`backend/layouts/Test01.json`을 보면 사용자 레이아웃에 `audioMonitor`
(`AudioMonitorWidget.tsx`, 오디오 신호 모니터)와 `canAudioLatency`
(`CanAudioLatencyWidget.tsx`, 이번에 문제가 보고된 위젯)가 **동시에** 배치돼 있다.
그런데 위 "위젯 사용 후 CAN Simulator 전역 Start가 먹통 되는 심각한 렉" 항목에서 이미
`CanAudioLatencyWidget.tsx`의 폴링을 `setInterval`(고정 주기, 이전 요청 완료 여부와
무관하게 계속 발사)에서 "요청이 끝난 뒤에만 다음 요청을 예약"하는 자기재스케줄
`setTimeout`으로 고쳤지만, 그 항목에 명시적으로 적어둔 대로 **`AudioMonitorWidget.tsx`는
이번 범위가 아니라 고치지 않았다** -- 그리고 실제로 지금도
`AudioMonitorWidget.tsx`의 파형 폴링(60ms, 372번째 줄 근처 `setInterval(poll,
WAVEFORM_POLL_MS)`)과 레벨 폴링(100ms)이 여전히 옛 `setInterval` 패턴이다. 두 위젯이
동시에 열려 있으면: 백엔드가 아주 잠깐이라도(예: Windows에서 이 사용자의 오디오
인터페이스가 macOS보다 큰 blocksize/샘플레이트를 쓰거나 GIL 경합으로) 60ms/100ms보다
느려지는 순간부터 `AudioMonitorWidget`의 요청이 무한정 쌓이고, `/api/audio/waveform`·
`/api/audio/level`과 `/api/tx/signal`(CAN 신호 전송)·`/api/run/start`·`/api/run/stop`이
전부 `main.py`에서 `async def`가 아닌 동기 `def` 핸들러라 Starlette의 같은
스레드풀을 공유하므로 -- 밀린 오디오 요청이 스레드풀을 다 차지하면 CAN 신호 전송도
전역 Stop도 그 뒤에서 자기 차례를 기다리게 된다(이전에 전역 Start가 먹통 됐던 것과
동일한 메커니즘, 이번엔 CAN 전송/Stop/전역 Stop에도 적용). 오래 쌓이면 브라우저의
호스트당 동시 연결 제한에 걸려 이후 요청이 아예 나가지도 못하고 "Failed to fetch"로
실패할 수 있다.

**추가로 실측으로 이미 확인된 보조 원인(기존 항목)**: "CAN periodic 신호 영향 점검"
항목에서 `waveform_slice()`가 GIL을 오래 쥐고 있으면 `tx_scheduler`의 1ms 틱 루프가
지연될 수 있음을 실측으로 확인해뒀다 -- Windows에서 오디오 인터페이스의 콜백
블록사이즈가 더 크면(또는 이 사용자의 다채널 장치가 더 무거우면) 이 효과가 더 크게
나타날 수 있다. "Stop 클릭 후 10여초 지연"은 별도 후보로, `sd.InputStream.stop()`/
`close()`는 블로킹 PortAudio 호출이라 Windows의 특정 host API(WASAPI/WDM-KS 등)에서
디바이스 반환 대기가 길어지는 경우가 있다고 알려져 있다 -- 이번 로그로 이 세 가지
후보(스레드풀 정체, GIL 경합, PortAudio stop/close 블로킹) 중 실제로 어느 것이
지배적인지 구분할 수 있다.

**이번엔 로그만 추가(사용자 요청 범위)** -- `AudioMonitorWidget.tsx`를 고치지는
않았다. 위 분석이 맞다면 그쪽에도 동일한 자기재스케줄 `setTimeout` 수정이 필요하지만,
이는 사용자가 재현·로그 확인 후 별도로 결정할 일이라 판단해 이번엔 진단 로그만
심었다.

**추가한 진단 로그** (모두 `logging` 표준 모듈, 평상시엔 조용하고 임계값을 넘을 때만
WARNING으로 찍힘 -- uvicorn이 root logger를 직접 설정하지 않는 환경이라
`backend/main.py`에 `cansim` 네임스페이스 전용 핸들러를 새로 달아 INFO/WARNING이
콘솔에 항상 보이게 함):
- `backend/audio_service.py`: 콜백 처리 시간(>20ms 시 경고, `_status` 오버플로우
  플래그도 즉시 경고), 스트림 open 소요시간, **`stream.stop()`/`close()` 각각의
  소요시간을 분리해서 측정**(>300ms 시 경고 -- "Stop 10여초 지연"의 정확한 위치를
  집어냄), `_stream_lock` 대기시간(>50ms 경고), `waveform_slice()`/`get_waveform()`
  소요시간(>20ms 경고, 스캔한 청크 수 포함).
- `backend/tx_scheduler.py`: 틱 루프 실제 주기(목표 ~1ms 대비 >10ms 지연 시 경고),
  `_lock` 대기시간(>5ms 경고), 개별 송신 job 소요시간(>5ms 경고 -- GIL 경합이 아니라
  `CanManager.send()`(드라이버) 자체가 느린 경우와 구분).
- `backend/main.py`: 모든 HTTP 요청에 대해 300ms 넘는 요청을 in-flight 요청 수와
  함께 경고(스레드풀 정체 여부 직접 확인), asyncio 브로드캐스트 루프(WS 상태/RX
  스트림)의 틱 지연을 100ms 넘을 때 경고(이벤트 루프 자체가 GIL에 굶주렸는지 확인).

검증: 백엔드 전체 226개 테스트 통과(동작 변경 없음, 로그만 추가 -- 순수 부가 기능
확인). `waveform_slice()`에 30초치 합성 오디오(콜백 블록사이즈 256, 가장 무거운
프로파일링 시나리오)를 채운 뒤 직접 호출해 경고 로그가 실제로 찍히는지 수동
확인(`scanned 5625/5625 chunks in 76.3ms ... -- held the GIL for this long`) --
임계값 로직 자체는 동작 확인됨. Windows에서 실제로 세 후보 중 무엇이 지배적인지,
그리고 `AudioMonitorWidget.tsx` 동시 사용이 정말 원인인지는 사용자가 다음 실사용
시(가능하면 문제 재현 직후) 서버 콘솔 로그를 확인·공유해줄 것을 권장한다 --
`cansim.audio`/`cansim.tx_scheduler`/`cansim.http` 로거 이름으로 필터링하면 관련
줄만 볼 수 있다.

**추가 조치 (사용자 확인 후, 같은 세션에서 진행): `AudioMonitorWidget.tsx`에도
동일한 자기재스케줄 수정 적용** -- 위에서 가장 유력한 원인으로 지목한 것이 바로
이 파일의 옛 `setInterval` 폴링이라, 사용자가 "지금 같이 고친다"를 선택해 로그
추가에 더해 실제 수정도 반영했다. `WaveformChart`의 파형 폴링(`WAVEFORM_POLL_MS`,
기존 60ms `setInterval`)과 `AudioMonitorWidget`의 레벨 폴링(`LEVEL_POLL_MS`, 기존
100ms `setInterval`) 둘 다 `CanAudioLatencyWidget.tsx`와 동일한 패턴(요청이 끝난
뒤에만 다음 요청을 `setTimeout`으로 예약, 채널/용도당 항상 최대 1개 요청만
동시 진행)으로 교체했다. 이제 두 위젯을 동시에 열어둬도 어느 쪽 폴링도 무한정
쌓일 수 없다.

검증: `tsc -b --noEmit`/`vite build`/`oxlint` 클린(수정한 두 폴링 지점 관련 경고
없음, `dist/`에 대한 기존 oxlint 경고는 이번 변경과 무관한 기존 상태). 백엔드는
변경 없음(프론트 전용 수정). 실제로 두 위젯을 동시에 켜둔 채 장시간 방치해도
CAN 전송/전역 Stop이 더는 느려지지 않는지는 이 환경에 브라우저 자동화 도구가 없어
직접 재현·확인하지 못했다 -- 사용자가 Windows에서 이전과 동일한 재현 절차(두 위젯
동시 사용 중 Start→CAN 전송→Stop→전역 Stop)로 확인해줄 것을 권장한다. 위에서
추가한 진단 로그는 그대로 남아있으니, 이 수정 이후에도 같은 증상이 재현되면
로그를 보면 다른 원인(GIL 경합 또는 PortAudio stop/close 블로킹)이 지배적임을
바로 알 수 있다.

### 후속 버그 2건 (2026-08-10, 사용자가 Windows에서 위 진단 로그를 실제로 받아본 뒤
보고): 로그 폭주 + Ctrl-C로 서버가 종료되지 않음

사용자가 위에서 추가한 진단 로그를 켜고 Windows에서 실사용하다가 터미널에 같은
경고가 끝없이 쏟아지고, 그 상태에서 백엔드를 Ctrl-C로 종료해도 멈추지 않는다고
보고. 사용자가 붙여준 실제 로그가 결정적인 증거였다:

```
WARNING cansim.tx_scheduler: tick delayed 11.7ms (target ~1ms) ...
WARNING cansim.audio: waveform_slice: scanned 186/962 chunks in 89.8ms (window=5.00s, max_points=781) -- held the GIL for this long
WARNING cansim.audio: get_waveform: 217.3ms total for 2 channel(s), window=5.00s, max_points=781
WARNING cansim.audio: waveform_slice: scanned 151/962 chunks in 46.6ms (window=0.23s, max_points=947) -- held the GIL for this long
... (수백 줄 반복, 20~40ms 간격)
```

**이 로그로 확인된 것 (가설이 아니라 실측)**: `waveform_slice()`/`get_waveform()`이
윈도우에서 macOS 프로파일링(같은 5초 창에서 ~14-31ms 추정)보다 훨씬 느리게(90~220ms)
걸리고 있고, `window=5.00s`와 `window=0.23s` 두 종류의 요청이 동시에 20~40ms
간격으로 계속 들어오고 있다 -- 바로 위에서 지목한 대로 `AudioMonitorWidget`
(기본 5초 창)과 `CanAudioLatencyWidget`(줌 인해서 0.23초 창)이 실제로 동시에
열려 있었다는 뜻이다. `tick delayed 11.7ms`도 한 번 확인되어 GIL 경합이
`tx_scheduler`에도 실제로 영향을 준다는 것 역시 실측으로 확인됐다.

**로그 폭주 자체는 새 버그가 아니라, 진단 로그에 빠뜨린 안전장치였다**: 임계값
초과라는 조건이 폴링 주기(60~100ms)보다 계속 오래 참으로 유지되니, 매 요청마다
경고가 찍혀 사실상 매 60~100ms 스팸이 된 것 -- 조건 자체는 정확했지만 사용할 수
없을 정도로 로그가 넘쳤다.

**Ctrl-C 종료 안 됨은 별도 원인**: `main.py`의 `lifespan` 종료 블록을 다시 확인한
결과 `audio_service`를 아예 건드리지 않고 있었다 -- 스트림이 열려 있는 상태로
서버가 종료되면 PortAudio `InputStream`이 닫히지 않은 채 남는다. Windows에서
`stream.stop()`/`close()`가 멈춰버리는 경우(이전 항목에서 후보로 지목한 것과 동일한
현상) 이게 그대로 프로세스 종료를 막을 수 있다.

**수정**:
1. **`backend/diag_log.py`(신규)**: 로그 지점(key)별로 최소 2초에 한 번만 실제로
   찍고, 그 사이 억제된 횟수를 다음 로그에 "(+N more suppressed in the last 2s)"로
   붙여주는 공용 rate limiter. `audio_service.py`/`tx_scheduler.py`/`main.py`의
   반복 가능성이 있는 경고 지점(콜백 지연/오버플로우, `waveform_slice`/
   `get_waveform` 소요시간, 락 대기, 틱 지연, job 지연, HTTP 느린 요청 -- 경로별로
   따로 제한해 특정 엔드포인트가 다른 엔드포인트를 가리지 않게 함, 브로드캐스트
   루프 지연)에 전부 적용했다. 1회성인 스트림 open/stop 타이밍 로그(Start/Stop
   클릭당 한 번뿐)는 제한 없이 그대로 둠.
2. **`AudioService.shutdown()`(신규, `backend/audio_service.py`)**: 앱 종료
   시(`main.py`의 lifespan) 열려 있는 스트림을 정리하는 경로가 아예 없던 빈틈을
   메움. `_stream_lock`으로 스트림 상태를 스냅샷하고 즉시 idle로 비운 뒤, 실제
   블로킹 close는 락 밖에서 별도 데몬 스레드로 실행하고 최대 3초(`_SHUTDOWN_CLOSE_
   TIMEOUT_S`)만 기다린다 -- PortAudio의 `stop()`/`close()`가 Windows에서 정말로
   멈춰버려도 그 스레드만 버려두고 프로세스는 계속 종료될 수 있게 했다(같은
   스레드에서 `_stream_lock`을 다시 잡지 않으므로 데드락 불가능). `main.py`의
   `lifespan` 종료 블록에 `audio_service.shutdown()` 호출 추가.

검증: 백엔드 전체 226개 테스트 통과(회귀 없음). `diag_log.should_log()`를 직접
호출해 버스트 억제 및 억제 횟수 리포트 동작 확인. `AudioService.shutdown()`을
(a) 스트림 없음(즉시 no-op), (b) 0.5초 걸리는 가짜 스트림(정상 대기 후 완료), (c)
5초간 멈추는 가짜 스트림(타임아웃을 0.3초로 낮춰서 테스트 -- 실제로 306ms 만에
포기하고 반환, 스트림 상태는 즉시 비워짐)로 직접 시뮬레이션해 동작 확인.
Windows에서 실제로 로그가 읽을 수 있는 수준으로 줄고 Ctrl-C가 즉시 먹히는지는
사용자가 다음 실사용 시 확인해줄 것을 권장한다 -- 특히 Ctrl-C가 여전히 안 먹히면
그건 오디오 스트림이 원인이 아니라는 뜻이므로(이제 최대 3초 안에 포기하게 만들어
뒀으니), 그 시점의 스레드 덤프나 어느 지점에서 멈춰 있는지가 다음 조사에 필요하다.

### Ctrl-C 종료 안 됨, 3번째 확인 (2026-08-10, 사용자가 로그 폭주는 해결됐다고
확인했지만 "여전히 Ctrl-C로 종료가 안된다"고 재보고) -- uvicorn 자체의 무제한
graceful-shutdown 대기가 진짜 원인으로 확인됨

로그 폭주는 rate limiter로 확실히 해결됐지만(사용자가 붙여준 새 로그가 2초당 1회로
정상적으로 억제되고 있는 것으로 확인), `AudioService.shutdown()`(3초 bound)을 추가한
뒤에도 Ctrl-C가 여전히 안 먹힌다는 재보고를 받아 uvicorn 자체의 종료 시퀀스를
소스코드로 직접 확인했다(`uvicorn.server.Server.shutdown()`/`_wait_tasks_to_complete()`):

1. Ctrl-C(SIGINT) 시 uvicorn은 새 연결만 막고, **기존 연결/백그라운드 task가 전부
   끝날 때까지** `asyncio.wait_for(self._wait_tasks_to_complete(), timeout=self.config
   .timeout_graceful_shutdown)`로 대기한다.
2. `timeout_graceful_shutdown`의 **기본값이 `None`**이다(`uvicorn.config.Config.
   __init__` 확인) -- 즉 기본 설정으로는 **무제한 대기**. 이 대기가 끝나야만(또는
   타임아웃돼야만) 비로소 `self.lifespan.shutdown()`(우리 `main.py`의 `lifespan`
   종료 코드, `audio_service.shutdown()` 호출 포함)이 실행된다 -- **직전 항목에서
   추가한 `AudioService.shutdown()`의 3초 bound가 전혀 발동하지 못한 이유**: 그
   코드에 도달하기도 전에 이 무제한 대기에서 멈춰 있었을 가능성이 높다.
3. 이 서버는 프론트가 `/ws`로 WebSocket을 계속 열어두고(상태 브로드캐스트),
   오디오 위젯이 열려 있으면 `/api/audio/level`·`/api/audio/waveform`도 계속
   폴링한다 -- 브라우저 탭을 열어둔 채 백엔드만 Ctrl-C로 끄려고 하면 이 연결/요청들이
   실제로 "아직 안 끝난 연결"로 잡혀 무제한 대기의 대상이 될 수 있다.

**적용한 수정**: `--timeout-graceful-shutdown 5`를 uvicorn 실행 커맨드에 추가해
1번 단계의 대기 자체를 5초로 제한했다 -- 이 시점 이후엔 남은 연결/task를 강제
취소하고 무조건 `lifespan.shutdown()`으로 넘어간다(이제 `AudioService.shutdown()`의
3초 bound가 실제로 발동할 기회를 얻음). `backend/run_windows.bat`(사용자가 실제
쓰는 실행 스크립트), `backend/run_mac.txt`, `.claude/launch.json`(이 개발 환경의
백엔드 실행 설정) 세 곳 모두에 반영.

**검증 중 발견한, 솔직히 밝혀야 할 제약**: 이 mac 환경에서 실제로 `/ws`에 진짜
WebSocket 연결을 열어둔 채(raw 소켓으로 핸드셰이크 성공 확인) uvicorn에 SIGINT를
보내는 시뮬레이션을 해봤는데, **이 환경에서는 연결이 열려 있어도 uvicorn이 즉시
종료됐다**(`connection.shutdown()`이 진행 중인 연결도 강제로 닫는 것으로 보임) --
즉 "브라우저 탭을 열어두면 연결이 안 끊겨서 무한 대기한다"는 이론을 이 환경에서는
재현하지 못했다. `--timeout-graceful-shutdown` 추가는 표준적으로 권장되는 안전한
조치라 그대로 반영했지만(부작용 없음, 있으면 무조건 도움이 됨), Windows에서
여전히 Ctrl-C가 안 먹힌다면 이건 이 앱 코드의 문제가 아니라 **Windows의 asyncio
`ProactorEventLoop`가 SIGINT/Ctrl-C 전달 자체를 지연시키거나 놓치는, 잘 알려진
플랫폼 특성**일 가능성이 더 높다 -- 이 경우 uvicorn 쪽 타임아웃 설정과 무관하게
신호 자체가 제대로 전달되지 않는 문제라 이 앱에서 고칠 수 있는 범위를 벗어난다.

**사용자가 다음에 확인해줄 것**:
1. Ctrl-C를 눌렀을 때 터미널에 `Shutting down` / `Waiting for connections/
   background tasks to complete` 같은 메시지가 **찍히는지** -- 안 찍히면 신호
   자체가 uvicorn에 전달이 안 된 것(위 3번 플랫폼 문제), 찍히는데 그 다음에
   멈추면 이번 타임아웃 수정으로 5~8초 안에는 해결될 것으로 예상.
2. Ctrl-C를 **두 번** 눌러보기 -- uvicorn 자체가 "두 번째 Ctrl-C는 강제 종료"라고
   안내하는 메시지를 준다(`force_exit`); 이게 되는지 확인되면 신호 전달 자체는
   되고 있다는 뜻.
3. 급할 때 임시 대응: 브라우저 탭을 먼저 닫고 Ctrl-C, 또는 작업 관리자/`taskkill
   /F /IM python.exe`로 강제 종료.

### OTA Tester의 securityAccess sub-function을 [27 01]/[27 02] → [27 11]/[27 12]로
변경 (2026-08-10, 사용자 요청)

`backend/ota_tester_download_manager.py`의 RequestSeed/SendKey가
`build_security_access_request_seed()`/`build_security_access_send_key()`를
인자 없이 호출해 `uds_core.py`의 ISO 기본값(`0x01`/`0x02`)을 쓰고 있었다. 같은
핸드셰이크를 하는 `uds_download_manager.py`(CAN-SWDL)는 이미 XML의 `accessMode`
파라미터가 없을 때 `0x11`/`0x12`를 기본값으로 쓰고 있어(`access_mode_seed =
int(seed_step.params.get("accessMode", "0x11"), 16)`) 두 UDS 실행기가 서로 다른
레벨을 쓰고 있었던 상태 -- 사용자 요청대로 OTA Tester도 `0x11`/`0x12`로 맞췄다.

OTA Tester의 test-rule XML 스키마는 securityAccess를 requestSeed/sendKey
서브스텝으로 나누지 않아(`_execute_security_access()`의 기존 주석 참고) CAN-SWDL
같은 XML `accessMode` 파라미터 경로가 없다 -- 그래서 설정 가능한 값이 아니라
`ota_tester_download_manager.py` 상단에 고정 상수 `SECURITY_ACCESS_MODE_SEED =
0x11`/`SECURITY_ACCESS_MODE_KEY = 0x12`를 추가하고, RequestSeed/SendKey 실제
실행 경로 2곳 + 체크리스트 PDU 미리보기(`_preview_pdu`) 1곳까지 3곳 모두 이
상수를 명시적으로 넘기도록 수정했다.

검증: `tests/test_ota_tester_download_manager.py`의 가짜 ECU 핸들러(`sid == 0x27`
분기)와 `test_get_case_steps_security_access_preview`의 기대값(`"27 01"` →
`"27 11"`)을 새 서브펑션 값에 맞게 같이 갱신 -- 갱신하지 않았다면 가짜 ECU가
`last[1] == 0x01`을 더 이상 만나지 못해 RequestSeed도 SendKey 응답(`67 02`)으로
잘못 처리했을 것(실제로 처음엔 이 불일치로 실패했고, 두 곳을 맞춰 통과시켰다).
백엔드 전체 226개 테스트 통과.

### Ctrl-C 종료 안 됨, 4번째 확인 (2026-08-10, `--timeout-graceful-shutdown 5` 추가
후에도 여전히 재현) + 전역 Stop 후에도 경고 로그가 계속 나오는 문제

사용자가 두 가지를 다시 보고: ① 시뮬레이터의 전역 Stop을 눌러도 진단 경고
로그가 계속 나오고, 브라우저를 완전히 종료해야 멈춤. ② `--timeout-graceful-
shutdown 5`를 추가했는데도 Ctrl-C가 여전히 안 먹힘 -- 이번엔 브라우저를 강제로
닫는 순간 아래 예외까지 같이 찍혔다:

```
Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)
...
ConnectionResetError: [WinError 10054] An existing connection was forcibly closed by the remote host
```

**①은 버그가 아니라 현재 설계상 당연한 동작**: 전역 Stop(`/api/run/stop`)은
`tx_scheduler`를 pause하고 CAN 관련 처리를 멈출 뿐, "CAN-오디오 지연 확인"/
"오디오 신호 모니터" 위젯이 연 오디오 스트림은 별개로 계속 켜져 있다(위젯 자체의
Stop 버튼을 눌러야 꺼짐) -- 그래서 오디오 관련 경고(`cansim.audio`)는 전역 Stop과
무관하게 계속 나오는 게 맞다. 다만 `cansim.tx_scheduler`의 "tick delayed... 
periodic CAN sends are late" 경고는 **pause 중에도(`_paused=True`, 실제로는 아무
CAN도 안 보내는 중) 계속 찍히고 있어 오해를 부르는 것**이었다 -- 이건 진단 로그
자체의 결함이라 고쳤다(`tx_scheduler.py`: `paused` 여부를 확인한 뒤에만 이 경고를
찍도록 순서 변경, 락 대기/job 지연 경고는 그대로 둠 -- 그건 pause 여부와 무관하게
유효한 신호).

**②의 `ConnectionResetError`는 별개의, 대체로 무해한 Windows asyncio 노이즈**:
`_ProactorBasePipeTransport._call_connection_lost`가 이미 리셋된 소켓에
`shutdown(SHUT_RDWR)`을 호출하다 나는 예외로, Windows의 `ProactorEventLoop`가
피어(브라우저)로부터 RST로 끊긴 연결을 뒤늦게 정리할 때 흔히 나는 잘 알려진
로그다 -- asyncio가 콜백 예외를 개별적으로 잡아 로그만 남기고 이벤트 루프
자체는 계속 돌아가므로, 이 예외 자체가 프로세스를 멈추게 하는 원인은 아닐 가능성이
높다.

**`--timeout-graceful-shutdown`으로도 안 됐다는 것에서 확인한 것**: uvicorn의
"연결/작업 대기" 단계가 5초로 막혔더라도, 그 다음 우리 앱의 `lifespan` 종료
코드(`main.py`) 자체가 어디서 멈추는지는 여전히 안 보였다 -- 그 블록을 다시
훑어보니 `can_manager.disconnect()`(`bus.shutdown()`, 실제 하드웨어 드라이버
호출)와 `power_supply_service.disconnect()`(VISA `instrument.close()`)에
**타임아웃이 전혀 없었다** -- Windows에서 실제 PCAN/Vector/전원공급장치가
연결돼 있다면 이 두 곳도 무한 대기 후보다. (`AudioService.shutdown()`은 이미
3초로 막아뒀었다.)

**적용한 조치**:
1. **`main.py`에 `_run_bounded(fn, label, timeout_s=3.0)` 추가**: 별도 데몬
   스레드에서 `fn()`을 실행하고 최대 3초만 기다리는, `AudioService.shutdown()`과
   동일한 패턴의 범용 헬퍼. `can_manager.disconnect()`/`power_supply_service.
   disconnect()` 호출을 이걸로 감쌌다.
2. **`lifespan` 종료 블록 각 단계에 `shutdown_logger`(`cansim.shutdown`) 진행
   로그 추가**: "이 단계 시작" 로그 없이 어디서 멈췄는지 전혀 알 수 없었던
   문제를 고침 -- 다음에 멈추면 마지막으로 찍힌 줄이 정확히 어느 단계인지
   알려준다.
3. **`tx_scheduler.py`의 tick-delay 경고를 `paused`일 때는 찍지 않도록 수정**
   (위 ① 참고).

검증: 백엔드 전체 226개 테스트 통과(회귀 없음). 이 mac 환경(virtual 버스,
전원공급장치 미연결)에서 실제 앱을 띄우고 SIGINT를 보내 `cansim.shutdown` 로그가
`lifespan shutdown starting` → 각 단계 → `can_manager.disconnect() done in 1ms`
→ `power_supply_service.disconnect() done in 0ms` → `lifespan shutdown complete`
순서로 정확히 찍히고 0초 안에 정상 종료되는 것을 직접 확인했다 -- 다만 이 환경엔
실제 하드웨어가 없어 `_run_bounded`의 타임아웃 발동 자체(하드웨어 드라이버가
정말 멈췄을 때)는 여기서 재현하지 못했다(개념은 `AudioService.shutdown()`과
동일한 방식으로 이미 별도 검증됨).

**여전히 남은 불확실성 (사용자 확인 필요)**: 이번에도 Ctrl-C가 안 먹히면, 이제는
`cansim.shutdown` 로그의 **마지막 줄이 정확히 어디인지**가 결정적인 정보다 --
① `lifespan shutdown starting`조차 안 찍히면 uvicorn/OS 레벨(Windows
ProactorEventLoop의 SIGINT 전달 문제)이 원인이라는 뜻이고, ② 특정 단계 이름에서
멈추면 그 서비스의 disconnect/stop 자체가 원인. 사용자가 다음에 재현할 때
`cansim.shutdown`으로 시작하는 줄만 필터링해서 마지막 줄을 확인해 공유해줄 것을
권장한다. 또한 Ctrl-C를 누른 시점에 터미널에 `Shutting down` 메시지가 찍히는지,
두 번째 Ctrl-C(`force_exit`)는 되는지도 여전히 미확인 상태다.

### Ctrl-C 종료 안 됨, 5번째 확인 — 실제 원인 확정: Windows asyncio
`ProactorEventLoop`가 SIGINT 자체를 놓침 (2026-08-10, 사용자가 위 3가지 확인 질문에
답변)

사용자 답변: **① `cansim.shutdown` 로그 전혀 안 찍힘, ② `Shutting down` 메시지도
안 뜸, ③ Ctrl-C를 여러 번 눌러도 안 됨.** 이번엔 브라우저를 완전히 종료한
뒤에도 백엔드 터미널이 오디오 경고 로그를 마지막으로 찍은 채 그대로 멈춰 있었다.

**이걸로 원인이 확정됐다**: `Shutting down`은 uvicorn이 SIGINT 핸들러에서 가장
먼저 찍는 로그다(`uvicorn.server.Server.shutdown()`의 첫 줄). 이게 안 찍혔다는
것은 uvicorn/우리 앱 코드에 도달하기도 전에, **SIGINT 신호 자체가 프로세스에
전달·처리되지 않고 있다**는 뜻이다 -- 지금까지 추가한 `AudioService.shutdown()`,
`--timeout-graceful-shutdown`, `_run_bounded()`, `shutdown_logger` 전부 SIGINT가
일단 처리되기 시작한 *이후*에나 의미가 있는 조치라 애초에 발동할 기회가 없었다.

이건 잘 알려진 Windows 플랫폼 특성이다: Windows의 asyncio 기본 이벤트루프는
Python 3.8부터 `ProactorEventLoop`(IOCP 기반)인데, 이 루프가 계속 바쁜 상태로
돌고 있으면(이 앱은 WS 브로드캐스트 루프가 30ms마다 틱하고, 오디오 위젯이 열려
있으면 HTTP 폴링도 계속 들어옴) Ctrl-C의 `KeyboardInterrupt`를 체크할 기회
자체를 거의 못 얻어 신호가 통째로 씹히는 경우가 있다고 널리 보고돼 있다 -- 이번
증상(로그도 전혀 안 찍히고 여러 번 눌러도 안 됨)이 정확히 그 패턴과 일치한다.

**적용한 수정**: `backend/main.py` 최상단(다른 import보다 먼저, uvicorn이 이
모듈을 import한 뒤에야 이벤트루프를 만들므로 이 시점이면 충분히 이르다)에
`sys.platform == "win32"`일 때만 `asyncio.set_event_loop_policy(asyncio.
WindowsSelectorEventLoopPolicy())`를 설정했다. `select()` 기반의 Selector
루프는 Python의 신호 체크와 훨씬 안정적으로 맞물려 동작한다고 알려진, 바로 이
증상에 대한 표준적인 해결책이다. 이 앱은 `asyncio.create_subprocess_*`를 쓰는
곳이 없어(grep 확인) Selector 루프의 유일한 실질적 제약(비동기 서브프로세스
미지원)이 해당되지 않는다. macOS/Linux는 원래 Selector 계열 루프를 쓰므로 이
분기는 아무 영향이 없다(no-op).

검증: 백엔드 전체 226개 테스트 통과(macOS라 `win32` 분기 자체는 실행되지 않음,
문법과 나머지 동작에 회귀 없음만 확인 -- **이 macOS 환경에는 Windows가 없어
실제 효과는 검증하지 못했다**, 사용자가 Windows에서 확인해야 하는 부분). 만약
이 수정 이후에도 Ctrl-C가 안 먹히면, `WindowsSelectorEventLoopPolicy`로도
안 되는 것이므로 다음엔 프로세스를 어떻게 실행 중인지(터미널 종류, 콘솔
창 포커스 여부, `run_windows.bat`을 더블클릭했는지 vs 터미널에서 직접
실행했는지)를 확인해야 한다 -- 더블클릭으로 실행한 `.bat`은 새 콘솔 창을 열고
그 창에 포커스가 있어야 Ctrl-C가 그 프로세스로 전달된다.

### 재현 조건 추가 확인 (2026-08-10, 사용자 추가 테스트) — CAN 단독은 문제 없음,
Power/Audio 관련 동작에서만 재현

사용자가 세 가지를 확인: ① 서버만 켜둔 상태로 Ctrl-C → 됨. ② 브라우저로
시뮬레이터 연결만 한 상태로 Ctrl-C → 됨. ③ Start→Stop(+ 파워서플라이 연결/ACC_IGN
조작 반복) 후 Ctrl-C → **안 됨**. 이어서 별도로: **CAN 신호만 테스트하면 문제
없음, 파워서플라이를 연결해서 테스트하면 문제 발생, 오디오 관련 동작을 해도
문제 발생**이라고 확인.

단, ③ 테스트 시점엔 지난 항목의 `WindowsSelectorEventLoopPolicy` 수정이 아직
push되지 않아 사용자 환경에 반영되지 못한 상태였다(로그에 여전히
`_ProactorBasePipeTransport` 관련 예외가 찍힘 -- 그 수정이 적용됐다면 이 경로 자체를
안 씀). 즉 ③은 그 수정 이전 코드로 얻은 결과다.

**CAN 단독이 문제없다는 것이 주는 의미**: `tx_scheduler`(1ms 틱 스레드)와
`can_manager`(python-can Notifier 스레드)도 블로킹 드라이버 호출을 쓰지만 순수
파이썬 스레딩 + 일반적인 블로킹 I/O(호출 중 GIL을 정상적으로 반납)라 문제가 안
된다는 뜻이다. 반면 Power/Audio 둘 다 **네이티브 코드와의 경계가 다른 방식** --
`audio_service.py`의 sounddevice/PortAudio는 콜백 기반(PortAudio가 관리하는
네이티브 스레드가 매 오디오 버퍼마다 파이썬 콜백을 호출, `PyGILState_Ensure`류
진입/해제가 반복됨)이고, `power_supply_service.py`의 PyVISA도 드라이버(NI-VISA
등) 백엔드에 따라 유사하게 일반적인 "블로킹 후 GIL 반납" 패턴과 다르게 동작할
여지가 있다 -- 이게 Windows에서 SIGINT 체크 기회 자체를 놓치게 만드는 진짜
차이일 가능성이 높다(가설, 실기 확인 필요).

**추가 조치**: `power_supply_service.py`에도 `audio_service.py`와 동일한 패턴의
진단 로그를 추가했다 -- PyVISA `write()`/`query()`/`close()` 호출을
`_timed_visa_call()`로 감싸 100ms 넘게 걸리면(rate-limited) `cansim.power`
로거로 경고. `connect()`/`set_power()`/`_apply_battery()`(수동 전압 설정, On/Off
반복, 스윕이 전부 공유)의 모든 VISA 호출 지점에 적용. 기존 fake-instrument
기반 테스트(`_inst.writes` 리스트 검사)는 래핑 후에도 동일하게 `fn(*args)`로
그대로 호출되므로 변경 없이 통과.

검증: 백엔드 전체 226개 테스트 통과. `_timed_visa_call`에 150ms 걸리는 가짜
인스턴스를 직접 넣어 경고가 실제로 찍히는지 수동 확인.

**다음에 확인할 것**: 아직 push하지 않은 `WindowsSelectorEventLoopPolicy` 수정을
반영한 뒤, 이번에 사용자가 찾아낸 깔끔한 재현 절차(Power 연결+조작, 또는 오디오
위젯 사용) 그대로 다시 테스트 -- 그래도 안 되면 이번에 추가한 `cansim.power`
로그가 PyVISA 쪽이 실제로 얼마나 걸리는지 보여줄 것이고, `cansim.audio`/
`cansim.shutdown`과 함께 대조하면 어느 쪽이 실제 원인인지 좁혀질 것이다.

### 원인 확정: PyVISA(NI-VISA 등) 드라이버가 Windows 콘솔의 Ctrl+C 전달 자체를
막는 것으로 보임 -- 오디오 쪽은 `WindowsSelectorEventLoopPolicy`로 완전히
해결됨 (2026-08-10, 사용자가 `WindowsSelectorEventLoopPolicy` 적용 후 재테스트)

사용자가 지난 수정을 반영한 뒤 6가지를 테스트: **① 전원 연결 후 Ctrl-C 안 됨,
② 전원 연결 해제 후 Ctrl-C 안 됨(①②모두 로그가 전혀 없음), ③ 오디오 디바이스
연결 후 Ctrl-C 정상, ④ 오디오 연결+Start/Stop 후 Ctrl-C 정상, ⑤ 오디오
Start+CAN 신호 Start 후 Ctrl-C 정상, ⑥ 오디오 레코딩+CAN 동작 전후 Ctrl-C
정상.**

**오디오 쪽은 완전히 해결됐다** -- `WindowsSelectorEventLoopPolicy`(지난 항목)가
실제로 오디오/CAN 관련 모든 시나리오에서 Ctrl-C를 정상화시켰다. 이제 남은 건
**전원(PyVISA) 연결 관련 동작뿐**이고, 그 경우 `cansim.shutdown`도
`cansim.power`도 **전혀 로그가 안 찍힌다** -- `Shutting down`조차 안 뜬다는
이전 확인과 결합하면, SIGINT가 Python 프로세스에 도달하기 전에 뭔가가 그 자체를
막고 있다는 뜻이다.

**유력한 원인(가설, 이 환경엔 실제 VISA 장비가 없어 직접 검증 불가)**: PyVISA가
기본으로 로드하는 벤더 백엔드(NI-VISA 등 `visa32.dll`/`visa64.dll` 같은 네이티브
드라이버)가 초기화될 때 **자체적으로 Windows 콘솔 컨트롤 핸들러
(`SetConsoleCtrlHandler`)를 등록**해 Ctrl+C 콘솔 이벤트를 자기가 먼저 가로채고,
Python 런타임에 아예 전달하지 않는 경우가 산업용 계측 드라이버에서 실제로
보고된 바 있다 -- 이건 애플리케이션 코드(이 프로젝트의 파이썬 코드)가 아니라
**설치된 VISA 드라이버 자체의 동작**이라, `asyncio` 이벤트루프 정책이나 우리
쪽 신호 핸들링을 아무리 고쳐도 닿지 않는 영역이다. 오디오(sounddevice/
PortAudio)는 콘솔 핸들러를 건드리지 않는 종류의 콜백 방식이라 이 문제가 없었던
것으로 보인다.

**제안하는 대응 두 갈래(사용자 확인 후 진행)**:
1. **우회책(권장, 확실히 동작)**: Ctrl+C와 무관하게 HTTP 요청으로 서버를 종료시키는
   `POST /api/shutdown` 엔드포인트 + 프론트 종료 버튼을 추가한다. HTTP 요청 경로는
   콘솔 신호 경로를 전혀 타지 않으므로(전원 연결 상태에서도 `/api/power/disconnect`
   자체는 정상 응답했던 것처럼) 드라이버가 콘솔 Ctrl+C를 가로채더라도 영향받지
   않는다. 가능한 범위에서 기존 정리 로직(`audio_service.shutdown()` 등)을 최선껏
   실행한 뒤 `os.kill(os.getpid(), signal.SIGTERM)`(Windows에서는 `TerminateProcess`
   로 매핑됨, 콘솔 핸들러 경로를 타지 않는 별도의 강제 종료 경로)로 마무리.
2. **근본 원인 시도(불확실, 실기 확인 필요)**: `pyvisa.ResourceManager()` 대신
   `pyvisa.ResourceManager('@py')`(순수 파이썬 `pyvisa-py` 백엔드, 벤더 네이티브
   드라이버 DLL을 전혀 로드하지 않음)로 바꾸면 이 문제가 근본적으로 사라질 수
   있다 -- 단, 사용 중인 계측기의 연결 방식(USB/Serial/TCPIP는 보통 호환, GPIB는
   추가 설정 필요)에 따라 아예 연결이 안 될 수도 있어 실기 확인 없이는 적용할 수
   없다.

사용자에게 확인 요청 후 진행할 예정 -- 두 방법을 어떻게 조합할지(우회책만 먼저,
또는 백엔드 전환도 같이 시도)는 다음 턴에서 결정.

### 우회책 구현: `POST /api/shutdown` + "필수 설정 > 전원 연결" 아래 "서버 종료"
버튼 (2026-08-10, 사용자 선택 — 우회책만 진행, `pyvisa-py` 전환은 보류)

사용자가 "1번 우회책을 구현하고 서버종료 버튼은 필수설정 메뉴에 전원연결 아래에
추가"하는 쪽을 선택했다.

- **`backend/main.py`**: `POST /api/shutdown` 추가. 응답을 먼저 클라이언트에
  흘려보낼 시간(0.3초)을 준 뒤 백그라운드 스레드에서 `tx_scheduler.shutdown()` →
  `can_manager.disconnect()`/`power_supply_service.disconnect()`(둘 다
  `_run_bounded()`로 감싸 하드웨어 쪽이 멈춰도 최대 3초 후 포기) →
  `audio_service.shutdown()` 순으로 정리한 뒤 `os.kill(os.getpid(), signal.
  SIGTERM)`으로 프로세스를 끝낸다. Windows에서 `os.kill`은 공식 문서 기준
  "프로세스를 무조건 종료"(`TerminateProcess`로 처리됨)라 콘솔 컨트롤 핸들러
  경로를 전혀 타지 않는다 -- PyVISA/NI-VISA 드라이버가 콘솔의 Ctrl+C 전달
  자체를 가로채고 있다는 이번 조사 결과와 무관하게 항상 프로세스를 끝낼 수
  있다(이 요청 자체가 이미 정상 동작하는 일반 HTTP 경로이기 때문).
- **`frontend/src/api/client.ts`**: `shutdownServer()` 추가.
- **`frontend/src/App.tsx`**: "필수 설정" 패널의 "전원 연결" 섹션 바로 아래에
  "서버 종료" 섹션 추가(사용자 지시대로 위치). 클릭 시 `window.confirm()`으로
  한 번 확인 후 `api.shutdownServer()` 호출, 결과를 `notify()` 토스트로 표시.

검증: `backend/tests/test_api.py`에 신규 테스트 1개
(`test_shutdown_endpoint_cleans_up_then_kills_process`) -- `tx_scheduler.
shutdown`/`can_manager.disconnect`/`power_supply_service.disconnect`/
`audio_service.shutdown`/`os.kill`을 전부 `monkeypatch`로 교체해(모듈 전역
싱글턴을 실제로 건드리거나 테스트 프로세스를 실제로 죽이지 않도록) 응답이
즉시 오고, 그 뒤 배경 스레드에서 네 서비스 정리가 전부 호출되고 마지막에
`os.kill(getpid(), SIGTERM)`이 정확한 인자로 호출되는지 확인. 백엔드 전체
227개 테스트 통과(+1). 프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린.

브라우저에서 실제로 "서버 종료" 버튼을 눌러 확인 다이얼로그가 뜨고, 확인 시
서버가 실제로 종료되는지는(특히 파워서플라이 연결 상태에서 Ctrl+C가 안 먹히던
바로 그 상황에) 사용자가 다음에 확인해줄 것을 권장한다 -- 이 환경엔 실제
VISA 하드웨어가 없어 그 시나리오까지는 재현하지 못했다. `pyvisa-py` 백엔드
전환(근본 원인 시도)은 사용자가 보류를 선택해 이번엔 진행하지 않았다.

## 오디오 신호 모니터 + CAN-오디오 지연 확인 위젯: 파형차트/폴링 공용 컴포넌트로
추출 (2026-08-10, 사용자 요청 — 위젯 통합의 장단점을 먼저 리뷰한 뒤, "위젯 자체를
합치기보다 중복 로직만 공용 컴포넌트로" 쪼갠다는 절충안으로 진행)

사용자가 두 위젯을 합치고 싶다고 해서 먼저 장단점을 리뷰했다: 두 위젯이 캔버스
드로잉/줌팬/폴링을 거의 동일하게(각자 원래 GraphWidget.tsx의 내부 컴포넌트가
export되지 않아 재사용 못하고 복제했던 코드) 중복 구현하고 있어 합치면 유지보수
이득이 크지만, 실제 용도(녹음+장시간 모니터링 vs CAN 오버레이+지연 비교)가
달라서 위젯 자체를 합치면 컨트롤이 늘어난 무거운 위젯이 되고, 두 위젯 다 이미
여러 차례 실기 검증을 거쳐 안정화된 코드라 합치는 과정에서 회귀 위험이 있다고
판단해 "위젯은 그대로 두고 중복 로직만 공용 컴포넌트로 추출"을 추천했고, 사용자가
동의해 그 방향으로 진행했다.

### 설계

`frontend/src/widgets/AudioWaveformChart.tsx`(신규)에 두 위젯의 오디오 채널
미니차트(`AudioMonitorWidget.tsx`의 `WaveformChart`, `CanAudioLatencyWidget.tsx`의
`AudioChannelChart`)를 하나의 `AudioWaveformChart` 컴포넌트로 합쳤다. 두 원본이
실제로 다르게 동작하던 지점은 전부 prop으로 남겨 동작을 그대로 보존했다(코드를
나란히 대조해서 찾은 것들 -- 처음엔 놓쳤다가 재대조로 잡은 것도 있음):

| 차이 | AudioMonitorWidget | CanAudioLatencyWidget | 처리 방식 |
|---|---|---|---|
| X뷰 소유 | 차트가 각자 소유 | 부모와 형제 차트끼리 공유 | `shared?: {xViewRef, xVersion, notifyChange}` (없으면 standalone) |
| 휠 줌 시 스팬 제한 | 없음(무제한) | `[MIN,MAX]_X_WINDOW_MS`로 클램프 | `wheelZoomSpanClamp?: {min,max}` (옵션) |
| X축 눈금 기준점 | 스트림 시작 시각(값이 계속 증가) | 현재 뷰의 왼쪽 끝(항상 0부터) | `xTickMode: 'sinceStreamStart' \| 'sinceWindowLeft'` |
| X축 눈금 소수점 | 2자리(`1.23s`) | 1자리(`1.2s`) | `xTickDecimals: number` — 나란히 비교하다 뒤늦게 발견한 차이 |
| streamStartedAt 클램프 | 있음(스트림 시작 전으로 스크롤 불가) | 없음 | `streamStartedAtMs: number \| null` |
| "라이브 중 계속 스크롤" 틱 | 차트 자신의 200ms 인터벌 | 형제 CAN차트의 인터벌에 의존(자체 틱 없음) | `shared`가 없을 때만 내부 200ms 인터벌 실행 |
| 폴링 시작 게이팅 | `active`가 한 번이라도 true였어야 폴링(그 전엔 무기한 대기 안 함) | 항상 폴링(부모가 채널 없으면 마운트 자체를 안 함) | `pollEnabled` + 컴포넌트 내부 `hasEverEnabledRef` 래치 |

`niceTicks`/`orFallback`/`Geom` 타입도 공용 파일에서 export해 `CanAudioLatencyWidget.tsx`의
CAN 신호 차트(`CanSignalChart`, 다른 데이터소스라 이번엔 통합하지 않음)가 그대로
재사용하도록 바꿔 그쪽의 중복도 같이 줄였다.

`AudioMonitorWidget.tsx`는 이제 `AudioWaveformChart`를 standalone 모드(각 채널
차트가 독립 X뷰 + 독립 200ms 라이브 틱)로 쓰고, `CanAudioLatencyWidget.tsx`는
공유 모드(부모의 `sharedXRef`/`sharedVersion`을 CAN 차트와 함께 씀)로 쓴다.

### 검증

프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린(빌드 산출물 크기 410.59KB →
406.65KB로 줄어든 것으로 중복 제거가 실제 반영됐음을 확인). `AudioWaveformChart.tsx`가
컴포넌트 외에 헬퍼 함수/타입도 export해서 `react(only-export-components)` 린트
경고가 새로 2개 뜨는데, 이미 `UdsGlobalControls.tsx`/`controls.tsx`에도 동일한
패턴(과 동일한 경고)이 있어 이 코드베이스에서 이미 받아들여진 컨벤션이라 그대로
둠. 백엔드는 이번 변경과 무관(프론트 전용), 227개 테스트 그대로 통과.

이 환경엔 브라우저 자동화 도구가 없어(반복적으로 명시된 제약, 위 여러 항목 참고)
실제 브라우저에서 두 위젯의 Start/Stop, 줌/팬, 리셋, 30분 세그먼트 표시 등이 전과
동일하게 동작하는지는 코드 대조로만 확인했고 직접 클릭 테스트는 못 했다 --
**사용자가 다음 실사용 시 두 위젯 모두(특히 CanAudioLatencyWidget의 공유 X뷰
줌/팬이 CAN 차트와 함께 움직이는지, AudioMonitorWidget의 X축 눈금이 스트림
시작부터 계속 증가하는지) 확인해줄 것을 권장한다.**

## "CAN-오디오 지연 확인" 위젯: X축 시간 간격 측정용 difference cursor 추가
(2026-08-11, 사용자 요청)

`frontend/src/widgets/DiffCursor.ts`(신규)에 두 세로선 커서(A/B, 색상 구분)의
드로잉·hit-test(가장 가까운 커서 찾기)·Δt 포맷 로직을 뽑아, `CanSignalChart`(CAN
신호, 로컬)와 `AudioWaveformChart`(오디오 채널, 공용 -- `cursor` prop이
optional이라 `AudioMonitorWidget.tsx`는 전달하지 않아 영향 없음) 양쪽 차트가
같은 로직을 공유하게 했다.

- 상태(`cursorMode`/`cursorA`/`cursorB`)는 부모(`CanAudioLatencyWidget`)가
  소유하고 `sharedXRef`처럼 두 차트 종류 모두에 prop으로 내려준다 -- CAN
  차트와 오디오 차트가 X축을 공유하는 것과 같은 이유로, 커서도 공유해야 두
  차트에서 같은 위치에 그려진다.
- 툴바에 "커서 ON/OFF" 토글 버튼 + 켜져 있을 때만 `Δ` 델타 표시(`fmtDelta`,
  1초 미만은 ms, 이상은 초 3자리).
- 커서 모드가 켜져 있으면 차트 드래그가 팬(pan) 대신 가장 가까운 커서를
  옮긴다(휠 줌은 그대로 동작 -- 확대해서 미세 조정 가능). 처음 켤 때 두
  커서가 모두 안 잡혀 있으면 현재 뷰의 1/3, 2/3 지점에 기본 배치. 꺼도
  값은 유지(다시 켜면 그 자리에 그대로) -- 실수로 토글해도 애써 맞춘
  위치를 잃지 않게.
- 커서 이동은 공유 X뷰와 동일한 `notifyChange()`(→ `xVersion` bump) 경로로
  재드로잉을 트리거해, 팬/줌과 같은 기존 메커니즘을 그대로 재사용했다(새
  redraw 경로를 따로 안 만듦).

검증: 프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린(새 경고 없음, 기존
`only-export-components` 경고만 그대로). 백엔드는 무관, 227개 테스트 그대로
통과. 브라우저 자동화 도구가 없어 실제 드래그로 커서를 옮기고 Δt가 정확히
계산되는지는 코드 검토로만 확인했다 -- 사용자가 다음 실사용 시 확인해줄 것을
권장한다.

## ISO-TP 수신측: Block Size 기반 후속 Flow Control 프레임 미전송 버그 수정
(2026-08-11, 사용자 요청 — 최초 설명은 부정확했으나 재확인 후 정확한 버그 특정)

사용자가 처음엔 "8바이트 초과 전송에 flow control이 구현 안 됨"이라고 했으나,
`isotp_service.py`의 SEND 경로(그리고 RECEIVE 경로 모두)가 이미 완전히 구현·
테스트(기존 18개 통과)돼 있어 재확인을 요청했다. 정확한 설명: "전송 후 수신되는
데이터를 8바이트(= 1 CAN 프레임) 수신 후 FC message를 보내야 하는데 보내지
않고 있다."

**실제 버그(수신측)**: `receive()`가 First Frame을 받은 직후 Flow Control을
**딱 한 번만** 보내고, 그 뒤로는 `fc_block_size`가 몇이든 상관없이 다시는 보내지
않았다. ISO 15765-2 기준 Block Size가 0(무제한)이 아니면 송신측은 그 개수만큼
Consecutive Frame을 보낸 뒤 반드시 다음 FC를 기다려야 하는데, 이 코드는 최초
FC 이후 후속 FC를 전혀 안 보내니 그런 송신측은 첫 블록만 보내고 영원히 대기하게
된다 — 사용자가 정확히 이 증상을 보고했다. (송신측 `send()`는 원래도 FC의
block_size/STmin을 정상적으로 따르고 있었다 — 이건 반대쪽, 우리가 수신자 역할일
때의 문제였다.)

**수정**(`backend/isotp_service.py`): Consecutive Frame을 `fc_block_size`개
받을 때마다(그리고 아직 남은 데이터가 있을 때만) 같은 값으로 새 FC(CTS)를
보내도록 루프에 `block_count` 카운터를 추가했다. `fc_block_size == 0`(기본값,
"무제한")일 때는 조건이 전혀 발동하지 않아 기존 동작(최초 1회 FC만) 그대로다 --
100% 하위 호환.

검증: `tests/test_isotp_service.py`에 신규 테스트 2개 —
`test_receive_sends_follow_up_fc_after_each_block`(21바이트, block_size=1로
받아 CF마다 새 FC가 실제로 오는지 확인 — 고치기 전엔 두 번째 FC가 영원히 안 와서
이 테스트가 타임아웃으로 실패했을 것), `test_receive_bs_zero_sends_only_the_initial_fc`
(기본값 0에서는 여전히 후속 FC가 전혀 안 감을 확인, 기존 테스트들과의 하위
호환성 보증). 백엔드 전체 229개 테스트 통과.

**참고로 확인한 사실**: 이 앱의 실제 호출 지점(`uds_download_manager.py`,
`ota_tester_download_manager.py`)은 현재 `fc_block_size`를 지정하지 않아 항상
기본값 0(무제한)을 쓴다 — STmin은 이미 위젯에서 오버라이드 가능(`_get_fc_stmin()`/
`global_stmin_tx`)하지만 Block Size는 그런 오버라이드 경로가 아예 없다. 사용자의
실제 ECU가 (우리가 무제한이라고 알려줘도) 자체적으로 블록 단위 페이싱을 요구하는
것으로 보이는데, 그렇다면 이번 수정만으로는 아직 재현할 방법이 없다 — STmin과
동일한 패턴의 "Block Size 오버라이드" 위젯 컨트롤을 추가할지 사용자 확인 필요
(다음 턴에서 결정).

## 페이지 전환 시 위젯 값 초기화 버그 일괄 수정 (2026-08-11, 사용자 요청 —
"모든 위젯 다 고침" 선택)

App.tsx는 `activePage.widgets`만 렌더링/마운트한다(비활성 페이지의 위젯은 DOM에서
완전히 unmount됨) -- 그래서 위젯이 `config.options`(위젯마다 유지되는, 마운트
여부와 무관한 상태)가 아니라 컴포넌트 로컬 `useState`에만 값을 들고 있으면 다른
페이지로 갔다가 돌아왔을 때 그 값이 초기값으로 리셋된다. 조사 서브에이전트로
`frontend/src/widgets/` 전체를 훑어 실제로 이 버그가 있는 위젯을 특정했다
(`IsoTpBox.tsx`/`TxBox.tsx`/`CanAudioLatencyWidget.tsx` 등은 이미 `config.options`/
`config.binding`에 저장하는 기존 패턴을 쓰고 있어 문제없음으로 확인됨). 사용자가
"모든 위젯 다 고침(추천)"을 선택해 아래 6개 파일을 전부 수정했다.

- **`controls.tsx`**: `CheckboxWidget.checked`, `DropdownWidget.selected`,
  `SliderWidget`의 현재 슬라이더 위치(`config.options.currentValue`, 기존
  `default`와 분리 — 드래그 중 매 틱마다 config를 쓰지 않고 `flush()`(pointerup/
  키보드 스텝)에서만 저장해 퍼포먼스 문제 없음), `ManualValueWidget`의 입력
  텍스트(`config.options.currentText`) — 전부 `config.options`로 이전. 콘피그
  모달에서 `default`를 바꾸면 즉시 반영되던 기존 동작은 "첫 렌더(마운트)에는
  건드리지 않고, 이후 실제 변경에만 반응"하도록 `isFirstRender` ref로 유지.
- **`MultiControls.tsx`**: 같은 문제가 그리드 셀 단위로 있었다 —
  `MultiCell`(`types.ts`)에 `checked`/`selectedRaw`/`sliderCurrent`/`inputCurrent`
  필드를 추가해 `MultiCheckboxWidget`/`MultiDropdownWidget`/`MultiSliderWidget`/
  `MultiManualValueWidget`이 로컬 `Record<number,...>` 대신 이 필드들을
  `updateCell()`로 저장하도록 변경.
- **`PowerControlWidget.tsx`**: 원래 `config`를 완전히 버리고(`_: {config}`) 12개
  숫자 입력 필드(배터리 전압/전류, On/Off 반복 6개, 스윕 4개)를 전부 로컬
  `useState`로만 들고 있었다 — 전부 `config.options`로 이전.
- **`ReplayBox.tsx`**: `mode`(Pass/Stop), `selectedIds`(필터 메시지 선택)를
  `config.options`로 이전.
- **`OtaTesterWidget.tsx`**: `reqId`/`respId`(요청/응답 CAN ID)를
  `config.options`로 이전. `Props.config`의 로컬 축소 타입을 실제 `WidgetConfig`로
  교체(런타임에는 이미 그 타입의 객체가 전달되고 있었음).
- **`UdsSwdlWidget.tsx`**(가장 큰 작업): `selectedSteps`(슬롯별 체크된 스텝,
  `Set` 그대로는 `JSON.stringify`(레이아웃 저장 시 실제로 발생)에서 깨지므로
  `config.options`에는 배열로 저장하고 컴포넌트 안에서만 `Set`으로 변환),
  `slots[].paramOverrides`(스텝별 파라미터 오버라이드)를 `config.options`로
  이전. 부수적으로, 조사에서 같이 발견된 관련 문제 — 마운트 시 백엔드 상태를
  다시 가져오는 로직이 아예 없어서(⟳ 버튼을 눌러야만 갱신) 이미 백엔드에 XML/BIN
  이 로드돼 있어도 페이지 전환 후 빈 화면으로 보이는 문제 — 도 같이 고쳤다:
  마운트 시 `udsStatus()` + (procedure_loaded인 슬롯마다) `udsSteps()`를 자동
  호출.

검증: 프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린(새 경고 없음, 기존
`only-export-components` 경고만 그대로). 백엔드는 무관, 229개 테스트 그대로
통과. 브라우저 자동화 도구가 없어 실제로 페이지를 전환했다가 돌아왔을 때 값이
유지되는지는 코드 검토로만 확인했다 -- 사용자가 다음 실사용 시 6개 위젯 전부
확인해줄 것을 권장한다. GraphWidget/AudioMonitorWidget/CanAudioLatencyWidget의
차트 확대/축소 창 크기(`xWindowMs`)와 TxBox의 TX/RX 필터는 audit에서 "낮은
우선순위(설정이 아니라 뷰 상태에 더 가까움)"로 분류돼 이번엔 건드리지 않았다
-- 필요하면 별도 요청.

## ISO-TP 전송 위젯에 "응답 대기" 기능 추가 (2026-08-11, 사용자 요청 — FC가
전혀 안 나간다는 재보고를 조사한 결과 버그가 아니라 누락된 기능으로 확인)

사용자가 "시뮬레이터에서 FC를 한번도 안 보낸다"며 실제 버스 트레이스(`783: 03 22
F1 C1 ...` 요청 → `78B: 10 23 62 F1 C1 ...` ECU의 멀티프레임 응답)를 제시했다.
조사 결과 버그가 아니라: **"ISO-TP 전송" 위젯(`IsoTpBox.tsx`)이 순수 송신
전용**이라 `/api/isotp/send`만 호출하고 응답을 기다리거나 수신측 역할(FC 발송
포함)을 하는 로직이 전혀 없었다 — `isotp_service.receive()`는 UDS SWDL/OTA
Tester의 내부 시퀀스에서만 쓰이고, 수동 ISO-TP 전송 경로에는 아예 연결돼 있지
않았다. 그래서 사용자가 수동으로 UDS 요청을 보내고 ECU 응답을 관찰하려던
시나리오에서는 애초에 FC를 보낼 코드 자체가 실행되지 않았다.

**추가한 기능**: "응답 대기" 토글 — 켜면 전송 후 지정한 응답 ID에서 메시지를
기다리고, Single Frame이면 즉시, Multi Frame이면 `isotp_service.receive()`로
Flow Control을 보내며 Consecutive Frame을 수집해 재조립한 뒤 화면에 표시한다.

- **`backend/main.py`**: `IsoTpSendRequest`에 `resp_id`(옵션, 기본 None=기존
  송신 전용 동작 그대로), `resp_timeout_ms`(기본 2000), `resp_fc_block_size`(기본
  0), `resp_fc_stmin`(기본 0) 추가. `resp_id`가 있으면 `send()` 후 `receive()`를
  호출해 결과에 `response`(hex) 또는 `response_error`를 덧붙인다.
- **`frontend/src/api/client.ts`**: `isotpSend()` 옵션에 위 4개 필드 추가,
  반환 타입에 `response`/`response_error` 추가.
- **`frontend/src/widgets/IsoTpBox.tsx`**: "응답 대기" 체크박스 + 켜졌을 때만
  보이는 "응답 ID"/"응답 타임아웃"/"응답 FC Block Size"(바로 위에서 고친 수신측
  Block Size 버그를 이 위젯에서 직접 테스트할 수 있도록 노출) 필드, 응답 hex
  표시. 전부 `config.options`에 저장(기존 IsoTpBox 패턴 그대로 유지).

검증: `backend/tests/test_api.py`에 신규 테스트 3개 —
`test_isotp_send_with_response_wait_single_frame`(SF 응답 즉시 반환),
`test_isotp_send_with_response_wait_multi_frame_sends_flow_control`(정확히
사용자가 보고한 시나리오 재현: 21바이트 멀티프레임 응답에 실제로 FC가 나가고
재조립되는지 확인), `test_isotp_send_response_wait_timeout_reports_response_error`
(응답 없을 때 `response_error`로 우아하게 처리). 백엔드 전체 232개 테스트 통과.
프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린.

브라우저 자동화 도구가 없어 실제 하드웨어로 요청→응답→FC 왕복을 확인하지는
못했다 -- 사용자가 다음 실사용 시(원래 재현했던 것과 같은 시나리오,
`22 F1 C1` 같은 ReadDataByIdentifier 요청) 확인해줄 것을 권장한다.

## 페이지 탭 드래그 재배치 (2026-08-12, 사용자 요청)

페이지 탭 순서를 바꿀 UI가 전혀 없었다(추가/이름변경/삭제만 가능, 순서는 생성
순서로 고정) -- 새 의존성 없이 표준 HTML5 Drag and Drop API로 드래그 재배치를
추가했다.

- **`frontend/src/App.tsx`**: `reorderPages(fromId, toId)` 추가 — 드래그한
  페이지를 배열에서 뺀 뒤, **뺀 다음 기준으로** 목표 페이지의 인덱스를 다시
  찾아 그 바로 앞에 끼워 넣는다(제거 전 인덱스를 그대로 쓰면 오른쪽으로 끌 때
  목표보다 한 칸 더 밀려 들어가는 문제가 있어, 드래그 방향과 무관하게 항상
  "놓은 자리 바로 앞"에 오도록 통일). `PageTabs`에 `draggable`/`onDragStart`/
  `onDragOver`/`onDrop`/`onDragEnd` 핸들러 추가 — **편집 모드에서만** 드래그
  가능(이름변경/삭제와 동일한 게이팅, 평상시 탭 클릭이 드래그로 오인되는 일이
  없게 함). 드래그 중인 대상 위에 올라가면 파란 테두리로 표시.
- **`frontend/src/styles.css`**: `.page-tab-drag-over` 추가.

검증: 프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린(새 경고 없음). 백엔드는
무관. 브라우저 자동화 도구가 없어 실제 드래그 동작은 코드 검토로만 확인했다 --
사용자가 다음 실사용 시(편집 모드에서 탭을 드래그해 순서가 바뀌는지, 저장 후
다시 불러왔을 때도 그 순서가 유지되는지) 확인해줄 것을 권장한다.

## UDS suppress-bit / NRC 0x78 / CAN 표시창 버그 3건 수정 (2026-08-12, 사용자
요청 — 조사 서브에이전트로 원인 특정 후 수정)

사용자가 세 가지를 보고: ① suppressPosRspMsgIndicationBit(구독함수 바이트의
0x80)이 설정된 요청은 타임아웃이 정상인데 실패로 처리됨, ② `783: 03 22 F1 B1
...` → `78b: 03 7f 22 78 ...`(NRC 0x78, Pending) → `78b: 07 62 F1 B1 ...`(실제
응답) 시나리오에서 실제 응답이 왔는데도 "응답 프레임을 기다리다 시간
초과되었습니다"로 실패, ③ CAN 메시지 표시창을 "스크롤" 모드로 두면 일정 시간
지나면 메시지가 지워짐. 조사 서브에이전트로 `backend/` 전체를 훑어 세 가지 모두
실제 버그로 확인했다.

### ①+② 원인 (uds_download_manager.py / ota_tester_download_manager.py /
isotp_service.py)

- **① Suppress bit 미처리**: 어느 호출 지점도 요청의 subfunction 바이트
  0x80 비트를 확인하지 않고, receive() 타임아웃을 무조건 실패로 처리했다.
  `ota_tester_download_manager.py`의 `_uds_request_with_retry`는 더 심해서
  send/receive에 try/except가 전혀 없어 타임아웃이 `isotp_service.IsoTpError`
  그대로 새어나가고, `_execute_step`은 `except UdsError`만 잡아서
  `confirmPositiveResponse="no"`로도 못 막고 **케이스 전체가 ERROR로 중단**됐다
  (실제로 이 프로젝트의 참조 XML에 `diagnosticSessionType="0x81"
  confirmPositiveResponse="no"`가 이미 있어 바로 재현 가능한 상태였다).
- **② NRC 0x78 재시도 로직 자체는 이미 맞게 구현돼 있었다**(P2*Server_max로
  타임아웃 연장, 재전송 안 함) — 진짜 원인은 그보다 미묘했다:
  `isotp_service.receive()`가 **호출마다** 새 `can.BufferedReader`를
  `add_listener`→`remove_listener`로 열고 닫는데, 0x78 수신 후 재시도(로그+
  `time.sleep(retry_delay_s)`)와 그다음 `receive()` 호출 사이에 리스너가 전혀
  등록되지 않은 공백이 생긴다. python-can의 `Notifier`는 그 순간 등록된
  리스너에게만 프레임을 전달하므로, 하필 그 공백에 도착한 진짜 최종 응답은
  이 수신 경로 입장에서는 그냥 사라지고, 다음 시도는 이미 지나가 버린 프레임을
  기다리며 타임아웃난다 — 사용자가 보고한 바로 그 증상.

**수정**:
- `uds_core.py`: `expects_no_response(request)`(subfunction 기반 서비스
  {0x10,0x11,0x28,0x31,0x3E,0x85}만 대상, 0x22 같은 비-subfunction 서비스의
  DID 상위 바이트가 우연히 0x80을 가진 경우를 오검출하지 않도록 서비스 목록으로
  제한) + `suppressed_response_result(request)`(성공 응답과 동일한 shape의
  synthetic 결과) 추가.
- `isotp_service.py`의 `receive()`에 `reader: Optional[can.BufferedReader] =
  None` 파라미터 추가 — 넘겨주면 그 리스너를 그대로 쓰고 자기가 안 만들고 안
  지운다(호출자가 수명 관리). 안 넘기면(기존 모든 호출자) 원래 동작 그대로.
- `uds_download_manager.py`/`ota_tester_download_manager.py`의
  `_uds_request`/`_uds_request_with_retry`: (a) receive 실패 시
  `expects_no_response()`이고 타임아웃 메시지("시간 초과")면 `UdsError`
  대신 `suppressed_response_result()`를 반환, (b) 재시도 루프 전체에 걸쳐
  **하나의 리스너**를 만들어 모든 `_isotp_receive()` 호출에 `reader=`로
  넘기고 끝에서 한 번만 정리 — 재시도 사이 공백을 없앰. `ota_tester_download_
  manager.py`의 `_uds_request_with_retry`엔 send/receive try/except도 새로
  추가해 어떤 실패든 `UdsError`로 감싸지도록 함(이게 없어서 이전엔
  `confirmPositiveResponse="no"`가 타임아웃을 못 막았다).
- `uds_download_manager.py`의 ECUReset 호출 지점(`_uds_request` +
  `except UdsError: pass`)은 건드리지 않았다 — 이미 임의 실패를 관용하는
  의도적 설계로 보이고(리셋 명령은 ECU가 응답하기 전에 재부팅될 수 있음),
  이번 fix로 suppress-bit 타임아웃은 이제 그 블록 없이도 정상 성공 처리된다.

검증: `tests/test_isotp_service.py`(20개, 기존 회귀 확인), `tests/
test_uds_download_manager.py`(4개 신규: reader 재사용 확인, suppress-bit
타임아웃 성공 2가지 경로, 실제 negative response는 여전히 실패, non-suppress
타임아웃은 여전히 실패), `tests/test_ota_tester_download_manager.py`(3개
신규: 동일 항목). 기존 두 테스트 파일의 fake CAN 매니저(`type("FakeCan", (),
{"notifier": object()})()`)가 이제 `add_listener`/`remove_listener`를
호출받게 돼 `_FakeNotifier`(no-op) 클래스로 전체 교체(27곳). 백엔드 전체
240개 테스트 통과.

### ③ 원인 (canStore.ts / displays.tsx)

`canStore.trace`(스크롤 모드가 읽는 원시 프레임 배열)가 들어오는 프레임 배치마다
**최근 60초 초과 + 3만개 초과분을 무조건 삭제**한다(사용자가 지금 무엇을 보고
있는지와 무관하게) -- "고정" 모드가 읽는 `canStore.frames`(ID별 Map)는 안 지워짐,
"스크롤" 모드만 해당. 게다가 살아있는(일시중지 안 한) 스크롤 뷰는 새 프레임이
올 때마다 맨 아래로 강제 스크롤하는 효과가 있어, 일시중지 없이 위로 스크롤해도
다음 프레임에 곧바로 바닥으로 끌려 내려간다.

기존에 "일시중지" 버튼이 이미 이 문제의 해법으로 존재했다(누르는 순간 스냅샷을
고정). 사용자가 "스크롤 중엔 자동으로 캐치/일시중지"를 선택해, 수동 버튼 없이도
위로 스크롤하는 순간 자동으로 얼리는 동작을 추가했다.

- `frontend/src/widgets/displays.tsx`의 `TraceView`: live 모드의 "새 프레임마다
  바닥으로" 효과를 "사용자가 이미 바닥에 있을 때만 바닥으로 붙임, 아니면(위로
  스크롤한 상태) 강제로 끌어내리지 않고 `onScrollAway` 콜백으로 부모에게
  넘김"으로 변경 — 스크롤 이벤트가 아니라 **다음 프레임이 도착하는 시점**에
  현재 스크롤 위치를 확인하는 방식이라, 이 effect 자신의 프로그래매틱 스크롤과
  경쟁하는 레이스가 없다(감지가 스크롤 즉시가 아니라 다음 프레임 도착 시점이라는
  트레이드오프는 있음 -- 보통 수신 주기 내라 체감상 즉시에 가까움). 얼려진(비
  live) 상태에서 바닥으로 다시 스크롤하면 `onScrollToBottom` 콜백.
- `MessageDisplayCore`: `autoFrozen`(새 state, `paused`와 별개)를 추가 —
  `onScrollAway`가 오면 스냅샷을 찍고 `autoFrozen=true`(수동 "일시중지"와 동일한
  스냅샷 로직 재사용), `onScrollToBottom`이 오면 `autoFrozen`만 해제(수동
  `paused`는 버튼으로만 해제 -- 자동으로 멈춘 것만 자동으로 풀리게, 사용자가
  의도적으로 누른 일시중지는 스크롤만으로 안 풀리게). 툴바 힌트에 "자동
  정지(스크롤 중)" 상태 추가.

검증: 프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린(새 경고 없음). 백엔드
무관. 브라우저 자동화 도구가 없어 실제 스크롤 동작(위로 스크롤 시 자동 정지,
바닥으로 스크롤 시 자동 재개)은 코드 검토로만 확인했다 -- 사용자가 다음
실사용 시 확인해줄 것을 권장한다.

---

## 2026-08-12 롤백 및 4건 수정

### ① CAN 메시지 표시창 "자동 정지(스크롤 중)" 기능 롤백

사용자가 위 2026-08-11(추정) 자동 정지 기능을 원래의 단순 "항상 바닥으로
스크롤" 동작으로 되돌리라고 요청 — 60초 경과 시 메시지가 사라지는 현상은
알려진 트레이드오프로 감수하겠다고 명시.

`frontend/src/widgets/displays.tsx`를 해당 기능이 추가되기 전 커밋
(`d754af7`)의 내용으로 전체 되돌림 (`git checkout d754af7 --
frontend/src/widgets/displays.tsx`). `git diff d754af7 c5b8472 --
frontend/src/widgets/displays.tsx`로 이 기능의 diff가 이 파일 하나에만
고립되어 있음을 먼저 확인 — 같은 커밋에 같이 들어있던 페이지 탭 드래그 재정렬
(App.tsx/styles.css)이나 UDS suppress-bit/NRC 0x78 백엔드 수정과는 겹치지
않으므로, 이 파일만 되돌려도 나머지 작업은 전혀 영향받지 않는다.

`autoFrozen`/`handleScrollAway`/`handleScrollToBottom`/`onScrollAway`/
`onScrollToBottom`/`AT_BOTTOM_TOLERANCE_PX` 전부 제거되고, `TraceView`/
`MessageDisplayCore`는 원래의 단순 "항상 바닥으로 스크롤"(일시중지 버튼으로만
멈춤) 동작으로 복귀.

검증: `tsc -b --noEmit`/`vite build`/`oxlint src` 클린.

### ② NRC 0x78 (ResponsePending) 재시도 횟수 제한 제거

증상: 실제 ECU가 pending을 여러 번 보내는 상황에서 기본값 3회를 넘기면
타임아웃 실패로 처리됐다. 사용자 요청: 횟수 제한 없이, ECU가 pending을 보내는
동안은 계속 기다린다 (ISO 14229-1상 pending 응답 자체의 횟수 제한은 없고,
개별 대기 시간만 P2*Server_max로 한정됨).

`uds_download_manager.py`/`ota_tester_download_manager.py`의
`_uds_request_with_retry`:
- `max_retries: int = 3` → `max_retries: Optional[int] = None`(기본값
  "무제한"). 내부 루프를 `for attempt in range(max_retries + 1)`에서
  `while True` + `max_retries is None or attempt < max_retries` 조건으로
  변경.
- 두 파일의 `RoutineControl`/`RequestDownload`/`TransferData`/
  `RequestTransferExit` 호출부에 있던 명시적 `max_retries=10` 오버라이드도
  제거 — pending 자체에 인위적 상한을 두지 않는 것이 사용자 요청의 취지이므로
  기본(무제한)을 따르게 함.
- `test_uds_request_with_retry_raises_after_max_consecutive_pending`
  테스트는 `max_retries=3`을 명시적으로 넘겨서 호출하므로 그대로 유효(명시적
  오버라이드는 계속 지원).

검증: 백엔드 전체 240개 테스트 통과 (`.venv/bin/python -m pytest -q`).

### ③ ASK(SeedKey) 파일 선택 시 자동 업로드

"ask 파일"은 `backend/seedkey_client.py`가 감싸는 HKMC Advanced SeedKey DLL을
가리킴(벤더 헤더 `HKMC_ASK_Client.h`, UI 라벨도 "ASK 선택"). 기존에는 파일
선택 후 별도로 "SeedKey DLL 업로드" 버튼을 눌러야 실제 업로드가 실행됐다.

`frontend/src/widgets/UdsGlobalControls.tsx`: `uploadSeedKey`가 선택적
`file?: File` 파라미터를 받도록 변경(안 넘기면 기존처럼 state의
`seedKeyFile` 사용). 파일 `<input>`의 `onChange`에서 파일을 고르는 즉시
`uploadSeedKey(f)`를 직접 호출 — 버튼 클릭 없이 자동 업로드. 기존 "SeedKey
DLL 업로드" 버튼은 그대로 남겨 재업로드 등 수동 트리거도 계속 가능
(`onClick={() => uploadSeedKey()}`로 인자 없이 호출하도록 수정 — 그냥
`onClick={uploadSeedKey}`로 두면 클릭 이벤트 객체가 `file` 인자로 들어가
타입 에러가 나는 문제를 미리 수정).

검증: `tsc -b --noEmit`/`vite build`/`oxlint src` 클린.

### ④ OTA Tester 위젯이 진행 로그 갱신 시 전체 페이지 스크롤을 가져가는 문제 수정

원인: `frontend/src/widgets/OtaTesterWidget.tsx`가 이벤트 로그 갱신마다
맨 아래 sentinel(`eventsEndRef`)에 대해 `scrollIntoView({ behavior:
'smooth' })`를 호출했는데, 이 로그 영역 자체는 이미 `overflow: 'auto'`인
스크롤 컨테이너지만 `scrollIntoView`는 대상이 뷰포트에 보이도록 필요한 모든
조상(문서/페이지 스크롤 포함)까지 같이 스크롤시킨다 — 그래서 OTA 진행 중
다른 위젯을 보고 있어도 로그가 갱신될 때마다 페이지 전체가 이 위젯으로
끌려왔다.

수정: sentinel div와 `eventsEndRef`를 제거하고, 로그 컨테이너 자체에
`eventsContainerRef`를 달아 `el.scrollTop = el.scrollHeight`로 직접
설정 — 이 위젯 내부 스크롤만 움직이고 페이지/문서 스크롤은 전혀 건드리지
않는다.

검증: `tsc -b --noEmit`/`vite build`/`oxlint src` 클린. 백엔드 무관.

---

## 2026-08-12 (2차) Req/Rsp ID 자동설정 버그, STmin 미적용, TransferData 응답 누락 수정

### ① OTA Tester Req ID/Rsp ID가 vehicleInfo.json에서 자동설정되지 않음

증상 확인 결과 이 기능(폴더 선택 시 `VehicleInfo/vehicleInfo.json`의
`communicationInfo.settings.requestID/responseID`를 읽어 Req/Resp ID를 자동
채움)은 이미 구현되어 있었으나(commit 72c3ba2), 실제로는 두 값 중 하나만
반영되는 버그가 있었다.

**원인**: `OtaTesterWidget.tsx`의 `setReqId(v)`/`setRespId(v)`가 각각
`updateWidget({ ...config, options: { ...config.options, reqId: v } })`
형태로, 렌더 시점에 캡처된 `config`(prop) 스냅샷을 기준으로 새 위젯
객체를 만들어 전체 교체(`App.tsx`의 `updateWidget`은 `p.widgets.map((x) =>
x.id === cfg.id ? cfg : x)`로 병합이 아니라 통째로 교체)한다. 폴더 선택
핸들러가 `setReqId(...)`를 호출한 뒤 곧바로(같은 동기 실행 안에서, 리렌더
전) `setRespId(...)`를 호출하면, 두 번째 호출도 첫 번째 호출 *이전의* 오래된
`config.options`를 기준으로 새 옵션을 만들어 위젯을 덮어써버려 — 결과적으로
마지막에 호출된 쪽(respId)만 남고 먼저 호출된 reqId 갱신은 사라진다.

**수정**: `reqId`/`respId`를 한 번의 `updateWidget` 호출로 원자적으로 함께
설정하는 `setReqRespId(req, resp)`를 추가하고, vehicleInfo.json 파싱 결과
반영 부분을 이걸로 교체. 사용자가 입력 필드에서 직접 하나씩 고칠 때 쓰는
개별 `setReqId`/`setRespId`는 그대로 유지(한 번에 하나만 바뀌므로 문제
없음).

검증: `tsc -b --noEmit`/`vite build`/`oxlint src` 클린.

### ② OTA Tester STmin 설정이 적용되지 않음 (XML의 localSTMinTx 무시됨)

`uds_xml_parser.parse_test_rule_xml`이 각 스텝의 `localSTMinTx` XML
속성(스텝별 Flow Control STmin_Tx 오버라이드)을 파싱해 `local_stmin_tx`
필드에 담아 두긴 했지만, `ota_tester_download_manager.py`의 실행 경로
(`_run_case_steps` → `_execute_step` → ... → `_get_fc_stmin()`) 어디에서도
이 값을 실제로 읽지 않아 완전히 죽은 코드였다 — 스텝이 자기 XML에
`localSTMinTx="0x0A"`를 명시해도 항상 전역 STmin(UdsGlobalControls) 또는
기본값 0x0A만 적용됐다.

게다가 파싱 자체에도 버그가 있었다: 실제 GITAuto 내보내기 XML은 거의 모든
스텝에 `localSTMinTx=""`(빈 문자열) 속성을 항상 갖고 있는데, 기존 파싱
조건(`"localSTMinTx" in step_info.params`)은 속성이 "존재하기만 하면"
참이 되어 `_int_hex("")`(=0)을 실제 오버라이드로 취급했다. 즉 이 죽은
코드를 그대로 연결했다면 거의 모든 스텝에서 STmin=0(딜레이 없음)이
강제되어 전역 설정을 오히려 항상 무시하는 정반대의 회귀가 발생했을
것이다.

**수정**:
- `uds_xml_parser.py`: `localSTMinTx` 속성값이 빈 문자열이면 `None`(오버라이드
  없음)으로, 실제 값이 있을 때만 `_int_hex()`로 파싱하도록 변경.
- `ota_tester_download_manager.py`: `_local_stmin_override`(스텝 실행 중에만
  설정되는 인스턴스 필드) 추가. `_get_fc_stmin()`의 우선순위를 "스텝 자신의
  localSTMinTx > 공유 전역 STmin(UdsGlobalControls) > 기본값 0x0A"로 변경.
  `_run_case_steps`에서 각 스텝 실행 직전에 `_local_stmin_override`를 그
  스텝의 `local_stmin_tx`로 설정하고, `finally`로 스텝이 끝나면 `None`으로
  복원(다음 스텝에 새어나가지 않게).

검증: 신규 테스트 2건(`test_local_stmin_tx_empty_attribute_parses_to_none`,
`test_local_stmin_tx_override_applied_during_step_and_cleared_after`) 포함
백엔드 전체 242개 테스트 통과.

### ③ TransferData 응답을 매우 빠르게(0.004688초) 받았는데 놓치고 타임아웃 처리됨

**원인**: `_uds_request()`/`_uds_request_with_retry()`(uds_download_manager.py,
ota_tester_download_manager.py)와 `/api/isotp/send`(main.py, "응답 대기" 모드)
모두 지금까지 "먼저 `send()` 호출 → 끝나면 그제서야 receive용 리스너
생성/등록"순서였다. python-can의 `Notifier`는 프레임이 도착하는 *그 순간*
등록되어 있는 리스너에게만 전달하므로, `send()`가 반환된 시점과 receive용
`can.BufferedReader()`를 실제로 만들어 `add_listener`하는 시점 사이의
(로깅·예외처리 등 파이썬 레벨 오버헤드로 생기는) 짧은 공백에 ECU 응답이
도착하면 그냥 사라진다. 이번에 실제로 관찰된 응답 지연 0.004688초(4.688ms)는
이 공백보다 충분히 빠를 수 있는 값이었다.

이전에 고친 NRC 0x78 재시도 사이 공백(리스너 재사용)과 같은 계열의 버그이지만,
이번엔 "송신 직후 ~ 수신 리스너 등록 전" 구간이라는 점이 다르다.

**수정**: `isotp_service.py`의 `send()`에 `receive()`와 동일한 패턴으로
`reader: Optional[can.BufferedReader] = None` 파라미터 추가(넘기면 그
리스너를 그대로 쓰고 자기가 안 만들고 안 지움 — 멀티프레임 송신 중 Flow
Control 대기에 사용, Single Frame 송신에는 영향 없음). 호출부 3곳
(`uds_download_manager._uds_request`/`_uds_request_with_retry`,
`ota_tester_download_manager._uds_request_with_retry`, `main.py`의
`/api/isotp/send` "응답 대기" 분기)을 모두 "리스너를 먼저 만들어
등록 → `reader=`를 넘겨 `send()` 호출 → 같은 `reader`로 `receive()` 호출
→ 끝나면 한 번만 해제" 순서로 재구성 — 송신 시작부터 수신 완료까지 리스너가
공백 없이 계속 등록되어 있다.

검증: 신규 테스트 3건(`test_uds_request_registers_listener_before_sending`,
`test_uds_request_with_retry_registers_listener_before_sending` ×2, 각
uds_download_manager/ota_tester_download_manager) 포함 백엔드 전체 245개
테스트 통과. `main.py`는 syntax 확인 + 기존 `test_api.py`의 `/api/isotp/send`
resp_id 테스트로 회귀 확인.

### ④ (보류) CAN/전원 연결 상태 저장 안 함 요구사항 — 조사 결과 재현 불가

`App.tsx`의 `CanConfig`(iface/channel/bitrate/fd), `PowerControlWidget.tsx`,
`canStore.ts`의 모든 `localStorage` 키를 확인한 결과, CAN/전원의
연결/해제 상태는 현재 어디에도 저장되지 않고 있었다 -- 둘 다 항상
`canStore.status`(백엔드 실시간 조회)로만 표시되며, 레이아웃 저장 JSON에도
연결 플래그가 없다. 사용자에게 재현 경로(레이아웃 불러오기 후? 프론트만
새로고침? 백엔드까지 재시작?)를 확인 요청, 답변 대기 중.

## CAN-SWDL 진단 세션전환/보안 액세스 디코딩 표시 수정 (2026-08-13, 사용자 요청)

사용자가 `UdsSwdlWidget.tsx`(CAN-SWDL 위젯)의 XML 파라미터 표시/기본값에 대해
4가지를 요청:

1. `diagnosticSessionControl` 스텝에 `diagnosticSessionType`과
   `background_diagnosticSessionType`이 둘 다 있을 때, 기본 선택값이 XML
   속성 순서(예: `background_diagnosticSessionType="0x03"
   diagnosticSessionType="0x02"`처럼 background가 먼저 나오면 0x03이 기본
   선택됨)를 따라가고 있었다. 즉시 세션(`diagnosticSessionType`)이 항상
   기본값이어야 함 (예시 기준 0x02).
2. `securityAccess` 스텝은 `algorithm`만 표시되고, 실제 `[27 11]` 요청을
   결정하는 `requestSeed` 하위 스텝의 `accessMode`(예: 0x11)는 위젯에 전혀
   보이지 않았다. 표시 추가 요청.
3. `diagnosticSessionControl`의 `skipTask` 속성이 위젯에 입력창으로
   노출되고 있었다 (백엔드에서는 사용되지 않는 값). 삭제 요청.
4. `securityAccess`의 `skipTask`도 동일하게 삭제, `accessMode` 표시 추가.

**조사 결과**: 백엔드(`uds_download_manager.py`
`_execute_security_access()`)는 이미 XML의 `requestSeed`/`sendKey` 하위
스텝 `accessMode`를 그대로 읽어 `[0x27, accessMode]`로 전송하고 있어
(`access_mode_seed = int(seed_step.params.get("accessMode", "0x11"), 16)`,
`access_mode_key = ... "0x12"`) 실제 전송 로직은 이미 요구사항대로
동작 중이었다. 문제는 프론트엔드 표시 쪽: (a) 세션타입 기본 선택이 XML
속성 순서에 의존, (b) `accessMode`가 UI에 아예 표시되지 않음, (c) 쓸모없는
`skipTask`가 편집 가능한 입력창으로 노출.

**수정** (`frontend/src/widgets/UdsSwdlWidget.tsx`만 변경, 백엔드 변경 없음):
- `SESSION_TYPE_ORDER = ['diagnosticSessionType', 'background_diagnosticSessionType']`
  상수를 추가하고, 세션타입 후보를 모으는 3곳(업로드 시 자동 선택, 폴더
  자동로드 시 자동 선택, 드롭다운 옵션 목록)을 모두 이 고정 순서로
  필터링하도록 교체 -- `diagnosticSessionType`이 항상 우선 선택/표시된다.
- `securityAccess` 스텝에 `step.sub_steps`에서 `requestSeed`를 찾아
  `accessMode`를 읽기 전용으로 표시하는 블록 추가 (편집 가능한 입력은 아님
  -- 백엔드가 sub-step 파라미터 오버라이드 경로를 갖고 있지 않아, 편집
  가능하게 만들면 실제로는 반영되지 않는 필드가 되어 오히려 오해를 부름).
- 파라미터 렌더링 루프에서 `pk === 'skipTask'`인 경우 `return null`로
  완전히 숨김 (모든 서비스 공통 -- `diagnosticSessionControl`,
  `securityAccess` 둘 다 적용됨). 기존에 `skipTask`와 함께 묶여 있던
  `confirmPositiveResponse` 분기는 삭제해도 동작 변화 없음 (일반 파라미터
  렌더링 분기와 JSX가 완전히 동일했던 죽은 특수 케이스였음).

검증: `tsc -b --noEmit`/`vite build`/`oxlint src/widgets/UdsSwdlWidget.tsx`
클린. 백엔드는 변경하지 않았으므로 기존 테스트 스위트에 영향 없음(별도
재실행 안 함). 브라우저 자동화 도구가 없어 실제 XML 업로드 후 화면
표시까지는 코드 검토로만 확인 -- 사용자가 실제 XML로 CAN-SWDL 위젯을 열어
(1) 세션타입 기본값, (2) `accessMode` 표시, (3) `skipTask` 미표시를
확인해줄 것을 권장한다.

## "CAN-오디오 지연 확인" 위젯: 그래프 영역 휠 확대/축소가 X/Y 동시에 적용되던 버그 수정 (2026-08-13, 사용자 요청)

사용자가 그래프 플롯 영역 안에서 휠로 확대/축소하면 X축과 Y축이 동시에
바뀌고, 왼쪽 Y축 눈금 영역(플롯 바깥쪽 세로 스트립)에서 휠을 돌리면 Y축만
바뀌는 게 이미 잘 동작한다고 보고 (전자만 수정 요청, 후자는 그대로 유지).

**원인**: `onWheel` 핸들러가 "플롯 영역 안(`inX && inY`)"을 X 줌 조건과 Y 줌
조건 양쪽에 모두 걸어뒀다 (`zoomX = overXAxisStrip || (inX && inY)`,
`zoomY = overYAxisStrip || (inX && inY)`). 플롯 영역 밖 X축 눈금 스트립
(`overXAxisStrip`)/Y축 눈금 스트립(`overYAxisStrip`)에서는 각각 해당 축만
바뀌지만, 플롯 영역 내부에서는 두 조건이 동시에 참이 되어 X/Y가 함께
줌됐다. 이 로직이 두 곳에 거의 동일하게 복제돼 있었다: 오디오 채널 차트
공용 컴포넌트(`AudioWaveformChart.tsx`, `AudioMonitorWidget`과
`CanAudioLatencyWidget` 양쪽에서 재사용)와, `CanAudioLatencyWidget.tsx`
자체의 CAN 시그널 차트(오디오 파형과 다른 데이터소스라 공용 컴포넌트를
안 씀).

**수정**: 두 파일 모두 `zoomY`에서 `(inX && inY)` 항을 제거해
`zoomY = overYAxisStrip`로 변경 -- 플롯 영역 안에서는 X만 줌되고
(`zoomX`는 그대로 `overXAxisStrip || (inX && inY)`라 변화 없음), Y축
스트립에서만 Y가 줌된다. `AudioMonitorWidget`은 `AudioWaveformChart`를
그대로 재사용하므로 같은 수정이 자동으로 적용된다 (요청 범위는 아니지만
동일 버그였으므로 일관되게 고침). 일반 그래프 위젯(`GraphWidget.tsx`)에도
같은 패턴이 있으나 이번 요청 범위 밖이라 그대로 뒀다.

검증: `tsc -b --noEmit`/`vite build`/`oxlint` 클린. 백엔드 변경 없음.
브라우저 자동화 도구가 없어 실제 휠 동작은 코드 검토로만 확인 -- 사용자가
실제로 플롯 영역/Y축 스트립에서 휠 동작을 확인해줄 것을 권장한다.

## "CAN 신호 그래프" 위젯에도 동일한 X전용 휠 확대/축소 적용 + 줌 비율 10%로 변경 (2026-08-13, 사용자 요청)

바로 위 항목에서 오디오/CAN-오디오 지연 위젯을 고치면서 `GraphWidget.tsx`
(일반 CAN 신호 그래프 위젯)에도 같은 패턴의 버그가 있다고 보고했는데,
사용자가 이 위젯도 동일한 방식으로 고쳐달라고 요청. 추가로 휠 1틱당
확대/축소 비율을 10% 단위로 지정.

**수정** (`frontend/src/widgets/GraphWidget.tsx`):
- `onWheel`의 `zoomY`에서 `(inX && inY)` 항을 제거해
  `zoomY = overYAxisStrip`로 변경 -- 플롯 영역 안에서는 X만 줌되고, 왼쪽
  Y축 눈금 스트립에서만 Y가 줌된다 (`AudioWaveformChart.tsx`/
  `CanAudioLatencyWidget.tsx`와 동일한 수정).
- `ZOOM_STEP`을 `1.15`(15%)에서 `1.10`(10%)으로 변경. 이 위젯만 요청받아
  변경했고, 같은 상수를 각자 갖고 있는 `AudioWaveformChart.tsx`/
  `CanAudioLatencyWidget.tsx`의 `ZOOM_STEP`(1.15)은 이번 요청 범위가
  아니라 그대로 뒀다.

검증: `tsc -b --noEmit`/`vite build`/`oxlint src/widgets/GraphWidget.tsx`
클린. 백엔드 변경 없음. 브라우저 자동화 도구가 없어 실제 휠 동작은 코드
검토로만 확인.

## "ISO-TP 전송" 위젯 "응답 대기" 기본값을 OFF에서 ON으로 변경 (2026-08-13, 사용자 요청)

`IsoTpBox.tsx`의 `waitForResponse`는 `config.options.waitForResponse ??
false`로 기본 꺼짐 상태였다 (2026-08-11 "응답 대기" 기능 추가 당시 신규
위젯 기본 동작을 바꾸지 않으려고 OFF로 시작한 것 — Requirement.md 해당
항목 참고). 사용자 요청으로 기본값을 `true`로 변경.

**수정**: `const waitForResponse = opts.waitForResponse ?? true;` 한 줄만
변경. 응답 대기가 켜졌을 때 필요한 `respId` 등은 이미 기본값(`78B`)이
있어 `canSend` 조건에 영향 없음 -- 새로 올린 위젯도 바로 전송 가능.

검증: `tsc -b --noEmit`/`vite build`/`oxlint src/widgets/IsoTpBox.tsx`
클린. 백엔드 변경 없음.

## "CAN-오디오 지연 확인" 위젯: 오디오 캡처 지연 편향 보정 기능 추가 (2026-08-13,
사용자 요청 — 실측 약 40ms 지연 보고)

사용자가 위젯으로 CAN 트리거 → 오디오 반응 지연을 실측해보니 약 40ms의 오차가
있다고 보고. 앞선 대화에서 원인을 리뷰한 결과: `audio_service.py`의
sounddevice 콜백이 `time.time()`을 찍는 시점이 "그 블록을 다 캡처해서 콜백이
막 호출된 순간"인데, 코드는 이 값을 그 블록의 **시작** 시각으로 써왔다 —
그래서 오디오 파형 전체가 "블록 전송 시간 + OS 오디오 스택 버퍼링"만큼 항상
실제보다 늦게 찍히는 고정 편향이 있다(Requirement.md의 "CAN-오디오 지연 확인"
Phase 1 설계 리뷰에서 10~30ms대로 예측했던 항목과 동일, 40ms 실측치는 그
범위와 맞음). CAN 쪽 타임스탬프는 서브 ms 오차라 오디오 쪽만 보정하면 된다.

드라이버별 ADC 타임스탬프로 자동 계산하는 방식은 플랫폼 의존도가 커서 리스크가
크므로, Phase 1 설계 문서에 이미 적어둔 대로 **사용자가 실측한 값을 수동으로
입력하는 보정값**으로 구현(자동 감지 없음).

**수정** (백엔드 변경 없음, 프론트엔드만):
- `frontend/src/widgets/AudioWaveformChart.tsx`: `xOffsetMs?: number`(기본
  0) prop 추가. 폴링 시 쿼리 구간을 `[xMin+xOffsetMs, xMax+xOffsetMs]`로
  넓혀 가져오고, 그리기 시 각 점을 `p.t*1000 - xOffsetMs`로 그려 오디오
  파형만 왼쪽(과거)으로 당겨 보이게 한다 -- 쿼리 구간도 같이 옮겨야 뷰
  오른쪽 끝에 데이터 공백이 생기지 않는다. 기본값 0이라 `AudioMonitorWidget`
  (이 prop을 넘기지 않음)은 동작 변화 없음.
- `frontend/src/widgets/CanAudioLatencyWidget.tsx`: 툴바에 "오디오 보정"
  숫자 입력 추가(`config.options.audioOffsetMs`로 영구 저장, 기본값 40 —
  사용자의 실측값). 오디오 채널 차트에만 `xOffsetMs`로 전달, CAN 신호
  차트는 그대로(보정 대상 아님).

검증: `tsc -b --noEmit`/`vite build`/`oxlint` 클린. 백엔드 무관. 브라우저
자동화 도구가 없어 실제 보정 결과(두 그래프가 정확히 겹치는지)는 코드
검토로만 확인 -- 사용자가 다음 실사용 시 CAN 트리거와 오디오 반응이 기본
40ms 보정 상태에서 잘 정렬되는지, 필요시 값을 미세조정해 확인해줄 것을
권장한다.

## CAN-SWDL: 진단 세션전환 스텝이 2개 이상일 때 선택하지 않은 백그라운드
세션까지 잘못 전송되는 버그 수정 (2026-08-13, 사용자 실사용 보고)

사용자가 "선택한 스텝만 실행되어야 하는데 잘못 동작하는 것 같다"고 보고한
실행 로그를 분석: [1]~[7] 스텝만 체크하고 시작했는데, [1] 진단 세션 전환
스텝이 `0x02`(선택한 세션)를 정상 전송한 직후 **체크하지 않은/선택하지 않은
백그라운드 세션 `0x03`까지 추가로 전송**했고, ECU가 이를 거부(NRC 0x12)해
전체 다운로드가 실패 → error-rule 복구 절차가 자동 실행되는 연쇄까지
이어졌다.

**원인 재현**: 먼저 슬롯별 스텝 선택 라우팅(`MultiUdsDownloadManager.start_all`
의 `resolve()`)과 스텝 스킵 로직(`_is_step_selected`/`_run_steps`)을 각각
직접 실행해 검증했는데 — 둘 다 정확했다(부분 선택 `[1,3]`을 넣으면 정확히
그 두 스텝만 실행됨을 확인). 문제는 그 다음 단계: `_execute_step`의
`diagnosticSessionControl` 처리부가 "이 스텝에 사용자가 선택한 세션 타입"을
찾을 때 `step_params`에서 `_sessionType_`로 시작하는 키를 **아무거나**
집어 썼다(`next(iter(selected_type_keys))`). 그런데 `modified_params`는
*서비스 이름*으로만 키가 나뉘어 있어(`_get_effective_params()`), 프로시저
안에 `diagnosticSessionControl` 스텝이 **2개 이상**이면 각 스텝의
`_sessionType_<step_idx>` 오버라이드가 전부 한 딕셔너리에 합쳐져 모든
occurrence의 `step_params`에 다 섞여 들어간다. 즉 스텝[1]을 실행할 때도
스텝[9](예시) 것까지 후보에 함께 보여, `next(iter(set))`이 엉뚱한 스텝의
오버라이드 키를 집으면 그 값이 현재 스텝의 실제 파라미터와 맞지 않아 "선택
없음" 폴백으로 빠지고, 그 폴백은 "둘 다 있으면 둘 다 보낸다"는 기존 동작이라
선택하지 않은 백그라운드 세션까지 전송된 것. `set`의 반복 순서는 문자열
해시에 의존하고 CPython은 프로세스마다 해시 시드를 무작위화하므로, 같은
XML로도 실행할 때마다 재현되거나 안 되거나 하는 비결정적 버그였다(실제로
`next(iter({'_sessionType_1','_sessionType_3'}))`를 5번 반복 실행해보면
매번 다른 값이 나오는 것으로 재현·확인).

**수정** (`backend/uds_download_manager.py`):
- `_run_steps()`가 `_execute_step()`을 호출할 때 그 스텝의 전역
  `step_idx`도 함께 넘기도록 변경(`_execute_step(step, phase_name,
  modified_params, step_idx)`).
- `diagnosticSessionControl` 처리부에서 "아무 `_sessionType_*` 키나 집기"
  대신 `step_params.get(f"_sessionType_{step_idx}")`로 **이 스텝 자신의**
  오버라이드만 직접 조회하도록 변경 — 다른 스텝의 오버라이드와 충돌할 여지
  자체를 없앴다.
- 에러 복구(`_run_error_recovery`)는 자체 인덱스 공간(0부터 별도 카운트)을
  쓰는 기존 설계라, 메인 프로시저와 에러룰 사이에 우연히 같은 인덱스가
  같은 서비스로 겹치는 극단적 케이스까지는 여전히 이론적으로 남아있다 --
  이건 이번 버그 리포트의 실제 재현 케이스가 아니고, `_run_error_recovery`
  docstring에 이미 문서화된 기존 한계와 같은 종류라 이번 수정 범위에 넣지
  않았다.

검증: `backend/tests/test_uds_download_manager.py`에 회귀 테스트
`test_second_diagnostic_session_control_step_uses_its_own_session_choice`
추가(진단 세션전환 스텝 2개, 각각 `_sessionType_<idx>` 오버라이드가
`diagnosticSessionType`을 가리키는 상황을 재현 — 수정 전 코드였다면
프로세스 해시 시드에 따라 간헐적으로 실패했을 테스트). 백엔드 전체 246개
테스트 통과. 프론트엔드 변경 없음.

### 추가 조사: 위 수정 후에도 재현됨 — 진짜 원인은 폴더 자동로드의 3슬롯 동시
업로드 경쟁 상태 (2026-08-13, 사용자가 재현 로그 재보고)

위 수정을 배포한 뒤에도 사용자가 같은 증상(선택한 `0x02`만 보내야 하는데
`0x03` 백그라운드 세션까지 전송 → NRC 0x12 → 에러 복구 연쇄)을 다시
보고했다. 이번엔 실행 로그의 `실행: diagnosticSessionControl {...}` 줄에
`_sessionType_1` 오버라이드 자체가 아예 없었다 — 즉 문제는 "여러 후보 중
잘못된 것을 골랐다"가 아니라 "오버라이드가 백엔드에 도달하지도 못했다"였다.
이 XML은 실제로 진단 세션전환 스텝이 dual-type(진단+백그라운드 둘 다 있는)
스텝을 **하나만** 가지고 있어([1]), 앞서 고친 다중 스텝 충돌 자체는 이번
사고의 트리거가 아니었다(그 수정 자체는 여전히 유효한 별도의 견고화이고
회귀 테스트로 남겨둔다).

**진짜 원인**: `UdsSwdlWidget.tsx`의 "📁 패키지 폴더선택" 자동로드
핸들러가 슬롯 3개의 XML을 `Promise.all(uploadPromises)`로 **동시에**
업로드한다. 각 슬롯의 업로드가 끝나면 그 슬롯의 `diagnosticSessionType`
자동선택 값을 `setSlotParamOverrides(slotIdx, ...)`로 저장하는데, 이
함수는 `{ ...allParamOverrides, [slotIdx]: overrides }`처럼 "기존
전체 상태 + 내 슬롯만 갱신"하는 방식으로 새 값을 만든다 -- 그런데
`allParamOverrides`는 이 폴더선택 핸들러가 시작된 그 렌더 시점에 **한 번만**
캡처된 값이고, 세 슬롯의 업로드 프로미스가 사실상 동시에(로그상 11ms
간격) 끝나면서 각자 **같은 stale `allParamOverrides` 스냅샷**을 기준으로
`updateWidget`을 호출한다. 즉 세 번의 쓰기가 서로의 결과를 전혀 보지 못한
채 "내 슬롯 값만 반영된 객체"로 매번 덮어써서, **가장 나중에 끝난 슬롯의
값만 남고 나머지 두 슬롯의 자동선택은 통째로 사라진다** (React의 `setState`
updater 함수 형태와 달리, `updateWidget`은 평범한 객체를 받는 API라 최신
상태를 다시 읽지 않는다). 사용자의 3슬롯 동시 업로드 워크플로에서 슬롯1의
`_sessionType_1`이 다른 슬롯의 나중 쓰기에 덮여 사라진 것이 정확히 이번
증상이었다.

**수정** (`frontend/src/widgets/UdsSwdlWidget.tsx`, 폴더 자동로드 핸들러만):
각 슬롯의 자동선택 결과를 즉시 `setSlotParamOverrides`로 쓰지 않고
지역 변수 `autoParamsBySlot`(슬롯 인덱스 → autoParams)에 모아두기만
하도록 변경. `Promise.all(uploadPromises)`가 전부 끝난 뒤, 그 시점의
`allParamOverrides`를 기준으로 **모든 슬롯을 한 번에 병합**해
`updateWidget`을 단 한 번만 호출 -- 세 번의 경쟁하는 부분 쓰기를 하나의
원자적 쓰기로 바꿔 경쟁 상태 자체를 없앴다.

**참고 (이번 수정 범위에는 포함하지 않음)**: 슬롯별 "▲ XML 업로드" 버튼도
`uploadXml(slotIdx)` 안에서 같은 `setSlotParamOverrides` 패턴을 쓰므로,
사용자가 서로 다른 슬롯의 업로드 버튼을 응답이 오기 전에 연달아 클릭하면
이론적으로 같은 경쟁 상태가 발생할 수 있다. 이번에 실제 재현된 경로는
폴더 자동로드(항상 3슬롯 동시 실행)뿐이라 그쪽만 고쳤다 -- 단일 버튼
경로도 필요하면 별도로 알려달라.

검증: `tsc -b --noEmit`/`vite build`/`oxlint src/widgets/UdsSwdlWidget.tsx`
클린. 이 프로젝트에는 프론트엔드 테스트 러너가 없어(package.json에
vitest/jest 등 없음, 기존 검증도 항상 tsc/vite/oxlint까지만) 경쟁 상태
자체를 자동화 테스트로 재현하지는 못했다 -- 코드 리뷰로 원인·수정 모두
확인. 사용자가 다음에 폴더 자동로드로 3슬롯을 로드한 뒤, 여러 진단
세션전환 스텝이 있는 슬롯이 있다면 그 슬롯의 세션타입 오버라이드가
남아있는지(각 슬롯 카드에서 "세션 타입" 드롭다운이 자동 선택된 상태로
보이는지) 확인해줄 것을 권장한다.

## UX 개선 6건: OTA Tester 진행률, 텍스트 표시창 초기화, CAN-SWDL 시인성
(2026-08-13, 사용자 요청 7건 중 1~6번 — 7번 CAN 로그 자동저장은 별도 계획 승인 대기)

### 1. OTA Tester에 TransferData 블록 진행률 표시 (CAN-SWDL에 이미 있던 것과 동일)
`ota_tester_download_manager.py`는 기존에 "케이스 N/M · Step X/Y" 수준의
진행률만 있었고, TransferData처럼 한 스텝 안에서 블록 수백 개를 오래
전송하는 동안은 그 바가 전혀 움직이지 않았다. CAN-SWDL(`uds_download_manager.py`)
의 `_progress`(current_step/total_blocks/current_block/percent) 패턴을
그대로 포팅: `__init__`에 `_progress` dict + `_update_progress()` 헬퍼
추가, `_execute_transfer_data()`에서 블록마다 갱신, `_run_case_steps()`
시작 시 케이스 전환 때마다 리셋, `get_status_dict()`에 `progress` 포함.
프론트(`OtaTesterWidget.tsx`)는 기존 케이스/스텝 바 아래에 CAN-SWDL과
동일한 스타일의 블록/퍼센트 바를 추가(`total_blocks>0`일 때만 표시).
`types.ts`에 `OtaTesterProgress` 타입 추가.

### 2. "텍스트 표시창" 위젯에 초기화 버튼 추가
`canStore.ts`에 `clearActivity()`(activityLog를 비우고 `markDirty()`)
추가, `displays.tsx`의 `TextDisplay`에 작은 헤더 바 + "초기화" 버튼 추가.

### 3. CAN-SWDL: 현재 실행 중인 진단 스텝을 빨간색으로 표기
백엔드에 `_progress.current_step_idx`(현재 실행 중인 스텝의 전역 인덱스,
`get_procedure_steps()`의 평평한 리스트와 동일 인덱스 체계, 유휴 시 -1)
추가 -- `_run_steps()`가 스텝을 실행하기 직전에 갱신. 단, `force_all=True`
(에러 복구 전용 -- 별도의 짧은 인덱스 체계를 쓰므로 메인 프로시저 스텝과
번호가 우연히 겹치면 엉뚱한 스텝이 빨갛게 표시될 수 있음)일 때는 갱신하지
않음. 프론트에서 `slot.status.running && slot.status.progress.current_step_idx
=== stepIdx`인 스텝의 서비스명 텍스트를 빨간색으로 표시.

### 4~6. CAN-SWDL/공용 위젯 시인성 개선 (스타일만, 로직 변경 없음)
- `UdsGlobalControls.tsx`: "ASK 선택" 버튼을 "SeedKey DLL 업로드"와 동일한
  파란 배경/흰 글자로 변경.
- `UdsSwdlWidget.tsx`: "이 슬롯 전체 선택"/"이 슬롯 전체 해제" 버튼도 동일한
  파란 배경/흰 글자로 통일.
- `UdsSwdlWidget.tsx`: 진단 서비스명(보안 액세스, 루틴 제어 등) 텍스트를
  `fontWeight: 700` + 검정(`#000`)으로 변경 (실행 중일 때는 위 3번 규칙에
  따라 빨간색으로 대체됨).

검증: 백엔드 `test_uds_download_manager.py`에 회귀 테스트
`test_progress_current_step_idx_tracks_the_running_step_and_resets_when_done`
추가 — 선택된 스텝마다 인덱스가 올바르게 보고되고 완료 시 -1로 리셋되는지,
에러복구 경로에서는 보고되지 않는지 확인. 백엔드 전체 247개 테스트 통과.
프론트 `tsc -b --noEmit`/`vite build`/`oxlint` 클린. 브라우저 자동화
도구가 없어 실제 화면(진행률 바 움직임, 빨간 강조, 버튼 색상)은 코드
검토로만 확인 -- 사용자가 다음 실사용 시 확인해줄 것을 권장한다.

## 7. CAN-SWDL/OTA Tester 실행 시 자동 CAN 로그 저장 (2026-08-13, 사용자 요청
7건 중 마지막 — 사전 질의로 3가지 확정 후 진행)

### 확정된 사양 (사용자 답변)
1. **분리 단위**: CAN-SWDL은 슬롯(파일)마다, OTA Tester는 "파일 단위"(=
   케이스마다, 케이스 하나가 XML 파일 하나에서 옴) — 각각 별도 로그 파일.
2. **중단(Stop) 시**: 성공/실패 어느 쪽도 아니지만 `_fail`로 처리(파일명
   규칙을 `_success`/`_fail` 둘로만 단순하게 유지).
3. **ASCII 포맷**: Vector ASC(`.asc`) — python-can이 이미 내장 지원하는
   포맷(`can.ASCWriter`/`can.ASCReader`)이고, 기존 수동 BLF 로거
   (`log_service.py`)와 완전히 같은 구조라 리스크가 낮음.

### 설계
기존 수동 "CAN 로그 저장"(`LogService`, 전역 BLF 토글, `canlog_<timestamp>.blf`
이름)과는 완전히 독립된 별도 자동 로거로 구현 -- `CanManager.add_listener()`가
리스너를 여러 개 동시에 지원하므로 사용자가 수동 로깅을 켠 채로 CAN-SWDL/
OTA Tester를 돌려도 둘 다 각자 파일에 기록되며 서로 간섭하지 않는다.

- **`backend/log_service.py`**: 새 클래스 `AutoCanLogger` 추가.
  `start(label)`가 `canlog_<timestamp>_<label>.asc`를 열고
  `can.ASCWriter`를 리스너로 등록(라벨의 파일시스템에 안전하지 않은 문자는
  `_`로 치환); `stop(success)`가 리스너를 해제하고 파일명에
  `_success`/`_fail`을 붙여 rename. 이미 실행 중이거나 CAN이 연결 안 된
  상태의 `start()`, 시작 안 된 상태의 `stop()`은 조용히 no-op(로깅
  실패/미연결이 실제 다운로드를 막아선 안 됨).
- **`backend/uds_download_manager.py`**: `UdsDownloadManager.__init__`에
  `log_dir`/`slot_label` 파라미터 추가, `log_dir`가 주어지면 자체
  `AutoCanLogger` 생성(테스트 등에서 생략하면 `None`이라 전부 no-op).
  스레드 진입점 `_run()`에서 프로시저 실행 직전 `start(label)`(라벨은 로드된
  XML 파일명의 stem, 없으면 `slot{N}`), `finally`에서
  `stop(success=(state==COMPLETED))` — 에러 복구(error-rule) 구간까지
  포함해 슬롯의 전체 실행을 하나의 로그로 담는다. `MultiUdsDownloadManager`
  는 `log_dir`를 받아 슬롯별로 `slot1`/`slot2`/`slot3` 라벨과 함께 전달.
- **`backend/ota_tester_download_manager.py`**: 같은 패턴, 케이스 단위로
  `_run_case_steps()`를 감싸 케이스 시작 시 `start(case['label'])`, 그
  케이스가 끝나면(성공 반환/실패 반환/예외 모두) `stop(success=...)` —
  `_run_case_steps_inner()`로 기존 로직을 그대로 옮기고 바깥쪽
  `_run_case_steps()`가 try/except로 감싸 정상 반환값과 예상 밖 예외
  양쪽 다 정확한 성공/실패로 기록되도록 함(케이스는 실패 시 즉시 전체 순차
  실행이 멈추므로, 다음 케이스가 실행될 때 `_error_message`가 이전
  케이스의 값으로 오염돼 있을 일이 없음을 확인).
- **`backend/main.py`**: 두 매니저 생성 시 기존 수동 로거와 같은
  `CAN_LOG_DIR`(`backend/can_logs/`)를 `log_dir=`로 전달.

### 항목 1(OTA Tester 진행률)에서 이미 추가한 `_progress`와의 관계
서로 무관 -- 하나는 UI 진행률 표시, 하나는 CAN 로그 파일 저장. 우연히 같은
날 같은 파일(`ota_tester_download_manager.py`)에 손을 대서 언급.

검증: `backend/tests/test_log_service.py`에 `AutoCanLogger` 단위 테스트
6개 추가(실제 virtual CAN 버스 + 실제 `.asc` 파일 쓰기/rename까지 확인,
`can.ASCReader`로 재읽기해 프레임 수·ID까지 검증). `test_uds_download_manager.py`/
`test_ota_tester_download_manager.py`에 각각 `_run()`/`_run_case_steps()`
가 성공/실패에 맞춰 정확히 start/stop을 호출하는지 스파이 더블로 확인하는
테스트 추가. 백엔드 전체 256개 테스트 통과(기존 246 + 이번 세션에서 추가한
총 10개). 프론트엔드 변경 없음(완전 자동 백그라운드 기능이라 UI가 필요
없음). 실제 하드웨어로 CAN-SWDL/OTA Tester를 돌려 `backend/can_logs/`에
`canlog_<timestamp>_<라벨>_success.asc`/`_fail.asc` 파일이 실제로 생기고
Vector 등 ASC 뷰어로 열리는지는 사용자가 다음 실사용 때 확인해줄 것을
권장한다.
