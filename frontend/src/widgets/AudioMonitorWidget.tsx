// 오디오 신호 모니터: 선택된 오디오 입력 장치(CH3/CH4)의 실시간 레벨(Peak/RMS) +
// 실시간 파형. 채널이 2개면 CAN 신호 그래프 위젯처럼 각 채널을 별도 미니 차트로
// 분리해서 세로로 쌓고, 각 차트는 X(시간)/Y(진폭) 축을 독립적으로 확대/축소·팬할
// 수 있다 (GraphWidget.tsx의 SignalChart와 동일한 상호작용).
//
// Start는 파형만 보여주고(monitor), Record는 같은 스트림을 보여주면서 WAV로도
// 저장한다(recording). 테스트 러너가 이미 녹음 중이면 같은 백엔드 스트림(오디오
// 장치당 하나만 열 수 있음)에서 레벨/파형을 그대로 읽어오되, 이 위젯의 Stop은
// 테스트 러너의 녹음을 절대 끄지 않는다 (owner로 소유권 구분, audio_service.py 참고).
//
// 파형 데이터는 전체 샘플을 다 받을 수 없으므로(초당 수만 개), 현재 보고 있는
// 시간 구간을 서버에 알려주면 그 구간을 캔버스 폭만큼 다운샘플(픽셀 컬럼당
// min/max)해서 돌려받는다 -- GraphWidget과 달리 클라이언트가 전체 히스토리를
// 들고 있지 않고, 확대/축소 상태에 맞는 만큼만 매번 서버에서 받아온다.

import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import type { AudioLevel, AudioWaveformPoint, WidgetConfig } from '../types';

const LEVEL_POLL_MS = 100;
const WAVEFORM_POLL_MS = 60;
const CHANNEL_COLORS = ['#3b82f6', '#f97316'];

const MARGIN = { left: 40, right: 10, top: 8, bottom: 20 };
const ZOOM_STEP = 1.15;
const DEFAULT_X_WINDOW_MS = 5000;
const MIN_X_WINDOW_MS = 20;
const MAX_X_WINDOW_MS = 30_000; // matches the backend's 30s raw ring buffer
const X_WINDOW_STEP_MS = 500;

function levelColor(v: number): string {
  if (v >= 0.85) return '#ef4444';
  if (v >= 0.6) return '#f59e0b';
  return '#10b981';
}

function LevelBar({ label, peak, rms, color }: { label: string; peak: number; rms: number; color: string }) {
  return (
    <div className="audio-level-row">
      <span className="audio-level-label" style={{ color }}>{label}</span>
      <div className="audio-level-track">
        <div className="audio-level-rms" style={{ width: `${Math.min(100, rms * 100)}%` }} />
        <div
          className="audio-level-peak"
          style={{ left: `${Math.min(100, peak * 100)}%`, backgroundColor: levelColor(peak) }}
        />
      </div>
      <span className="audio-level-value mono">{(peak * 100).toFixed(0)}%</span>
    </div>
  );
}

