// Real-time CAN data store with throttled UI notification.
//
// Requirement: incoming WebSocket data must NOT touch the DOM directly.
// Frames are merged into plain objects here on every message, and React is
// notified at most `fps` times per second from a requestAnimationFrame loop
// (fps is user-configurable, 10..60).

import { useSyncExternalStore } from 'react';
import type { BackendStatus, FrameEntry, RxFrame } from '../types';

const FPS_KEY = 'can-sim.ui-fps';
const TRACE_WINDOW_S = 60; // keep the last minute of raw frames for pause/scroll
const TRACE_CAP = 30000; // hard memory cap for the trace buffer

class CanStore {
  frames = new Map<number, FrameEntry>();
  signals = new Map<string, number | string>(); // "Message.Signal" -> value
  trace: RxFrame[] = []; // chronological raw frames (last TRACE_WINDOW_S seconds)
  timeBase: number | null = null; // ts of the first frame after (re)start = 0 ms
  status: BackendStatus | null = null;
  wsConnected = false;

  version = 0;
  private listeners = new Set<() => void>();
  private dirty = false;
  private lastEmit = 0;
  private fps: number;

  constructor() {
    const saved = Number(localStorage.getItem(FPS_KEY));
    this.fps = saved >= 10 && saved <= 60 ? saved : 30;
    requestAnimationFrame(this.tick);
  }

  getFps() {
    return this.fps;
  }

  setFps(fps: number) {
    this.fps = Math.min(60, Math.max(10, fps));
    localStorage.setItem(FPS_KEY, String(this.fps));
    this.markDirty();
  }

  ingestFrames(rx: RxFrame[]) {
    if (rx.length === 0) return;
    if (this.timeBase === null) this.timeBase = rx[0].ts;
    for (const f of rx) {
      const prev = this.frames.get(f.id);
      const cycleMs = prev ? (f.ts - prev.ts) * 1000 : null;
      this.frames.set(f.id, {
        ...f,
        count: (prev?.count ?? 0) + 1,
        cycleMs: cycleMs !== null && cycleMs > 0 ? cycleMs : prev?.cycleMs ?? null,
      });
      if (f.decoded) {
        for (const [sig, value] of Object.entries(f.decoded.signals)) {
          this.signals.set(`${f.decoded.name}.${sig}`, value);
        }
      }
      this.trace.push(f);
    }
    // prune the trace buffer: drop frames older than the window, then cap
    const cutoff = rx[rx.length - 1].ts - TRACE_WINDOW_S;
    let stale = 0;
    while (stale < this.trace.length && this.trace[stale].ts < cutoff) stale++;
    if (stale > 0) this.trace.splice(0, stale);
    if (this.trace.length > TRACE_CAP) {
      this.trace.splice(0, this.trace.length - TRACE_CAP);
    }
    this.markDirty();
  }

  /** ms since the first frame received after the last (re)start. */
  relMs(ts: number): number {
    return this.timeBase === null ? 0 : (ts - this.timeBase) * 1000;
  }

  resetTimeBase() {
    this.timeBase = null;
    this.trace = [];
    this.markDirty();
  }

  ingestStatus(status: BackendStatus) {
    // global Start pressed -> restart the 0 ms time base
    if (status.run?.running && this.status?.run?.running === false) {
      this.resetTimeBase();
    }
    this.status = status;
    this.markDirty();
  }

  setWsConnected(connected: boolean) {
    this.wsConnected = connected;
    this.markDirty();
  }

  clearFrames() {
    this.frames.clear();
    this.signals.clear();
    this.resetTimeBase();
  }

  private markDirty() {
    this.dirty = true;
  }

  private tick = (t: number) => {
    if (this.dirty && t - this.lastEmit >= 1000 / this.fps) {
      this.dirty = false;
      this.lastEmit = t;
      this.version++;
      for (const listener of this.listeners) listener();
    }
    requestAnimationFrame(this.tick);
  };

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

export const canStore = new CanStore();

/** Re-renders the caller at most `fps` times per second when data changed. */
export function useCanVersion(): number {
  return useSyncExternalStore(canStore.subscribe, () => canStore.version);
}
