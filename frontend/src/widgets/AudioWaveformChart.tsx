// Shared audio waveform mini-chart: canvas drawing (min/max band silhouette),
// wheel-zoom + drag-pan interaction, and self-rescheduling /api/audio/waveform
// polling -- extracted out of AudioMonitorWidget.tsx's WaveformChart and
// CanAudioLatencyWidget.tsx's AudioChannelChart, which had grown to duplicate
// this same ~250 lines almost verbatim (and, before this file existed, had
// already once diverged and caused a real bug -- see Requirement.md's
// "위젯 사용 후 CAN Simulator 전역 Start가 먹통 되는 심각한 렉" entry, where the
// unbounded-polling fix landed in one copy but not the other).
//
// The two call sites still differ in a few deliberate ways, so those stay as
// props instead of being collapsed away:
// - AudioMonitorWidget: each chart owns its own X view, wheel-zoom is
//   unbounded, X ticks read as elapsed time since the stream started (climbs
//   as the live window scrolls), and the view can't scroll back past
//   streamStartedAtMs.
// - CanAudioLatencyWidget: the X view is shared with a sibling CAN chart via
//   a ref from the parent, wheel-zoom clamps the resulting span, X ticks read
//   as elapsed time since the current view's left edge (matches its CAN
//   chart sibling), and there's no stream-start clamp.

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { api } from '../api/client';
import type { AudioWaveformPoint } from '../types';