interface ViewRange {
  xMin: number | null; // epoch ms
  xMax: number | null;
  yMin: number | null; // -1..1
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

function niceTicks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

function fmtWindow(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function fmtXTick(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;
}

function orFallback(x: number | null, fallback: number): number {
  return x === null ? fallback : x;
}

// One independent mini-chart for a single channel: own canvas, own X/Y view
// (zoom + pan), own waveform polling scoped to its current view.
function WaveformChart({
  channelIndex,
  color,
  showXAxis,
  xWindowMs,
  active,
  streamStartedAtMs,
}: {
  channelIndex: number;
  color: string;
  showXAxis: boolean;
  xWindowMs: number;
  active: boolean;
  streamStartedAtMs: number | null; // epoch ms; while set, the live window can't scroll back past it
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<ViewRange>({ xMin: null, xMax: null, yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; view: ViewRange } | null>(null);
  const lastGeomRef = useRef<Geom>({
    xMin: 0,
    xMax: 1,
    yMin: -1,
    yMax: 1,
    plotLeft: MARGIN.left,
    plotTop: MARGIN.top,
    plotW: 1,
    plotH: 1,
  });
  const pointsRef = useRef<AudioWaveformPoint[]>([]);
  const [size, setSize] = useState({ w: 260, h: 150 });
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);

  // Live/frozen "now" anchor: while active, liveAnchorRef tracks the current
  // moment (refreshed on every redraw, i.e. every ~60-200ms); the instant
  // `active` goes false, frozenAnchorRef latches onto whatever liveAnchorRef
  // last held and stops changing, so a chart that was auto-scrolling with
  // the live edge freezes exactly where it was instead of continuing to
  // slide with Date.now() (which would otherwise scroll the last-fetched
  // waveform straight out of view within a few seconds of Stop). Cleared
  // back to live the moment Start/Record reopens the stream.
  const liveAnchorRef = useRef<{ nowMs: number; streamStartedAtMs: number | null }>({
    nowMs: Date.now(),
    streamStartedAtMs: null,
  });
  const frozenAnchorRef = useRef<{ nowMs: number; streamStartedAtMs: number | null } | null>(null);
  // Only poll at all once this chart has actually been started at least
  // once -- a widget that was never Start/Record'ed has nothing to show and
  // shouldn't burn a background request every 60ms forever.
  const hasEverStartedRef = useRef(false);
  if (active) hasEverStartedRef.current = true;

  const nowAnchor = (): { nowMs: number; streamStartedAtMs: number | null } => {
    if (active) {
      liveAnchorRef.current = { nowMs: Date.now(), streamStartedAtMs };
      frozenAnchorRef.current = null;
      return liveAnchorRef.current;
    }
    if (frozenAnchorRef.current === null) frozenAnchorRef.current = liveAnchorRef.current;
    return frozenAnchorRef.current;
  };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const resetView = () => {
    viewRef.current = { xMin: null, xMax: null, yMin: null, yMax: null };
    redraw();
  };

  // ---- polling: fetch a decimated waveform slice for the current view ----
  // Kept running after Stop (not gated on `active`) so that panning/zooming
  // into the frozen view still fetches the newly-visible part of the last
  // RAW_BUFFER_SECONDS of audio (still served by the backend after Stop --
  // see get_waveform() in audio_service.py); gated on hasEverStartedRef
  // instead, so a chart that was never Start/Record'ed doesn't poll at all.
  // Self-rescheduling (setTimeout after each request settles) instead of a
  // plain setInterval: get_waveform() does real per-chunk numpy work and a
  // plain setInterval fires a new request every WAVEFORM_POLL_MS regardless
  // of whether the previous one has resolved, so if the backend is ever even
  // briefly slower than that (a busy backend, a slow moment, another widget)
  // requests pile up faster than they drain and the queue only grows from
  // there. This chart never unmounts on its own once started, so that
  // backlog would keep growing the longer the widget is left open, until
  // the piled-up requests exhaust the same thread pool
  // /api/tx/signal|/api/run/start|/api/run/stop also run on -- see
  // CanAudioLatencyWidget.tsx (which hit exactly this) and Requirement.md's
  // "위젯 사용 후 CAN Simulator 전역 Start가 먹통 되는 심각한 렉" entry; this
  // widget was originally left out of that fix's scope.
  useEffect(() => {
    if (!active && !hasEverStartedRef.current) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      let xMin = viewRef.current.xMin;
      let xMax = viewRef.current.xMax;
      if (xMin === null || xMax === null) {
        const anchor = nowAnchor();
        xMax = anchor.nowMs;
        xMin = xMax - xWindowMs;
        // Start/Record just began -- there's no audio before its own 0s
        // mark, so the live window can't reach back past it either.
        if (anchor.streamStartedAtMs !== null) xMin = Math.max(xMin, anchor.streamStartedAtMs);
      }
      const maxPoints = Math.max(50, Math.round(size.w));
      try {
        const wf = await api.audioWaveform(xMin, xMax, maxPoints);
        if (cancelled) return;
        const ch = wf.channels.find((c) => c.index === channelIndex);
        pointsRef.current = ch?.points ?? [];
        redraw();
      } catch {
        /* ignore -- keep showing the last good frame */
      } finally {
        if (!cancelled) timer = setTimeout(poll, WAVEFORM_POLL_MS);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelIndex, xWindowMs, active, size.w, streamStartedAtMs]);

  // keep the rolling window scrolling forward even between polls -- only
  // while live; once frozen, the view has already stopped changing so
  // there's nothing to keep re-rendering for.
  useEffect(() => {
    const id = setInterval(() => {
      if (active && viewRef.current.xMin === null) redraw();
    }, 200);
    return () => clearInterval(id);
  }, [active]);

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

    const bottomMargin = showXAxis ? MARGIN.bottom : 4;
    const plotLeft = MARGIN.left;
    const plotTop = MARGIN.top;
    const plotW = Math.max(1, w - MARGIN.left - MARGIN.right);
    const plotH = Math.max(1, h - MARGIN.top - bottomMargin);

    let xMin = viewRef.current.xMin;
    let xMax = viewRef.current.xMax;
    const anchor = nowAnchor();
    if (xMin === null || xMax === null) {
      xMax = anchor.nowMs;
      xMin = xMax - xWindowMs;
      if (anchor.streamStartedAtMs !== null) xMin = Math.max(xMin, anchor.streamStartedAtMs);
    }

    const points = pointsRef.current;

    let yMin = viewRef.current.yMin;
    let yMax = viewRef.current.yMax;
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

    // Ticks read as elapsed seconds since Start/Record began (0s at that
    // fixed moment) -- identical in both modes since streamStartedAtMs is
    // now set for a plain monitor stream too, not just an actual recording.
    // Anchoring to that fixed point (rather than to the current view's own
    // xMin) is what makes the numbers keep advancing as the live window
    // scrolls forward -- anchoring to xMin instead would freeze every tick
    // at a constant offset (e.g. always "0ms, 667ms, 1.33s, 2.00s"), since
    // xMin scrolls forward at the same rate as the ticks themselves.
    const xTickRef = anchor.streamStartedAtMs ?? xMax!;
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
      if (showXAxis) ctx.fillText(fmtXTick(t - xTickRef), px - 16, h - 5);
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
  });

  // ---- interaction: wheel-zoom (per-axis) + drag-to-pan (same as GraphWidget) --

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
      const cursorX = g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin);
      const xMin = v.xMin ?? g.xMin;
      const xMax = v.xMax ?? g.xMax;
      let newMin = cursorX - (cursorX - xMin) * factor;
      let newMax = cursorX + (xMax - cursorX) * factor;
      const span = Math.min(MAX_X_WINDOW_MS, Math.max(MIN_X_WINDOW_MS, newMax - newMin));
      const center = (newMin + newMax) / 2;
      newMin = center - span / 2;
      newMax = center + span / 2;
      v.xMin = newMin;
      v.xMax = newMax;
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
        <span className="graph-swatch" style={{ background: color }} />
        <span className="graph-chart-title">CH{channelIndex + 1}</span>
        <span className="spacer" />
        <button className="icon-btn" title="X/Y 축 자동 맞춤으로 리셋" onClick={resetView}>
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

export function AudioMonitorWidget(_: { config: WidgetConfig }) {
  useCanVersion();
  const audio = canStore.status?.audio;
  const [level, setLevel] = useState<AudioLevel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [xWindowMs, setXWindowMs] = useState(DEFAULT_X_WINDOW_MS);
  // Tracks the widget's own Record filename across polls so a 30-minute
  // segment rotation (server-side, see audio_service.py's rotation timer)
  // can be surfaced as an activity line instead of happening silently.
  const lastFilenameRef = useRef<string | null>(null);

  // Self-rescheduling (setTimeout after each request settles) instead of a
  // plain setInterval -- same rationale as WaveformChart's poll above: caps
  // this at one in-flight /api/audio/level request at a time so a slow
  // backend moment can never turn into a growing backlog.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = () =>
      api.audioLevel().then((lvl) => {
        setLevel(lvl);
        if (lvl.owner === 'widget_record' && lvl.current_filename) {
          const prev = lastFilenameRef.current;
          if (prev !== null && prev !== lvl.current_filename) {
            canStore.pushActivity(`오디오 녹음 구간 저장됨: ${prev} (다음 구간: ${lvl.current_filename})`);
          }
          lastFilenameRef.current = lvl.current_filename;
        } else {
          lastFilenameRef.current = null;
        }
      }).catch(() => {}).finally(() => {
        if (!cancelled) timer = setTimeout(poll, LEVEL_POLL_MS);
      });
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const zoomXWindow = (deltaMs: number) => {
    setXWindowMs((w) => Math.min(MAX_X_WINDOW_MS, Math.max(MIN_X_WINDOW_MS, w + deltaMs)));
  };

  const start = async () => {
    setError(null);
    setSavedMsg(null);
    try {
      const r = await api.audioMonitorStart();
      if (!r.ok && r.reason) setError(r.reason);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const record = async () => {
    setError(null);
    setSavedMsg(null);
    try {
      const r = await api.audioRecordStart();
      if (!r.ok && r.reason) setError(r.reason);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const stop = async () => {
    setError(null);
    try {
      if (owner === 'widget_record') {
        const r = await api.audioRecordStop();
        if (!r.ok && r.reason) setError(r.reason);
        else if (r.filename) setSavedMsg(`저장됨: ${r.filename} (${r.frames ?? 0} frames)`);
      } else {
        const r = await api.audioMonitorStop();
        if (!r.ok && r.reason) setError(r.reason);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const active = level?.active ?? false;
  const owner = level?.owner ?? null;
  const canStop = owner === 'monitor' || owner === 'widget_record';
  const channels = level?.channels ?? [{ index: 0, peak: 0, rms: 0 }, { index: 1, peak: 0, rms: 0 }];
  const inputDevices = (audio?.devices ?? []).filter((d) => d.channels > 0);
  const streamStartedAtMs = level?.stream_started_at != null ? level.stream_started_at * 1000 : null;

  return (
    <div className="audio-monitor">
      <div className="audio-monitor-toolbar">
        <button className={`small-btn ${active ? '' : 'primary'}`} onClick={start} disabled={owner !== null}>
          ▶ Start
        </button>
        <button className="small-btn" onClick={record} disabled={owner === 'recording' || owner === 'widget_record'}>
          ● Record
        </button>
        <button className={`small-btn ${canStop ? 'danger' : ''}`} onClick={stop} disabled={!canStop}>
          ■ Stop
        </button>
        <select
          value={audio?.device_index ?? ''}
          disabled={active}
          title="오디오 입력 장치 선택 (테스트 Sequence 실행기 위젯과 공유됨)"
          onChange={(e) => e.target.value && api.audioSelectDevice(Number(e.target.value))}
        >
          <option value="">오디오 장치 선택…</option>
          {inputDevices.map((d) => (
            <option key={d.index} value={d.index}>
              {d.name} ({d.channels}ch)
            </option>
          ))}
        </select>
        <button className="icon-btn" title="오디오 장치 목록 새로고침" onClick={() => api.audioDevices()}>
          ⟲
        </button>
        <span className="spacer" />
        <span className="graph-xwindow mono">{fmtWindow(xWindowMs)}</span>
        <button className="icon-btn" title="X축 축소" onClick={() => zoomXWindow(X_WINDOW_STEP_MS)}>
          −
        </button>
        <button className="icon-btn" title="X축 확대" onClick={() => zoomXWindow(-X_WINDOW_STEP_MS)}>
          +
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {savedMsg && <div className="hint">{savedMsg}</div>}
      {owner === 'recording' && <div className="hint">테스트 러너 녹음 중 (같은 스트림에서 표시 중)</div>}
      <div className="audio-levels">
        {channels.map((ch, i) => (
          <LevelBar
            key={ch.index}
            label={`CH${i + 1}`}
            peak={ch.peak}
            rms={ch.rms}
            color={CHANNEL_COLORS[i % CHANNEL_COLORS.length]}
          />
        ))}
      </div>
      <div className="graph-charts-col">
        {channels.map((ch, i) => (
          <WaveformChart
            key={ch.index}
            channelIndex={ch.index}
            color={CHANNEL_COLORS[i % CHANNEL_COLORS.length]}
            showXAxis={i === channels.length - 1}
            xWindowMs={xWindowMs}
            active={active}
            streamStartedAtMs={streamStartedAtMs}
          />
        ))}
      </div>
    </div>
  );
}
