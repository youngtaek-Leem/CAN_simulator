// CAN signal graph widget: left signal picker (DBC-based) + right chart stack.
// Left layout mirrors CanLogAnalysisWidget: "signal 이름 직접입력" + 메시지별/Signal별
// Right shows selected signals as stacked canvas charts sharing xWindowMs.

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { canStore, useCanVersion, type HistoryPoint } from '../store/canStore';
import { groupedMessages, sortedMessages, useApp } from '../store/appContext';
import { MessageFilter, type MessageFilterMode } from './MessageOptions';
import type { WidgetConfig } from '../types';
import {
  CURSOR_A_COLOR,
  CURSOR_B_COLOR,
  drawDiffCursors,
  fmtDelta,
  nearestCursor,
  type DiffCursorState,
} from './DiffCursor';

interface GraphSeries {
  message: string;
  signal: string;
  color: string;
}

interface ViewRange {
  xMin: number | null;
  xMax: number | null;
  yMin: number | null;
  yMax: number | null;
}

interface Geom {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
  plotLeft: number;
  plotTop: number;
  plotW: number;
  plotH: number;
}

const PALETTE = [
  '#3b82f6',
  '#f87171',
  '#34d399',
  '#fbbf24',
  '#a78bfa',
  '#f472b6',
  '#22d3ee',
  '#fb923c',
];

const MARGIN = { left: 52, right: 10, top: 8, bottom: 22 };
const ZOOM_STEP = 1.10;
const BUTTON_ZOOM_FACTOR = 1.3;
const DOT_RADIUS = 2.5;
const DEFAULT_X_WINDOW_MS = 10_000;
const MIN_X_WINDOW_MS = 500;
const MAX_X_WINDOW_MS = 300_000;
const X_WINDOW_STEP_MS = 5_000;
const LIVE_TICK_MS = 200;

function getSeries(config: WidgetConfig): GraphSeries[] {
  return (config.options.series as GraphSeries[] | undefined) ?? [];
}

function seriesKey(s: GraphSeries) {
  return `${s.message}.${s.signal}`;
}

