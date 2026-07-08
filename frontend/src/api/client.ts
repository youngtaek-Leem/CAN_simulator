// REST client for the local Python backend.

const BASE =
  (import.meta.env.VITE_BACKEND_URL as string | undefined) ??
  (location.port === '5173' ? 'http://127.0.0.1:8000' : '');

export const WS_URL =
  BASE.replace(/^http/, 'ws') + '/ws' ||
  `ws://${location.host}/ws`;

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

  uploadDbc: (file: File) => upload('/api/dbc/upload', file),
  getDbc: () => request('/api/dbc'),
  overrideSendType: (message_name: string, signal_name: string, send_type: string) =>
    post('/api/dbc/send-type', { message_name, signal_name, send_type }),

  txConfigure: (entries: unknown[]) => post('/api/tx/configure', { entries }),
  txStart: () => post('/api/tx/start'),
  txStop: () => post('/api/tx/stop'),
  txSignal: (message_name: string, values: Record<string, number | string>) =>
    post('/api/tx/signal', { message_name, values }),
  txAutoStop: (message_name?: string) =>
    post('/api/tx/auto/stop', { message_name: message_name ?? null }),

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

  listLayouts: () => request<{ layouts: string[] }>('/api/layouts'),
  getLayout: (name: string) => request(`/api/layouts/${encodeURIComponent(name)}`),
  saveLayout: (name: string, body: unknown) =>
    post(`/api/layouts/${encodeURIComponent(name)}`, body),
  deleteLayout: (name: string) =>
    request(`/api/layouts/${encodeURIComponent(name)}`, { method: 'DELETE' }),
};
