// Display widgets: CAN message grid and the widget/test-runner activity log.
// Both read from canStore and re-render only on the throttled version tick.

import { Fragment, useEffect, useRef, useState } from 'react';
import { canStore, useCanVersion } from '../store/canStore';
import { groupedMessages, useApp } from '../store/appContext';
import type { DbcSummary, FrameEntry, RxFrame, WidgetConfig } from '../types';

const fmtId = (id: number) => `0x${id.toString(16).toUpperCase().padStart(3, '0')}`;
const fmtData = (hex: string) => hex.toUpperCase().replace(/(..)/g, '$1 ').trim();
const fmtTime = (ts: number) => canStore.relMs(ts).toFixed(0);

export function CanMessageDisplay({ config }: { config: WidgetConfig }) {
  return <MessageDisplayCore config={config} />;
}

interface SignalRow {
  ts: number;
  message: string;
  signal: string;
  value: number | string;
  unit: string | null;
}

/** "수신 CAN 신호 표시창": a flat, per-signal (not per-message) live table of
 * the AMP TX signals -- i.e. signals belonging to messages the simulator
 * itself transmits (groupedMessages' "tx" set, relative to the configured
 * RX node / real DUT), showing only currently-valid values. Unlike
 * CanMessageDisplay, there's no message-row-with-expandable-detail: each row
 * IS a signal. */
