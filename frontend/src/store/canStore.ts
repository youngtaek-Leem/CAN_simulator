// Real-time CAN data store with throttled UI notification.
//
// Requirement: incoming WebSocket data must NOT touch the DOM directly.
// Frames are merged into plain objects here on every message, and React is
// notified at most `fps` times per second from a requestAnimationFrame loop
// (fps is user-configurable, 10..60).

import { useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type {
  BackendStatus,
  DbcMessage,
  DbcSignal,
  DbcSummary,
  FrameEntry,
  RxFrame,
  TestRunnerEvent,
} from '../types';

const FPS_KEY = 'can-sim.ui-fps';
const RX_NODE_KEY = 'can-sim.rx-node';
const STMIN_ENABLED_KEY = 'can-sim.uds-stmin-enabled';
const STMIN_VALUE_KEY = 'can-sim.uds-stmin-value';
const TRACE_WINDOW_S = 60; // keep the last minute of raw frames for pause/scroll
const TRACE_CAP = 30000; // hard memory cap for the trace buffer
const HISTORY_CAP = 10000; // points kept per watched signal (graph widgets)
const ACTIVITY_CAP = 300; // lines kept in the widget/test-runner activity log
const TESTRUNNER_POLL_MS = 400; // matches TestRunnerBox's own poll cadence
// How many newly-ingested trace rows the live "스크롤" view is allowed to
// reveal per animation frame (~60Hz). A WS batch can carry hundreds of
// frames at once when the backend falls behind its own flush cadence (e.g.
// TransferData at minimum STmin) -- revealing all of them in a single
// render jumps the live view's scroll position in one big leap, which reads
// as stutter even though rendering itself is cheap (already virtualized).
// Draining the backlog at a bounded rate instead spreads that same jump
// over a handful of frames, so it reads as a smooth scroll -- no data is
// dropped, this only paces how fast the live view catches up (see
// revealedTrace()/tick()). A paused snapshot or the fixed-by-ID table are
// unaffected -- both already show the true, unpaced data.
const TRACE_REVEAL_PER_TICK = 40;

export interface HistoryPoint {
  ts: number; // raw backend timestamp (seconds)
  value: number;
}

export interface ActivityEntry {
  ts: number; // epoch ms
  text: string;
}

/** Shared with TestRunnerBox.tsx, which renders the same events in its own
 * per-run log panel -- kept here so both views format a step identically. */
export function formatTestRunnerEvent(ev: TestRunnerEvent): string {
  return ev.msg ?? `[${ev.type ?? '?'}] ${ev.message ?? ''} ${ev.signal ?? ''} → ${ev.status ?? ''}`.trim();
}

export interface LastValidSignal {
  ts: number;
  message: string;
  signal: string;
  value: number | string;
}

class CanStore {
  frames = new Map<number, FrameEntry>();
  signals = new Map<string, number | string>(); // "Message.Signal" -> value
  // "Message.Signal" -> last VALID decoded value (per backend's valid_signals
  // for that frame). Unlike `signals` above (overwritten every frame,
  // invalid or not), this only ever updates on a valid reading and is never
  // deleted for a signal once seen -- so RxSignalDisplay can keep showing a
  // signal's last-known-good value forever instead of the row disappearing
  // whenever the current frame happens to decode as invalid.
  lastValidSignal = new Map<string, LastValidSignal>();
  trace: RxFrame[] = []; // chronological raw frames (last TRACE_WINDOW_S seconds)
  // How many of trace's leading entries the live scroll view has been
  // allowed to reveal so far -- grows toward trace.length by at most
  // TRACE_REVEAL_PER_TICK per animation frame (see tick()). Always an index
  // into the CURRENT trace array; adjusted in ingestFrames() whenever trace
  // is spliced from the front (stale-window/cap pruning) so it keeps
  // pointing at the same conceptual position instead of silently
  // over-revealing. Read through revealedTrace(), never directly.
  private revealedCount = 0;
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

  // Widget/test-runner activity log (see TextDisplay widget).
  activityLog: ActivityEntry[] = [];
  private dbcMessages: DbcMessage[] = [];
  // "Message.Signal" -> last logged display value, so a Periodic signal only
  // logs a new line when its value actually changes (the backend otherwise
  // keeps silently re-transmitting the same value every cycle in the
  // background, with no further widget interaction to hook a log call into).
  private lastPeriodicValue = new Map<string, string>();
  // how many of the test runner's self._events we've already turned into
  // activity lines -- events is reset to [] server-side on every new run, so
  // a shrink means a new run started and we should treat all of it as new.
  private lastTestRunnerEventCount = 0;

  version = 0;
  private listeners = new Set<() => void>();
  private dirty = false;
  private lastEmit = 0;
  private fps: number;
  private rxNode: string;
  private stminEnabled: boolean;
  private stminValue: string;

  constructor() {
    const saved = Number(localStorage.getItem(FPS_KEY));
    this.fps = saved >= 10 && saved <= 60 ? saved : 30;
    this.rxNode = localStorage.getItem(RX_NODE_KEY) ?? '';
    this.stminEnabled = localStorage.getItem(STMIN_ENABLED_KEY) === '1';
    this.stminValue = localStorage.getItem(STMIN_VALUE_KEY) ?? '0A';
    requestAnimationFrame(this.tick);
    setInterval(this.pollTestRunnerEvents, TESTRUNNER_POLL_MS);
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

  /** Flow Control STmin override for UDS SecurityAccess/transferData, shared
   * between CAN-SWDL and OTA Tester (both widgets set/show the same value,
   * see UdsGlobalControls.tsx) -- undefined-equivalent (disabled) means each
   * widget's own XML/default timing is used instead. */
  getGlobalStminEnabled() {
    return this.stminEnabled;
  }

  setGlobalStminEnabled(enabled: boolean) {
    this.stminEnabled = enabled;
    localStorage.setItem(STMIN_ENABLED_KEY, enabled ? '1' : '0');
    this.markDirty();
  }

  getGlobalStminTx() {
    return this.stminValue;
  }

  setGlobalStminTx(value: string) {
    this.stminValue = value;
    localStorage.setItem(STMIN_VALUE_KEY, value);
    this.markDirty();
  }

  /** Rebuild the choice-label -> raw-value reverse lookup used to chart
   * enum/VAL_ signals numerically (the backend decodes them to a string
   * label for display, e.g. "On"/"Off"). Call whenever the loaded DBC changes. */
  setDbc(dbc: DbcSummary) {
    this.dbcMessages = dbc.messages ?? [];
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

  private findDbcSignal(message: string, signal: string): DbcSignal | undefined {
    return this.dbcMessages.find((m) => m.name === message)?.signals.find((s) => s.name === signal);
  }

  private formatSignalValue(sig: DbcSignal | undefined, physical: number | string): string {
    if (typeof physical === 'string') return physical;
    const label = sig?.choices?.[physical];
    if (label !== undefined) return label;
    const num = Number.isInteger(physical) ? String(physical) : String(parseFloat(physical.toFixed(3)));
    return sig?.unit ? `${num} ${sig.unit}` : num;
  }

  pushActivity(text: string, ts = Date.now()) {
    this.activityLog.push({ ts, text });
    if (this.activityLog.length > ACTIVITY_CAP) {
      this.activityLog.splice(0, this.activityLog.length - ACTIVITY_CAP);
    }
    this.markDirty();
  }

  clearActivity() {
    this.activityLog = [];
    this.markDirty();
  }

  /** Log a widget-driven CAN signal send, subject to the two display rules
   * from Requirement.md: a Periodic signal only logs when its value actually
   * changed, and an Event signal never logs an "invalid" send. The latter is
   * mostly already guaranteed by the caller -- this app's widgets only ever
   * request an explicit invalid send (kind: 'invalid') for Periodic signals
   * in the first place (see usePeriodicInvalidToggle) -- this check just
   * makes that invariant explicit instead of accidental. */
  private logSignalSend(
    message: string,
    signal: string,
    display: string,
    sendType: string | undefined,
    kind: 'valid' | 'invalid',
  ) {
    if (sendType === 'event' && kind === 'invalid') return;
    const key = `${message}.${signal}`;
    if (sendType === 'periodic') {
      if (this.lastPeriodicValue.get(key) === display) return;
      this.lastPeriodicValue.set(key, display);
    }
    this.pushActivity(`${message}.${signal} = ${display}`);
  }

  // ---- wrapped send entry points ----------------------------------------
  // Widgets call these instead of api.txSignal/sendGenerated/sendInvalid
  // directly, so every CAN signal a widget sends passes through one place
  // for the activity log (see displays.tsx's TextDisplay).

  async sendSignal(message: string, values: Record<string, number | string>) {
    const result = await api.txSignal(message, values);
    for (const [signal, value] of Object.entries(values)) {
      const sig = this.findDbcSignal(message, signal);
      this.logSignalSend(message, signal, this.formatSignalValue(sig, value), sig?.send_type, 'valid');
    }
    return result;
  }

  async sendGenerated(message: string, signal: string) {
    const result = await api.sendGenerated(message, signal);
    const sig = this.findDbcSignal(message, signal);
    const physical = sig ? result.raw_value * sig.scale + sig.offset : result.raw_value;
    this.logSignalSend(message, signal, this.formatSignalValue(sig, physical), result.send_type, 'valid');
    return result;
  }

  async sendInvalid(message: string, signal: string) {
    const result = await api.sendInvalid(message, signal);
    this.logSignalSend(message, signal, 'INVALID', result.send_type, 'invalid');
    return result;
  }

  private pollTestRunnerEvents = () => {
    api
      .testRunnerStatus()
      .then((s) => {
        const events = s.events;
        // a shorter list than what we've already consumed means a new run
        // reset the backend's log (see TestRunnerService.start()) -- treat
        // everything currently present as new.
        if (events.length < this.lastTestRunnerEventCount) this.lastTestRunnerEventCount = 0;
        const fresh = events.slice(this.lastTestRunnerEventCount);
        this.lastTestRunnerEventCount = events.length;
        for (const ev of fresh) this.pushActivity(formatTestRunnerEvent(ev), ev.ts * 1000);
      })
      .catch(() => {});
  };

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
        const validNames = new Set(f.decoded.valid_signals);
        for (const [sig, value] of Object.entries(f.decoded.signals)) {
          const key = `${f.decoded.name}.${sig}`;
          this.signals.set(key, value);
          if (validNames.has(sig)) {
            this.lastValidSignal.set(key, { ts: f.ts, message: f.decoded.name, signal: sig, value });
          }
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
    // prune the trace buffer: drop frames older than the window, then cap.
    // Each splice removes from the front, so revealedCount (an index into
    // this same array) must shrink by the same amount to keep pointing at
    // the same conceptual position -- otherwise it would silently jump
    // ahead relative to the remaining content once older rows are dropped.
    const cutoff = rx[rx.length - 1].ts - TRACE_WINDOW_S;
    let stale = 0;
    while (stale < this.trace.length && this.trace[stale].ts < cutoff) stale++;
    if (stale > 0) {
      this.trace.splice(0, stale);
      this.revealedCount = Math.max(0, this.revealedCount - stale);
    }
    if (this.trace.length > TRACE_CAP) {
      const excess = this.trace.length - TRACE_CAP;
      this.trace.splice(0, excess);
      this.revealedCount = Math.max(0, this.revealedCount - excess);
    }
    this.markDirty();
  }

  /** The prefix of `trace` currently allowed to be shown by the live
   * "스크롤" view -- grows toward trace.length at a bounded rate (see
   * tick()) instead of jumping straight to it, so a bursty backend delivery
   * (e.g. TransferData at minimum STmin) reads as a smooth scroll on
   * screen. A paused snapshot bypasses this entirely and shows the full,
   * unpaced `trace` -- pacing only matters while new rows are actively
   * streaming in. */
  get revealedTrace(): RxFrame[] {
    return this.trace.slice(0, Math.min(this.revealedCount, this.trace.length));
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
    this.revealedCount = 0;
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
    this.lastValidSignal.clear();
    this.resetTimeBase();
  }

  private markDirty() {
    this.dirty = true;
  }

  private tick = (t: number) => {
    // Advance the live trace's reveal pointer every animation frame
    // (~60Hz), independent of the fps-gated render below -- this is what
    // actually paces the catch-up rate; the render throttle below just
    // controls how often React sees the (already-paced) result.
    if (this.revealedCount < this.trace.length) {
      this.revealedCount = Math.min(this.trace.length, this.revealedCount + TRACE_REVEAL_PER_TICK);
      this.dirty = true;
    }
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
