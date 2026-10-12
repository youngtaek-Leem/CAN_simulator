// Serial Monitor widget: dual-port UART RX monitor (backend/serial_service.py).
//
// Two independent receive channels ("A" and "B"), each with its own port +
// baud setting (8N1 fixed). The widget window splits horizontally (top/bottom)
// or vertically (left/right) so each port's incoming data is monitored in its
// own pane. Display defaults to ASCII with a per-pane ASCII/HEX toggle.
//
// Lifecycle follows the CAN simulator, not the widget: configured ports open
// automatically on global Start (/api/run/start) and close on Stop/
// disconnect, and the CAN "로깅" button also records per-port text logs
// (serial_A_<ts>.log / serial_B_<ts>.log). This widget itself has no
// open/close buttons -- it only shows status (open/error/logging) and data.
//
// All settings live in config.options (PowerControlWidget pattern) so they
// survive page switches and layout saves. Live lines accumulate in
// canStore.serialLines (WS {type:"serial"}); entering the widget backfills
// from the backend scrollback REST when the buffer is empty.

import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { useApp } from '../store/appContext';
import type { SerialLine, SerialPortList, WidgetConfig } from '../types';

const BAUDS = [4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];
// DOM cost guard: render at most this many trailing lines per pane.
const RENDER_LINES = 500;

type SplitDir = 'row' | 'col'; // row = 가로 분할(상·하), col = 세로 분할(좌·우)
type TextFormat = 'ascii' | 'hex';

function hexToAscii(hex: string): string {
  let out = '';
  for (let i = 0; i + 1 < hex.length; i += 2) {
    const b = parseInt(hex.slice(i, i + 2), 16);
    out += b >= 0x20 && b <= 0x7e ? String.fromCharCode(b) : '.';
  }
  return out;
}

function spacedHex(hex: string): string {
  const parts: string[] = [];
  for (let i = 0; i + 1 < hex.length; i += 2) parts.push(hex.slice(i, i + 2));
  return parts.join(' ');
}

