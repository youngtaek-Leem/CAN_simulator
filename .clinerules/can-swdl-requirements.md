## Brief overview
CAN-SWDL (UDS Software Download) 위젯 개선을 위한 요구사항. 기존 단일 XML/BIN 파일 로딩에서 3개의 파일을 동시에 로딩하고, 진단 서비스를 구조화된 UI로 표시하며 선택적으로 실행하는 방식으로 확장한다.

## 1. 다중 파일 동시 로딩

- **XML, BIN 파일 3세트(01/02/03)를 동시에 로딩**
  - XML 3개 (ex: RS4PE_96370T4BB0_01_2671.xml, 02, 03)
  - BIN 3개 (ex: 01_mcu_swdl.bin, 02_dsp_swdl.bin, 03_sffs_swdl.bin)
- **파일 선택 UI**
  - 파일 1/파일2/파일3 탭 또는 아코디언 형태로 구분
  - 각 슬롯마다 XML 선택 버튼 + BIN 선택 버튼
- **Backend**
  - `UdsDownloadManager` 인스턴스를 3개 생성 또는 상태를 리스트로 관리
  - 각 파일셋은 독립적인 `state`, `progress`, `events` 를 가짐

## 2. 진단 서비스 구조화된 UI 표시

### 2-1. 체크리스트 + 서비스 이름 + 파라미터
- XML 파싱 결과를 텍스트 이벤트 로그 대신 **계층적 체크리스트**로 표시
- 각 진단 서비스(step)별로:
  - **체크박스**: 선택 시 실행, 미선택 시 skip
  - **서비스 이름**: 한글로 표시 (ex: 진단 세션 전환, 보안 액세스, 루틴 제어, 다운로드 요청, 데이터 전송, ECU 리셋)
  - **핵심 파라미터 요약**: 입력창(inline rext input)으로 표시하여 수정 가능

### 2-2. 서비스 이름 매핑
| 서비스 ID | 표시 이름 |
|----------|---------|
| startCommunication | CAN 통신 시작 |
| stopCommunication | CAN 통신 종료 |
| diagnosticSessionControl | 진단 세션 전환 |
| securityAccess | 보안 액세스 |
| routineControl | 루틴 제어 |
| requestDownload | 다운로드 요청 |
| transferData | 데이터 전송 |
| requestTransferExit | 전송 종료 요청 |
| ecuReset | ECU 리셋 |
| controlDTCSetting | DTC 설정 제어 |
| communicationControl | 통신 제어 |
| readDataByIdentifier | DID 읽기 |
| completeDecision | 완료 판정 |

### 2-3. 수정 가능한 파라미터
- XML에서 파싱된 파라미터를 `input` / `select` 필드로 표시
- 사용자가 수정을 하면 실행 시 해당 값이 반영됨
- 파라미터 예시:
  - `diagnosticSessionType`: `0x01`, `0x02`, `0x03` (드롭다운)
  - `memoryAddress`: hex 입력
  - `memorySize`: hex 입력
  - `requestDownload` 의 writeSize: hex 입력

### 2-4. 3개 파일 각각 표시
- 각 파일 (01/02/03)의 서비스 단계를 **독립된 영역**으로 표시
- 3개 영역이 동시에 보이도록 배치 (세 칸 그리드 또는 세로 스크롤 영역)

### 2-5. 순차 실행
- **실행 버튼 클릭 시**:
  1. 파일1에 대해 체크된 서비스 단계를 순서대로 실행
  2. 파일1 완료 후 파일2 실행
  3. 파일2 완료 후 파일3 실행
- 각 진행 상태는 개별 파일 영역에 표시
- 중간에 실패 시 다음 파일 실행은 사용자 선택 (계속 / 중단)

## 3. Frontend 컴포넌트 구조

```
UdsSwdlWidget (기존 확장)
├── FileSlot[0] (파일 1 그룹)
│   ├── XML File Picker + BIN File Picker
│   ├── ServiceChecklist (체크박스 + 파라미터)
│   └── Progress Bar / 상태 표시
├── FileSlot[1] (파일 2 그룹)
│   └── ...
├── FileSlot[2] (파일 3 그룹)
│   └── ...
└── Control Bar (전체 시작/중지/상태)
```

## 4. Backend API 확장

- `GET /api/uds/swdl/status` → 3개 슬롯의 상태를 배열로 반환
- `POST /api/uds/swdl/load_xml` → `slot_index` (0/1/2) 와 path 받기
- `POST /api/uds/swdl/load_bin` → `slot_index` (0/1/2) 와 path 받기
- `POST /api/uds/swdl/start` → `slot_index` 배열, 선택된 step 목록 전달
- `POST /api/uds/swdl/stop` → `slot_index` 지정
- `PUT /api/uds/swdl/step_params` → `slot_index`, `step_name`, 파라미터 수정 내역

## 5. 우측 이전 이벤트 로그 (보존 여부)

- 기존 이벤트 로그 텍스트는 화면 우측 또는하단에 유지할지 결정
  - 직급: 구조화된 체크리를 우선으로 하고, 이벤트 로그는 부가 정보로 유지
  - 대안: 체크리스트만 사용, 이벤트는 로그 패뷰어로 분리

## 6. 결정 사항

- [x] 3개 파일은 **순차 실행** (파일1 완료 → 파일2 → 파일3)
- [x] 이벤트 로그 **보존** (보조 패널로 유지)
- [x] 서비스 step **전체 선택 / 전체 선택 해제** 메뉴 제공
- [x] 파라미터 입력란 미기입 시 **원래값(XML 파싱 값) 사용**