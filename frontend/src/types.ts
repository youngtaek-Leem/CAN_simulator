// Shared types between API layer, stores and widgets.

export interface DbcSignal {
  name: string;
  start: number;
  length: number;
  is_signed: boolean;
  scale: number;
  offset: number;
  minimum: number | null;
  maximum: number | null;
  unit: string | null;
  choices: Record<number, string> | null;
  send_type: 'event' | 'periodic';
  invalid_raw: number;
}

export interface DbcMessage {
  name: string;
  frame_id: number;
  senders: string[];
  is_extended: boolean;
  is_fd: boolean;
  length: number;
  cycle_time_ms: number | null;
  send_type: string;
  comment: string | null;
  signals: DbcSignal[];
}

export interface DbcSummary {
  loaded: boolean;
  filename?: string;
  nodes?: string[];
  messages?: DbcMessage[];
}

export interface RxFrame {
  ts: number;
  id: number;
  ext: boolean;
  dlc: number;
  data: string;
  fd: boolean;
  brs: boolean;
  decoded?: {
    name: string;
    signals: Record<string, number | string>;
    valid_signals: string[];
  };
}

export interface FrameEntry extends RxFrame {
  count: number;
  cycleMs: number | null;
}

export interface BackendStatus {
  can: { connected: boolean; config: Record<string, unknown>; counters: { rx: number; tx: number; errors: number } };
  tx: {
    running: boolean;
    // "Enable Msg" bulk toggle's own on/off state -- independent of whether
    // any auto_entries exist, since a widget sending one periodic signal
    // also creates an auto_entries entry on its own (see tx_scheduler.py's
    // _enable_msg_armed).
    periodic_enabled: boolean;
    entries: {
      key: string;
      arbitration_id: number;
      period_ms: number;
      enabled: boolean;
      message_name: string | null;
      is_fd: boolean;
      bitrate_switch: boolean;
      tx_count: number;
    }[];
    auto_entries: { message_name: string; period_ms: number; tx_count: number }[];
  };
  replay: {
    loaded: boolean;
    filename: string | null;
    message_count: number;
    tx_count: number;
    rx_count: number;
    duration_s: number;
    progress: { sent: number; skipped: number; total: number; running: boolean };
  };
  dbc: { loaded: boolean; filename: string | null };
  settings: { ws_flush_ms: number };
  run: { running: boolean };
  test_runner: TestRunnerSummary;
  ota_tester: OtaTesterStatus;
  power: PowerStatus;
  audio: AudioStatus;
  log: LogStatus;
}

export interface LogStatus {
  recording: boolean;
  filename: string | null;
  count: number;
  duration_s: number;
}

export interface PowerOnOffState {
  enabled: boolean;
  on_voltage: number;
  on_current: number;
  on_s: number;
  off_voltage: number;
  off_current: number;
  off_s: number;
  phase: 'on' | 'off';
}

export interface PowerSweepState {
  enabled: boolean;
  low: number;
  high: number;
  current: number;
  leg_s: number;
}

export interface PowerStatus {
  initialized: boolean;
  error: string | null;
  status_bits: number;
  acc: boolean;
  ign: boolean;
  battery_voltage: number;
  battery_current: number;
  onoff: PowerOnOffState;
  sweep: PowerSweepState;
}

export interface AudioDevice {
  index: number;
  name: string;
  channels: number;
}

export interface AudioStatus {
  initialized: boolean;
  error: string | null;
  device_index: number | null;
  devices: AudioDevice[];
  recording: boolean;
}

export interface AudioChannelLevel {
  index: number;
  peak: number;
  rms: number;
}

export interface AudioLevel {
  active: boolean;
  recording: boolean;
  monitoring: boolean;
  owner: 'monitor' | 'recording' | 'widget_record' | null;
  channels: AudioChannelLevel[];
  stream_started_at: number | null; // epoch seconds the current stream (Start OR Record) began; waveform x-axis "0s" reference
  current_filename: string | null; // current segment's filename while recording
}

export interface AudioWaveformPoint {
  t: number; // epoch seconds
  min: number;
  max: number;
}

export interface AudioWaveformChannel {
  index: number;
  points: AudioWaveformPoint[];
}

export interface AudioWaveform {
  active: boolean;
  samplerate: number | null;
  channels: AudioWaveformChannel[];
}

export interface TestRunnerSummary {
  loaded: boolean;
  filename: string | null;
  running: boolean;
  running_case: string | null;
  case_count: number;
  result_count: number;
  functions: TestRunnerFunctionsSummary;
}

export interface TestRunnerFunctionsSummary {
  loaded: boolean;
  filename: string | null;
  names: string[];
}

export interface TestRunnerEvent {
  ts: number;
  case?: string;
  type?: string;
  message?: string;
  signal?: string;
  status?: string;
  msg?: string;
}

export interface TestRunnerResult {
  case: string;
  cycle: number;
  status: 'OK' | 'Fail';
}

export interface TestRunnerStatus extends TestRunnerSummary {
  events: TestRunnerEvent[];
  results: TestRunnerResult[];
}

export interface UdsProgress {
  current_step: string;
  total_blocks: number;
  current_block: number;
  percent: number;
  phase: string;
  current_step_idx: number;
}

export interface UdsCommInfo {
  request_id: number;
  response_id: number;
}

export interface SeedKeyVersionInfo {
  vendor_id: number;
  module_id: number;
  version: string;
}

export interface SeedKeyStatus {
  loaded: boolean;
  filename: string | null;
  version: SeedKeyVersionInfo | null;
}

