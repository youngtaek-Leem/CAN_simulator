// CAN signal graph widget: left signal picker (DBC-based) + right chart stack.
// Shared X: 좌측 0s 단조 증가, 좌→우로 채운 뒤 우측 도달 후 우→좌 슬라이드. Pause는 그리기만 동결.

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

type SharedXView = { xMin: number | null; xMax: number | null };

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

function fmtTimeMs(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(3)}s`;
}

function fmtValueRaw(v: number, mode: 'dec' | 'hex' | 'desc', choices: Record<string, string> | null): string {
  if (mode === 'hex') {
    const n = Math.round(v);
    const abs = Math.abs(n).toString(16).toUpperCase();
    return n < 0 ? `-0x${abs}` : `0x${abs}`;
  }
  if (mode === 'desc' && choices) {
    const lbl = choices[String(Math.round(v))];
    if (lbl !== undefined) return `${lbl} (${Math.round(v)})`;
  }
  return String(Math.round(v));
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

function findHeldPoint(points: HistoryPoint[], hoverX: number): HistoryPoint | null {
  let held: HistoryPoint | null = null;
  for (const p of points) {
    if (canStore.relMs(p.ts) <= hoverX) held = p;
    else break;
  }
  return held;
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

  // 광역 값 표시 모드 (모든 그래프에 일괄 적용) — 레이아웃에 저장
  const globalValueMode = (config.options.globalValueMode as 'dec' | 'hex' | 'desc' | undefined) ?? 'dec';
  const [globalValueVersion, setGlobalValueVersion] = useState(0);
  const cycleGlobalValueMode = () => {
    const next = globalValueMode === 'dec' ? 'hex' : globalValueMode === 'hex' ? 'desc' : 'dec';
    updateWidget({ ...config, options: { ...config.options, globalValueMode: next } });
    setGlobalValueVersion((v) => v + 1);
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
      const xMax = sharedXRef.current.xMax ?? canStore.nowMs();
      const xMin = sharedXRef.current.xMin ?? Math.max(0, xMax - xWindowMs);
      setCursorA(xMin + (xMax - xMin) / 3);
      setCursorB(xMin + ((xMax - xMin) * 2) / 3);
    }
    setCursorMode((m) => !m);
  };
  const cursor: DiffCursorState = { mode: cursorMode, a: cursorA, b: cursorB, onMove: onCursorMove };
  const cursorDeltaMs = cursorA !== null && cursorB !== null ? Math.abs(cursorB - cursorA) : null;

  // 공유 X + Pause + Hover (전 차트 동기)
  const sharedXRef = useRef<SharedXView>({ xMin: null, xMax: null });
  const [sharedVersion, setSharedVersion] = useState(0);
  const notifyChange = () => setSharedVersion((n) => n + 1);
  const [paused, setPaused] = useState(false);
  const frozenXMaxRef = useRef<number | null>(null);
  const hoverXRef = useRef<number | null>(null);
  const hoveredIdxRef = useRef<number | null>(null);
  const setHoverX = (x: number | null) => {
    hoverXRef.current = x;
    notifyChange();
  };
  const [resetToken, setResetToken] = useState(0);

  const togglePause = () => {
    if (!paused) {
      frozenXMaxRef.current = sharedXRef.current.xMax ?? canStore.nowMs();
      setPaused(true);
    } else {
      frozenXMaxRef.current = null;
      sharedXRef.current = { xMin: null, xMax: null };
      setPaused(false);
      notifyChange();
    }
  };
  const resetSharedX = () => {
    sharedXRef.current = { xMin: null, xMax: null };
    setResetToken((n) => n + 1);
    notifyChange();
  };

  // 라이브 롤링 — 공유 X가 auto일 때만 tick, Pause 시 정지
  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => {
      if (sharedXRef.current.xMin === null) notifyChange();
    }, LIVE_TICK_MS);
    return () => clearInterval(id);
  }, [paused]);

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
        <button className="icon-btn" title="모든 그래프 값 표시 형식 일괄 전환 (DEC/HEX/DESC) — 각 그래프 로컬 버튼은 해당 그래프만 변경" onClick={cycleGlobalValueMode}>
          {globalValueMode === 'dec' ? 'DEC' : globalValueMode === 'hex' ? 'HEX' : 'DESC'}
        </button>
        <button className={`small-btn ${paused ? 'primary' : ''}`} title={paused ? '그래프 재개 (수집은 계속됨)' : '그래프 일시정지 (그리기만 멈춤, 수집은 계속)'} onClick={togglePause}>
          {paused ? '▶ Resume' : '⏸ Pause'}
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
        <button className="icon-btn" title="모든 그래프 X/Y 리셋" onClick={resetSharedX}>
          ⟲
        </button>
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
                xViewRef={sharedXRef}
                xVersion={sharedVersion}
                notifyChange={notifyChange}
                paused={paused}
                frozenXMax={frozenXMaxRef.current}
                hoverXRef={hoverXRef}
                hoveredIdxRef={hoveredIdxRef}
                setHoverX={setHoverX}
                graphIndex={i}
                cursor={cursor}
                resetToken={resetToken}
                globalValueMode={globalValueMode}
                globalValueVersion={globalValueVersion}
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

// 공유 X + Pause + Hover + 광역/로컬 값 표시 대응 SignalChart
function SignalChart({
  series,
  showXAxis,
  xWindowMs,
  xViewRef,
  xVersion,
  notifyChange,
  paused,
  frozenXMax,
  hoverXRef,
  hoveredIdxRef,
  setHoverX,
  graphIndex,
  cursor,
  resetToken,
  globalValueMode,
  globalValueVersion,
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
  xViewRef: MutableRefObject<SharedXView>;
  xVersion: number;
  notifyChange: () => void;
  paused: boolean;
  frozenXMax: number | null;
  hoverXRef: MutableRefObject<number | null>;
  hoveredIdxRef: MutableRefObject<number | null>;
  setHoverX: (x: number | null) => void;
  graphIndex: number;
  cursor: DiffCursorState;
  resetToken: number;
  globalValueMode: 'dec' | 'hex' | 'desc';
  globalValueVersion: number;
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
  const version = useCanVersion();
  const key = seriesKey(series);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const yViewRef = useRef<{ yMin: number | null; yMax: number | null }>({ yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; xView: SharedXView; yView: { yMin: number | null; yMax: number | null } } | null>(null);
  const cursorDragRef = useRef<'a' | 'b' | null>(null);
  // 로컬 값 표시 모드 (null이면 광역 추종) — 광역 변경 시 덮어쓰기
  const [localValueMode, setLocalValueMode] = useState<'hex' | 'dec' | 'desc' | null>(null);
  useEffect(() => {
    setLocalValueMode(null);
  }, [globalValueVersion]);
  const valueMode = localValueMode ?? globalValueMode;
  const cycleLocalValueMode = () => {
    const eff = localValueMode ?? globalValueMode;
    const next = eff === 'dec' ? 'hex' : eff === 'hex' ? 'desc' : 'dec';
    setLocalValueMode(next);
  };
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
  const { dbc } = useApp();
  const choices = (() => {
    const msg = dbc.messages?.find((m) => m.name === series.message);
    const sig = msg?.signals.find((s) => s.name === series.signal);
    return sig?.choices ?? null;
  })();

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
    yViewRef.current = { yMin: null, yMax: null };
  }, [resetToken]);

  const zoomXButton = (factor: number) => {
    const g = lastGeomRef.current;
    const xMin = xViewRef.current.xMin ?? g.xMin;
    const xMax = xViewRef.current.xMax ?? g.xMax;
    const center = (xMin + xMax) / 2;
    const halfWidth = ((xMax - xMin) / 2) * factor;
    xViewRef.current.xMin = center - halfWidth;
    xViewRef.current.xMax = center + halfWidth;
    notifyChange();
  };
  const zoomYButton = (factor: number) => {
    const g = lastGeomRef.current;
    const v = yViewRef.current;
    const yMin = v.yMin ?? g.yMin;
    const yMax = v.yMax ?? g.yMax;
    const center = (yMin + yMax) / 2;
    const halfHeight = ((yMax - yMin) / 2) * factor;
    v.yMin = center - halfHeight;
    v.yMax = center + halfHeight;
    notifyChange();
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

    let xMin = xViewRef.current.xMin;
    let xMax = xViewRef.current.xMax;
    if (xMin === null || xMax === null) {
      const now = paused && frozenXMax !== null ? frozenXMax : canStore.nowMs();
      xMax = now;
      xMin = Math.max(0, xMax - xWindowMs);
      // 초기 + Pause 중에도 xMin이 0에서 시작해 단조 증가하도록 클램프
      // xMax가 window보다 작을 때는 0~xMax로 신축, 이후 window 폭 유지
      if (xMax < xWindowMs) {
        xMin = 0;
        // xMax는 그대로 now (창이 다 차기 전까지는 오른쪽 여백이 생김 — 좌→우 채우기)
      }
    }

    const visible = points.filter((p) => {
      const x = canStore.relMs(p.ts);
      return x >= xMin! && x <= xMax!;
    });

    let yMin = yViewRef.current.yMin;
    let yMax = yViewRef.current.yMax;
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
    for (const t of niceTicks(xMin, xMax, 10)) {
      const px = xToPx(t);
      ctx.beginPath();
      ctx.moveTo(px, plotTop);
      ctx.lineTo(px, plotTop + plotH);
      ctx.stroke();
      if (showXAxis) ctx.fillText(fmtTimeMs(t), px - 16, h - 6);
    }
    // Y 틱: DESC이고 distinct가 12개 이하면 실제 값으로, 아니면 niceTicks
    const distinct = [...new Set(visible.map((p) => p.value))].sort((a, b) => a - b);
    const yTickValues = valueMode === 'desc' && distinct.length > 0 && distinct.length <= 12 ? distinct : niceTicks(yMin, yMax, 4);
    for (const t of yTickValues) {
      const py = yToPx(t);
      if (py < plotTop - 0.5 || py > plotTop + plotH + 0.5) continue;
      ctx.beginPath();
      ctx.moveTo(plotLeft, py);
      ctx.lineTo(plotLeft + plotW, py);
      ctx.stroke();
      ctx.fillText(fmtValueRaw(t, valueMode, choices), 2, py + 3);
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

    // 전 차트 동기 Hover: 십자선 + 툴팁
    const hoverX = hoverXRef.current;
    if (hoverX !== null && hoverX >= xMin && hoverX <= xMax) {
      const px = xToPx(hoverX);
      ctx.save();
      ctx.strokeStyle = '#ffffff88';
      ctx.setLineDash([2, 2]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, plotTop);
      ctx.lineTo(px, plotTop + plotH);
      ctx.stroke();
      ctx.restore();
    }
    if (hoverX !== null && hoveredIdxRef.current === graphIndex) {
      const held = findHeldPoint(points, hoverX);
      const text = `${fmtTimeMs(hoverX)}  ${held ? fmtValueRaw(held.value, valueMode, choices) : '-'}`;
      ctx.font = '10px monospace';
      const tw = ctx.measureText(text).width;
      const px = xToPx(hoverX);
      let tx = px + 6;
      if (tx + tw + 6 > w) tx = px - tw - 6;
      const ty = plotTop + 12;
      ctx.fillStyle = 'rgba(0,0,0,0.8)';
      ctx.fillRect(tx - 3, ty - 10, tw + 6, 14);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(text, tx, ty);
    }

    lastGeomRef.current = { xMin, xMax, yMin, yMax, plotLeft, plotTop, plotW, plotH };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, xWindowMs, xVersion, paused, frozenXMax, series, showXAxis, valueMode, resetToken, version]);

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    if (!e.ctrlKey) return;
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

    if (zoomX) {
      const cursorX = g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin);
      const xMin = xViewRef.current.xMin ?? g.xMin;
      const xMax = xViewRef.current.xMax ?? g.xMax;
      xViewRef.current.xMin = cursorX - (cursorX - xMin) * factor;
      xViewRef.current.xMax = cursorX + (xMax - cursorX) * factor;
      notifyChange();
      return;
    }
    if (zoomY) {
      const cursorY = g.yMax - ((py - g.plotTop) / g.plotH) * (g.yMax - g.yMin);
      const yMin = yViewRef.current.yMin ?? g.yMin;
      const yMax = yViewRef.current.yMax ?? g.yMax;
      yViewRef.current.yMin = cursorY - (cursorY - yMin) * factor;
      yViewRef.current.yMax = cursorY + (yMax - cursorY) * factor;
      notifyChange();
    }
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
      xView: {
        xMin: orFallback(xViewRef.current.xMin, g.xMin),
        xMax: orFallback(xViewRef.current.xMax, g.xMax),
      },
      yView: {
        yMin: orFallback(yViewRef.current.yMin, g.yMin),
        yMax: orFallback(yViewRef.current.yMax, g.yMax),
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
    if (drag) {
      const g = lastGeomRef.current;
      const dxPx = e.clientX - drag.x;
      const dyPx = e.clientY - drag.y;
      const dataDx = (dxPx / g.plotW) * (drag.xView.xMax! - drag.xView.xMin!);
      const dataDy = (dyPx / g.plotH) * (drag.yView.yMax! - drag.yView.yMin!);
      xViewRef.current = {
        xMin: drag.xView.xMin! - dataDx,
        xMax: drag.xView.xMax! - dataDx,
      };
      yViewRef.current = {
        yMin: drag.yView.yMin! + dataDy,
        yMax: drag.yView.yMax! + dataDy,
      };
      notifyChange();
      return;
    }
    // Hover (전 차트 동기) — 드래그/커서 드래그 중이 아닐 때만
    const g = lastGeomRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < g.plotLeft || px > g.plotLeft + g.plotW || py < g.plotTop || py > g.plotTop + g.plotH) {
      if (hoverXRef.current !== null) {
        setHoverX(null);
        hoveredIdxRef.current = null;
      }
      return;
    }
    setHoverX(g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin));
    hoveredIdxRef.current = graphIndex;
  };
  const onPointerUp = () => {
    dragRef.current = null;
    cursorDragRef.current = null;
  };
  const onPointerLeave = () => {
    dragRef.current = null;
    cursorDragRef.current = null;
    if (hoverXRef.current !== null) {
      setHoverX(null);
      hoveredIdxRef.current = null;
    }
  };

  return (
    <div className={`graph-chart${isDragOver ? ' syslog-chart-dragover' : ''}`} onDragOver={onDragOver} onDrop={onDrop}>
      <div className="graph-chart-header syslog-chart-header-draggable" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} title="드래그해서 그래프 순서 바꾸기">
        <span className="graph-swatch" style={{ background: series.color }} />
        <span className="graph-chart-title" title={`${series.message}.${series.signal}`}>
          {series.signal}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="이 그래프만 값 표시 형식 전환 (로컬, DEC/HEX/DESC)" onClick={cycleLocalValueMode}>
          {valueMode === 'dec' ? 'DEC' : valueMode === 'hex' ? 'HEX' : 'DESC'}
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
        <button className="icon-btn" title="X/Y 리셋 (공유 X)" onClick={() => { yViewRef.current = { yMin: null, yMax: null }; xViewRef.current = { xMin: null, xMax: null }; notifyChange(); }}>
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
          onPointerLeave={onPointerLeave}
        />
      </div>
    </div>
  );
}
