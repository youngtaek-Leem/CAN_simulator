// Difference cursor: two draggable vertical lines shared across every chart
// in CanAudioLatencyWidget.tsx (the CAN signal chart + each audio channel
// chart) for reading a precise time delta between two points on the shared
// X axis (e.g. "CAN trigger" vs "audio response start"). On/off toggle lives
// in the parent widget; this module only holds the drawing + hit-testing
// math shared by CanSignalChart (local, CAN-specific) and AudioWaveformChart
// (shared with AudioMonitorWidget, which never sets `cursor` and is
// therefore unaffected).

export interface DiffCursorState {
  mode: boolean;
  a: number | null; // epoch ms
  b: number | null; // epoch ms
  onMove: (which: 'a' | 'b', ms: number) => void;
}

export const CURSOR_A_COLOR = '#facc15';
export const CURSOR_B_COLOR = '#a78bfa';

/** Draws whichever of cursor.a/b fall within [xMin, xMax] as a dashed
 * vertical line spanning the plot area. No-op if cursor is undefined or its
 * mode is off. */
export function drawDiffCursors(
  ctx: CanvasRenderingContext2D,
  cursor: DiffCursorState | undefined,
  xMin: number,
  xMax: number,
  plotTop: number,
  plotH: number,
  xToPx: (ms: number) => number,
): void {
  if (!cursor?.mode) return;
  const drawLine = (ms: number | null, color: string) => {
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
    ctx.restore();
  };
  drawLine(cursor.a, CURSOR_A_COLOR);
  drawLine(cursor.b, CURSOR_B_COLOR);
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

export function fmtDelta(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(3)}s`;
}