export interface UdsDownloadStatus {
  state: string;
  running: boolean;
  procedure_loaded: boolean;
  xml_filename: string | null;
  binary_loaded: boolean;
  binary_filename: string | null;
  binary_size: number;
  progress: UdsProgress;
  events: UdsEvent[];
  error: string | null;
  comm_info: UdsCommInfo | null;
}

export interface UdsEvent {
  ts: number;
  level?: string;
  service?: string;
  msg?: string;
}

export interface UdsStepInfo {
  rule: string;
  phase: string;
  service: string;
  params: Record<string, string>;
  sub_steps: UdsSubStep[];
}

export interface UdsSubStep {
  service: string;
  params: Record<string, string>;
}

export interface UdsSlotStatus {
  slot_index: number;
  success: boolean;
  error?: string;
  status?: UdsDownloadStatus;
}

// ---- OTA Tester types (folder-driven, GITAuto test-rule XML format) ----

export interface OtaTesterCase {
  id: string;
  label: string;
  kind: 'hook' | 'testBlock' | 'manual';
  order: number;
  enabled: boolean;
  total_steps: number;
  binary_loaded: boolean;
  binary_filename: string | null;
  binary_path_hint: string | null;
  selected_steps: number[] | null;
}

export interface OtaTesterStepInfo {
  service: string;
  params: Record<string, string>;
  sub_steps: { service: string; params: Record<string, string> }[];
  confirm_positive_response: boolean;
  pdu_preview: string | null;
  pdu_note: string | null;
}

export interface OtaTesterProgress {
  current_step: string;
  total_blocks: number;
  current_block: number;
  percent: number;
}

export interface OtaTesterStatus {
  state: string;
  running: boolean;
  cases: OtaTesterCase[];
  total_cases: number;
  current_case_index: number;
  current_case_label: string | null;
  current_step_index: number;
  total_steps_in_case: number;
  progress: OtaTesterProgress;
  events: UdsEvent[];
  error: string | null;
}

export interface SysLogStatus {
  log_filename: string | null;
  db_filename: string | null;
  record_count: number;
  filename?: string;
  entry_count?: number;
}

export interface SysLogIdInfo {
  id: number;
  name: string;
  count: number;
}

export interface SysLogPoint {
  seq: number;
  x_ms: number;
  value: number;
  day: number;
  hour: number;
  minute: number;
  ms: number;
}

export interface SysLogSeries {
  name: string;
  count: number;
  points: SysLogPoint[];
}

export interface SysLogTimelineSegment {
  plot_x_start: number;
  abs_ms_start: number;
  plot_x_end: number;
  abs_ms_end: number;
}

export interface SysLogTimeline {
  segments: SysLogTimelineSegment[];
  plot_x_min: number;
  plot_x_max: number;
}

export interface SysLogScriptWarning {
  log_id: number;
  log_name: string;
  matched_message: string;
  matched_signal: string;
}

export interface SysLogScriptError {
  log_id: number;
  log_name: string | null;
  reason: string;
}

export interface SysLogScriptResult {
  steps: Record<string, unknown>[];
  warnings: SysLogScriptWarning[];
  errors: SysLogScriptError[];
  matched_count: number;
}

export const SERVICE_DISPLAY_NAMES: Record<string, string> = {
  startCommunication: 'CAN 통신 시작',
  stopCommunication: 'CAN 통신 종료',
  diagnosticSessionControl: '진단 세션 전환',
  securityAccess: '보안 액세스',
  routineControl: '루틴 제어',
  requestDownload: '다운로드 요청',
  transferData: '데이터 전송',
  requestTransferExit: '전송 종료 요청',
  ecuReset: 'ECU 리셋',
  controlDTCSetting: 'DTC 설정 제어',
  communicationControl: '통신 제어',
  readDataByIdentifier: 'DID 읽기',
  completeDecision: '완료 판정',
};

export type WidgetType =
  | 'rxSignalDisplay'
  | 'canMessageDisplay'
  | 'textDisplay'
  | 'button'
  | 'checkbox'
  | 'dropdown'
  | 'slider'
  | 'manualValue'
  | 'txBox'
  | 'replayBox'
  | 'multiButton'
  | 'multiCheckbox'
  | 'multiDropdown'
  | 'multiSlider'
  | 'multiManualValue'
  | 'isotpTx'
  | 'signalGraph'
  | 'testRunner'
  | 'functionButton'
  | 'randomButton'
  | 'functionMultiButton'
  | 'randomMultiButton'
  | 'udsSwdl'
  | 'otaTester'
  | 'audioMonitor'
  | 'powerControl'
  | 'canAudioLatency'
  | 'sysLogAnalysis';

export interface SignalBinding {
  message: string;
  signal: string;
}

export interface MultiCell {
  binding?: SignalBinding;
  label?: string;
  value?: number;
  onValue?: number;
  offValue?: number;
  funcName?: string;
  mode?: 'random' | 'range';
  rangeMin?: number;
  rangeMax?: number;
  step?: number;
  sliderMin?: number;
  sliderMax?: number;
  sliderStep?: number;
  sliderDefault?: number;
  inputDefault?: string;
  // Runtime values the user set through the cell itself (not its config
  // modal) -- persisted here (not local useState) so they survive switching
  // to another page and back; App.tsx only mounts the active page's
  // widgets, so anything kept only in local useState resets on remount.
  checked?: boolean;
  selectedRaw?: string;
  sliderCurrent?: number;
  inputCurrent?: string;
}

export interface WidgetConfig {
  id: string;
  type: WidgetType;
  title: string;
  binding?: SignalBinding;
  options: Record<string, unknown>;
}

export interface TxRow {
  key: string;
  idHex: string;
  periodMs: number;
  dataHex: string;
  messageName: string | null;
  enabled: boolean;
  periodic: boolean;
  isFd: boolean;
  bitrateSwitch: boolean;
}