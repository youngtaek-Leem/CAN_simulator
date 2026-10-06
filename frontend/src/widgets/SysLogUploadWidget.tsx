import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import type { SysLogUploadStatus, WidgetConfig } from '../types';
import { SeedKeyControls } from './UdsGlobalControls';

interface Props {
  config: WidgetConfig;
}

const STATE_LABELS: Record<string, string> = {
  IDLE: '대기',
  RUNNING: '취득 중',
  COMPLETED: '완료',
  ERROR: '오류',
};

const STATE_COLORS: Record<string, string> = {
  IDLE: '#6b7280',
  RUNNING: '#f59e0b',
  COMPLETED: '#10b981',
  ERROR: '#ef4444',
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function SysLogUploadWidget({ config }: Props) {
  const { updateWidget } = useApp();
  const [status, setStatus] = useState<SysLogUploadStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Persisted in config.options so IDs/address survive page switches --
  // App.tsx only mounts the active page's widgets (see OtaTesterWidget).
  const txId = String(config.options.txId ?? '6D1');
  const rxId = String(config.options.rxId ?? '6B0');
  const address = String(config.options.address ?? '0');
  const securityEnable = (config.options.securityEnable as boolean | undefined) ?? true;
  const setOpt = (patch: Record<string, unknown>) =>
    updateWidget({ ...config, options: { ...config.options, ...patch } });

  const eventsRef = useRef<HTMLDivElement>(null);
  const runningRef = useRef(false);
  runningRef.current = status?.running ?? false;

  useEffect(() => {
    api.syslogUploadStatus().then(setStatus).catch(() => {});
  }, []);

  // Poll status while running — self-rescheduling setTimeout to avoid overlap
  useEffect(() => {
    if (!status?.running) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await api.syslogUploadStatus();
        if (!cancelled && s) setStatus(s);
      } catch {
        // ignore
      } finally {
        if (!cancelled && runningRef.current) setTimeout(poll, 500);
      }
    };
    const timer = setTimeout(poll, 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [status?.running]);

  // Auto-scroll event log to bottom on new entries
  useEffect(() => {
    const el = eventsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [status?.events?.length]);

  const parseHexId = (v: string): number | null => {
    const t = v.trim().toLowerCase().replace(/^0x/, '');
    if (!/^[0-9a-f]+$/.test(t)) return null;
    const n = parseInt(t, 16);
    return n >= 0 && n <= 0x1fffffff ? n : null;
  };

  const parseAddress = (v: string): number | null => {
    const t = v.trim().toLowerCase().replace(/^0x/, '');
    if (!/^[0-9a-f]+$/.test(t)) return null;
    const n = parseInt(t, 16);
    return n >= 0 && n <= 0xffffffff ? n : null;
  };

  const start = async () => {
    const tx = parseHexId(txId);
    const rx = parseHexId(rxId);
    const addr = parseAddress(address);
    if (tx === null || rx === null || addr === null) {
      setError('TX/RX ID와 주소(hex)를 확인하세요');
      return;
    }
    setError(null);
    try {
      const s = await api.syslogUploadStart(tx, rx, addr, securityEnable);
      setStatus(s);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const stop = async () => {
    try {
      const s = await api.syslogUploadStop();
      setStatus(s);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const downloadUrl = status?.saved_filename
    ? `/api/syslog_upload/download?file=${encodeURIComponent(status.saved_filename)}`
    : null;

  const p = status?.progress;
  const stateColor = STATE_COLORS[status?.state ?? 'IDLE'] ?? '#6b7280';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
          TX
          <input
            className="mono" value={txId} disabled={status?.running}
            onChange={e => setOpt({ txId: e.target.value })}
            placeholder="6D1" style={{ width: 64 }} />
        </label>
        <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
          RX
          <input
            className="mono" value={rxId} disabled={status?.running}
            onChange={e => setOpt({ rxId: e.target.value })}
            placeholder="6B0" style={{ width: 64 }} />
        </label>
        <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }} title="RequestUpload 메모리 주소 (기본 0)">
          주소
          <input
            className="mono" value={address} disabled={status?.running}
            onChange={e => setOpt({ address: e.target.value })}
            placeholder="0" style={{ width: 80 }} />
        </label>
        <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }} title="체크 시 27 11 → Seed → 27 12 ASK 인증 수행, 해제 시 생략">
          <input
            type="checkbox" checked={securityEnable} disabled={status?.running}
            onChange={e => setOpt({ securityEnable: e.target.checked })} />
          Security Enable
        </label>
        <SeedKeyControls />
        <span style={{ flex: 1 }} />
        {!status?.running ? (
          <button className="small-btn primary" onClick={start}>▶ 취득 시작</button>
        ) : (
          <button className="small-btn danger" onClick={stop}>■ 중지</button>
        )}
      </div>

      <div style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 600, color: stateColor }}>
          {STATE_LABELS[status?.state ?? 'IDLE'] ?? status?.state}
        </span>
        {p && p.total_bytes > 0 && (
          <span style={{ color: '#6b7280' }}>
            {fmtBytes(p.received_bytes)}/{fmtBytes(p.total_bytes)} ({p.percent.toFixed(1)}%)
            {p.total_blocks > 0 && ` · 블록 ${p.current_block}/${p.total_blocks}`}
            {p.phase && ` · ${p.phase}`}
          </span>
        )}
        {status?.saved_filename && (
          <a
            href={downloadUrl ?? undefined}
            download={status.saved_filename}
            style={{ marginLeft: 'auto', fontSize: 11 }}
            title={`${status.saved_filename} (${fmtBytes(status.saved_size)}) 다운로드`}
          >
            ⬇ {status.saved_filename} ({fmtBytes(status.saved_size)})
          </a>
        )}
      </div>
      {p && p.total_bytes > 0 && (
        <div style={{ background: '#374151', borderRadius: 4, height: 8 }}>
          <div style={{
            width: `${Math.min(100, p.percent)}%`, height: '100%',
            backgroundColor: '#3b82f6', borderRadius: 4, transition: 'width 0.3s',
          }} />
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {status?.error && <div className="error">{status.error}</div>}

      <div ref={eventsRef} style={{
        flex: 1, overflow: 'auto', background: '#1e293b', borderRadius: 6, padding: 8,
        fontSize: 12, fontFamily: 'monospace', minHeight: 80,
      }}>
        {(status?.events ?? []).map((ev, i) => (
          <div key={i} style={{ color: ev.level === 'ERROR' ? '#ef4444' : ev.level === 'WARN' ? '#f59e0b' : '#94a3b8', padding: '2px 0' }}>
            <span style={{ color: '#64748b', marginRight: 8 }}>{new Date(ev.ts * 1000).toLocaleTimeString()}</span>
            {ev.service && <span style={{ color: '#3b82f6', marginRight: 6 }}>[{ev.service}]</span>}
            {ev.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
