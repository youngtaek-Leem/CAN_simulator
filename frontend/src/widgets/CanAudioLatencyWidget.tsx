// CAN-오디오 지연 확인: CAN 신호 트리거와 오디오 반응 사이의 지연시간을 눈으로
// 재기 위한 위젯. CAN 신호 그래프(GraphWidget)와 오디오 파형(AudioMonitorWidget)을
// 하나의 위젯 안에서 같은 시간축(절대 epoch ms)으로 겹쳐 보여준다 -- 두 위젯은
// 각자 relMs(런 시작 기준 상대시간)/epoch ms를 독립적으로 쓰기 때문에 그대로
// 나란히 놓아도 시간축이 맞지 않는다. 이 위젯은 CAN 쪽도 signalHistory의 원시
// 백엔드 타임스탬프(pt.ts, epoch seconds)를 relMs 변환 없이 그대로 써서 오디오
// 쪽(GET /api/audio/waveform, 이미 epoch ms 기준)과 같은 좌표계를 공유한다.
//
// 두 미니 차트(CAN 신호 1개 + 오디오 채널마다 1개)는 X(시간)축 뷰만 위젯 레벨에서
// 공유해서 한쪽을 휠 줌/드래그 팬하면 같이 움직인다. Y축은 각자 독립.
//
// 자동 델타 계산은 하지 않는다(Phase 1) -- 사용자가 확대해서 두 차트의 X 눈금을
// 직접 비교해 지연시간을 읽는다. Requirement.md "CAN-오디오 지연 확인" 위젯 절 참고.
//
// 오디오 채널 차트(캔버스 드로잉/줌팬/폴링)는 AudioWaveformChart.tsx(공용,
// AudioMonitorWidget.tsx와 공유)를 "부모와 X뷰를 공유하는" 모드로 쓴다. CAN
// 신호 차트(step-line, 다른 데이터 소스)는 이 파일에 그대로 남아있다 -- 아직
// GraphWidget.tsx의 SignalChart와는 통합하지 않았다(그 위젯도 내부 컴포넌트가
// export되지 않아 재사용할 수 없고, 이번 범위 밖).

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { useApp } from '../store/appContext';
import { SignalPicker } from './MessageOptions';
import { AudioWaveformChart, niceTicks, orFallback, type AudioChartXView, type Geom } from './AudioWaveformChart';
import {
  drawDiffCursors,
  fmtDelta,
  nearestCursor,
  CURSOR_A_COLOR,
  CURSOR_B_COLOR,
  type DiffCursorState,
} from './DiffCursor';
import type { DbcSummary, SignalBinding, WidgetConfig } from '../types';

const MARGIN = { left: 52, right: 10, top: 8, bottom: 22 };
const ZOOM_STEP = 1.15;
const DOT_RADIUS = 2.5;
const DEFAULT_X_WINDOW_MS = 10_000;
const MIN_X_WINDOW_MS = 200;
const MAX_X_WINDOW_MS = 300_000;
const X_WINDOW_STEP_FACTOR = 1.1; // +/- toolbar buttons resize the window by 10% per click
const LIVE_TICK_MS = 200; // keeps the rolling window scrolling with no new data
// backend's get_waveform() does real per-chunk numpy work (measured
// ~10-30ms for a typical 10s/300-point request even after the bisect/break
// optimization in audio_service.py -- the per-chunk math itself dominates,
// not the scan), and every call briefly holds Python's GIL; wider than
// AudioMonitorWidget's original 60ms to cut how much of that cost this
// widget adds on top while still feeling responsive for visual analysis --
// see Requirement.md's "CAN periodic 신호 영향 점검" entry.
const WAVEFORM_POLL_MS = 100;
const LEVEL_POLL_MS = 100;
const CHANNEL_COLORS = ['#f97316', '#22d3ee'];
const CAN_COLOR = '#3b82f6';

// Local alias so CanSignalChart's existing type annotations don't all need
// renaming -- same shape as AudioWaveformChart.tsx's exported AudioChartXView.
type SharedXView = AudioChartXView;

interface YView {
  yMin: number | null;
  yMax: number | null;
}

function fmt(v: number): string {
  return Math.round(v).toString();
}

function fmtWindow(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** X-axis tick label as elapsed time since the view's left edge (e.g. "0.0s",
 * "5.0s") -- time flows left-to-right, left edge is always 0 and increases
 * toward the right edge (the live/frozen "now"). CanSignalChart-specific
 * (AudioWaveformChart.tsx has its own, parametrized, private copy). */
