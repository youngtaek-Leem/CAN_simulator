# Graph Report - CAN_simulator  (2026-09-15)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1757 nodes · 3764 edges · 99 communities (76 shown, 20 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 160 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9d855a32`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- DbcService
- TxScheduler
- GraphWidget.tsx
- types.ts
- UdsDownloadManager
- test_isotp_service.py
- main.py
- test_api.py
- fake_receive
- AudioService
- _FakeNotifier
- BaseModel
- App.tsx
- CanStore
- registry.tsx
- package.json
- canStore.ts
- test_syslog_service.py
- test_tx_scheduler.py
- post
- OtaTesterDownloadManager
- test_uds_download_manager.py
- test_test_runner_service.py
- SysLogAnalysisWidget.tsx
- test_ota_tester_download_manager.py
- uds_core.py
- OtaTesterWidget.tsx
- ._uds_request_with_retry
- appContext.ts
- CanManager
- UploadFile
- TestRunnerService
- UdsProcedure
- _timed_lock
- isotp_service.py
- _require_running
- ota_tester_download_manager.py
- PowerSupplyService
- AutoCanLogger
- test_power_supply_service.py
- displays.tsx
- compilerOptions
- _ChannelLevelTracker
- _broadcast_loop
- SeedKeyService
- test_syslog_script_generator.py
- SysLogService
- UdsStep
- compilerOptions
- MultiUdsDownloadManager
- audio_service.py
- xlsx_to_script.py
- uds_download_manager.py
- ReplayService
- test_runner_service.py
- syslog_service.py
- test_replay_service.py
- fake_send
- ._uds_request
- ._apply_battery
- find_matching_signal
- runner_with_fakes
- diag_log.py
- LogService
- _FakeStream
- test_uds_request_registers_listener_before_sending
- _BufferListener
- .status
- stereo
- IsoTpBox.tsx
- .refresh_devices
- ota_tester_case_selected_steps
- _CountingWriter
- .oxlintrc.json
- ._rotate_segment
- decode_stmin
- parse_log
- test_uds_request_with_retry_stop_event_interrupts_unlimited_pending_wait
- _SpyAutoLogger
- generate_monitor_filename
- .add_listener
- _SuppressNoisyAccessLog
- test_uds_request_with_retry_uses_the_xml_derived_p2_star_value
- test_status_bitmask_logic_matches_apptest_py
- test_ota_suppress_bit_timeout_is_success_not_failure
- test_transfer_data_sends_tester_present_keepalive_periodically
- test_uds_request_with_retry_reuses_same_reader_across_pending_retries
- test_batt_command_writes_apply
- tsconfig.json
- .get_waveform
- _wrapped
- start.sh
- .__init__
- .script_raw
- .functions_raw
- dirs

## God Nodes (most connected - your core abstractions)
1. `OtaTesterDownloadManager` - 77 edges
2. `AudioService` - 56 edges
3. `CanStore` - 54 edges
4. `UdsDownloadManager` - 53 edges
5. `useApp()` - 50 edges
6. `CanManager` - 40 edges
7. `_FakeNotifier` - 38 edges
8. `useCanVersion()` - 34 edges
9. `TestRunnerService` - 33 edges
10. `DbcService` - 31 edges

## Surprising Connections (you probably didn't know these)
- `runner_with_fakes()` --uses--> `DbcService`  [INFERRED]
  backend/tests/test_test_runner_service.py → backend/dbc_service.py
- `stack()` --uses--> `DbcService`  [INFERRED]
  backend/tests/test_test_runner_service.py → backend/dbc_service.py
- `runner_with_fakes()` --uses--> `TxScheduler`  [INFERRED]
  backend/tests/test_test_runner_service.py → backend/tx_scheduler.py
- `stack()` --uses--> `TxScheduler`  [INFERRED]
  backend/tests/test_test_runner_service.py → backend/tx_scheduler.py
- `Props` --references--> `WidgetConfig`  [EXTRACTED]
  frontend/src/widgets/UdsSwdlWidget.tsx → frontend/src/types.ts

## Import Cycles
- None detected.

## Communities (99 total, 20 thin omitted)

### Community 0 - "DbcService"
Cohesion: 0.06
Nodes (27): CanLogService, CAN log 분석 위젯 백엔드: BLF/ASC CAN 로그 + DBC로 신호 시계열을 만든다. sysLog와 유사하지만 차이점: - 파서는…, 메시지별: 메시지 리스트 + 하위 신호 목록, CAN log를 시뮬레이터 TX 신호 기준 테스트 스크립트로 생성한다. range_ms: {"a_ms": int, "b_ms": int} |…, CAN log 1개만 세션에 유지하는 교체 방식. DBC는 전역 DbcService를 참조., DbcService, _invalid_raw(), _message_send_type() (+19 more)