function nextColor(existing: GraphSeries[]): string {
  return PALETTE[existing.length % PALETTE.length];
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

function fmtValue(v: number, mode: 'hex' | 'dec'): string {
  const n = Math.round(v);
  if (mode === 'dec') return n.toString();
  const abs = Math.abs(n).toString(16).toUpperCase();
  return n < 0 ? `-0x${abs}` : `0x${abs}`;
}

function fmtWindow(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function orFallback(x: number | null, fallback: number): number {
  return x === null ? fallback : x;
}

function visibleWithPadding(points: HistoryPoint[], xMin: number, xMax: number): HistoryPoint[] {
  let start = points.findIndex((p) => canStore.relMs(p.ts) >= xMin);
  if (start === -1) return [];
  if (start > 0) start -= 1;
  let end = points.length - 1;
  while (end >= 0 && canStore.relMs(points[end].ts) > xMax) end -= 1;
  if (end < points.length - 1) end += 1;
  if (start > end) return [];
  return points.slice(start, end + 1);
}

export function GraphWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const series = getSeries(config);
  const [xWindowMs, setXWindowMs] = useState(DEFAULT_X_WINDOW_MS);
  const [search, setSearch] = useState('');
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const viewMode = (config.options.viewMode as 'byMessage' | 'bySignal' | undefined) ?? 'byMessage';
  const setViewMode = (m: 'byMessage' | 'bySignal') =>
    updateWidget({ ...config, options: { ...config.options, viewMode: m } });

  const existingKeys = new Set(series.map(seriesKey));

  const toggleKey = (key: string) => {
    if (existingKeys.has(key)) {
      canStore.unwatchSignal(key);
      updateWidget({ ...config, options: { ...config.options, series: series.filter((s) => seriesKey(s) !== key) } });
    } else {
      const [msg, sig] = key.split('.');
      const next = [...series, { message: msg, signal: sig, color: nextColor(series) }];
      updateWidget({ ...config, options: { ...config.options, series: next } });
    }
  };

  const toggleMessage = (m: { name: string; signals: { name: string }[] }, checked: boolean) => {
    const keys = m.signals.map((s) => `${m.name}.${s.name}`);
    if (checked) {
      const toAdd = keys.filter((k) => !existingKeys.has(k));
      if (toAdd.length === 0) return;
      const next = [...series];
      for (const k of toAdd) {
        const [msg, sig] = k.split('.');
        next.push({ message: msg, signal: sig, color: nextColor(next) });
      }
      updateWidget({ ...config, options: { ...config.options, series: next } });
    } else {
      const removeSet = new Set(keys);
      const next = series.filter((s) => !removeSet.has(seriesKey(s)));
      for (const k of keys) if (existingKeys.has(k)) canStore.unwatchSignal(k);
      updateWidget({ ...config, options: { ...config.options, series: next } });
    }
  };

  const removeSeries = (key: string) => {
    canStore.unwatchSignal(key);
    updateWidget({ ...config, options: { ...config.options, series: series.filter((s) => seriesKey(s) !== key) } });
  };
  const moveSeries = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= series.length) return;
    const next = [...series];
    [next[index], next[target]] = [next[target], next[index]];
    updateWidget({ ...config, options: { ...config.options, series: next } });
  };
  const zoomXWindow = (deltaMs: number) => {
    setXWindowMs((w) => Math.min(MAX_X_WINDOW_MS, Math.max(MIN_X_WINDOW_MS, w + deltaMs)));
  };

  const [cursorMode, setCursorMode] = useState(false);
  const [cursorA, setCursorA] = useState<number | null>(null);
  const [cursorB, setCursorB] = useState<number | null>(null);
  const onCursorMove = (which: 'a' | 'b', ms: number) => {
    if (which === 'a') setCursorA(ms);
    else setCursorB(ms);
  };
  const toggleCursorMode = () => {
    if (!cursorMode && cursorA === null && cursorB === null) {
      const xMax = canStore.nowMs();
      const xMin = xMax - xWindowMs;
      setCursorA(xMin + (xMax - xMin) / 3);
      setCursorB(xMin + ((xMax - xMin) * 2) / 3);
    }
    setCursorMode((m) => !m);
  };
  const cursor: DiffCursorState = { mode: cursorMode, a: cursorA, b: cursorB, onMove: onCursorMove };
  const cursorDeltaMs = cursorA !== null && cursorB !== null ? Math.abs(cursorB - cursorA) : null;

  const rxNode = canStore.getRxNode();
  const searchLower = search.trim().toLowerCase();
  const allMessages = (() => {
    if (!dbc.messages) return [];
    const { tx, rx, grouped } = groupedMessages(dbc, rxNode);
    if (msgFilter === 'tx') return tx;
    if (msgFilter === 'rx') return rx;
    if (!grouped) return sortedMessages(dbc);
    return sortedMessages(dbc);
  })();
  const filteredMessages = searchLower
    ? allMessages
        .map((m) => ({ ...m, signals: m.signals.filter((s) => s.name.toLowerCase().includes(searchLower)) }))
        .filter((m) => m.signals.length > 0)
    : allMessages.map((m) => ({ ...m }));
  const allSignalsFlat = (() => {
    const list: { key: string; message: string; signal: string; length: number; send_type: string; frame_id: number }[] = [];
    for (const m of allMessages) for (const s of m.signals) list.push({ key: `${m.name}.${s.name}`, message: m.name, signal: s.name, length: s.length, send_type: s.send_type, frame_id: m.frame_id });
    return list;
  })();
  const filteredSignalsBase = searchLower ? allSignalsFlat.filter((s) => s.signal.toLowerCase().includes(searchLower)) : allSignalsFlat;
  const filteredSignals = [...filteredSignalsBase].sort((a, b) => a.signal.toLowerCase().localeCompare(b.signal.toLowerCase()));

  const graphsColRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = graphsColRef.current;
    if (!el) return;
    const blockPageZoomOnCtrlWheel = (e: WheelEvent) => {
      if (e.ctrlKey) e.preventDefault();
    };
    el.addEventListener('wheel', blockPageZoomOnCtrlWheel, { passive: false });
    return () => el.removeEventListener('wheel', blockPageZoomOnCtrlWheel);
  }, []);

  const dragIdRef = useRef<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const handleChartDragStart = (key: string) => (e: React.DragEvent) => {
    dragIdRef.current = key;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', key);
  };
  const handleChartDragOver = (key: string) => (e: React.DragEvent) => {
    if (dragIdRef.current === null || dragIdRef.current === key) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverId(key);
  };
  const handleChartDrop = (key: string) => (e: React.DragEvent) => {
    e.preventDefault();
    const dragged = dragIdRef.current;
    dragIdRef.current = null;
    setDragOverId(null);
    if (dragged === null || dragged === key) return;
    const from = series.findIndex((s) => seriesKey(s) === dragged);
    const to = series.findIndex((s) => seriesKey(s) === key);
    if (from === -1 || to === -1) return;
    const next = [...series];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    updateWidget({ ...config, options: { ...config.options, series: next } });
  };
  const handleChartDragEnd = () => {
    dragIdRef.current = null;
    setDragOverId(null);
  };

  return (
    <div className="syslog-widget">
      <div className="graph-toolbar">
        <span className="hint">{series.length > 0 ? `${series.length}개 신호` : '신호를 선택하세요'}</span>
        <span className="spacer" />
        <span className="graph-xwindow mono">{fmtWindow(xWindowMs)}</span>
        <button className="icon-btn" title="X축 축소 (시간 범위 5초 넓게)" onClick={() => zoomXWindow(X_WINDOW_STEP_MS)}>
          −
        </button>
        <button className="icon-btn" title="X축 확대 (시간 범위 5초 좁게)" onClick={() => zoomXWindow(-X_WINDOW_STEP_MS)}>
          +
        </button>
        <button className={`small-btn ${cursorMode ? 'primary' : ''}`} title="차트에서 드래그해 두 커서를 움직이면 그 사이의 시간 간격을 읽을 수 있습니다" onClick={toggleCursorMode}>
          커서 {cursorMode ? 'ON' : 'OFF'}
        </button>
        {cursorMode && cursorDeltaMs !== null && (
          <span className="graph-xwindow mono">
            <span style={{ color: CURSOR_A_COLOR }}>A</span>
            {' - '}
            <span style={{ color: CURSOR_B_COLOR }}>B</span>
            {`: Δ ${fmtDelta(cursorDeltaMs)}`}
          </span>
        )}
      </div>
      <div className="syslog-body">
        <div className="syslog-idlist">
          <div className="syslog-section-title">CAN Signal 검색</div>
          <input className="layout-input" style={{ width: '100%', marginBottom: 6 }} placeholder="signal 이름 직접 입력 (부분 일치)" value={search} onChange={(e) => setSearch(e.target.value)} />
          {searchLower && (
            <div className="syslog-id-list" style={{ maxHeight: 140, marginBottom: 8 }}>
              {filteredSignals.length === 0 && <div className="hint">일치하는 signal 없음</div>}
              {filteredSignals.map((s) => {
                const checked = existingKeys.has(s.key);
                return (
                  <label key={s.key} className="syslog-id-row">
                    <input type="checkbox" checked={checked} onChange={() => toggleKey(s.key)} />
                    <span className="syslog-id-name" title={s.key}>
                      {s.signal}
                    </span>
                    <span className="hint" style={{ fontSize: 10 }}>
                      {s.message}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <MessageFilter value={msgFilter} onChange={setMsgFilter} />
          </div>
          <div className="syslog-section-title">
            <label>
              <input type="radio" checked={viewMode === 'byMessage'} onChange={() => setViewMode('byMessage')} /> 메시지별
            </label>
            <label style={{ marginLeft: 8 }}>
              <input type="radio" checked={viewMode === 'bySignal'} onChange={() => setViewMode('bySignal')} /> Signal별
            </label>
          </div>
          <div className="syslog-id-list">
            {!dbc.loaded ? (
              <div className="hint">DBC를 업로드하세요.</div>
            ) : viewMode === 'byMessage' ? (
              filteredMessages.length === 0 ? (
                <div className="hint">일치하는 신호 없음</div>
              ) : (
                filteredMessages.map((m) => {
                  const selectableKeys = m.signals.map((s) => `${m.name}.${s.name}`);
                  const allChecked = selectableKeys.length > 0 && selectableKeys.every((k) => existingKeys.has(k));
                  const someChecked = selectableKeys.some((k) => existingKeys.has(k)) && !allChecked;
                  const countHint = `${m.signals.length} signals`;
                  return (
                    <div key={m.name} className="syslog-id-group">
                      <div className="syslog-id-group-header">
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={allChecked}
                            ref={(el) => {
                              if (el) el.indeterminate = someChecked;
                            }}
                            onChange={(e) => toggleMessage(m as unknown as { name: string; signals: { name: string }[] }, e.target.checked)}
                          />
                          <span>
                            {m.name} (0x{m.frame_id.toString(16).toUpperCase()})
                          </span>
                        </label>
                        <span className="spacer" />
                        <span className="hint">{countHint}</span>
                      </div>
                      {m.signals.map((s) => {
                        const key = `${m.name}.${s.name}`;
                        const checked = existingKeys.has(key);
                        return (
                          <label key={key} className="syslog-id-row">
                            <input type="checkbox" checked={checked} onChange={() => toggleKey(key)} />
                            <span className="syslog-id-name" title={key}>
                              {s.name}
                            </span>
                            <span className="hint" style={{ fontSize: 10 }}>
                              {s.length}bit {s.send_type}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  );
                })
              )
            ) : filteredSignals.length === 0 ? (
              <div className="hint">신호 없음</div>
            ) : (
              filteredSignals.map((s) => {
                const checked = existingKeys.has(s.key);
                return (
                  <label key={s.key} className="syslog-id-row">
                    <input type="checkbox" checked={checked} onChange={() => toggleKey(s.key)} />
                    <span className="syslog-id-name" title={s.key}>
                      {s.signal}
                    </span>
                    <span className="hint" style={{ fontSize: 10 }}>
                      {s.message} · {s.length}bit {s.send_type}
                    </span>
                  </label>
                );
              })
            )}
          </div>
        </div>
        <div className="syslog-graphs-wrap">
          <div className="graph-charts-col syslog-graphs" ref={graphsColRef}>
            {series.length === 0 && <div className="hint">왼쪽에서 signal을 선택하세요.</div>}
            {series.map((s, i) => (
              <SignalChart
                key={seriesKey(s)}
                series={s}
                showXAxis={i === series.length - 1}
                xWindowMs={xWindowMs}
                cursor={cursor}
                onRemove={() => removeSeries(seriesKey(s))}
                onMoveUp={() => moveSeries(i, -1)}
                onMoveDown={() => moveSeries(i, 1)}
                canMoveUp={i > 0}
                canMoveDown={i < series.length - 1}
                isDragOver={dragOverId === seriesKey(s)}
                onDragStart={handleChartDragStart(seriesKey(s))}
                onDragOver={handleChartDragOver(seriesKey(s))}
                onDrop={handleChartDrop(seriesKey(s))}
                onDragEnd={handleChartDragEnd}
              />
            ))}
          </div>
          <VerticalScrollbar targetRef={graphsColRef} />
        </div>
      </div>
    </div>
  );
}

function VerticalScrollbar({ targetRef }: { targetRef: MutableRefObject<HTMLDivElement | null> }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [trackH, setTrackH] = useState(1);
  const [metrics, setMetrics] = useState({ scrollTop: 0, scrollHeight: 1, clientHeight: 1 });
  const draggingRef = useRef<{ startY: number; startScrollTop: number } | null>(null);
  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const m = () => setTrackH(el.clientHeight);
    m();
    const ro = new ResizeObserver(m);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;
    const read = () => setMetrics({ scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    read();
    el.addEventListener('scroll', read);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    const mo = new MutationObserver(read);
    mo.observe(el, { childList: true, subtree: true });
    return () => {
      el.removeEventListener('scroll', read);
      ro.disconnect();
      mo.disconnect();
    };
  }, [targetRef]);
  const { scrollTop, scrollHeight, clientHeight } = metrics;
  const scrollableH = scrollHeight - clientHeight;
  const canScroll = scrollableH > 1;
  const thumbH = canScroll ? Math.max(24, (clientHeight / scrollHeight) * trackH) : trackH;
  const maxThumbTop = Math.max(0, trackH - thumbH);
  const thumbTop = canScroll && maxThumbTop > 0 ? (scrollTop / scrollableH) * maxThumbTop : 0;
  const onThumbDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = { startY: e.clientY, startScrollTop: scrollTop };
  };
  const onThumbMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = draggingRef.current;
    const el = targetRef.current;
    if (!d || !el || maxThumbTop <= 0) return;
    const dy = e.clientY - d.startY;
    const delta = (dy / maxThumbTop) * scrollableH;
    el.scrollTop = Math.min(scrollableH, Math.max(0, d.startScrollTop + delta));
  };
  const onThumbUp = () => {
    draggingRef.current = null;
  };
  const onTrackDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || !canScroll) return;
    const el = targetRef.current;
    if (!el) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickY = e.clientY - rect.top;
    const targetTop = Math.min(maxThumbTop, Math.max(0, clickY - thumbH / 2));
    el.scrollTop = maxThumbTop > 0 ? (targetTop / maxThumbTop) * scrollableH : 0;
  };
  return (
    <div className="syslog-scrollbar-track" ref={trackRef} onPointerDown={onTrackDown}>
      {canScroll && <div className="syslog-scrollbar-thumb" style={{ top: thumbTop, height: thumbH }} onPointerDown={onThumbDown} onPointerMove={onThumbMove} onPointerUp={onThumbUp} onPointerLeave={onThumbUp} />}
    </div>
  );
}

// One independent mini-chart for a single signal: own canvas, own X/Y view
function SignalChart({
  series,
  showXAxis,
  xWindowMs,
  cursor,
  onRemove,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
  isDragOver,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  series: GraphSeries;
  showXAxis: boolean;
  xWindowMs: number;
  cursor: DiffCursorState;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  isDragOver?: boolean;
  onDragStart?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
  onDragEnd?: () => void;
}) {
  useCanVersion();
  const key = seriesKey(series);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<ViewRange>({ xMin: null, xMax: null, yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; view: ViewRange } | null>(null);
  const cursorDragRef = useRef<'a' | 'b' | null>(null);
  const [valueMode, setValueMode] = useState<'hex' | 'dec'>('hex');
  const lastGeomRef = useRef<Geom>({
    xMin: 0,
    xMax: 1,
    yMin: 0,
    yMax: 1,
    plotLeft: MARGIN.left,
    plotTop: MARGIN.top,
    plotW: 1,
    plotH: 1,
  });
  const [size, setSize] = useState({ w: 260, h: 200 });
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);

  useEffect(() => {
    canStore.watchSignal(key);
    return () => canStore.unwatchSignal(key);
  }, [key]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      if (viewRef.current.xMin === null) redraw();
    }, LIVE_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const resetView = () => {
    viewRef.current = { xMin: null, xMax: null, yMin: null, yMax: null };
    redraw();
  };

  const zoomXButton = (factor: number) => {
    const g = lastGeomRef.current;
    const v = viewRef.current;
    const xMin = v.xMin ?? g.xMin;
    const xMax = v.xMax ?? g.xMax;
    const center = (xMin + xMax) / 2;
    const halfWidth = ((xMax - xMin) / 2) * factor;
    v.xMin = center - halfWidth;
    v.xMax = center + halfWidth;
    redraw();
  };
  const zoomYButton = (factor: number) => {
    const g = lastGeomRef.current;
    const v = viewRef.current;
    const yMin = v.yMin ?? g.yMin;
    const yMax = v.yMax ?? g.yMax;
    const center = (yMin + yMax) / 2;
    const halfHeight = ((yMax - yMin) / 2) * factor;
    v.yMin = center - halfHeight;
    v.yMax = center + halfHeight;
    redraw();
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, size.w);
    const h = Math.max(1, size.h);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const bottomMargin = showXAxis ? MARGIN.bottom : 4;
    const plotLeft = MARGIN.left;
    const plotTop = MARGIN.top;
    const plotW = Math.max(1, w - MARGIN.left - MARGIN.right);
    const plotH = Math.max(1, h - MARGIN.top - bottomMargin);

    const points = canStore.signalHistory.get(key) ?? [];

    let xMin = viewRef.current.xMin;
    let xMax = viewRef.current.xMax;
    if (xMin === null || xMax === null) {
      xMax = canStore.nowMs();
      xMin = xMax - xWindowMs;
    }

    const visible = points.filter((p) => {
      const x = canStore.relMs(p.ts);
      return x >= xMin! && x <= xMax!;
    });

    let yMin = viewRef.current.yMin;
    let yMax = viewRef.current.yMax;
    if (yMin === null || yMax === null) {
      if (visible.length > 0) {
        const ys = visible.map((p) => p.value);
        const lo = Math.min(...ys);
        const hi = Math.max(...ys);
        const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 1;
        yMin = Math.max(0, lo - pad);
        yMax = hi + pad;
      } else {
        yMin = 0;
        yMax = 1;
      }
    }

    const xToPx = (xMs: number) => plotLeft + ((xMs - xMin!) / (xMax! - xMin!)) * plotW;
    const yToPx = (v: number) => plotTop + plotH - ((v - yMin!) / (yMax! - yMin!)) * plotH;

    ctx.strokeStyle = '#363b47';
    ctx.fillStyle = '#8b909c';
    ctx.font = '10px monospace';
    ctx.lineWidth = 1;
    for (const t of niceTicks(xMin, xMax, 3)) {
      const px = xToPx(t);
      ctx.beginPath();
      ctx.moveTo(px, plotTop);
      ctx.lineTo(px, plotTop + plotH);
      ctx.stroke();
      if (showXAxis) ctx.fillText(`${Math.round(t)}`, px - 12, h - 6);
    }
    for (const t of niceTicks(yMin, yMax, 4)) {
      const py = yToPx(t);
      ctx.beginPath();
      ctx.moveTo(plotLeft, py);
      ctx.lineTo(plotLeft + plotW, py);
      ctx.stroke();
      ctx.fillText(fmtValue(t, valueMode), 2, py + 3);
    }
    ctx.strokeStyle = '#4b5160';
    ctx.strokeRect(plotLeft, plotTop, plotW, plotH);

    let drawPoints = visibleWithPadding(points, xMin, xMax);
    if (drawPoints.length > plotW * 2) {
      const step = Math.ceil(drawPoints.length / (plotW * 2));
      drawPoints = drawPoints.filter((_, i) => i % step === 0);
    }
    if (drawPoints.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotW, plotH);
      ctx.clip();

      ctx.strokeStyle = series.color;
      ctx.fillStyle = series.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let prevPy = 0;
      drawPoints.forEach((p: HistoryPoint, i: number) => {
        const px = xToPx(canStore.relMs(p.ts));
        const py = yToPx(p.value);
        if (i === 0) {
          ctx.moveTo(px, py);
        } else {
          ctx.lineTo(px, prevPy);
          ctx.lineTo(px, py);
        }
        prevPy = py;
      });
      ctx.stroke();
      for (const p of drawPoints) {
        const px = xToPx(canStore.relMs(p.ts));
        const py = yToPx(p.value);
        if (px < plotLeft - 5 || px > plotLeft + plotW + 5) continue;
        ctx.beginPath();
        ctx.arc(px, py, DOT_RADIUS, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawDiffCursors(ctx, cursor, xMin, xMax, plotTop, plotH, xToPx);

    lastGeomRef.current = { xMin, xMax, yMin, yMax, plotLeft, plotTop, plotW, plotH };
  });

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const g = lastGeomRef.current;
    const factor = e.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    const inX = px >= g.plotLeft && px <= g.plotLeft + g.plotW;
    const inY = py >= g.plotTop && py <= g.plotTop + g.plotH;
    const overXAxisStrip = px >= g.plotLeft && px <= g.plotLeft + g.plotW && py > g.plotTop + g.plotH;
    const overYAxisStrip = py >= g.plotTop && py <= g.plotTop + g.plotH && px < g.plotLeft;

    const zoomX = overXAxisStrip || (inX && inY);
    const zoomY = overYAxisStrip;
    const v = viewRef.current;

    if (zoomX) {
      const cursorX = g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin);
      const xMin = v.xMin ?? g.xMin;
      const xMax = v.xMax ?? g.xMax;
      v.xMin = cursorX - (cursorX - xMin) * factor;
      v.xMax = cursorX + (xMax - cursorX) * factor;
    }
    if (zoomY) {
      const cursorY = g.yMax - ((py - g.plotTop) / g.plotH) * (g.yMax - g.yMin);
      const yMin = v.yMin ?? g.yMin;
      const yMax = v.yMax ?? g.yMax;
      v.yMin = cursorY - (cursorY - yMin) * factor;
      v.yMax = cursorY + (yMax - cursorY) * factor;
    }
    redraw();
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const g = lastGeomRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < g.plotLeft || px > g.plotLeft + g.plotW || py < g.plotTop || py > g.plotTop + g.plotH) {
      return;
    }
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
    if (cursor.mode) {
      const msToPx = (x: number) => g.plotLeft + ((x - g.xMin) / (g.xMax - g.xMin)) * g.plotW;
      const which = nearestCursor(cursor, px, msToPx);
      cursorDragRef.current = which;
      cursor.onMove(which, g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin));
      return;
    }
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      view: {
        xMin: orFallback(viewRef.current.xMin, g.xMin),
        xMax: orFallback(viewRef.current.xMax, g.xMax),
        yMin: orFallback(viewRef.current.yMin, g.yMin),
        yMax: orFallback(viewRef.current.yMax, g.yMax),
      },
    };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (cursorDragRef.current) {
      const g = lastGeomRef.current;
      const rect = canvasRef.current!.getBoundingClientRect();
      const px = e.clientX - rect.left;
      cursor.onMove(cursorDragRef.current, g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin));
      return;
    }
    const drag = dragRef.current;
    if (!drag) return;
    const g = lastGeomRef.current;
    const dxPx = e.clientX - drag.x;
    const dyPx = e.clientY - drag.y;
    const dataDx = (dxPx / g.plotW) * (drag.view.xMax! - drag.view.xMin!);
    const dataDy = (dyPx / g.plotH) * (drag.view.yMax! - drag.view.yMin!);
    viewRef.current = {
      xMin: drag.view.xMin! - dataDx,
      xMax: drag.view.xMax! - dataDx,
      yMin: drag.view.yMin! + dataDy,
      yMax: drag.view.yMax! + dataDy,
    };
    redraw();
  };
  const onPointerUp = () => {
    dragRef.current = null;
    cursorDragRef.current = null;
  };

  return (
    <div className={`graph-chart${isDragOver ? ' syslog-chart-dragover' : ''}`} onDragOver={onDragOver} onDrop={onDrop}>
      <div className="graph-chart-header syslog-chart-header-draggable" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} title="드래그해서 그래프 순서 바꾸기">
        <span className="graph-swatch" style={{ background: series.color }} />
        <span className="graph-chart-title" title={`${series.message}.${series.signal}`}>
          {series.signal}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="Y값 표시 형식(16진수/10진수) 전환" onClick={() => setValueMode((m) => (m === 'hex' ? 'dec' : 'hex'))}>
          {valueMode === 'hex' ? 'HEX' : 'DEC'}
        </button>
        <button className="icon-btn" title="X축 확대" onClick={() => zoomXButton(1 / BUTTON_ZOOM_FACTOR)}>
          X+
        </button>
        <button className="icon-btn" title="X축 축소" onClick={() => zoomXButton(BUTTON_ZOOM_FACTOR)}>
          X−
        </button>
        <button className="icon-btn" title="Y축 확대" onClick={() => zoomYButton(1 / BUTTON_ZOOM_FACTOR)}>
          Y+
        </button>
        <button className="icon-btn" title="Y축 축소" onClick={() => zoomYButton(BUTTON_ZOOM_FACTOR)}>
          Y−
        </button>
        <button className="icon-btn" title="X/Y 축 자동 맞춤으로 리셋" onClick={resetView}>
          ⟲
        </button>
        <button className="icon-btn" title="위로 이동" disabled={!canMoveUp} onClick={onMoveUp}>
          ▲
        </button>
        <button className="icon-btn" title="아래로 이동" disabled={!canMoveDown} onClick={onMoveDown}>
          ▼
        </button>
        <button className="icon-btn" title="제거" onClick={onRemove}>
          ✕
        </button>
      </div>
      <div className="graph-canvas-wrap" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        />
      </div>
    </div>
  );
}
