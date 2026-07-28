import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { OtaTesterStatus, UdsEvent } from '../types';
import { SERVICE_DISPLAY_NAMES } from '../types';

interface Props {
  config: { id: string; title: string; options: Record<string, unknown> };
}

const STATE_LABELS: Record<string, string> = {
  IDLE: '대기',
  LOADING: '로딩 중',
  READY: '준비',
  RUNNING: '실행 중',
  COMPLETED: '완료',
  ERROR: '오류',
};

const STATE_COLORS: Record<string, string> = {
  IDLE: '#6b7280',
  LOADING: '#f59e0b',
  READY: '#3b82f6',
  RUNNING: '#f59e0b',
  COMPLETED: '#10b981',
  ERROR: '#ef4444',
};

export default function OtaTesterWidget({ config: _config }: Props) {
  const [xmlFile, setXmlFile] = useState<File | null>(null);
  const [status, setStatus] = useState<OtaTesterStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reqId, setReqId] = useState('18DA00F1');
  const [respId, setRespId] = useState('18DA00F1');

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  // Poll status while running
  useEffect(() => {
    if (status?.running) {
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.otaTesterStatus();
          if (s) setStatus(s);
        } catch (e) {
          // ignore polling errors
        }
      }, 800);
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [status?.running]);

  // Scroll events to bottom
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [status?.events]);

  const handleUploadXml = async () => {
    if (!xmlFile) return;
    setError(null);
    setLoading(true);
    try {
      await api.otaTesterLoadXml(xmlFile);
      const s = await api.otaTesterStatus();
      setStatus(s);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleStart = async () => {
    setError(null);
    try {
      const rid = parseInt(reqId, 16) || 0x18DA00F1;
      const rsp = parseInt(respId, 16) || 0x18DA00F1;
      await api.otaTesterStart(rid, rsp);
      const s = await api.otaTesterStatus();
      setStatus(s);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  };

  const handleStop = async () => {
    try {
      await api.otaTesterStop();
      const s = await api.otaTesterStatus();
      setStatus(s);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  };

  const st = status;
  const stateLabel = st ? (STATE_LABELS[st.state] ?? st.state) : '대기';
  const stateColor = st ? (STATE_COLORS[st.state] ?? '#6b7280') : '#6b7280';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontWeight: 600 }}>OTA Tester</span>
        <span style={{
          display: 'inline-block',
          padding: '2px 10px',
          backgroundColor: stateColor,
          color: '#fff',
          borderRadius: 4,
          fontSize: 13,
          fontWeight: 600,
        }}>
          {stateLabel}
        </span>
      </div>

      {error && (
        <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>
      )}

      {/* CAN ID settings */}
      <div style={{ display: 'flex', gap: 8, fontSize: 13 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Req ID
          <input
            type="text"
            value={reqId}
            onChange={e => setReqId(e.target.value)}
            style={{ width: 80, padding: '2px 4px', fontFamily: 'monospace' }}
            placeholder="18DA00F1"
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Resp ID
          <input
            type="text"
            value={respId}
            onChange={e => setRespId(e.target.value)}
            style={{ width: 80, padding: '2px 4px', fontFamily: 'monospace' }}
            placeholder="18DA00F1"
          />
        </label>
      </div>

      {/* XML file upload */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          type="file"
          accept=".xml"
          onChange={e => setXmlFile(e.target.files?.[0] || null)}
          style={{ flex: 1, minWidth: 0, fontSize: 12 }}
        />
        <button
          disabled={!xmlFile || loading || status?.running}
          onClick={handleUploadXml}
          style={{
            padding: '4px 12px',
            backgroundColor: '#1e40af',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: (!xmlFile || loading || status?.running) ? 'not-allowed' : 'pointer',
            opacity: (!xmlFile || loading || status?.running) ? 0.5 : 1,
            whiteSpace: 'nowrap',
          }}
        >
          {loading ? '로딩...' : 'Load XML'}
        </button>
      </div>

      {st?.xml_filename && (
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          Loaded: {st.xml_filename}&nbsp;({st.total_steps} steps)
        </div>
      )}

      {/* Progress bar */}
      {st?.total_steps > 0 && (
        <div style={{ fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span>Step {Math.min(st.current_step_index + 1, st.total_steps)}/{st.total_steps}</span>
            {st.current_step_name && (
              <span style={{ color: '#3b82f6' }}>
                {st.current_step_name in SERVICE_DISPLAY_NAMES ? SERVICE_DISPLAY_NAMES[st.current_step_name] : st.current_step_name}
              </span>
            )}
          </div>
          <div style={{ background: '#374151', borderRadius: 4, height: 8 }}>
            <div style={{
              width: `${st.total_steps > 0 ? ((st.current_step_index + 1) / st.total_steps) * 100 : 0}%`,
              height: '100%',
              backgroundColor: '#3b82f6',
              borderRadius: 4,
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      )}

      {/* Control buttons */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          disabled={status?.running || status?.state === 'RUNNING' || !status?.procedure_loaded}
          onClick={handleStart}
          style={{
            flex: 1,
            padding: 8,
            backgroundColor: '#10b981',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: (status?.running || !status?.procedure_loaded) ? 'not-allowed' : 'pointer',
            opacity: (status?.running || !status?.procedure_loaded) ? 0.5 : 1,
            fontWeight: 600,
          }}
        >
          ▶ Start
        </button>
        <button
          disabled={!status?.running}
          onClick={handleStop}
          style={{
            flex: 1,
            padding: 8,
            backgroundColor: '#ef4444',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: status?.running ? 'pointer' : 'not-allowed',
            opacity: status?.running ? 1 : 0.5,
            fontWeight: 600,
          }}
        >
          ⏹ Stop
        </button>
      </div>

      {/* Event log */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        background: '#1e293b',
        borderRadius: 6,
        padding: 8,
        fontSize: 12,
        fontFamily: 'monospace',
        minHeight: 120,
      }}>
        {st?.events?.map((ev, i) => (
          <div key={i} style={{ color: ev.level === 'ERROR' ? '#ef4444' : ev.level === 'WARNING' ? '#f59e0b' : '#94a3b8', padding: '2px 0' }}>
            <span style={{ color: '#64748b', marginRight: 8 }}>
              {new Date(ev.ts * 1000).toLocaleTimeString()}
            </span>
            {ev.service && <span style={{ color: '#3b82f6', marginRight: 6 }}>[{ev.service}]</span>}
            {ev.msg}
          </div>
        ))}
        <div ref={eventsEndRef} />
      </div>
    </div>
  );
}