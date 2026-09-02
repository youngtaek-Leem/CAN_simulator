// Display widgets: CAN message grid and the widget/test-runner activity log.
// Both read from canStore and re-render only on the throttled version tick.

import { Fragment, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { canStore, useCanVersion } from '../store/canStore';
import { groupedMessages, useApp } from '../store/appContext';
import type { DbcSummary, FrameEntry, RxFrame, WidgetConfig } from '../types';

const fmtId = (id: number) => `0x${id.toString(16).toUpperCase().padStart(3, '0')}`;
const fmtData = (hex: string) => hex.toUpperCase().replace(/(..)/g, '$1 ').trim();
const fmtTime = (ts: number) => canStore.relMs(ts).toFixed(0);

/** Numeric signal values honor the hex/dec toggle; choice-decoded values
 * (e.g. "Off"/"On") are already a string label and display unchanged. */
function fmtSignalValue(v: number | string, mode: 'hex' | 'dec'): string {
  if (typeof v !== 'number') return v;
  if (mode === 'dec') return v.toString();
  const n = Math.round(v);
  const abs = Math.abs(n).toString(16).toUpperCase();
  return n < 0 ? `-0x${abs}` : `0x${abs}`;
}

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
 * the AMP TX signals -- i.e. signals belonging to messages the real DUT
 * (AMP) sends, which the simulator receives (groupedMessages' "rx" set,
 * relative to the configured RX node). Once a signal has been seen with a
 * valid value at least once, its row stays in the table for good and only
 * updates when a NEW valid value arrives -- a frame decoding as invalid
 * just leaves the row showing its last-known-good value instead of making
 * it disappear (see canStore.lastValidSignal). Unlike CanMessageDisplay,
 * there's no message-row-with-expandable-detail: each row IS a signal. */
export function RxSignalDisplay({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const ampTxNames = new Set(groupedMessages(dbc, canStore.getRxNode()).rx.map((m) => m.name));

  // 정렬 기준과 Hex/Dec 표시 모드는 config.options에 저장해 레이아웃 저장/불러오기 후에도 유지한다.
  const sortBy = (config.options.rxSortBy as 'message' | 'signal' | undefined) ?? 'message';
  const setSortBy = (m: 'message' | 'signal') =>
    updateWidget({ ...config, options: { ...config.options, rxSortBy: m } });
  const valueMode = (config.options.rxValueMode as 'hex' | 'dec' | undefined) ?? 'dec';
  const setValueMode = (m: 'hex' | 'dec') =>
    updateWidget({ ...config, options: { ...config.options, rxValueMode: m } });

  const rows: SignalRow[] = [];
  for (const entry of canStore.lastValidSignal.values()) {
    if (!ampTxNames.has(entry.message)) continue;
    const message = dbc.messages?.find((m) => m.name === entry.message);
    rows.push({
      ts: entry.ts,
      message: entry.message,
      signal: entry.signal,
      value: entry.value,
      unit: message?.signals.find((s) => s.name === entry.signal)?.unit ?? null,
    });
  }
  rows.sort((a, b) =>
    sortBy === 'message'
      ? a.message.localeCompare(b.message) || a.signal.localeCompare(b.signal)
      : a.signal.localeCompare(b.signal) || a.message.localeCompare(b.message),
  );

  return (
    <div className="msg-display">
      <div className="msg-toolbar">
        <span className="hint">{rows.length}개 신호</span>
        <span className="spacer" />
        <span className="seg">
          <button
            className={`small-btn ${sortBy === 'message' ? 'seg-active' : ''}`}
            onClick={() => setSortBy('message')}
            title="Message 이름 기준으로 정렬"
          >
            Message순
          </button>
          <button
            className={`small-btn ${sortBy === 'signal' ? 'seg-active' : ''}`}
            onClick={() => setSortBy('signal')}
            title="Signal 이름 기준으로 정렬"
          >
            Signal순
          </button>
        </span>
        <button
          className="icon-btn"
          title="Value 표시 형식(16진수/10진수) 전환"
          onClick={() => setValueMode(valueMode === 'hex' ? 'dec' : 'hex')}
        >
          {valueMode === 'hex' ? 'HEX' : 'DEC'}
        </button>
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
              <td className="mono">{fmtSignalValue(r.value, valueMode)}</td>
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
  const [showFilterPicker, setShowFilterPicker] = useState(false);

  const setMode = (m: 'fixed' | 'trace') =>
    updateWidget({ ...config, options: { ...config.options, viewMode: m } });

  const togglePause = () => {
    if (!paused) {
      const src = filterActive ? canStore.trace.filter((f) => filterSet.has(f.id)) : canStore.trace;
      setSnapshot(src.slice(-30000));
    }
    setPaused(!paused);
  };

  const handleUserScrollPause = () => {
    if (!paused && mode === 'trace') {
      const src = filterActive ? canStore.trace.filter((f) => filterSet.has(f.id)) : canStore.trace;
      setSnapshot(src.slice(-30000));
      setPaused(true);
    }
  };

  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Pass 필터: 선택한 ID만 표시 (아무것도 선택 안 하면 전체 표시). 선택 목록은
  // 위젯 config에 저장되어 레이아웃과 함께 저장/복원된다.
  const passFilterIds = (config.options.passFilterIds as number[] | undefined) ?? [];
  const filterActive = passFilterIds.length > 0;
  const filterSet = new Set(passFilterIds);
  const setPassFilterIds = (ids: number[]) =>
    updateWidget({ ...config, options: { ...config.options, passFilterIds: ids } });

  const frames = [...canStore.frames.values()]
    .filter((f) => !filterActive || filterSet.has(f.id))
    .sort((a, b) => a.id - b.id);
  // Live trace: oldest at top, newest at bottom, auto-scroll down.
  // While live we decimate (full skip) to reduce render load — show only sampled
  // rows so the UI stays responsive even with burst CAN traffic. Paused snapshot
  // shows all rows without skipping (up to TRACE_CAP 30000).
  const rawTraceSource = filterActive ? canStore.trace.filter((f) => filterSet.has(f.id)) : canStore.trace;
  const snapshotRows = filterActive ? snapshot.filter((f) => filterSet.has(f.id)) : snapshot;
  // Decimate for live display: keep at most ~1000 rows, always keep the last row
  const decimate = (rows: RxFrame[]): RxFrame[] => {
    if (rows.length <= 1000) return rows;
    const step = Math.ceil(rows.length / 1000);
    const out: RxFrame[] = [];
    for (let i = 0; i < rows.length; i += step) out.push(rows[i]);
    if (out[out.length - 1] !== rows[rows.length - 1]) out.push(rows[rows.length - 1]);
    return out;
  };
  const traceSource = rawTraceSource; // keep original for counts
  const displayRows = decimate(rawTraceSource);

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
        <button
          className={`small-btn ${filterActive ? 'primary' : ''}`}
          onClick={() => setShowFilterPicker(true)}
          title="Pass 필터: 선택한 메시지 ID만 표시"
        >
          필터{filterActive ? ` (${passFilterIds.length})` : ''}
        </button>
        <span className="hint">
          {paused
            ? `일시중지 — 최근 1분 ${snapshotRows.length}개`
            : mode === 'fixed'
              ? `${frames.length} IDs`
              : displayRows.length < traceSource.length
                ? `${displayRows.length}/${traceSource.length}개 표시 중 (최근 1분, 간략)`
                : `${traceSource.length}개 (최근 1분)`}
        </span>
        <span className="spacer" />
        <button className="small-btn" onClick={() => canStore.clearFrames()}>
          Clear
        </button>
      </div>
      {paused ? (
        <TraceView rows={snapshotRows} live={false} />
      ) : mode === 'trace' ? (
        <TraceView rows={displayRows} rawTotal={traceSource.length} live={true} onUserScroll={handleUserScrollPause} />
      ) : (
        <FixedTable frames={frames} dbc={dbc} expanded={expanded} onToggle={toggleExpanded} />
      )}
      {showFilterPicker && (
        <IdFilterPicker
          selectedIds={passFilterIds}
          onSave={(ids) => {
            setPassFilterIds(ids);
            setShowFilterPicker(false);
          }}
          onClose={() => setShowFilterPicker(false)}
        />
      )}
    </div>
  );
}

// Pass 필터 메시지 선택 모달 -- DBC에 정의된 메시지뿐 아니라 현재 수신 중인
// (DBC에 없는) ID도 함께 골라서 필터에 넣을 수 있다. ReplayBox.tsx의
// MessagePicker와 같은 draft/commit + portal 패턴.
function IdFilterPicker({
  selectedIds,
  onSave,
  onClose,
}: {
  selectedIds: number[];
  onSave: (ids: number[]) => void;
  onClose: () => void;
}) {
  const { dbc } = useApp();
  const [draft, setDraft] = useState<Set<number>>(new Set(selectedIds));

  const names = new Map<number, string>();
  for (const m of dbc.messages ?? []) names.set(m.frame_id, m.name);
  for (const id of canStore.frames.keys()) {
    if (!names.has(id)) names.set(id, '(DBC 미정의)');
  }
  const items = [...names.entries()].sort((a, b) => a[0] - b[0]);

  const toggle = (id: number) =>
    setDraft((d) => {
      const next = new Set(d);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Pass 필터 메시지 선택</h3>
        <p className="hint">선택한 메시지 ID만 표시됩니다. 아무것도 선택하지 않으면 전체 표시.</p>
        <div className="picker-list">
          {items.map(([id, name]) => (
            <label key={id} className="picker-item">
              <input type="checkbox" checked={draft.has(id)} onChange={() => toggle(id)} />
              <span className="mono">{fmtId(id)}</span>
              <span>{name}</span>
            </label>
          ))}
          {items.length === 0 && <div className="empty">표시할 메시지가 없습니다</div>}
        </div>
        <div className="modal-buttons">
          <button className="small-btn" onClick={() => setDraft(new Set())}>
            전체 해제
          </button>
          <button className="small-btn" onClick={() => setDraft(new Set(items.map(([id]) => id)))}>
            전체 선택
          </button>
          <span className="spacer" />
          <button onClick={() => onSave([...draft])}>적용</button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
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
// Live mode: oldest at top, newest at bottom, auto-scroll down only when
// user is already at bottom. While live we decimate rows in the parent
// (full skip) so render load stays low; paused shows all rows.
const ROW_H = 22;
const OVERSCAN = 10;

function TraceView({
  rows,
  live,
  rawTotal: _rawTotal,
  onUserScroll,
}: {
  rows: RxFrame[];
  live: boolean;
  rawTotal?: number;
  onUserScroll?: () => void;
}) {
  const outerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(200);
  const total = rows.length;
  const rafScrollRef = useRef<number | null>(null);
  const tickingRef = useRef(false);

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const measure = () => setViewH(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // auto-scroll down only when already at bottom (natural scroll)
  useEffect(() => {
    if (!live || !outerRef.current) return;
    const el = outerRef.current;
    const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (!wasAtBottom) return;
    if (rafScrollRef.current !== null) cancelAnimationFrame(rafScrollRef.current);
    rafScrollRef.current = requestAnimationFrame(() => {
      if (outerRef.current) outerRef.current.scrollTop = outerRef.current.scrollHeight;
      rafScrollRef.current = null;
    });
    return () => {
      if (rafScrollRef.current !== null) cancelAnimationFrame(rafScrollRef.current);
    };
  }, [live, total]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(total, Math.ceil((scrollTop + viewH) / ROW_H) + OVERSCAN);
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
        onScroll={(e) => {
          const el = e.target as HTMLDivElement;
          if (tickingRef.current) return;
          tickingRef.current = true;
          requestAnimationFrame(() => {
            tickingRef.current = false;
            setScrollTop(el.scrollTop);
            if (live && onUserScroll) {
              const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
              if (!atBottom) onUserScroll();
            }
          });
        }}
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
        <div style={{ height: Math.max(0, (total - last) * ROW_H) }} />
        {total === 0 && <div className="empty">수신된 메시지가 없습니다</div>}
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '2px 4px', flexShrink: 0 }}>
        <button className="icon-btn" title="이벤트 로그 초기화" onClick={() => canStore.clearActivity()}>
          초기화
        </button>
      </div>
      <div className="text-display" ref={bodyRef} style={{ flex: 1, minHeight: 0 }}>
        {entries.length === 0 && <div className="hint">아직 발생한 이벤트가 없습니다</div>}
        {entries.map((e, i) => (
          <div className="text-display-line mono" key={i}>
            <span className="text-display-time">{fmtClock(e.ts)}</span> {e.text}
          </div>
        ))}
      </div>
    </div>
  );
}
