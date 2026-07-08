// WebSocket connection to the backend with automatic reconnect.

import { canStore } from '../store/canStore';
import type { BackendStatus, RxFrame } from '../types';
import { WS_URL } from './client';

let socket: WebSocket | null = null;
let retryTimer: number | null = null;

export function connectWebSocket(): void {
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  socket = new WebSocket(WS_URL);

  socket.onopen = () => canStore.setWsConnected(true);

  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data as string);
    if (msg.type === 'rx') {
      canStore.ingestFrames(msg.frames as RxFrame[]);
    } else if (msg.type === 'status') {
      canStore.ingestStatus(msg as unknown as BackendStatus);
    }
  };

  const scheduleRetry = () => {
    canStore.setWsConnected(false);
    socket = null;
    if (retryTimer === null) {
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        connectWebSocket();
      }, 1000);
    }
  };

  socket.onclose = scheduleRetry;
  socket.onerror = () => socket?.close();
}