function fmtXTick(elapsedMs: number): string {
  return elapsedMs < 1000 ? `${Math.round(elapsedMs)}ms` : `${(elapsedMs / 1000).toFixed(1)}s`;
}

export function CanAudioLatencyWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const binding = config.binding;
  const bindingKey = binding ? `${binding.message}.${binding.signal}` : null;

  const [showPicker, setShowPicker] = useState(false);
  const [xWindowMs, setXWindowMs] = useState(DEFAULT_X_WINDOW_MS);
  const [level, setLevel] = useState<import('../types').AudioLevel | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Shared X (time) view across the CAN chart + every audio channel chart --
  // stored in a ref (not React state) so drag/wheel handlers can mutate it at
  // pointer-move frequency without a full re-render; `sharedVersion` is the
  // one piece of React state that both charts key their draw effect off of,
  // so bumping it from either chart redraws both.
  const sharedXRef = useRef<SharedXView>({ xMin: null, xMax: null });
  const [sharedVersion, setSharedVersion] = useState(0);
  const notifyChange = () => setSharedVersion((n) => n + 1);

  // Difference cursor: two draggable vertical lines (epoch ms), drawn on
  // every chart via the shared X view so they line up across CAN and audio.
  // Toggling off only hides them (cursorA/B values are kept, not cleared) so
  // a careful placement survives an accidental toggle. First turn-on seeds
  // sensible defaults from whatever's currently in view.
  const [cursorMode, setCursorMode] = useState(false);
  const [cursorA, setCursorA] = useState<number | null>(null);
  const [cursorB, setCursorB] = useState<number | null>(null);
  const onCursorMove = (which: 'a' | 'b', ms: number) => {
    if (which === 'a') setCursorA(ms);
    else setCursorB(ms);
    notifyChange(); // redraw every chart sharing xVersion, same as pan/zoom
  };
  const toggleCursorMode = () => {
    if (!cursorMode && cursorA === null && cursorB === null) {
      const v = sharedXRef.current;
      const xMax = v.xMax ?? nowAnchor();
      const xMin = v.xMin ?? xMax - xWindowMs;
      setCursorA(xMin + (xMax - xMin) / 3);
      setCursorB(xMin + ((xMax - xMin) * 2) / 3);
    }
    setCursorMode((m) => !m);
  };
  const cursor: DiffCursorState = { mode: cursorMode, a: cursorA, b: cursorB, onMove: onCursorMove };
  const cursorDeltaMs = cursorA !== null && cursorB !== null ? Math.abs(cursorB - cursorA) : null;

  const active = level?.active ?? false;

  // Live/frozen "now" anchor for the rolling window's right edge, shared by
  // both chart types: while the audio stream is active, it tracks Date.now()
  // as usual; the instant Stop makes it inactive, the anchor freezes at
  // whatever it last held instead of continuing to advance with wall-clock
  // time (which would otherwise scroll the last-captured CAN/audio samples
  // straight out of the view within one xWindowMs, making Stop look like the
  // waveform "disappears" instead of freezing for analysis -- same failure
  // mode AudioMonitorWidget's WaveformChart already solved). Before the
  // audio stream has ever been started at least once, stays fully live so
  // the CAN chart (which has nothing to do with this widget's own Start/Stop)
  // keeps scrolling normally.
  //
  // `active`/`everActive` are tracked in refs (not read from the `active`
  // closure variable) so `nowAnchor` -- passed to the chart children and
  // called from inside their own effects/callbacks -- always reflects the
  // current state even if a child is holding an older render's closure.
  const activeRef = useRef(active);
  activeRef.current = active;
  const audioEverActiveRef = useRef(false);
  if (active) audioEverActiveRef.current = true;
  const liveAnchorRef = useRef<number>(Date.now());
  const frozenAnchorRef = useRef<number | null>(null);
  const nowAnchor = (): number => {
    if (!audioEverActiveRef.current || activeRef.current) {
      liveAnchorRef.current = Date.now();
      frozenAnchorRef.current = null;
      return liveAnchorRef.current;
    }
    if (frozenAnchorRef.current === null) frozenAnchorRef.current = liveAnchorRef.current;
    return frozenAnchorRef.current;
  };

  useEffect(() => {
    if (!bindingKey) return;
    canStore.watchSignal(bindingKey);
    return () => canStore.unwatchSignal(bindingKey);
  }, [bindingKey]);

  // Self-rescheduling (setTimeout after each request settles) instead of a
  // plain setInterval -- a fixed-cadence interval keeps firing a new request
  // every LEVEL_POLL_MS regardless of whether the previous one has resolved
  // yet, so if the backend/network is ever slower than that for a moment,
  // requests pile up faster than they drain and the queue only grows from
  // there (this is exactly what made the whole app sluggish after leaving
  // this widget running for a while -- see the matching fix on the waveform
  // poll below, which does much more work per request and is the more
  // likely trigger). Rescheduling only after settling guarantees at most one
  // in-flight request at a time.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = () => {
      api.audioLevel().then(setLevel).catch(() => {}).finally(() => {
        if (!cancelled) timer = setTimeout(poll, LEVEL_POLL_MS);
      });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Bumped by resetEverything() (below) so both chart children know to clear
  // their own (chart-local) Y view too -- resetSharedView used to only touch
  // the shared X ref, leaving a chart's Y zoom stuck after "reset".
  const [resetToken, setResetToken] = useState(0);

  // Single reset path for the top toolbar's "⟲", each chart's own "⟲", and
  // the auto-reset below -- always returns to a fresh live/frozen-anchored
  // view (shared X back to auto, both charts' Y back to auto-fit).
  const resetEverything = () => {
    sharedXRef.current = { xMin: null, xMax: null };
    setResetToken((n) => n + 1);
    notifyChange();
  };

  // +/- window-size buttons must have a visible effect regardless of
  // whether the view is currently live or a manually zoomed/panned fixed
  // range -- previously they only adjusted the *default* window used when
  // xViewRef.current was null, so once the user had zoomed even once (the
  // normal workflow: Start, watch, Stop, zoom in to inspect) the buttons
  // silently did nothing. Resize the *current* span in place, anchored on
  // its right edge, whether live or frozen. Step is proportional (±10% of
  // the current window), not a fixed number of seconds, so it stays useful
  // at both very narrow (sub-second) and very wide (minutes) zoom levels.
  const zoomXWindow = (factor: number) => {
    setXWindowMs((w) => {
      const next = Math.min(MAX_X_WINDOW_MS, Math.max(MIN_X_WINDOW_MS, w * factor));
      const v = sharedXRef.current;
      if (v.xMin !== null && v.xMax !== null) v.xMin = v.xMax - next;
      return next;
    });
    notifyChange();
  };

  // Snap back to a fresh live view the moment the audio stream actually
  // starts (inactive -> active), not just on mount -- otherwise a view left
  // zoomed/panned from a previous Stop-and-inspect session stays frozen on
  // that old fixed range forever after pressing Start again (Start itself
  // doesn't touch xViewRef), making it look like Start "does nothing".
  // Edge-triggered (via prevActiveRef) so it doesn't keep fighting the
  // user's own zoom/pan while already live.
  const prevActiveRef = useRef(active);
  useEffect(() => {
    if (active && !prevActiveRef.current) resetEverything();
    prevActiveRef.current = active;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const start = async () => {
    setError(null);
    try {
      const r = await api.audioMonitorStart();
      if (!r.ok && r.reason) setError(r.reason);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const stop = async () => {
    setError(null);
    try {
      const r = owner === 'widget_record' ? await api.audioRecordStop() : await api.audioMonitorStop();
      if (!r.ok && r.reason) setError(r.reason);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const owner = level?.owner ?? null;
  const canStop = owner === 'monitor' || owner === 'widget_record';
  const channels = level?.channels ?? [];
  const inputDevices = (canStore.status?.audio?.devices ?? []).filter((d) => d.channels > 0);

  const canConfig = canStore.status?.can?.config as Record<string, unknown> | undefined;
  const showPcanWarning = canConfig?.interface === 'pcan' && canConfig?.epoch_aligned === false;

  return (
    <div className="graph-widget">
      <div className="graph-toolbar">
        <button className="small-btn" onClick={() => setShowPicker(true)}>
          {binding ? `CAN 신호: ${binding.signal}` : 'CAN 신호 선택…'}
        </button>
        <select
          value={canStore.status?.audio?.device_index ?? ''}
          disabled={active}
          title="오디오 입력 장치 선택 (오디오 신호 모니터 위젯과 공유됨)"
          onChange={(e) => e.target.value && api.audioSelectDevice(Number(e.target.value))}
        >
          <option value="">오디오 장치 선택…</option>
          {inputDevices.map((d) => (
            <option key={d.index} value={d.index}>
              {d.name} ({d.channels}ch)
            </option>
          ))}
        </select>
        <button className={`small-btn ${active ? '' : 'primary'}`} onClick={start} disabled={owner !== null}>
          ▶ Start
        </button>
        <button className={`small-btn ${canStop ? 'danger' : ''}`} onClick={stop} disabled={!canStop}>
          ■ Stop
        </button>
        <span className="spacer" />
        <span className="graph-xwindow mono">{fmtWindow(xWindowMs)}</span>
        <button className="icon-btn" title="시간창 넓히기 (10%)" onClick={() => zoomXWindow(X_WINDOW_STEP_FACTOR)}>
          −
        </button>
        <button className="icon-btn" title="시간창 좁히기 (10%)" onClick={() => zoomXWindow(1 / X_WINDOW_STEP_FACTOR)}>
          +
        </button>
        <button className="icon-btn" title="두 차트 모두 X/Y 축 자동 맞춤으로 리셋" onClick={resetEverything}>
          ⟲
        </button>
        <span className="spacer" />
        <button
          className={`small-btn ${cursorMode ? 'primary' : ''}`}
          title="차트에서 드래그해 두 커서를 움직이면 그 사이의 시간 간격을 읽을 수 있습니다"
          onClick={toggleCursorMode}
        >
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
      {error && <div className="error">{error}</div>}
      {owner === 'recording' && <div className="hint">테스트 러너 녹음 중 (같은 스트림에서 표시 중)</div>}
      {showPcanWarning && (
        <div className="error">
          PCAN 타임스탬프가 벽시계와 정렬되지 않았습니다(uptime 패키지 필요) — 지연 측정 결과를 신뢰할 수 없습니다.
        </div>
      )}
      {!binding && <div className="hint">CAN 신호를 선택하세요.</div>}
      {binding && channels.length === 0 && <div className="hint">오디오 장치를 선택하고 Start를 누르세요.</div>}
      <div className="graph-charts-col">
        {binding && (
          <CanSignalChart
            bindingKey={bindingKey!}
            label={binding.signal}
            xViewRef={sharedXRef}
            xVersion={sharedVersion}
            notifyChange={notifyChange}
            xWindowMs={xWindowMs}
            showXAxis={channels.length === 0}
            nowAnchor={nowAnchor}
            resetToken={resetToken}
            onResetAll={resetEverything}
            cursor={cursor}
          />
        )}
        {channels.map((ch, i) => (
          <AudioWaveformChart
            key={ch.index}
            channelIndex={ch.index}
            color={CHANNEL_COLORS[i % CHANNEL_COLORS.length]}
            margin={MARGIN}
            waveformPollMs={WAVEFORM_POLL_MS}
            pollEnabled
            streamStartedAtMs={null}
            shared={{ xViewRef: sharedXRef, xVersion: sharedVersion, notifyChange }}
            xWindowMs={xWindowMs}
            showXAxis={i === channels.length - 1}
            nowAnchor={nowAnchor}
            xTickMode="sinceWindowLeft"
            xTickDecimals={1}
            wheelZoomSpanClamp={{ min: MIN_X_WINDOW_MS, max: MAX_X_WINDOW_MS }}
            resetToken={resetToken}
            onResetClick={resetEverything}
            resetTitle="두 차트 모두 X/Y 축 자동 맞춤으로 리셋"
            cursor={cursor}
          />
        ))}
      </div>
      {showPicker && (
        <SignalPickerModal
          dbc={dbc}
          binding={binding}
          onSave={(b) => {
            updateWidget({ ...config, binding: b });
            setShowPicker(false);
          }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}

// CAN signal mini-chart: step-line, same drawing convention as
// GraphWidget's SignalChart, but the X view is the shared ref/version passed
// down from the parent instead of a chart-local one.
function CanSignalChart({
  bindingKey,
  label,
  xViewRef,
  xVersion,
  notifyChange,
  xWindowMs,
  showXAxis,
  nowAnchor,
  resetToken,
  onResetAll,
  cursor,
}: {
  bindingKey: string;
  label: string;
  xViewRef: MutableRefObject<SharedXView>;
  xVersion: number;
  notifyChange: () => void;
  xWindowMs: number;
  showXAxis: boolean;
  nowAnchor: () => number;
  resetToken: number;
  onResetAll: () => void;
  cursor: DiffCursorState;
}) {
  useCanVersion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const yViewRef = useRef<YView>({ yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; xView: SharedXView; yView: YView } | null>(null);
  const cursorDragRef = useRef<'a' | 'b' | null>(null);
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
  const [size, setSize] = useState({ w: 260, h: 150 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // keep the rolling window scrolling forward even when the signal (or the
  // whole bus) goes quiet -- the only source of ticks when no audio channel
  // chart is mounted yet (audio chart's own poll interval also ticks this).
  // `notifyChange` deliberately left out of the deps: it's a fresh closure
  // every parent render, and including it would tear down/recreate this
  // interval on every render -- if that happens faster than LIVE_TICK_MS
  // (it does, once any audio channel is polling every 60ms), the interval
  // would never survive long enough to actually fire. Any past closure still
  // calls the same stable setSharedVersion setter underneath, so staleness
  // here is harmless.
  useEffect(() => {
    const id = setInterval(() => {
      if (xViewRef.current.xMin === null) notifyChange();
    }, LIVE_TICK_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xViewRef]);

  // external "reset everything" trigger (top toolbar, or the other chart's
  // own reset button) -- clear this chart's Y view too, not just X.
  useEffect(() => {
    yViewRef.current = { yMin: null, yMax: null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken]);

  const resetView = () => {
    onResetAll();
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

    const points = canStore.signalHistory.get(bindingKey) ?? [];

    let xMin = xViewRef.current.xMin;
    let xMax = xViewRef.current.xMax;
    if (xMin === null || xMax === null) {
      xMax = nowAnchor();
      xMin = xMax - xWindowMs;
    }

    const visible = points.filter((p) => {
      const x = p.ts * 1000;
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

    const xToPx = (ms: number) => plotLeft + ((ms - xMin!) / (xMax! - xMin!)) * plotW;
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
      if (showXAxis) ctx.fillText(fmtXTick(t - xMin!), px - 16, h - 6);
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

    // points inside [xMin, xMax] plus one just outside each edge, so the
    // step line draws smoothly up to the clip boundary (same as GraphWidget)
    let start = points.findIndex((p) => p.ts * 1000 >= xMin!);
    let drawPoints: typeof points = [];
    if (start !== -1) {
      if (start > 0) start -= 1;
      let end = points.length - 1;
      while (end >= 0 && points[end].ts * 1000 > xMax!) end -= 1;
      if (end < points.length - 1) end += 1;
      if (start <= end) drawPoints = points.slice(start, end + 1);
    }

    if (drawPoints.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotW, plotH);
      ctx.clip();

      ctx.strokeStyle = CAN_COLOR;
      ctx.fillStyle = CAN_COLOR;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      // step-after: a CAN signal holds its value until the next sample
      let prevPy = 0;
      drawPoints.forEach((p, i) => {
        const px = xToPx(p.ts * 1000);
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
        const px = xToPx(p.ts * 1000);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, xWindowMs, xVersion, bindingKey]);

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
      xv.xMin = cursorX - (cursorX - xMin) * factor;
      xv.xMax = cursorX + (xMax - cursorX) * factor;
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
    if (cursor.mode) {
      const msToPx = (ms: number) => g.plotLeft + ((ms - g.xMin) / (g.xMax - g.xMin)) * g.plotW;
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
    cursorDragRef.current = null;
  };

  return (
    <div className="graph-chart">
      <div className="graph-chart-header">
        <span className="graph-swatch" style={{ background: CAN_COLOR }} />
        <span className="graph-chart-title" title={label}>
          {label}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="두 차트 모두 X/Y 축 자동 맞춤으로 리셋" onClick={resetView}>
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

function SignalPickerModal({
  dbc,
  binding,
  onSave,
  onClose,
}: {
  dbc: DbcSummary;
  binding: SignalBinding | undefined;
  onSave: (b: SignalBinding | undefined) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<SignalBinding | undefined>(binding);

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>지연 확인에 쓸 CAN 신호 선택</h3>
        {!dbc.loaded && <p className="hint">신호를 고르려면 먼저 DBC를 업로드하세요.</p>}
        {dbc.loaded && (
          <SignalPicker
            dbc={dbc}
            rxNode={canStore.getRxNode()}
            binding={draft}
            onChange={setDraft}
            messageLabelFor={(m) => m.name}
          />
        )}
        <div className="modal-buttons">
          <button onClick={() => onSave(draft)} disabled={!draft?.message || !draft.signal}>
            저장
          </button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
