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
  senders: string[]; // DBC node(s) that transmit this message
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
  data: string; // hex
  fd: boolean;
  brs: boolean; // bitrate switch (CAN-FD data phase)
  decoded?: { name: string; signals: Record<string, number | string> };
}

export interface FrameEntry extends RxFrame {
  count: number;
  cycleMs: number | null;
}

export interface BackendStatus {
  can: { connected: boolean; config: Record<string, unknown>; counters: { rx: number; tx: number; errors: number } };
  tx: {
    running: boolean;
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
  power: PowerStatus;
  audio: AudioStatus;
}

export interface PowerStatus {
  initialized: boolean;
  error: string | null;
  status_bits: number;
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

export type WidgetType =
  | 'canMessageDisplay'
  | 'textDisplay'
  | 'button'
  | 'checkbox'
  | 'dropdown'
  | 'slider'
  | 'txBox'
  | 'replayBox'
  | 'multiButton'
  | 'multiCheckbox'
  | 'isotpTx'
  | 'signalGraph'
  | 'testRunner'
  | 'functionButton'
  | 'randomButton'
  | 'functionMultiButton'
  | 'randomMultiButton';

export interface SignalBinding {
  message: string;
  signal: string;
}

// One cell of a multiButton / multiCheckbox / functionMultiButton /
// randomMultiButton grid widget.
export interface MultiCell {
  binding?: SignalBinding;
  label?: string;
  value?: number; // button: value to send on click
  onValue?: number; // checkbox: value to send when checked
  offValue?: number; // checkbox: value to send when unchecked
  funcName?: string; // functionMultiButton: FUNC name to trigger on click
  mode?: 'random' | 'range'; // randomMultiButton: value generation mode
  rangeMin?: number; // randomMultiButton range mode: raw min
  rangeMax?: number; // randomMultiButton range mode: raw max
  step?: number; // randomMultiButton range mode: raw step
}

export interface WidgetConfig {
  id: string;
  type: WidgetType;
  title: string;
  binding?: SignalBinding;
  // widget-specific options (button send value, checkbox on/off values,
  // slider min/max/step, ...)
  options: Record<string, unknown>;
}

export interface TxRow {
  key: string;
  idHex: string;
  periodMs: number;
  dataHex: string;
  messageName: string | null; // when set, payload comes from DBC signal state
  enabled: boolean;
  isFd: boolean; // raw-ID rows only; DBC-linked rows use the message's own FD flag
  bitrateSwitch: boolean;
}
