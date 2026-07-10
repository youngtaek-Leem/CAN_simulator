// Real-time CAN data store with throttled UI notification.
//
// Requirement: incoming WebSocket data must NOT touch the DOM directly.
// Frames are merged into plain objects here on every message, and React is
// notified at most `fps` times per second from a requestAnimationFrame loop
// (fps is user-configurable, 10..60).

import { useSyncExternalStore } from 'react';
import type { BackendStatus, DbcSummary, FrameEntry, RxFrame } from '../types';

const FPS_KEY = 'can-sim.ui-fps';
const RX_NODE_KEY = 'can-sim.rx-node';
const TRACE_WINDOW_S = 60; // keep the last minute of raw frames for pause/scroll
const TRACE_CAP = 30000; // hard memory cap for the trace buffer
const HISTORY_CAP = 10000; // points kept per watched signal (graph widgets)

export interface HistoryPoint {
  ts: number; // raw backend timestamp (seconds)
  value: number;
}

class CanStore {
  frames = new Map<number, FrameEntry>();
  signals = new Map<string, number | string>(); // "Message.Signal" -> value
  trace: RxFrame[] = []; // chronological raw frames (last TRACE_WINDOW_S seconds)
  // Per-signal time series, populated only for signals with an active graph
  // widget watching them (see watchSignal/unwatchSignal) so history isn't
  // recorded for every DBC signal, just the ones actually being charted.
  signalHistory = new Map<string, HistoryPoint[]>();
  private signalWatchers = new Map<string, number>();
  // "Message.Signal" -> {label -> raw value}, so choice/enum signals (decoded
  // by the backend as a string label) can still be charted numerically.
  private choiceReverse = new Map<string, Map<string, number>>();
  timeBase: number | null = null; // ts of the first frame after (re)start = 0 ms
  status: BackendStatus | null = null;
  wsConnected = false;

  version = 0;
  private listeners = new Set<() => void>();
  private dirty = false;
  private lastEmit = 0;
  private fps: number;
  private rxNode: string;

  constructor() {
    const saved = Number(localStorage.getItem(FPS_KEY));
    this.fps = saved >= 10 && saved <= 60 ? saved : 30;
    this.rxNode = localStorage.getItem(RX_NODE_KEY) ?? '';
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

  /** Real DUT node on the bus (e.g. the hardware ECU under test). Messages
   * that this node sends are what the simulator receives ("RX"); every
   * other message is something the simulator must transmit ("TX") to stand
   * in for the rest of the bus. Empty = no split (flat list). */
  getRxNode() {
    return this.rxNode;
  }

  setRxNode(node: string) {
    this.rxNode = node;
    localStorage.setItem(RX_NODE_KEY, node);
    this.markDirty();
  }

  /** Rebuild the choice-label -> raw-value reverse lookup used to chart
   * enum/VAL_ signals numerically (the backend decodes them to a string
   * label for display, e.g. "On"/"Off"). Call whenever the loaded DBC changes. */
  setDbc(dbc: DbcSummary) {
    this.choiceReverse.clear();
    for (const m of dbc.messages ?? []) {
      for (const s of m.signals) {
        if (!s.choices) continue;
        const reverse = new Map<string, number>();
        for (const [raw, label] of Object.entries(s.choices)) reverse.set(label, Number(raw));
        this.choiceReverse.set(`${m.name}.${s.name}`, reverse);
      }
    }
  }

  /** Start recording a time series for "Message.Signal" (ref-counted). */
  watchSignal(key: string) {
    this.signalWatchers.set(key, (this.signalWatchers.get(key) ?? 0) + 1);
    if (!this.signalHistory.has(key)) this.signalHistory.set(key, []);
  }

  unwatchSignal(key: string) {
    const n = (this.signalWatchers.get(key) ?? 1) - 1;
    if (n <= 0) {
      this.signalWatchers.delete(key);
      this.signalHistory.delete(key);
    } else {
      this.signalWatchers.set(key, n);
    }
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
          const key = `${f.decoded.name}.${sig}`;
          this.signals.set(key, value);
          if (this.signalWatchers.has(key)) {
            const numeric =
              typeof value === 'number' ? value : this.choiceReverse.get(key)?.get(value);
            if (numeric !== undefined) {
              const points = this.signalHistory.get(key)!;
              points.push({ ts: f.ts, value: numeric });
              if (points.length > HISTORY_CAP) points.splice(0, points.length - HISTORY_CAP);
            }
          }
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

  // While globally stopped, nowMs() must hold still at the moment Stop was
  // pressed instead of continuing to track Date.now() -- otherwise graph
  // widgets keep scrolling their rolling window with no new data arriving.
  private frozenNowMs: number | null = null;

  /** Current wall-clock position on the same timeline as relMs(), so a
   * rolling time window can keep scrolling even between samples (backend
   * and frontend share the same clock -- this is a local-only tool). Frozen
   * while globally stopped (see ingestStatus()). */
  nowMs(): number {
    if (this.frozenNowMs !== null) return this.frozenNowMs;
    return this.timeBase === null ? 0 : Date.now() - this.timeBase * 1000;
  }

  resetTimeBase() {
    this.timeBase = null;
    this.trace = [];
    for (const key of this.signalHistory.keys()) this.signalHistory.set(key, []);
    this.markDirty();
  }

  ingestStatus(status: BackendStatus) {
    const wasRunning = this.status?.run?.running;
    const isRunning = status.run?.running;
    if (isRunning && wasRunning === false) {
      // global Start pressed -> restart the 0 ms time base
      this.resetTimeBase();
      this.frozenNowMs = null;
    } else if (!isRunning && wasRunning === true) {
      // global Stop pressed -> freeze the rolling window where it is
      this.frozenNowMs = this.nowMs();
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