export interface AudioChartMargin {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface AudioChartXView {
  xMin: number | null; // epoch ms
  xMax: number | null;
}

interface YView {
  yMin: number | null;
  yMax: number | null;
}

// Exported for CanAudioLatencyWidget.tsx's CanSignalChart, which draws a
// different data source (CAN signal history, step-line) so isn't part of
// this shared component, but shares this exact geometry shape.
export interface Geom {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
  plotLeft: number;
  plotTop: number;
  plotW: number;
  plotH: number;
}

const ZOOM_STEP = 1.15;

export function niceTicks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

// AudioMonitorWidget's original showed 2 decimals ("1.23s"), CanAudioLatency
// Widget's showed 1 ("1.2s") -- a real (if minor) difference, kept as a prop
// rather than picking one.
function fmtXTick(elapsedMs: number, decimals: number): string {
  return elapsedMs < 1000 ? `${Math.round(elapsedMs)}ms` : `${(elapsedMs / 1000).toFixed(decimals)}s`;
}

export function orFallback(x: number | null, fallback: number): number {
  return x === null ? fallback : x;
}

export interface AudioWaveformChartProps {
  channelIndex: number;
  color: string;
  showXAxis: boolean;
  xWindowMs: number;
  margin: AudioChartMargin;
  waveformPollMs: number;
  /** Gate for whether to poll at all -- latched internally (once true,
   * polling never stops again for this chart's lifetime) so a chart that
   * has never been started doesn't burn a background request forever, but
   * one that has starts polling immediately on remount. */
  pollEnabled: boolean;
  /** epoch ms lower bound the live window can't scroll back past, or null
   * for no clamp. A plain prop (not folded into nowAnchor) so it can drive
   * the polling effect's own dependency array directly. */
  streamStartedAtMs: number | null;
  /** epoch ms "now" for this chart's live/frozen right edge -- owned by the
   * caller (freeze-on-Stop semantics differ in scope between the two
   * widgets: per-chart vs shared across the whole widget). */
  nowAnchor: () => number;
  /** 'sinceStreamStart': ticks read as elapsed time since streamStartedAtMs
   * (climbs as the live window scrolls forward). 'sinceWindowLeft': ticks
   * read as elapsed time since the current view's own left edge (always
   * starts at 0). */
  xTickMode: 'sinceStreamStart' | 'sinceWindowLeft';
  /** Decimal places for the ">=1s" tick label form (e.g. 2 -> "1.23s"). */
  xTickDecimals: number;
  /** If provided, wheel-zoom clamps the resulting X span to [min, max] and
   * re-centers -- if omitted, wheel-zoom on this chart is unbounded (the
   * caller's own +/- window-size buttons may still clamp separately). */
  wheelZoomSpanClamp?: { min: number; max: number };
  /** Omit for a standalone chart that owns its own X view (and runs its own
   * "keep scrolling while live" tick); pass all three to share one X view
   * (and defer live-ticking to whatever else drives xVersion, e.g. a
   * sibling chart) with other charts. */
  shared?: {
    xViewRef: MutableRefObject<AudioChartXView>;
    xVersion: number;
    notifyChange: () => void;
  };
  /** Bumped by the caller to clear this chart's Y view from outside (e.g. a
   * shared "reset everything" toolbar button) -- see onResetClick for X. */
  resetToken: number;
  /** Only meaningful when `shared` is set: called when this chart's own "⟲"
   * is clicked, so the parent can reset the shared X view too. A standalone
   * chart resets its own (locally-owned) X view directly and this is never
   * called for it. */
  onResetClick: () => void;
  resetTitle: string;
}

export function AudioWaveformChart({
  channelIndex,
  color,
  showXAxis,
  xWindowMs,
  margin,
  waveformPollMs,
  pollEnabled,
  streamStartedAtMs,
  nowAnchor,
  xTickMode,
  xTickDecimals,
  wheelZoomSpanClamp,
  shared,
  resetToken,
  onResetClick,
  resetTitle,
}: AudioWaveformChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const localXViewRef = useRef<AudioChartXView>({ xMin: null, xMax: null });
  const yViewRef = useRef<YView>({ yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; xView: AudioChartXView; yView: YView } | null>(null);
  const lastGeomRef = useRef<Geom>({
    xMin: 0,
    xMax: 1,
    yMin: -1,
    yMax: 1,
    plotLeft: margin.left,
    plotTop: margin.top,
    plotW: 1,
    plotH: 1,
  });
  const pointsRef = useRef<AudioWaveformPoint[]>([]);
  const [size, setSize] = useState({ w: 260, h: 150 });
  // Local version counter for a standalone chart's own redraws (wheel/pan/
  // poll all call notifyChange()) -- must be real state read in the draw
  // effect's deps below, not just a re-render trigger, or the draw effect
  // would never re-run despite the component re-rendering (`shared.xVersion`
  // plays the same role for a shared-X chart).
  const [localVersion, setLocalVersion] = useState(0);

  const xViewRef = shared ? shared.xViewRef : localXViewRef;
  const notifyChange = shared ? shared.notifyChange : () => setLocalVersion((n) => n + 1);
  const xVersion = shared ? shared.xVersion : localVersion;

  const hasEverEnabledRef = useRef(false);
  if (pollEnabled) hasEverEnabledRef.current = true;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // external "reset everything" trigger -- clear this chart's Y view too,
  // not just whatever cleared X (shared parent reset, or this chart's own
  // resetView() below for a standalone chart).
  useEffect(() => {
    yViewRef.current = { yMin: null, yMax: null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken]);

  const resetView = () => {
    yViewRef.current = { yMin: null, yMax: null };
    if (shared) {
      onResetClick();
    } else {
      localXViewRef.current = { xMin: null, xMax: null };
      notifyChange();
    }
  };

  // keep the rolling window scrolling forward even between polls, while
  // live -- only for a standalone chart; a shared-X chart is kept ticking
  // by whatever else drives xVersion (e.g. a sibling CAN chart's own timer).
  useEffect(() => {
    if (shared) return;
    const id = setInterval(() => {
      if (pollEnabled && localXViewRef.current.xMin === null) notifyChange();
    }, 200);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shared, pollEnabled]);

  // ---- polling: fetch a decimated waveform slice for the current view ----
  // Kept running after Stop (not gated on `pollEnabled` once it's ever been
  // true) so that panning/zooming into the frozen view still fetches the
  // newly-visible part of the last RAW_BUFFER_SECONDS of audio (still served
  // by the backend after Stop -- see get_waveform() in audio_service.py).
  //
  // Self-rescheduling (setTimeout after the request settles) instead of a
  // plain setInterval: get_waveform() does real per-chunk numpy work, and a
  // plain setInterval fires a new request every waveformPollMs regardless of
  // whether the previous one has resolved -- if the backend is ever even
  // briefly slower than that (a busy backend, a slow moment, another
  // widget), requests pile up faster than they drain and the queue only
  // grows from there, eventually exhausting the same thread pool
  // /api/tx/signal|/api/run/start|/api/run/stop also run on. Rescheduling
  // only after each request settles caps this at one in-flight request per
  // channel no matter how long the chart sits open -- see Requirement.md's
  // "위젯 사용 후 CAN Simulator 전역 Start가 먹통 되는 심각한 렉" entry, which is
  // exactly what happened when this fix landed in only one of the two
  // duplicate copies this component now replaces.
  useEffect(() => {
    if (!pollEnabled && !hasEverEnabledRef.current) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      let xMin = xViewRef.current.xMin;
      let xMax = xViewRef.current.xMax;
      if (xMin === null || xMax === null) {
        xMax = nowAnchor();
        xMin = xMax - xWindowMs;
        if (streamStartedAtMs !== null) xMin = Math.max(xMin, streamStartedAtMs);
      }
      const maxPoints = Math.max(50, Math.round(size.w));
      try {
        const wf = await api.audioWaveform(xMin, xMax, maxPoints);
        if (cancelled) return;
        const ch = wf.channels.find((c) => c.index === channelIndex);
        pointsRef.current = ch?.points ?? [];
        notifyChange();
      } catch {
        /* ignore -- keep showing the last good frame */
      } finally {
        if (!cancelled) timer = setTimeout(poll, waveformPollMs);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelIndex, xWindowMs, pollEnabled, size.w, streamStartedAtMs]);

  // ---- drawing -------------------------------------------------------------

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

    const bottomMargin = showXAxis ? margin.bottom : 4;
    const plotLeft = margin.left;
    const plotTop = margin.top;
    const plotW = Math.max(1, w - margin.left - margin.right);
    const plotH = Math.max(1, h - margin.top - bottomMargin);

    let xMin = xViewRef.current.xMin;
    let xMax = xViewRef.current.xMax;
    if (xMin === null || xMax === null) {
      xMax = nowAnchor();
      xMin = xMax - xWindowMs;
      if (streamStartedAtMs !== null) xMin = Math.max(xMin, streamStartedAtMs);
    }

    const points = pointsRef.current;

    let yMin = yViewRef.current.yMin;
    let yMax = yViewRef.current.yMax;
    if (yMin === null || yMax === null) {
      if (points.length > 0) {
        const lo = Math.min(...points.map((p) => p.min));
        const hi = Math.max(...points.map((p) => p.max));
        const pad = (hi - lo) * 0.15 || 0.05;
        yMin = lo - pad;
        yMax = hi + pad;
      } else {
        yMin = -1;
        yMax = 1;
      }
    }

    const xToPx = (ms: number) => plotLeft + ((ms - xMin!) / (xMax! - xMin!)) * plotW;
    const yToPx = (v: number) => plotTop + plotH - ((v - yMin!) / (yMax! - yMin!)) * plotH;

    // 'sinceStreamStart': anchored to the fixed stream-start moment, so tick
    // labels keep advancing as the live window scrolls forward (anchoring to
    // xMin instead would freeze every tick at a constant offset, since xMin
    // scrolls forward at the same rate as the ticks themselves).
    // 'sinceWindowLeft': anchored to the view's own left edge, so the
    // leftmost tick always reads 0 (matches a sibling CAN chart's ticks).
    const xTickRef = xTickMode === 'sinceStreamStart' ? streamStartedAtMs ?? xMax : xMin;
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
      if (showXAxis) ctx.fillText(fmtXTick(t - xTickRef, xTickDecimals), px - 16, h - 5);
    }
    for (const t of niceTicks(yMin, yMax, 3)) {
      const py = yToPx(t);
      ctx.beginPath();
      ctx.moveTo(plotLeft, py);
      ctx.lineTo(plotLeft + plotW, py);
      ctx.stroke();
      ctx.fillText(t.toFixed(2), 2, py + 3);
    }
    ctx.strokeStyle = '#4b5160';
    ctx.strokeRect(plotLeft, plotTop, plotW, plotH);

    if (points.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotW, plotH);
      ctx.clip();

      // each point is already a {min,max} column (server-side decimated) --
      // draw it as a vertical band, the standard waveform-viewer silhouette.
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1, plotW / Math.max(points.length, 1) - 0.5);
      ctx.beginPath();
      for (const p of points) {
        const px = xToPx(p.t * 1000);
        if (px < plotLeft - 2 || px > plotLeft + plotW + 2) continue;
        ctx.moveTo(px, yToPx(p.min));
        ctx.lineTo(px, yToPx(p.max));
      }
      ctx.stroke();
      ctx.restore();
    }

    lastGeomRef.current = { xMin, xMax, yMin, yMax, plotLeft, plotTop, plotW, plotH };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, xWindowMs, xVersion]);

  // ---- interaction: wheel-zoom (per-axis) + drag-to-pan ---------------------

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
    const xv = xViewRef.current;
    const yv = yViewRef.current;

    if (zoomX) {
      const cursorX = g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin);
      const xMin = xv.xMin ?? g.xMin;
      const xMax = xv.xMax ?? g.xMax;
      let newMin = cursorX - (cursorX - xMin) * factor;
      let newMax = cursorX + (xMax - cursorX) * factor;
      if (wheelZoomSpanClamp) {
        const span = Math.min(wheelZoomSpanClamp.max, Math.max(wheelZoomSpanClamp.min, newMax - newMin));
        const center = (newMin + newMax) / 2;
        newMin = center - span / 2;
        newMax = center + span / 2;
      }
      xv.xMin = newMin;
      xv.xMax = newMax;
    }
    if (zoomY) {
      const cursorY = g.yMax - ((py - g.plotTop) / g.plotH) * (g.yMax - g.yMin);
      const yMin = yv.yMin ?? g.yMin;
      const yMax = yv.yMax ?? g.yMax;
      yv.yMin = cursorY - (cursorY - yMin) * factor;
      yv.yMax = cursorY + (yMax - cursorY) * factor;
    }
    notifyChange();
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const g = lastGeomRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < g.plotLeft || px > g.plotLeft + g.plotW || py < g.plotTop || py > g.plotTop + g.plotH) return;
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
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
    const drag = dragRef.current;
    if (!drag) return;
    const g = lastGeomRef.current;
    const dxPx = e.clientX - drag.x;
    const dyPx = e.clientY - drag.y;
    const dataDx = (dxPx / g.plotW) * (drag.xView.xMax! - drag.xView.xMin!);
    const dataDy = (dyPx / g.plotH) * (drag.yView.yMax! - drag.yView.yMin!);
    xViewRef.current = { xMin: drag.xView.xMin! - dataDx, xMax: drag.xView.xMax! - dataDx };
    yViewRef.current = { yMin: drag.yView.yMin! + dataDy, yMax: drag.yView.yMax! + dataDy };
    notifyChange();
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };

  return (
    <div className="graph-chart">
      <div className="graph-chart-header">
        <span className="graph-swatch" style={{ background: color }} />
        <span className="graph-chart-title">CH{channelIndex + 1}</span>
        <span className="spacer" />
        <button className="icon-btn" title={resetTitle} onClick={resetView}>
          ⟲
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