### Community 1 - "TxScheduler"
Cohesion: 0.06
Nodes (26): build_workbook(), main(), parse_dbc(), parse_dbc_messages(), parse_dbc_values(), dbc_to_script_editor.py ======================== CAN DBC(.dbc) 파일을 읽어서, 아래 절차대로…, (signal, message, msg_id, cycle_ms) 리스트, 신호명 중복 딕셔너리를 반환., (signal, value_dec, value_hex, description) 리스트를 반환 (VAL_ 라인). (+18 more)

### Community 2 - "GraphWidget.tsx"
Cohesion: 0.09
Nodes (42): HistoryPoint, AudioWaveformPoint, AudioChartXView, AudioWaveformChart(), AudioWaveformChartProps, fmtXTick(), Geom, niceTicks() (+34 more)

### Community 3 - "types.ts"
Cohesion: 0.06
Nodes (44): AudioChannelLevel, AudioDevice, AudioStatus, AudioWaveform, AudioWaveformChannel, CanLogMessageInfo, CanLogPoint, CanLogScriptResult (+36 more)

### Community 4 - "UdsDownloadManager"
Cohesion: 0.08
Nodes (19): Any, Execute TransferData: send binary in blocks., Execute RequestTransferExit., Execute error-rule if present, running every one of its steps. The step…, Load and parse an XML procedure file., Load a BIN file for download., Start the download procedure in a background thread. Args: selected_steps: list…, Request graceful stop of the download procedure. (+11 more)

### Community 5 - "test_isotp_service.py"
Cohesion: 0.07
Nodes (30): drain(), fd_stack(), _min_fd_len(), _pad_to_fd_len(), fixture, send()'s min_stmin_s is a floor the caller can use to deliberately slow a…, The floor is one-directional -- it must never let a send go *faster* than what…, An explicit is_fd=True request can't make a classic-connected bus emit an FD… (+22 more)

### Community 6 - "main.py"
Cohesion: 0.08
Nodes (42): audio_devices(), audio_level(), audio_status(), audio_waveform(), canlog_messages(), canlog_series(), canlog_signals(), canlog_status() (+34 more)

