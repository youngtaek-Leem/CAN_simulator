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