function fmtTs(tsMs: number): string {
  const d = new Date(tsMs);
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

function renderLine(line: SerialLine, format: TextFormat): string {
  const body = format === 'hex' ? spacedHex(line.data) : hexToAscii(line.data);
  return `[${fmtTs(line.ts)}] ${body}`;
}

function PortPane({
  tag,
  title,
  lines,
  format,
  onFormat,
  statusText,
  onClear,
}: {
  tag: string;
  title: string;
  lines: SerialLine[];
  format: TextFormat;
  onFormat: (f: TextFormat) => void;
  statusText: string;
  onClear: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const autoTailRef = useRef(true);
  const [autoTail, setAutoTail] = useState(true);
  autoTailRef.current = autoTail;
  const shown = lines.slice(-RENDER_LINES);
  // Tail-follow: only when the user hasn't scrolled up (autoTail on).
  useEffect(() => {
    const el = scrollRef.current;
    if (el && autoTailRef.current) el.scrollTop = el.scrollHeight;
  });
  return (
    <div className="serial-pane">
      <div className="serial-pane-header">
        <span className="mono" title={statusText}>
          {title}
        </span>
        <span className="spacer" />
        <span className="hint mono">{lines.length}줄</span>
        <button
          className={`small-btn${format === 'ascii' ? ' primary' : ''}`}
          title="ASCII 표시 (기본)"
          onClick={() => onFormat('ascii')}
        >
          ASCII
        </button>
        <button
          className={`small-btn${format === 'hex' ? ' primary' : ''}`}
          title="HEX 표시"
          onClick={() => onFormat('hex')}
        >
          HEX
        </button>
        <button
          className={`small-btn${autoTail ? ' primary' : ''}`}
          title={autoTail ? '최신 줄 자동 추적 중 (클릭 시 고정)' : '고정됨 (클릭 시 최신 추적 재개)'}
          onClick={() => setAutoTail((v) => !v)}
        >
          {autoTail ? '추적중' : '고정'}
        </button>
        <button className="small-btn" title={`${tag} 포트 화면 지우기 (서버 버퍼·로그 파일은 유지)`} onClick={onClear}>
          지우기
        </button>
      </div>
      <div className="hint mono" title={statusText}>
        {statusText}
      </div>
      <div className="serial-log mono" ref={scrollRef}>
        {shown.map((l, i) => (
          <div key={`${l.ts}-${i}`}>{renderLine(l, format)}</div>
        ))}
      </div>
    </div>
  );
}

export function SerialMonitorWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { updateWidget } = useApp();
  const serial = canStore.status?.serial;
  const [error, setError] = useState<string | null>(null);
  const [ports, setPorts] = useState<SerialPortList | null>(null);

  // Persisted settings (page-switch + layout-save safe).
  const opts = config.options as Record<string, unknown>;
  const portA = String(opts.portA ?? '');
  const baudA = Number(opts.baudA ?? 115200);
  const portB = String(opts.portB ?? '');
  const baudB = Number(opts.baudB ?? 115200);
  const split = (opts.split as SplitDir | undefined) ?? 'col';
  const formatA = (opts.formatA as TextFormat | undefined) ?? 'ascii';
  const formatB = (opts.formatB as TextFormat | undefined) ?? 'ascii';
  const splitRatio = Math.min(0.8, Math.max(0.2, Number(opts.splitRatio ?? 0.5)));
  const setOpt = (patch: Record<string, unknown>) =>
    updateWidget({ ...config, options: { ...opts, ...patch } });

  // Push port settings to the backend as they change so the next global
  // Start opens exactly these ports (open itself happens on Start).
  const pushConfig = async (a: { port: string; baud: number }, b: { port: string; baud: number }) => {
    try {
      await api.serialConfig(a, b);
      setError(null);
    } catch (e) {
      setError(`포트 설정 저장 실패: ${(e as Error).message}`);
    }
  };
  const setPortA = (v: string) => {
    setOpt({ portA: v });
    void pushConfig({ port: v, baud: baudA }, { port: portB, baud: baudB });
  };
  const setBaudA = (v: number) => {
    setOpt({ baudA: v });
    void pushConfig({ port: portA, baud: v }, { port: portB, baud: baudB });
  };
  const setPortB = (v: string) => {
    setOpt({ portB: v });
    void pushConfig({ port: portA, baud: baudA }, { port: v, baud: baudB });
  };
  const setBaudB = (v: number) => {
    setOpt({ baudB: v });
    void pushConfig({ port: portA, baud: baudA }, { port: portB, baud: v });
  };

  // Port list + scrollback backfill (only when the shared buffer is empty --
  // canStore.serialLines accumulates globally across widgets/remounts).
  useEffect(() => {
    let cancelled = false;
    api
      .serialPorts()
      .then((r) => {
        if (!cancelled) setPorts(r);
      })
      .catch(() => {});
    for (const p of ['a', 'b'] as const) {
      if (canStore.serialLines[p].length === 0) {
        api
          .serialLines(p, 0, RENDER_LINES)
          .then((r) => {
            if (!cancelled && canStore.serialLines[p].length === 0 && r.lines.length > 0) {
              canStore.ingestSerial(p === 'a' ? r.lines : [], p === 'b' ? r.lines : []);
            }
          })
          .catch(() => {});
      }
    }
    // Push the persisted settings once on mount so a freshly loaded layout
    // (backend restarted since) re-arms the same ports for the next Start.
    api.serialConfig({ port: portA, baud: baudA }, { port: portB, baud: baudB }).catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openA = serial?.open.a ?? false;
  const openB = serial?.open.b ?? false;
  const statusText = (port: string, baud: number, open: boolean, err: string | null) => {
    if (!serial?.available) return 'pyserial 미설치 — CAN만 동작';
    if (!port) return '포트 미설정';
    if (err) return `오류: ${err}`;
    return `${port} @ ${baud} — ${open ? '수신 중 (Start 연동)' : '대기 중 (Start 시 open)'}`;
  };

  const logging = serial?.logging;
  const runRunning = canStore.status?.run.running ?? false;

  // Draggable split divider (TxBox/SysLog pattern, ratio persisted).
  const boxRef = useRef<HTMLDivElement>(null);
  const [dragRatio, setDragRatio] = useState<number | null>(null);
  const ratio = dragRatio ?? splitRatio;
  const onSplitDown = (e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const move = (ev: PointerEvent) => {
      const el = boxRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const frac =
        split === 'col' ? (ev.clientX - rect.left) / rect.width : (ev.clientY - rect.top) / rect.height;
      setDragRatio(Math.min(0.8, Math.max(0.2, frac)));
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      setDragRatio((r) => {
        if (r !== null) setOpt({ splitRatio: Math.round(r * 100) / 100 });
        return null;
      });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const paneA = (
    <PortPane
      tag="A"
      title={`A — ${portA || '미설정'} @ ${baudA}`}
      lines={canStore.serialLines.a}
      format={formatA}
      onFormat={(f) => setOpt({ formatA: f })}
      statusText={statusText(portA, baudA, openA, serial?.errors.a ?? null)}
      onClear={() => canStore.clearSerialLines('a')}
    />
  );
  const paneB = (
    <PortPane
      tag="B"
      title={`B — ${portB || '미설정'} @ ${baudB}`}
      lines={canStore.serialLines.b}
      format={formatB}
      onFormat={(f) => setOpt({ formatB: f })}
      statusText={statusText(portB, baudB, openB, serial?.errors.b ?? null)}
      onClear={() => canStore.clearSerialLines('b')}
    />
  );

  const portOptions = ports?.ports ?? [];
  const datalistId = `serial-ports-${config.id}`;
  const portField = (
    tag: 'A' | 'B',
    value: string,
    baud: number,
    setPort: (v: string) => void,
    setBaud: (v: number) => void,
  ) => (
    <span className="serial-port-field">
      <span className="mono">{tag}</span>
      <input
        className="mono"
        list={datalistId}
        value={value}
        placeholder="포트 (예: COM3)"
        title="시리얼 포트 — 목록에서 선택하거나 직접 입력"
        onChange={(e) => setPort(e.target.value)}
      />
      <select value={String(baud)} title="baud rate (8N1 고정)" onChange={(e) => setBaud(Number(e.target.value))}>
        {BAUDS.map((b) => (
          <option key={b} value={b}>
            {b}
          </option>
        ))}
      </select>
    </span>
  );

  return (
    <div className="serial-widget">
      <datalist id={datalistId}>
        {portOptions.map((p) => (
          <option key={p.device} value={p.device}>
            {p.description}
          </option>
        ))}
      </datalist>
      <div className="serial-toolbar">
        {portField('A', portA, baudA, setPortA, setBaudA)}
        {portField('B', portB, baudB, setPortB, setBaudB)}
        <span className="spacer" />
        <button
          className={`small-btn${split === 'col' ? ' primary' : ''}`}
          title="세로 분할 (좌·우)"
          onClick={() => setOpt({ split: 'col' })}
        >
          좌우
        </button>
        <button
          className={`small-btn${split === 'row' ? ' primary' : ''}`}
          title="가로 분할 (상·하)"
          onClick={() => setOpt({ split: 'row' })}
        >
          상하
        </button>
      </div>
      <div className="hint">
        {serial && !serial.available
          ? 'pyserial 미설치 — 포트를 열 수 없습니다 (CAN은 정상 동작)'
          : `포트 설정 시 CAN Start와 함께 수신 시작 · Stop 시 종료 · 로깅 버튼 시 파일 2개 기록${
              logging?.active
                ? ` (기록 중: ${logging.files.a ?? '-'} ${logging.counts.a}줄 / ${logging.files.b ?? '-'} ${logging.counts.b}줄)`
                : ''
            }${runRunning ? '' : ' (현재 Stop 상태)'}`}
      </div>
      {error && <span className="error">{error}</span>}
      <div ref={boxRef} className={`serial-split serial-split-${split}`}>
        <div className="serial-split-first" style={split === 'col' ? { width: `${ratio * 100}%` } : { height: `${ratio * 100}%` }}>
          {paneA}
        </div>
        <div className="serial-split-drag" onPointerDown={onSplitDown} title="드래그해서 분할 비율 조절" />
        <div className="serial-split-second">{paneB}</div>
      </div>
    </div>
  );
}
