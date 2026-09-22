// Difference cursor: two draggable vertical lines shared across every chart
// in CanAudioLatencyWidget.tsx (the CAN signal chart + each audio channel
// chart) for reading a precise time delta between two points on the shared
// X axis (e.g. "CAN trigger" vs "audio response start"). On/off toggle lives
// in the parent widget; this module only holds the drawing + hit-testing
// math shared by CanSignalChart (local, CAN-specific) and AudioWaveformChart
// (shared with AudioMonitorWidget, which never sets `cursor` and is
// therefore unaffected).
//
// Level cursor: two draggable horizontal lines for reading an amplitude
// level on an audio waveform chart's own Y axis (normalized -1..1, same unit
// as the chart's Y tick labels). Used by AudioMonitorWidget only so far --
// AudioWaveformChart renders them only when the `yCursor` prop is set, and
// CanAudioLatencyWidget doesn't set it, so that widget is unaffected.

export interface DiffCursorState {
  mode: boolean;
  a: number | null; // epoch ms
  b: number | null; // epoch ms
  onMove: (which: 'a' | 'b', ms: number) => void;
}

export interface LevelCursorState {
  mode: boolean;
  c: number | null; // amplitude ratio (-1..1)
  d: number | null; // amplitude ratio (-1..1)
  onMove: (which: 'c' | 'd', value: number) => void;
}

export const CURSOR_A_COLOR = '#facc15';
export const CURSOR_B_COLOR = '#a78bfa';
export const CURSOR_C_COLOR = '#34d399';
export const CURSOR_D_COLOR = '#f472b6';

/** Draws whichever of cursor.a/b fall within [xMin, xMax] as a dashed
 * vertical line spanning the plot area, each labeled at the top with its
 * elapsed time since `xLabelOriginMs` (the chart's X-axis time base -- pass
 * the same origin the tick labels use; defaults to the view's left edge).
 * No-op if cursor is undefined or its mode is off. */
export function drawDiffCursors(
  ctx: CanvasRenderingContext2D,
  cursor: DiffCursorState | undefined,
  xMin: number,
  xMax: number,
  plotTop: number,
  plotH: number,
  xToPx: (ms: number) => number,
  xLabelOriginMs: number | null = null,
): void {
  if (!cursor?.mode) return;
  const origin = xLabelOriginMs ?? xMin;
  const drawLine = (tag: string, ms: number | null, color: string, labelDy: number) => {
    if (ms === null || ms < xMin || ms > xMax) return;
    const px = xToPx(ms);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(px, plotTop);
    ctx.lineTo(px, plotTop + plotH);
    ctx.stroke();
    ctx.setLineDash([]);
    const label = `${tag} +${fmtDelta(ms - origin)}`;
    ctx.font = '10px monospace';
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = 'rgba(20, 22, 27, 0.85)';
    ctx.fillRect(px + 3, plotTop + labelDy, tw + 6, 14);
    ctx.fillStyle = color;
    ctx.fillText(label, px + 6, plotTop + labelDy + 11);
    ctx.restore();
  };
  drawLine('A', cursor.a, CURSOR_A_COLOR, 2);
  drawLine('B', cursor.b, CURSOR_B_COLOR, 18);
}

/** Which cursor ('a' or 'b') is pixel-nearest to `px` -- a cursor that isn't
 * placed yet (null) never wins, so clicking anywhere before both are placed
 * always grabs whichever one is still unset. */
export function nearestCursor(cursor: DiffCursorState, px: number, msToPx: (ms: number) => number): 'a' | 'b' {
  const aPx = cursor.a !== null ? msToPx(cursor.a) : null;
  const bPx = cursor.b !== null ? msToPx(cursor.b) : null;
  if (aPx === null) return 'b';
  if (bPx === null) return 'a';
  return Math.abs(px - aPx) <= Math.abs(px - bPx) ? 'a' : 'b';
}

/** Draws whichever of cursor.c/d fall within the plot's Y range as a dashed
 * horizontal line spanning the plot width, each labeled at the right edge
 * with its level value. No-op if yCursor is undefined or its mode is off.
 * Values are amplitude ratios; yToPx maps them to pixels. */
export function drawLevelCursors(
  ctx: CanvasRenderingContext2D,
  yCursor: LevelCursorState | undefined,
  plotLeft: number,
  plotW: number,
  plotTop: number,
  plotH: number,
  yToPx: (v: number) => number,
): void {
  if (!yCursor?.mode) return;
  const drawLine = (tag: string, v: number | null, color: string, above: boolean) => {
    if (v === null) return;
    const py = yToPx(v);
    if (py < plotTop || py > plotTop + plotH) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(plotLeft, py);
    ctx.lineTo(plotLeft + plotW, py);
    ctx.stroke();
    ctx.setLineDash([]);
    const label = `${tag} ${fmtLevel(v)}`;
    ctx.font = '10px monospace';
    const tw = ctx.measureText(label).width;
    const lx = plotLeft + plotW - tw - 9;
    const ly = above ? py - 16 : py + 2;
    ctx.fillStyle = 'rgba(20, 22, 27, 0.85)';
    ctx.fillRect(lx - 3, ly, tw + 6, 14);
    ctx.fillStyle = color;
    ctx.fillText(label, lx, ly + 11);
    ctx.restore();
  };
  drawLine('C', yCursor.c, CURSOR_C_COLOR, true);
  drawLine('D', yCursor.d, CURSOR_D_COLOR, false);
}

/** Pixel distance from `py` to the nearest placed level cursor, or null when
 * neither c nor d is placed yet. A cursor that isn't placed yet (null) never
 * wins. */
export function nearestLevelCursor(
  yCursor: LevelCursorState,
  py: number,
  vToPx: (v: number) => number,
): { which: 'c' | 'd'; dist: number } | null {
  const cPx = yCursor.c !== null ? vToPx(yCursor.c) : null;
  const dPx = yCursor.d !== null ? vToPx(yCursor.d) : null;
  if (cPx === null && dPx === null) return null;
  if (cPx === null) return { which: 'd', dist: Math.abs(py - dPx!) };
  if (dPx === null) return { which: 'c', dist: Math.abs(py - cPx) };
  return Math.abs(py - cPx) <= Math.abs(py - dPx)
    ? { which: 'c', dist: Math.abs(py - cPx) }
    : { which: 'd', dist: Math.abs(py - dPx) };
}

/** Amplitude ratio as "0.123 (12%)" -- matches the chart Y tick unit with a
 * LevelBar-style percent alongside. */
export function fmtLevel(v: number): string {
  return `${v.toFixed(3)} (${(v * 100).toFixed(0)}%)`;
}

/** Pixel distance from `px` to the nearest placed difference cursor, or null
 * when neither a nor b is placed yet. Same placement rule as nearestCursor
 * but also reports the distance, for picking between an X and a Y line. */
export function nearestDiffCursor(
  cursor: DiffCursorState,
  px: number,
  msToPx: (ms: number) => number,
): { which: 'a' | 'b'; dist: number } | null {
  const aPx = cursor.a !== null ? msToPx(cursor.a) : null;
  const bPx = cursor.b !== null ? msToPx(cursor.b) : null;
  if (aPx === null && bPx === null) return null;
  if (aPx === null) return { which: 'b', dist: Math.abs(px - bPx!) };
  if (bPx === null) return { which: 'a', dist: Math.abs(px - aPx) };
  return Math.abs(px - aPx) <= Math.abs(px - bPx)
    ? { which: 'a', dist: Math.abs(px - aPx) }
    : { which: 'b', dist: Math.abs(px - bPx) };
}

export function fmtDelta(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(3)}s`;
}