### Community 7 - "test_api.py"
Cohesion: 0.08
Nodes (38): make_client(), Regression: POST /api/run/stop (the top-bar global Stop) used to leave an in-…, resp_id turns the send-only endpoint into a request/response round-trip: after…, The actual bug being fixed: when the reply is multi-frame, this endpoint (as…, HTTP-level smoke test for the new folder-driven case endpoints…, /api/tx/periodic/enable_all + /disable_all REST wiring, and that the "Enable…, POST /api/shutdown is the alternate exit path for the Windows PyVISA/ Ctrl-C…, test_audio_devices_and_selection_api() (+30 more)

### Community 8 - "fake_receive"
Cohesion: 0.06
Nodes (19): A step's own XML localSTMinTx must win over the shared global STmin override…, Regression: the override must actually reach isotp_receive's fc_stmin, not just…, The wait after a 0x78 must use P2*Server_max, not the original (much shorter)…, [3E 80] -- sent on the functional broadcast ID (0x7DF), not this case's own…, test_local_stmin_tx_override_applied_during_step_and_cleared_after(), test_ota_suppress_bit_does_not_forgive_an_actual_negative_response(), test_send_tester_present_sends_suppressed_pdu_functionally_without_waiting(), test_uds_request_with_retry_does_not_retransmit_on_nrc78() (+11 more)

### Community 9 - "AudioService"
Cohesion: 0.10
Nodes (33): AudioService, choose_channel_count(), How many channels to request from InputStream for a device that reports…, Promote a just-recorded WAV to be the fixed reference for a case (e.g. on the…, 오디오 신호 모니터 위젯의 Stop 이후 파형 탐색: stop()/stop_monitor()는 _level_trackers를 지우지 않으므로,…, Only the widget's own Record splits into 30-min segments -- the test runner's…, Simulates the Windows scenario: the same physical mic enumerated under both MME…, A real microphone attached under a non-default host API must never disappear… (+25 more)

### Community 10 - "_FakeNotifier"
Cohesion: 0.09
Nodes (31): FakeEcu, _FakeNotifier, Fake ECU: inspects the outgoing PDU's SID and returns a canned positive…, Regression test for the removed TX-ONLY-TEST-MODE hardcoding: a real negative…, HOOK_XML's diagnosticSessionControl has confirmPositiveResponse="no"; the fake…, Deselecting the (always-failing, in this test) diagnosticSessionControl step…, User-reported example: routineControl(type=0x01, id=0xFF00,…, A real maxNumberOfBlockLength (e.g. 0x0C02 = 3074 bytes) must not be dumped in… (+23 more)

### Community 11 - "BaseModel"
Cohesion: 0.06
Nodes (34): audio_select_device(), AudioDeviceRequest, AutoStopRequest, canlog_generate_script(), CanLogScriptRequest, connect(), ConnectRequest, IsoTpSendRequest (+26 more)

### Community 12 - "App.tsx"
Cohesion: 0.08
Nodes (28): WS_URL, connectWebSocket(), App(), CanConfig, DEFAULT_CAN_CONFIG, DEFAULT_CHANNEL_BY_IFACE, freeCompactor, LegacySavedLayout (+20 more)

### Community 13 - "CanStore"
Cohesion: 0.10
Nodes (3): SettingsModal(), CanStore, BackendStatus

### Community 14 - "registry.tsx"
Cohesion: 0.21
Nodes (30): findSignal(), signalBitMax(), signalRawBounds(), useApp(), ButtonWidget(), CheckboxWidget(), DropdownWidget(), ManualValueWidget() (+22 more)

### Community 15 - "package.json"
Cohesion: 0.07
Nodes (29): dependencies, react, react-dom, react-grid-layout, devDependencies, oxlint, @types/node, @types/react (+21 more)

### Community 16 - "canStore.ts"
Cohesion: 0.14
Nodes (21): api, post(), request(), upload(), ActivityEntry, formatTestRunnerEvent(), LastValidSignal, useCanVersion() (+13 more)

### Community 17 - "test_syslog_service.py"
Cohesion: 0.14
Nodes (29): build_series(), log ID별로 원본 순서를 유지한 채 그룹핑한다. x좌표는 전체 스트림 기준으로 한 번만 계산한 전역…, SysLogRecord, _load_real_service(), _mk(), 범위를 벗어나는 조합(NaN, Inf, 양수, -120 미만)은 -120으로, 0000 0000은 0으로., 레코드 수가 홀수이면 마지막 하나는 무시된다., 1ms 이내 2개가 아니면 단일로 무시, 정상 페어는 변환. (+21 more)

### Community 18 - "test_tx_scheduler.py"
Cohesion: 0.21
Nodes (28): collect(), A widget sending one periodic signal (send_signal, as above) arms its own…, The reported bug: pressing "Enable Msg" then off again used to be the only way…, Global Start/Stop calls stop_auto() with no args -- must also reset the "Enable…, setup_stack(), teardown_stack(), test_disable_all_periodic_does_not_stop_widget_armed_signal(), test_enable_all_periodic_sets_flag_and_disable_all_clears_only_those() (+20 more)

### Community 19 - "post"
Cohesion: 0.07
Nodes (28): audio_monitor_start(), audio_monitor_stop(), audio_record_stop(), audio_recording_stop(), log_start(), log_stop(), ota_tester_cases_clear(), ota_tester_stop() (+20 more)

### Community 20 - "OtaTesterDownloadManager"
Cohesion: 0.11
Nodes (9): _extract_comm_timing(), OtaTesterDownloadManager, Look for a `startCommunication` step among a case's parsed steps and return its…, Manage OTA Tester execution across an ordered list of cases., Parse one test-rule XML file and add/replace it as a case., Return the parsed steps for one case, for the per-command checklist UI (mirrors…, selected_steps: None = all steps run (default); [] = none; [i, ...] = only…, Flow Control STmin, in priority order: this step's own XML localSTMinTx… (+1 more)

### Community 21 - "test_uds_download_manager.py"
Cohesion: 0.12
Nodes (15): _manager(), Tests for uds_download_manager.py's NRC 0x78 (ResponsePending) handling. Bug…, The suppress bit only means "no *positive* response" -- an actual negative…, No suppress bit (0x01, not 0x81) -> a timeout is a real failure, same as before…, The extended wait must come from whatever the loaded XML actually specifies,…, test_non_suppress_request_still_fails_on_timeout(), test_suppress_bit_does_not_forgive_an_actual_negative_response(), test_suppress_bit_timeout_is_success_not_failure() (+7 more)

### Community 22 - "test_test_runner_service.py"
Cohesion: 0.10
Nodes (9): drain(), test_canlogreplay_excludes_sender_node(), test_canlogreplay_sends_logged_frames(), test_canreq_converts_raw_hex_to_scaled_value(), test_canresp_pass_when_peer_replies_in_time(), test_loop_repeats_exact_count(), test_multi_signal_canreq(), test_start_function_runs_only_that_case() (+1 more)

### Community 23 - "SysLogAnalysisWidget.tsx"
Cohesion: 0.11
Nodes (24): SysLogIdInfo, SysLogPoint, SysLogScriptResult, SysLogSeries, SysLogStatus, SysLogTimeline, SysLogTimelineSegment, downloadTextFile() (+16 more)

### Community 24 - "test_ota_tester_download_manager.py"
Cohesion: 0.13
Nodes (20): iter_transfer_chunks(), Yield (block_sequence_number, offset, chunk_bytes) tuples covering…, _mgr(), Tests for ota_tester_download_manager.py. Covers: PDU param-name correctness…, Regression: nearly every real GITAuto export has a localSTMinTx attribute…, Matches every real test-rule sample seen in practice -- no timing config in the…, _SpyAutoLogger, test_add_case_keeps_defaults_when_no_startcommunication_step() (+12 more)

### Community 25 - "uds_core.py"
Cohesion: 0.09
Nodes (23): build_request_transfer_exit(), ext_id(), func_req(), is_positive_response(), is_response_pending(), parse_did_response(), parse_negative_response(), parse_routine_control_response() (+15 more)

### Community 26 - "OtaTesterWidget.tsx"
Cohesion: 0.13
Nodes (22): OtaTesterCase, OtaTesterStatus, OtaTesterStepInfo, SERVICE_DISPLAY_NAMES, UdsDownloadStatus, UdsStepInfo, buildFileIndex(), FileIndexEntry (+14 more)

### Community 27 - "._uds_request_with_retry"
Cohesion: 0.14
Nodes (11): Any, Execute a single step. Returns True on success (including an expected negative…, Safely convert a hex string ("0x..") or plain int/str from an XML attribute…, Send binary[seekAddress : seekAddress+writeSize] in blocks., _to_int(), build_tester_present(), parse_request_download_response(), UDS TesterPresent (0x3E). (+3 more)

### Community 28 - "appContext.ts"
Cohesion: 0.19
Nodes (18): groupedMessages(), MessageGroups, signalBitMin(), sortedMessages(), DbcMessage, DbcSignal, SignalBinding, TxRow (+10 more)

### Community 29 - "CanManager"
Cohesion: 0.14
Nodes (14): CanManager, CAN bus connection and RX buffering. Supported interfaces: pcan (PEAK PCAN),…, Whether Message.timestamp on this connection is wall-clock epoch seconds…, PCAN-FD 연결 시 사용하는 고정 레지스터 값(FD_CLOCK_HZ/FD_NOM_*/FD_DATA_*)이 실제로 의도한…, virtual (and Vector) timestamps are always wall-clock epoch seconds, unlike…, HS-CAN(classic) 연결에서는 행/DBC의 FD 체크가 켜져 있어도 실제로는 classic 프레임으로 나가야 한다 -- 연결 설정이…, test_classic_bus_forces_classic_frame_even_if_fd_requested(), test_classic_bus_rejects_oversized_payload() (+6 more)

### Community 30 - "UploadFile"
Cohesion: 0.15
Nodes (22): canlog_upload(), ota_tester_case_binary_upload(), ota_tester_case_xml_upload(), Path, Load an XML procedure file into a slot (0/1/2)., Load a BIN file into a UDS slot (0/1/2)., Load the real HKMC Advanced SeedKey DLL (Windows only) so SecurityAccess…, Load one hook/testBlock's test-rule XML as a case in the run sequence.… (+14 more)

### Community 31 - "TestRunnerService"
Cohesion: 0.20
Nodes (4): Any, 일시정지 중이면 재개/중지될 때까지 대기. 중지되면 True 반환., Signal=Value" (or comma-joined for a multi-signal "Signals" block), for the…, TestRunnerService

### Community 32 - "UdsProcedure"
Cohesion: 0.13
Nodes (15): _FakeNotifier, Bug report: a procedure with TWO diagnosticSessionControl steps (each offering…, UI feature: highlight the currently-executing step in the checklist. _run_steps…, [3E 80] (suppress positive response) -- sent on the functional broadcast ID…, test_progress_current_step_idx_tracks_the_running_step_and_resets_when_done(), test_run_auto_logs_failure_on_uds_error(), test_run_auto_logs_success_with_xml_stem_label(), test_second_diagnostic_session_control_step_uses_its_own_session_choice() (+7 more)

### Community 33 - "_timed_lock"
Cohesion: 0.11
Nodes (9): threading.Lock/RLock context manager that logs how long the caller waited to…, self._stream.stop()/close() -- both are blocking PortAudio calls that wait for…, Start a test-runner recording. If the 오디오 신호 모니터 widget already has a monitor-…, Best-effort, bounded-time stream close for app shutdown (`main.py`'s lifespan).…, Start (or piggyback on an already-open) live level stream for the 모니터 widget.…, Stop the monitor-only stream. A no-op (not an error) if nothing is open, and…, 오디오 신호 모니터 위젯의 Record 버튼. 이미 위젯이 연 모니터 스트림이 있으면 그 자리에서 녹음으로 업그레이드(재오픈 없이); 테스트…, 위젯의 Record 중지 -- 위젯이 직접 시작한 녹음일 때만 저장하고 닫는다 (테스트 러너의 녹음은 절대 건드리지 않는다). (+1 more)

### Community 34 - "isotp_service.py"
Cohesion: 0.18
Nodes (20): _build_fc(), _fd_frame_len(), IsoTpError, _pad(), _pad_fd(), Exception, Message, ISO-TP (ISO 15765-2) transport-layer sender/receiver, classic addressing.… (+12 more)

### Community 35 - "_require_running"
Cohesion: 0.10
Nodes (21): EnablePeriodicRequest, FunctionStartRequest, GenerateSendRequest, InvalidSendRequest, ota_tester_start(), OtaTesterStartRequest, Start OTA Tester procedure., TX box row's Send button, and live edits to a row's data field while the list… (+13 more)

### Community 36 - "ota_tester_download_manager.py"
Cohesion: 0.13
Nodes (18): OTA Tester Download Manager. Executes an ordered list of "cases" -- each parsed…, Best-effort PDU bytes for the checklist UI, built with the exact same param…, Dispatch to the appropriate builder for the "simple" services…, build_communication_control(), build_control_dtc_setting(), build_diagnostic_session(), build_ecu_reset(), build_read_data_by_id() (+10 more)

### Community 37 - "PowerSupplyService"
Cohesion: 0.15
Nodes (10): PowerSupplyService, Runs a PyVISA call (write/query/close) with timing -- logs (rate- limited) if…, Legacy entry point for the test-runner's Power step (JSON script `{"type":…, 전원 컨트롤 위젯의 ACC/IGN 토글 스위치., test_connect_without_hardware_degrades_gracefully(), test_info_includes_acc_ign_booleans(), test_onoff_repeat_rejected_when_not_connected(), test_set_battery_rejected_when_not_connected() (+2 more)

### Community 38 - "AutoCanLogger"
Cohesion: 0.14
Nodes (11): AutoCanLogger, Record live CAN traffic to a Vector .blf log file. Attaches an extra…, ``label``: short identifier for this run (e.g. the slot's loaded XML filename…, Per-execution-unit ASCII (Vector .asc, human-readable) CAN log, auto…, Path, test_auto_can_logger_double_start_is_noop(), test_auto_can_logger_renames_on_failure(), test_auto_can_logger_sanitizes_unsafe_label_characters() (+3 more)

### Community 39 - "test_power_supply_service.py"
Cohesion: 0.18
Nodes (17): _connected(), _FakeInst, Off phase now applies a user-configured off_voltage/off_current (not hardcoded…, test_disconnect_stops_both_auto_modes(), test_onoff_repeat_flips_phase_after_duration_elapses(), test_onoff_repeat_rejected_while_sweep_active(), test_onoff_repeat_rejects_non_positive_durations(), test_onoff_repeat_starts_in_on_phase_immediately() (+9 more)

### Community 40 - "displays.tsx"
Cohesion: 0.16
Nodes (16): FrameEntry, RxFrame, CanMessageDisplay(), FixedTable(), fmtClock(), fmtData(), fmtId(), fmtSignalValue() (+8 more)

### Community 41 - "compilerOptions"
Cohesion: 0.10
Nodes (19): compilerOptions, allowArbitraryExtensions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection (+11 more)

### Community 42 - "_ChannelLevelTracker"
Cohesion: 0.11
Nodes (14): _ChannelLevelTracker, Rolling level state for one captured channel. add_chunk() runs on the…, samples: one callback's worth of one channel, float32 in [-1, 1]. now:…, Shallow copy of the raw ring buffer -- O(number of chunks) but only copying…, Up to max_points {t, min, max} columns (epoch seconds, [-1,1]) covering…, test_channel_level_tracker_computes_peak_and_rms(), test_channel_level_tracker_ignores_empty_chunk(), test_channel_level_tracker_reset_clears_state() (+6 more)

### Community 43 - "_broadcast_loop"
Cohesion: 0.11
Nodes (18): _broadcast(), _broadcast_loop(), disconnect(), _frame_to_dict(), lifespan(), 가능한 범위에서 정리(lifespan의 종료 블록과 동일한 순서)한 뒤 프로세스를 종료한다. 응답을 먼저 흘려보낼 시간을 준 뒤(0.3초)…, _run_bounded(), run_start() (+10 more)

### Community 44 - "SeedKeyService"
Cohesion: 0.13
Nodes (9): AdvancedSeedKeyClient, Exception, HKMC Advanced SeedKey PC client wrapper. Wraps HKMC_AdvancedSeedKey_Win32.dll…, Real vendor algorithm. Raises RuntimeError if no DLL is loaded -- callers…, Loads one HKMC_AdvancedSeedKey_*.dll and exposes ASK_KeyGenerate. Exported…, Holds the currently-loaded SeedKey DLL client. One shared algorithm per running…, SeedKeyError, SeedKeyService (+1 more)

### Community 45 - "test_syslog_script_generator.py"
Cohesion: 0.37
Nodes (17): generate_can_test_script(), build_global_timeline(), 전체 레코드 스트림을 원본(seq) 순서로 훑으며 세그먼트 이어붙이기 x좌표(plot_x)를 한 번만 계산한다. 반환값은 (seq ->…, _fixed_send_type(), _mk(), test_generate_script_basic_flow_with_delay_and_step_types(), test_generate_script_does_not_merge_different_messages_even_at_same_time(), test_generate_script_does_not_merge_same_message_when_delay_nonzero() (+9 more)

### Community 46 - "SysLogService"
Cohesion: 0.16
Nodes (9): log 파일 1개 + DB 파일 1개만 세션에 유지하는 교체 방식 상태 저장소., 세그먼트마다 끝(plot_x_end/abs_ms_end -- 다음 세그먼트 시작 바로 전, 마지막 세그먼트는 plot_x_max)을 계산해…, ID name 알파벳순(대소문자 무시) 정렬. 이름이 같으면(드묾) ID로 타이브레이크(프론트가 이름순/ID순을 다시 고를 수 있어 이 순서…, 전역 plot_x 좌표 공간의 세그먼트 경계 + 전체 범위. 프론트가 모든 그래프의 기본(전체 범위) 뷰와 x축 눈금의 실제…, 체크된 시간 구간 안, log ID 0~399 범위 레코드를 DBC 신호와 매칭해 CANReq/CANEv 시나리오 JSON(steps)을…, SysLogService, test_service_list_ids_sorted_alphabetically(), test_service_timeline_reflects_real_syslog_bin() (+1 more)

### Community 47 - "UdsStep"
Cohesion: 0.17
Nodes (16): find_step(), get_step_params(), _int_hex(), _parse_rule_section(), _parse_step(), parse_xml(), UDS Software Download XML Procedure Parser. Parses the xfrm-format XML files…, Parse a single step element (e.g. <xfrm:diagnosticSessionControl>). (+8 more)

### Community 48 - "compilerOptions"
Cohesion: 0.12
Nodes (16): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, noEmit, noFallthroughCasesInSwitch (+8 more)

### Community 49 - "MultiUdsDownloadManager"
Cohesion: 0.13
Nodes (9): MultiUdsDownloadManager, resolve(), _run_sequence(), Path, Manages 3 independent UDS download slots (file 01/02/03)., Return status for all 3 slots., Return procedure steps for all 3 slots., Start specified slots sequentially: slot N+1 only starts once slot N has… (+1 more)

### Community 50 - "audio_service.py"
Cohesion: 0.30
Nodes (12): compare_channel(), _corrcoef_safe(), _cross_corr_similarity(), _ensure_compare_deps(), _fft_band_similarity(), _fft_similarity(), _match_length(), _mfcc_dtw_similarity() (+4 more)

### Community 51 - "xlsx_to_script.py"
Cohesion: 0.26
Nodes (13): testrunner_upload_script(), _build_step(), convert(), _count_leaf_steps(), main(), _num_str(), Convert a "CAN Test Script Editor" xlsx (see CAN_Test_Script_Editor_Rev01.xlsx)…, Parse the converted JSON with the actual backend parser, so a structural… (+5 more)

### Community 52 - "uds_download_manager.py"
Cohesion: 0.18
Nodes (11): RequestSeed -> generate key (SeedKey DLL or dummy) -> SendKey. The test-rule…, build_security_access_request_seed(), build_security_access_send_key(), generate_key(), Exception, UDS error with optional NRC code., Request seed. ``access_mode`` is the securityAccessType sub-function (an odd…, Send key. ``access_mode`` is the securityAccessType sub-function (an even… (+3 more)

### Community 53 - "ReplayService"
Cohesion: 0.21
Nodes (5): CAN log replay (BLF / ASC). Loads a log file into memory, then replays it on…, Start replay with a message filter. mode "pass": replay only the frames whose…, ReplayService, fixture, stack()

### Community 54 - "test_runner_service.py"
Cohesion: 0.22
Nodes (13): Case, _parse_boundary_blocks(), parse_functions(), parse_script(), _parse_step_list(), Test scenario runner: interprets a JSON step script (ported from…, Shared by parse_script (ID/num) and parse_functions (FUNC/name) -- both split a…, Step (+5 more)

### Community 55 - "syslog_service.py"
Cohesion: 0.17
Nodes (10): _is_valid_float(), _pair_to_float(), parse_db(), sysLog 분석 위젯 백엔드: 바이너리 sysLog 파일 + logDB(ID;NAME) 텍스트 파일을 파싱해 log ID별 시계열을 만든다.…, `ID;NAME` 형식 한 줄씩. NAME이 비어있는 줄도 그대로(빈 문자열) 반영한다., 직전 레코드(prev) 대비 현재 레코드(rec)가 새 세그먼트를 열어야 하는지 판단한다(Requirement.md "후속 보완 7" 참고).…, 두 uint16 값을 big-endian IEEE 754 float32로 변환한다. 0x0000 0x0000 → 0.0, 범위 밖(NaN,…, _starts_new_segment() (+2 more)

### Community 56 - "test_replay_service.py"
Cohesion: 0.29
Nodes (11): drain(), Path, 10 frames over ~0.45 s; ids alternate 0x100 / 0x101., test_blf_load_and_replay(), test_invalid_mode(), test_replay_no_filter_sends_all(), test_replay_pass_filter_selected_only(), test_replay_preserves_fd_frames() (+3 more)

### Community 57 - "fake_send"
Cohesion: 0.15
Nodes (6): Regression for "TransferData 도중 Stop을 눌러도 수십초 후에야 멈춘다": with max_retries=None…, test_uds_request_with_retry_does_not_retransmit_on_nrc78(), fake_send(), test_uds_request_with_retry_no_send_floor_when_stmin_checkbox_off(), test_uds_request_with_retry_passes_configured_stmin_as_send_floor(), test_uds_request_with_retry_stop_event_interrupts_unlimited_pending_wait()

### Community 58 - "._uds_request"
Cohesion: 0.15
Nodes (11): expects_no_response(), parse_response(), Parse a UDS response, returning a dict with 'positive', 'sid', 'nrc', 'data'., Synthetic success result for a suppress-bit request that correctly got no…, True if `request` is a subfunction-based UDS request with the Suppress Positive…, suppressed_response_result(), _is_timeout_error(), Exception (+3 more)

### Community 59 - "._apply_battery"
Cohesion: 0.18
Nodes (4): Shared low-level write for every voltage-setting path (manual OK button, on/off…, 전원 컨트롤 위젯의 전압/전류 입력 + OK 버튼., Runs for the process lifetime; cheap no-op tick when neither auto mode is…, Exposed with an optional `now` override so tests can drive phase transitions…

### Community 60 - "find_matching_signal"
Cohesion: 0.17
Nodes (11): build_signal_index(), find_matching_signal(), sysLog 기록을 CAN 테스트 시나리오(test_script_Rev01.json 호환 .json)로 변환한다. Requirement.md…, DBC summary(dbc_service.summary()["messages"])에서 (message_name, signal_name) 쌍…, 반환: ((message_name, signal_name) | None, 정확히 일치했는가). 못 찾으면 (None, False)., ScriptGenerationResult, test_find_matching_signal_ambiguous_exact_across_messages_falls_back_to_prefix(), test_find_matching_signal_ambiguous_prefix_fails() (+3 more)

### Community 61 - "runner_with_fakes"
Cohesion: 0.17
Nodes (5): FakeAudio, FakePower, fixture, runner_with_fakes(), stack()

### Community 62 - "diag_log.py"
Cohesion: 0.20
Nodes (9): Rate-limited WARNING logging shared by the Windows audio/CAN-lag diagnostic…, Returns -1 if this occurrence should be suppressed (too soon after the last log…, Formats should_log()'s suppressed-count into a log-message suffix (empty string…, should_log(), suffix(), _diag_timing_middleware(), Diagnostic-only: logs any request that takes longer than _SLOW_REQUEST_MS, with…, Programmable DC power supply control (PyVISA/SCPI), ported from… (+1 more)

### Community 63 - "LogService"
Cohesion: 0.22
Nodes (6): LogService, Path, fixture, stack(), test_start_requires_connection(), test_status_while_idle()

### Community 64 - "_FakeStream"
Cohesion: 0.18
Nodes (8): _FakeStream, Plain Start (monitor, no WAV) must set the x-axis anchor exactly like Record…, start_monitor() on an already-open stream (e.g. a recording already in…, test_monitor_only_stream_also_gets_a_stream_started_at(), test_start_monitor_piggyback_does_not_reset_existing_anchor(), test_start_widget_recording_sets_stream_started_at(), test_stop_clears_stream_started_at(), test_stop_monitor_clears_stream_started_at()

### Community 65 - "test_uds_request_registers_listener_before_sending"
Cohesion: 0.18
Nodes (4): _OrderTrackingNotifier, Records add_listener/remove_listener calls (interleaved with fake send/receive…, test_uds_request_registers_listener_before_sending(), test_uds_request_with_retry_registers_listener_before_sending()

### Community 66 - "_BufferListener"
Cohesion: 0.20
Nodes (4): _BufferListener, Exception, Message, deque

### Community 68 - "stereo"
Cohesion: 0.43
Nodes (8): ndarray, stereo(), test_compare_identical_signal_passes(), test_compare_returns_all_seven_metrics(), test_compare_silence_against_tone_fails(), test_rotate_segment_writes_current_file_and_starts_a_new_one(), test_save_as_golden_copies_file(), tone()

### Community 69 - "IsoTpBox.tsx"
Cohesion: 0.43
Nodes (7): framePreview(), getDataPart(), isCommentLine(), IsoTpBox(), IsoTpOptions, parseHexBytes(), splitLines()

### Community 71 - "ota_tester_case_selected_steps"
Cohesion: 0.29
Nodes (7): ota_tester_case_selected_steps(), OtaTesterCaseSelectedStepsRequest, Update parameters for a specific step on a specific slot., Set which step indices within a case run (None = all, [] = none)., UdsSwdlParamRequest, udswdl_set_params(), put

### Community 72 - "_CountingWriter"
Cohesion: 0.33
Nodes (3): _CountingWriter, Message, Wraps a BLFWriter so status() can report how many frames were logged without…

### Community 73 - ".oxlintrc.json"
Cohesion: 0.33
Nodes (5): plugins, rules, react/only-export-components, react/rules-of-hooks, $schema

### Community 75 - "decode_stmin"
Cohesion: 0.50
Nodes (3): decode_stmin(), STmin byte -> seconds. 0x00-0x7F = 0-127 ms, 0xF1-0xF9 = 100-900 us. Public…, Minimum inter-CF delay (seconds) to force on our own multi-frame *sends* (e.g.…

### Community 76 - "parse_log"
Cohesion: 0.40
Nodes (4): parse_log(), 8바이트씩 빅엔디안으로 파싱한다. 끝에 8바이트 미만이 남으면(잘린 파일) 그 나머지는 조용히 버린다 -- 파일 끝단의 자연스러운 경계…, test_parse_log_matches_known_records(), test_parse_log_truncated_trailing_bytes_ignored()

### Community 79 - "generate_monitor_filename"
Cohesion: 0.50
Nodes (4): generate_monitor_filename(), Timestamp-based filename for a 오디오 신호 모니터 recording (or one 30-minute segment…, audio_record_start(), 오디오 신호 모니터 위젯의 Record 버튼 -- 파형을 보여주는 동시에 WAV로 저장한다. 파일명은 타임스탬프로 자동 생성되고, 30분마다…

### Community 81 - "_SuppressNoisyAccessLog"
Cohesion: 0.50
Nodes (3): The frontend polls /api/testrunner/status every 400ms while the app is open…, _SuppressNoisyAccessLog, LogRecord

## Knowledge Gaps
- **123 isolated node(s):** `CanConfig`, `PageTabsProps`, `SavedFile`, `SavedLayout`, `ActivityEntry` (+118 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 595 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OtaTesterDownloadManager` connect `OtaTesterDownloadManager` to `ota_tester_download_manager.py`, `AutoCanLogger`, `main.py`, `fake_receive`, `_FakeNotifier`, `decode_stmin`, `test_uds_request_with_retry_stop_event_interrupts_unlimited_pending_wait`, `test_uds_request_with_retry_uses_the_xml_derived_p2_star_value`, `uds_download_manager.py`, `test_ota_suppress_bit_timeout_is_success_not_failure`, `test_transfer_data_sends_tester_present_keepalive_periodically`, `test_uds_request_with_retry_reuses_same_reader_across_pending_retries`, `test_ota_tester_download_manager.py`, `._uds_request_with_retry`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Why does `AudioService` connect `AudioService` to `_FakeStream`, `_timed_lock`, `stereo`, `.refresh_devices`, `main.py`, `._rotate_segment`, `audio_service.py`, `.get_waveform`?**
  _High betweenness centrality (0.061) - this node is a cross-community bridge._
- **Why does `PowerSupplyService` connect `PowerSupplyService` to `main.py`, `test_power_supply_service.py`, `test_status_bitmask_logic_matches_apptest_py`, `test_batt_command_writes_apply`, `._apply_battery`, `diag_log.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `OtaTesterDownloadManager` (e.g. with `AutoCanLogger` and `UdsError`) actually correct?**
  _`OtaTesterDownloadManager` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `UdsDownloadManager` (e.g. with `AutoCanLogger` and `UdsError`) actually correct?**
  _`UdsDownloadManager` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `CanConfig`, `PageTabsProps`, `SavedFile` to the rest of the system?**
  _123 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `DbcService` be split into smaller, more focused modules?**
  _Cohesion score 0.06077694235588972 - nodes in this community are weakly interconnected._