export function RxSignalDisplay({ config: _config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc } = useApp();
  const txNames = new Set(groupedMessages(dbc, canStore.getRxNode()).tx.map((m) => m.name));

  const rows: SignalRow[] = [];
  for (const f of canStore.frames.values()) {
    if (!f.decoded || !txNames.has(f.decoded.name)) continue;
    const message = dbc.messages?.find((m) => m.name === f.decoded!.name);
    for (const signalName of f.decoded.valid_signals) {
      rows.push({
        ts: f.ts,
        message: f.decoded.name,
        signal: signalName,
        value: f.decoded.signals[signalName],
        unit: message?.signals.find((s) => s.name === signalName)?.unit ?? null,
      });
    }
  }
  rows.sort((a, b) => a.message.localeCompare(b.message) || a.signal.localeCompare(b.signal));

  return (
    <div className="msg-display">
      <div className="msg-toolbar">
        <span className="hint">{rows.length}개 신호</span>
        <span className="spacer" />
        <button className="small-btn" onClick={() => canStore.clearFrames()}>
          Clear
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Time(ms)</th>
            <th>Message</th>
            <th>Signal</th>
            <th>Value</th>
            <th>Unit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.message}.${r.signal}`}>
              <td>{fmtTime(r.ts)}</td>
              <td>{r.message}</td>
              <td>{r.signal}</td>
              <td className="mono">{String(r.value)}</td>
              <td>{r.unit || ''}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                표시할 AMP TX 신호가 없습니다
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function MessageDisplayCore({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const mode = (config.options.viewMode as 'fixed' | 'trace') ?? 'fixed';
  const [paused, setPaused] = useState(false);
  // frozen copy of the last-minute trace, captured when pause is pressed
  const [snapshot, setSnapshot] = useState<RxFrame[]>([]);
  // IDs currently expanded to show their signal breakdown (fixed mode only)
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const setMode = (m: 'fixed' | 'trace') =>
    updateWidget({ ...config, options: { ...config.options, viewMode: m } });

  const togglePause = () => {
    if (!paused) setSnapshot([...canStore.trace]);
    setPaused(!paused);
  };

  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const frames = [...canStore.frames.values()].sort((a, b) => a.id - b.id);

  return (
    <div className="msg-display">
      <div className="msg-toolbar">
        <span className="seg">
          <button
            className={`small-btn ${mode === 'fixed' ? 'seg-active' : ''}`}
            onClick={() => setMode('fixed')}
            title="동일 ID는 한 줄에 고정하고 최신 값으로 갱신"
          >
            고정
          </button>
          <button
            className={`small-btn ${mode === 'trace' ? 'seg-active' : ''}`}
            onClick={() => setMode('trace')}
            title="모든 수신 메시지를 시간순으로 스크롤 표시"
          >
            스크롤
          </button>
        </span>
        <button
          className={`small-btn ${paused ? 'primary' : ''}`}
          onClick={togglePause}
          title="일시중지하면 최근 1분간 수신된 메시지를 스크롤로 확인할 수 있습니다"
        >
          {paused ? '▶ 재개' : '⏸ 일시중지'}
        </button>
        <span className="hint">
          {paused
            ? `일시중지 — 최근 1분 ${snapshot.length}개`
            : mode === 'fixed'
              ? `${frames.length} IDs`
              : `${canStore.trace.length}개 (최근 1분)`}
        </span>
        <span className="spacer" />
        <button className="small-btn" onClick={() => canStore.clearFrames()}>
          Clear
        </button>
      </div>
      {paused ? (
        <TraceView rows={snapshot} live={false} />
      ) : mode === 'trace' ? (
        <TraceView rows={canStore.trace} live={true} />
      ) : (
        <FixedTable frames={frames} dbc={dbc} expanded={expanded} onToggle={toggleExpanded} />
      )}
    </div>
  );
}

function FdBadge({ fd, brs }: { fd: boolean; brs: boolean }) {
  if (!fd) return null;
  return (
    <span className="fd-badge" title={brs ? 'CAN-FD, bitrate switch' : 'CAN-FD'}>
      FD{brs ? '+BRS' : ''}
    </span>
  );
}

function FixedTable({
  frames,
  dbc,
  expanded,
  onToggle,
}: {
  frames: FrameEntry[];
  dbc: DbcSummary;
  expanded: Set<number>;
  onToggle: (id: number) => void;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th></th>
          <th>Time(ms)</th>
          <th>ID</th>
          <th>Name</th>
          <th>DLC</th>
          <th>Data</th>
          <th>Cycle</th>
          <th>Cnt</th>
        </tr>
      </thead>
      <tbody>
        {frames.map((f) => {
          const canExpand = !!f.decoded;
          const isOpen = canExpand && expanded.has(f.id);
          return (
            <Fragment key={f.id}>
              <tr
                className={canExpand ? 'expandable-row' : ''}
                onClick={() => canExpand && onToggle(f.id)}
              >
                <td className="msg-caret">{canExpand ? (isOpen ? '▾' : '▸') : ''}</td>
                <td>{fmtTime(f.ts)}</td>
                <td>
                  {fmtId(f.id)} <FdBadge fd={f.fd} brs={f.brs} />
                </td>
                <td>{f.decoded?.name ?? '-'}</td>
                <td>{f.dlc}</td>
                <td>{fmtData(f.data)}</td>
                <td>{f.cycleMs !== null ? `${f.cycleMs.toFixed(0)}ms` : '-'}</td>
                <td>{f.count}</td>
              </tr>
              {isOpen && (
                <tr className="signal-detail-row">
                  <td colSpan={8}>
                    <SignalDetail frame={f} dbc={dbc} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
        {frames.length === 0 && (
          <tr>
            <td colSpan={8} className="empty">
              수신된 메시지가 없습니다
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function SignalDetail({ frame, dbc }: { frame: FrameEntry; dbc: DbcSummary }) {
  if (!frame.decoded) return null;
  const message = dbc.messages?.find((m) => m.name === frame.decoded!.name);
  const entries = Object.entries(frame.decoded.signals);
  if (entries.length === 0) {
    return <div className="hint">정의된 신호가 없습니다</div>;
  }
  return (
    <table className="signal-detail-table">
      <thead>
        <tr>
          <th>Signal</th>
          <th>Value</th>
          <th>Unit</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([name, value]) => {
          const unit = message?.signals.find((s) => s.name === name)?.unit;
          return (
            <tr key={name}>
              <td>{name}</td>
              <td className="mono">{String(value)}</td>
              <td>{unit || ''}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// Lightweight virtual list: the last-minute buffer can hold tens of
// thousands of frames, so only the visible rows are rendered.
const ROW_H = 22;
const OVERSCAN = 10;

function TraceView({ rows, live }: { rows: RxFrame[]; live: boolean }) {
  const outerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(200);

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const measure = () => setViewH(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // live mode sticks to the newest frame at the bottom
  useEffect(() => {
    if (live && outerRef.current) {
      outerRef.current.scrollTop = outerRef.current.scrollHeight;
    }
  }, [live, rows.length]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((scrollTop + viewH) / ROW_H) + OVERSCAN);
  const visible = rows.slice(first, last);

  return (
    <div className="trace-view">
      <div className="trace-header">
        <span className="t-time">Time(ms)</span>
        <span className="t-id">ID</span>
        <span className="t-fd"></span>
        <span className="t-name">Name</span>
        <span className="t-dlc">DLC</span>
        <span className="t-data">Data</span>
      </div>
      <div
        ref={outerRef}
        className="trace-body"
        onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      >
        <div style={{ height: first * ROW_H }} />
        {visible.map((f, i) => (
          <div className="trace-row" key={first + i}>
            <span className="t-time">{fmtTime(f.ts)}</span>
            <span className="t-id">{fmtId(f.id)}</span>
            <span className="t-fd">
              <FdBadge fd={f.fd} brs={f.brs} />
            </span>
            <span className="t-name">{f.decoded?.name ?? '-'}</span>
            <span className="t-dlc">{f.dlc}</span>
            <span className="t-data">{fmtData(f.data)}</span>
          </div>
        ))}
        <div style={{ height: Math.max(0, (rows.length - last) * ROW_H) }} />
        {rows.length === 0 && <div className="empty">수신된 메시지가 없습니다</div>}
      </div>
    </div>
  );
}

const fmtClock = (ts: number) => {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const ms = String(d.getMilliseconds()).padStart(3, '0');
  return `${hh}:${mm}:${ss}.${ms}`;
};

/** Live log of widget-triggered CAN sends and test-runner events (see
 * canStore's sendSignal/sendGenerated/sendInvalid wrappers and
 * pollTestRunnerEvents) -- newest at the bottom, auto-scrolling, so it reads
 * like a console. No longer a single-signal value readout -- see the CAN
 * 신호 그래프 / CAN 메시지 표시창 widgets for that. */
export function TextDisplay(_: { config: WidgetConfig }) {
  useCanVersion();
  const bodyRef = useRef<HTMLDivElement>(null);
  const entries = canStore.activityLog;

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <div className="text-display" ref={bodyRef}>
      {entries.length === 0 && <div className="hint">아직 발생한 이벤트가 없습니다</div>}
      {entries.map((e, i) => (
        <div className="text-display-line mono" key={i}>
          <span className="text-display-time">{fmtClock(e.ts)}</span> {e.text}
        </div>
      ))}
    </div>
  );
}
