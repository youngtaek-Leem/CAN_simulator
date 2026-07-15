// CAN signal graph widget: canvas-based time-series chart. Each watched
// signal gets its own mini-chart, stacked vertically so signals with very
// different value ranges don't fight over one shared Y axis. Since every
// chart shares the same time axis, only the bottom-most chart draws the X
// (time) tick labels. Each sample is drawn as a dot and consecutive samples
// are connected by a line. Every mini-chart zooms/pans its X (time) and Y
// (value) axes independently.

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { canStore, useCanVersion, type HistoryPoint } from '../store/canStore';
import { useApp } from '../store/appContext';
import { SignalPicker } from './MessageOptions';
import type { SignalBinding, WidgetConfig } from '../types';

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
const ZOOM_STEP = 1.15;
const DOT_RADIUS = 2.5;
const DEFAULT_X_WINDOW_MS = 10_000;
const MIN_X_WINDOW_MS = 500;
const MAX_X_WINDOW_MS = 300_000;
const X_WINDOW_STEP_MS = 5_000; // +/- toolbar buttons change the window by this much per click
const LIVE_TICK_MS = 200; // redraw cadence so the rolling window keeps scrolling with no new data

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

/** Y-axis tick label -- integers only (data/auto-fit math stays float). */
function fmt(v: number): string {
  return Math.round(v).toString();
}

