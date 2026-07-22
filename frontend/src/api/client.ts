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
  txSignal: (message_name: string, values: Record<string, number | string>) =>
    post('/api/tx/signal', { message_name, values }),
  txAutoStop: (message_name?: string) =>
    post('/api/tx/auto/stop', { message_name: message_name ?? null }),
  enableAllPeriodic: (rx_node: string) =>
    post<{ armed: string[] }>('/api/tx/periodic/enable_all', { rx_node }),
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
    opts?: { is_extended_id?: boolean; fc_timeout_ms?: number; max_wait_frames?: number },
  ) => post('/api/isotp/send', { tx_id, fc_id, data, ...opts }),

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

  powerConnect: () => post<import('../types').PowerStatus>('/api/power/connect'),
  powerDisconnect: () => post<import('../types').PowerStatus>('/api/power/disconnect'),
  audioDevices: () => request<import('../types').AudioStatus>('/api/audio/devices'),
  audioSelectDevice: (index: number) =>
    post<import('../types').AudioStatus>('/api/audio/device', { index }),

  listLayouts: () => request<{ layouts: string[] }>('/api/layouts'),
  getLayout: (name: string) => request(`/api/layouts/${encodeURIComponent(name)}`),
  saveLayout: (name: string, body: unknown) =>
    post(`/api/layouts/${encodeURIComponent(name)}`, body),
  deleteLayout: (name: string) =>
    request(`/api/layouts/${encodeURIComponent(name)}`, { method: 'DELETE' }),
};
