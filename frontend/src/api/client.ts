// REST client for the local Python backend.
//
// Production builds are always served by the same FastAPI process that
// exposes the API (see backend/main.py's StaticFiles mount), so BASE must
// stay relative ('') there -- any absolute dev-only override must never leak
// into the shipped bundle, or the app breaks whenever it's reached via a
// hostname other than the one the build happened to be made on (e.g. a
// Windows PC opening http://localhost:8000 instead of http://127.0.0.1:8000).
// Only the Vite dev server (a separate port from the backend) needs BASE to
// point elsewhere.
const BASE = import.meta.env.DEV
  ? ((import.meta.env.VITE_BACKEND_URL as string | undefined) ?? 'http://127.0.0.1:8000')
  : '';

export const WS_URL = BASE
  ? BASE.replace(/^http/, 'ws') + '/ws'
  : `ws://${location.host}/ws`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  return request<T>(path, { method: 'POST', body: form });
}

export const api = {
  status: () => request('/api/status'),
  runStart: () => post('/api/run/start'),
  runStop: () => post('/api/run/stop'),
  connect: (
    interface_: string,
    channel: string,
    bitrate: number,
    fd: boolean = false,
    data_bitrate: number = 2_000_000,
  ) => post('/api/connect', { interface: interface_, channel, bitrate, fd, data_bitrate }),
  disconnect: () => post('/api/disconnect'),
  updateSettings: (ws_flush_ms: number) => post('/api/settings', { ws_flush_ms }),

  logStart: () => post<import('../types').LogStatus>('/api/log/start'),
  logStop: () => post<import('../types').LogStatus>('/api/log/stop'),

  uploadDbc: (file: File) => upload('/api/dbc/upload', file),
  getDbc: () => request('/api/dbc'),
  getDbcRaw: () => request<{ filename: string; content: string } | { loaded: false }>('/api/dbc/raw'),
  overrideSendType: (message_name: string, signal_name: string, send_type: string) =>
    post('/api/dbc/send-type', { message_name, signal_name, send_type }),

  txConfigure: (entries: unknown[]) => post('/api/tx/configure', { entries }),
  txStart: () => post('/api/tx/start'),
  txStop: () => post('/api/tx/stop'),
  txSendOnce: (entry: {
    arbitration_id: number;
    data?: string;
    is_extended?: boolean;
    is_fd?: boolean;
    bitrate_switch?: boolean;
    key?: string;
  }) => post('/api/tx/send_once', entry),
  txSignal: (message_name: string, values: Record<string, number | string>) =>
    post('/api/tx/signal', { message_name, values }),
  txAutoStop: (message_name?: string) =>
    post('/api/tx/auto/stop', { message_name: message_name ?? null }),
  enableAllPeriodic: (rx_node: string) =>
    post<{ armed: string[] }>('/api/tx/periodic/enable_all', { rx_node }),
  disableAllPeriodic: () => post('/api/tx/periodic/disable_all'),
  setValueGenerator: (
    message_name: string,
    signal_name: string,
    mode: string,
    range_min?: number,
    range_max?: number,
    step?: number,
  ) => post('/api/tx/signal/generator', { message_name, signal_name, mode, range_min, range_max, step }),
  sendGenerated: (message_name: string, signal_name: string) =>
    post<{ sent: boolean; raw_value: number; send_type: 'event' | 'periodic' }>(
      '/api/tx/signal/generate',
      { message_name, signal_name },
    ),
  sendInvalid: (message_name: string, signal_name: string) =>
    post<{ sent: boolean; raw_value: number; send_type: 'event' | 'periodic' }>(
      '/api/tx/signal/invalid',
      { message_name, signal_name },
    ),

  isotpSend: (
    tx_id: number,
    fc_id: number,
    data: string,
    opts?: {
      is_extended_id?: boolean;
      fc_timeout_ms?: number;
      max_wait_frames?: number;
      // "응답 대기": after sending, also wait for and reassemble a reply on
      // resp_id (acting as ISO-TP receiver of that reply, including sending
      // its own Flow Control -- previously nothing in the app did this for
      // a manually-sent request, see Requirement.md).
      resp_id?: number;
      resp_timeout_ms?: number;
      resp_fc_block_size?: number;
      resp_fc_stmin?: number;
    },
  ) =>
    post<{
      frame_type: string;
      frames_sent: number;
      bytes_sent: number;
      duration_ms: number;
      response?: string;
      response_error?: string;
    }>('/api/isotp/send', { tx_id, fc_id, data, ...opts }),

  uploadReplay: (file: File) => upload('/api/replay/upload', file),
  replayStart: (mode: 'pass' | 'stop', frame_ids: number[]) =>
    post('/api/replay/start', { mode, frame_ids }),
  replayStop: () => post('/api/replay/stop'),

  uploadTestScript: (file: File) => upload('/api/testrunner/upload', file),
  getTestScriptRaw: () =>
    request<{ filename: string; content: string } | { loaded: false }>('/api/testrunner/script/raw'),
  uploadTestLogfile: (file: File) => upload('/api/testrunner/logfile/upload', file),
  uploadTestGolden: (file: File) => upload('/api/testrunner/golden/upload', file),
  testRunnerStart: () => post('/api/testrunner/start'),
  testRunnerStop: () => post('/api/testrunner/stop'),
  testRunnerStatus: () => request<import('../types').TestRunnerStatus>('/api/testrunner/status'),

  uploadFunctionScript: (file: File) => upload('/api/testrunner/functions/upload', file),
  getFunctionScriptRaw: () =>
    request<{ filename: string; content: string } | { loaded: false }>('/api/testrunner/functions/raw'),
  functionStart: (name: string) => post('/api/testrunner/functions/start', { name }),

  // UDS Software Download (CAN-SWDL) — Multi-slot
  udsUploadXml: (file: File, slotIndex: number = 0) => upload<{ slot: number; status: import('../types').UdsDownloadStatus }>('/api/udswdl/xml/upload?slot_index=' + slotIndex, file),
  udsUploadBinary: (file: File, slotIndex: number = 0) => upload<{ slot: number; status: import('../types').UdsDownloadStatus }>('/api/udswdl/binary/upload?slot_index=' + slotIndex, file),
  udsStart: (
    slotIndices: number[] = [0, 1, 2],
    selectedSteps?: (number[] | undefined),
    modifiedParams?: Record<string, Record<string, string>>,
    globalStminTx?: number,
    perSlotSelectedSteps?: Record<number, (number[] | undefined)>,
    perSlotModifiedParams?: Record<number, Record<string, Record<string, string>> | undefined>,
  ) => post<any[]>('/api/udswdl/start', {
    slot_indices: slotIndices,
    selected_steps: selectedSteps,
    modified_params: modifiedParams,
    global_stmin_tx: globalStminTx,
    per_slot_selected_steps: perSlotSelectedSteps,
    per_slot_modified_params: perSlotModifiedParams,
  }),
  udsStop: (slotIndex: number = 0) => post<import('../types').UdsDownloadStatus>('/api/udswdl/stop?slot_index=' + slotIndex),
  udsStatus: () => request<import('../types').UdsDownloadStatus[]>('/api/udswdl/status'),
  udsSteps: (slotIndex: number = 0) => request<import('../types').UdsStepInfo[]>('/api/udswdl/steps?slot_index=' + slotIndex),
  udsSetParams: (slotIndex: number, stepService: string, params: Record<string, string>) =>
    request<import('../types').UdsDownloadStatus>('/api/udswdl/step_params', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot_index: slotIndex, step_service: stepService, params }),
    }),
  seedkeyUpload: (file: File) => upload<import('../types').SeedKeyStatus>('/api/seedkey/upload', file),
  seedkeyStatus: () => request<import('../types').SeedKeyStatus>('/api/seedkey/status'),

  powerConnect: () => post<import('../types').PowerStatus>('/api/power/connect'),
  powerDisconnect: () => post<import('../types').PowerStatus>('/api/power/disconnect'),
  // Ctrl-C가 통하지 않는 환경(Requirement.md -- PyVISA/NI-VISA 드라이버가 Windows
  // 콘솔의 SIGINT 전달을 가로채는 것으로 보이는 사례) 대안 종료 경로.
  shutdownServer: () => post<{ ok: boolean; message: string }>('/api/shutdown'),
  powerSetBattery: (voltage: number, current: number) =>
    post<{ ok: boolean; reason?: string }>('/api/power/battery', { voltage, current }),
  powerSetAccIgn: (command: string) =>
    post<{ ok: boolean; reason?: string }>('/api/power/acc_ign', { command }),
  powerOnOffStart: (
    onVoltage: number,
    onCurrent: number,
    onS: number,
    offVoltage: number,
    offCurrent: number,
    offS: number,
  ) =>
    post<{ ok: boolean; reason?: string }>('/api/power/onoff/start', {
      on_voltage: onVoltage,
      on_current: onCurrent,
      on_s: onS,
      off_voltage: offVoltage,
      off_current: offCurrent,
      off_s: offS,
    }),
  powerOnOffStop: () => post<{ ok: boolean; reason?: string }>('/api/power/onoff/stop'),
  powerSweepStart: (low: number, high: number, current: number, legS: number) =>
    post<{ ok: boolean; reason?: string }>('/api/power/sweep/start', { low, high, current, leg_s: legS }),
  powerSweepStop: () => post<{ ok: boolean; reason?: string }>('/api/power/sweep/stop'),
  audioDevices: () => request<import('../types').AudioStatus>('/api/audio/devices'),
  audioSelectDevice: (index: number) =>
    post<import('../types').AudioStatus>('/api/audio/device', { index }),
  audioMonitorStart: () => post<{ ok: boolean; reason?: string }>('/api/audio/monitor/start'),
  audioMonitorStop: () => post<{ ok: boolean; reason?: string }>('/api/audio/monitor/stop'),
  audioRecordStart: () => post<{ ok: boolean; reason?: string; filename?: string }>('/api/audio/record/start'),
  audioRecordStop: () => post<{ ok: boolean; reason?: string; filename?: string; frames?: number }>('/api/audio/record/stop'),
  audioLevel: () => request<import('../types').AudioLevel>('/api/audio/level'),
  audioWaveform: (fromMs: number, toMs: number, maxPoints: number) =>
    request<import('../types').AudioWaveform>(
      `/api/audio/waveform?from_ms=${fromMs}&to_ms=${toMs}&max_points=${maxPoints}`,
    ),

  listLayouts: () => request<{ layouts: string[] }>('/api/layouts'),
  getLayout: (name: string) => request(`/api/layouts/${encodeURIComponent(name)}`),
  saveLayout: (name: string, body: unknown) =>
    post(`/api/layouts/${encodeURIComponent(name)}`, body),
  deleteLayout: (name: string) =>
    request(`/api/layouts/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // OTA Tester (folder-driven: CLI/cli_config.json -> Testcases/<id>/*.json -> hook/testBlock XML+bin)
  otaTesterStatus: () => request<import('../types').OtaTesterStatus>('/api/ota_tester/status'),
  otaTesterCaseUploadXml: (
    file: File, caseId: string, label: string, kind: string, order: number, enabled: boolean = true,
  ) => {
    const qs = new URLSearchParams({
      case_id: caseId, label, kind, order: String(order), enabled: String(enabled),
    });
    return upload<import('../types').OtaTesterStatus>(`/api/ota_tester/case/xml_upload?${qs}`, file);
  },
  otaTesterCaseUploadBinary: (file: File, caseId: string) =>
    upload<import('../types').OtaTesterStatus>(
      `/api/ota_tester/case/binary_upload?${new URLSearchParams({ case_id: caseId })}`, file,
    ),
  otaTesterCaseEnable: (caseId: string, enabled: boolean) =>
    post<import('../types').OtaTesterStatus>('/api/ota_tester/case/enable', { case_id: caseId, enabled }),
  otaTesterCaseSteps: (caseId: string) =>
    request<import('../types').OtaTesterStepInfo[]>(
      `/api/ota_tester/case/steps?${new URLSearchParams({ case_id: caseId })}`,
    ),
  otaTesterSetSelectedSteps: (caseId: string, selectedSteps: number[] | null) =>
    request<import('../types').OtaTesterStatus>('/api/ota_tester/case/selected_steps', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, selected_steps: selectedSteps }),
    }),
  otaTesterSetAllEnabled: (enabled: boolean) =>
    post<import('../types').OtaTesterStatus>('/api/ota_tester/cases/set_all_enabled', { enabled }),
  otaTesterClearCases: () => post<import('../types').OtaTesterStatus>('/api/ota_tester/cases/clear'),
  otaTesterStart: (request_id: number, response_id: number, global_stmin_tx?: number) =>
    post('/api/ota_tester/start', { request_id, response_id, global_stmin_tx }),
  otaTesterStop: () => post('/api/ota_tester/stop'),

  // sysLog 분석
  syslogUploadLog: (file: File) => upload<import('../types').SysLogStatus>('/api/syslog/upload', file),
  syslogUploadDb: (file: File) => upload<import('../types').SysLogStatus>('/api/syslog/db/upload', file),
  syslogStatus: () => request<import('../types').SysLogStatus>('/api/syslog/status'),
  syslogTimeline: () => request<import('../types').SysLogTimeline>('/api/syslog/timeline'),
  syslogGenerateScript: (checkedSegments: number[]) =>
    post<import('../types').SysLogScriptResult>('/api/syslog/generate_script', {
      checked_segments: checkedSegments,
    }),
  // segmentIndices를 주면(체크된 시간 구간 인덱스) 각 ID의 count가 그 구간
  // 안의 레코드 수로만 계산된다. 생략하면 전체 개수(필터 없음). 빈 배열이면
  // "체크된 구간 없음" -- 모든 ID count가 0으로 온다(백엔드 /api/syslog/ids
  // 참고, 파라미터 부재와 빈 문자열을 구분함).
  syslogIds: (segmentIndices?: number[]) =>
    request<import('../types').SysLogIdInfo[]>(
      segmentIndices !== undefined ? `/api/syslog/ids?segments=${segmentIndices.join(',')}` : '/api/syslog/ids',
    ),
  syslogSeries: (ids: number[]) =>
    request<Record<string, import('../types').SysLogSeries>>(
      `/api/syslog/series?ids=${ids.join(',')}`,
    ),

  // CAN log 분석
  canlogUpload: (file: File) => upload<import('../types').CanLogStatus>('/api/canlog/upload', file),
  canlogStatus: () => request<import('../types').CanLogStatus>('/api/canlog/status'),
  canlogTimeline: () => request<import('../types').CanLogTimeline>('/api/canlog/timeline'),
  canlogSignals: () => request<import('../types').CanLogSignalInfo[]>('/api/canlog/signals'),
  canlogMessages: () => request<import('../types').CanLogMessageInfo[]>('/api/canlog/messages'),
  canlogSeries: (keys: string[]) =>
    request<Record<string, import('../types').CanLogSeries>>(
      `/api/canlog/series?keys=${keys.map(encodeURIComponent).join(',')}`,
    ),
  canlogGenerateScript: (range: { a_ms: number; b_ms: number } | null, rxNode?: string) =>
    post<import('../types').CanLogScriptResult>('/api/canlog/generate_script', { range, rx_node: rxNode ?? null }),
};