/** X-axis rolling window size for display next to the +/- zoom buttons. */
function fmtWindow(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function orFallback(x: number | null, fallback: number): number {
  return x === null ? fallback : x;
}

/** Points inside [xMin, xMax], plus one point just outside each edge (if any)
 * so the connecting line draws smoothly up to the clip boundary instead of
 * visibly starting/ending flat at the first/last in-range sample. */
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
  const { editMode, updateWidget } = useApp();
  const series = getSeries(config);
  const [showAdd, setShowAdd] = useState(false);
  // X-axis rolling window size, shared by every mini-chart in this widget so
  // the top +/- buttons zoom all of them identically.
  const [xWindowMs, setXWindowMs] = useState(DEFAULT_X_WINDOW_MS);

  const addSeries = (s: GraphSeries) => {
    updateWidget({ ...config, options: { ...config.options, series: [...series, s] } });
  };
  const removeSeries = (key: string) => {
    canStore.unwatchSignal(key); // drop history immediately, not just on unmount
    updateWidget({
      ...config,
      options: { ...config.options, series: series.filter((s) => seriesKey(s) !== key) },
    });
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

  return (
    <div className="graph-widget">
      <div className="graph-toolbar">
        <span className="hint">{series.length > 0 ? `${series.length}개 신호` : '신호를 추가하세요'}</span>
        <span className="spacer" />
        <span className="graph-xwindow mono">{fmtWindow(xWindowMs)}</span>
        <button className="icon-btn" title="X축 축소 (시간 범위 5초 넓게)" onClick={() => zoomXWindow(X_WINDOW_STEP_MS)}>
          −
        </button>
        <button className="icon-btn" title="X축 확대 (시간 범위 5초 좁게)" onClick={() => zoomXWindow(-X_WINDOW_STEP_MS)}>
          +
        </button>
        {editMode && (
          <button className="small-btn" onClick={() => setShowAdd(true)}>
            + 신호 추가
          </button>
        )}
      </div>
      <div className="graph-charts-col">
        {series.map((s, i) => (
          <SignalChart
            key={seriesKey(s)}
            series={s}
            editMode={editMode}
            showXAxis={i === series.length - 1}
            xWindowMs={xWindowMs}
            onRemove={() => removeSeries(seriesKey(s))}
            onMoveUp={() => moveSeries(i, -1)}
            onMoveDown={() => moveSeries(i, 1)}
            canMoveUp={i > 0}
            canMoveDown={i < series.length - 1}
          />
        ))}
      </div>
      {showAdd && <AddSeriesModal existing={series} onAdd={addSeries} onClose={() => setShowAdd(false)} />}
    </div>
  );
}

// One independent mini-chart for a single signal: own canvas, own X/Y view
// (zoom + pan), own history subscription.
function SignalChart({
  series,
  editMode,
  showXAxis,
  xWindowMs,
  onRemove,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
}: {
  series: GraphSeries;
  editMode: boolean;
  showXAxis: boolean;
  xWindowMs: number;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  useCanVersion();
  const key = seriesKey(series);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<ViewRange>({ xMin: null, xMax: null, yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; view: ViewRange } | null>(null);
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

  // keep the rolling window scrolling forward even when this signal (or the
  // whole bus) goes quiet for a while, instead of freezing on the last sample
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

  // ---- drawing -----------------------------------------------------------

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

    // X: live mode rolls a fixed-size window forward with "now"; once the
    // user wheel-zooms/drags, xMin/xMax are frozen to a specific range.
    let xMin = viewRef.current.xMin;
    let xMax = viewRef.current.xMax;
    if (xMin === null || xMax === null) {
      xMax = canStore.nowMs();
      xMin = xMax - xWindowMs;
    }

    // Y auto-fit only looks at samples inside the visible X window, not the
    // whole history, so old outliers don't flatten what's on screen now.
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
        yMin = Math.max(0, lo - pad); // auto-fit never dips below 0
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
      ctx.fillText(fmt(t), 2, py + 3);
    }
    ctx.strokeStyle = '#4b5160';
    ctx.strokeRect(plotLeft, plotTop, plotW, plotH);

    const drawPoints = visibleWithPadding(points, xMin, xMax);
    if (drawPoints.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotW, plotH);
      ctx.clip();

      ctx.strokeStyle = series.color;
      ctx.fillStyle = series.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      // Step-after (horizontal-then-vertical) line: a CAN signal holds its
      // value until the next sample, so a straight diagonal between samples
      // would misleadingly show it "in transit" -- draw a staircase instead.
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

    // stash the resolved (possibly auto-fit) range + plot geometry for the
    // wheel/drag handlers below, without triggering another render
    lastGeomRef.current = { xMin, xMax, yMin, yMax, plotLeft, plotTop, plotW, plotH };
  });

  // ---- interaction: wheel-zoom (per-axis) + drag-to-pan -------------------

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
    const zoomY = overYAxisStrip || (inX && inY);
    const v = viewRef.current;

    if (zoomX) {
      // freeze this chart's own view at the cursor-anchored zoom level; the
      // shared rolling window (top +/- buttons) is unaffected
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
      return; // only pan when starting inside the plot area
    }
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
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
  };

  return (
    <div className="graph-chart">
      <div className="graph-chart-header">
        <span className="graph-swatch" style={{ background: series.color }} />
        <span className="graph-chart-title" title={`${series.message}.${series.signal}`}>
          {series.signal}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="X/Y 축 자동 맞춤으로 리셋" onClick={resetView}>
          ⟲
        </button>
        {editMode && (
          <>
            <button className="icon-btn" title="위로 이동" disabled={!canMoveUp} onClick={onMoveUp}>
              ▲
            </button>
            <button className="icon-btn" title="아래로 이동" disabled={!canMoveDown} onClick={onMoveDown}>
              ▼
            </button>
            <button className="icon-btn" title="제거" onClick={onRemove}>
              ✕
            </button>
          </>
        )}
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

function AddSeriesModal({
  existing,
  onAdd,
  onClose,
}: {
  existing: GraphSeries[];
  onAdd: (s: GraphSeries) => void;
  onClose: () => void;
}) {
  const { dbc } = useApp();
  const [binding, setBinding] = useState<SignalBinding | undefined>(undefined);

  const add = () => {
    if (!binding?.message || !binding.signal) return;
    onAdd({ message: binding.message, signal: binding.signal, color: nextColor(existing) });
    onClose();
  };

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>그래프에 신호 추가</h3>
        {!dbc.loaded && <p className="hint">신호 할당을 하려면 먼저 DBC를 업로드하세요.</p>}
        {dbc.loaded && (
          <SignalPicker
            dbc={dbc}
            rxNode={canStore.getRxNode()}
            binding={binding}
            onChange={setBinding}
            messageLabelFor={(m) => m.name}
          />
        )}
        <div className="modal-buttons">
          <button onClick={add} disabled={!binding?.message || !binding.signal}>
            추가
          </button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
