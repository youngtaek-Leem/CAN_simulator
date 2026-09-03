// 오디오 신호 모니터: 선택된 오디오 입력 장치(CH3/CH4)의 실시간 레벨(Peak/RMS) +
// 실시간 파형. 채널이 2개면 CAN 신호 그래프 위젯처럼 각 채널을 별도 미니 차트로
// 분리해서 세로로 쌓고, 각 차트는 X(시간)/Y(진폭) 축을 독립적으로 확대/축소·팬할
// 수 있다 (GraphWidget.tsx의 SignalChart와 동일한 상호작용) -- 캔버스 드로잉/줌팬/
// 폴링 자체는 AudioWaveformChart.tsx(공용, CanAudioLatencyWidget.tsx와 공유)가
// 담당하고, 이 파일은 그 차트를 "각자 자기 X뷰를 갖는 standalone" 모드로 쓴다.
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
import { AudioWaveformChart, type AudioChartMargin } from './AudioWaveformChart';
import type { AudioLevel, WidgetConfig } from '../types';

const LEVEL_POLL_MS = 100;
const WAVEFORM_POLL_MS = 60;
const CHANNEL_COLORS = ['#3b82f6', '#f97316'];

const MARGIN: AudioChartMargin = { left: 40, right: 10, top: 8, bottom: 20 };
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

function fmtWindow(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function AudioMonitorWidget(_: { config: WidgetConfig }) {
  useCanVersion();
  const audio = canStore.status?.audio;
  const [level, setLevel] = useState<AudioLevel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [xWindowMs, setXWindowMs] = useState(DEFAULT_X_WINDOW_MS);
  const [resetToken, setResetToken] = useState(0);
  // Tracks the widget's own Record filename across polls so a 30-minute
  // segment rotation (server-side, see audio_service.py's rotation timer)
  // can be surfaced as an activity line instead of happening silently.
  const lastFilenameRef = useRef<string | null>(null);

  // Self-rescheduling (setTimeout after each request settles) instead of a
  // plain setInterval -- same rationale as AudioWaveformChart's poll: caps
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
      else setResetToken((n) => n + 1);
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
      else setResetToken((n) => n + 1);
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

  // Start는 항상 Stop 상태에서만 호출되어야 하며, 버퍼 및 그래프를 모두 초기화 후 재시작
  const prevActiveRef = useRef(false);
  useEffect(() => {
    const wasActive = prevActiveRef.current;
    if (active && !wasActive) {
      setResetToken((n) => n + 1);
    }
    prevActiveRef.current = active;
  }, [active]);

  // Live/frozen "now" anchor: while active, tracks the current moment
  // (refreshed on every redraw); the instant `active` goes false, freezes at
  // whatever it last held instead of continuing to advance with wall-clock
  // time (which would otherwise scroll the last-fetched waveform straight
  // out of view within a few seconds of Stop). Cleared back to live the
  // moment Start/Record reopens the stream. One anchor shared by every
  // channel chart (they all freeze/unfreeze together), but each chart still
  // owns its own X *view* (zoom/pan) independently.
  const activeRef = useRef(active);
  activeRef.current = active;
  const liveAnchorMsRef = useRef<number>(Date.now());
  const frozenAnchorMsRef = useRef<number | null>(null);
  const nowAnchor = (): number => {
    if (activeRef.current) {
      liveAnchorMsRef.current = Date.now();
      frozenAnchorMsRef.current = null;
      return liveAnchorMsRef.current;
    }
    if (frozenAnchorMsRef.current === null) frozenAnchorMsRef.current = liveAnchorMsRef.current;
    return frozenAnchorMsRef.current;
  };

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
          <AudioWaveformChart
            key={ch.index}
            channelIndex={ch.index}
            color={CHANNEL_COLORS[i % CHANNEL_COLORS.length]}
            showXAxis={i === channels.length - 1}
            xWindowMs={xWindowMs}
            margin={MARGIN}
            waveformPollMs={WAVEFORM_POLL_MS}
            pollEnabled={active}
            streamStartedAtMs={streamStartedAtMs}
            nowAnchor={nowAnchor}
            xTickMode="sinceStreamStart"
            xTickDecimals={2}
            resetToken={resetToken}
            onResetClick={() => setResetToken((n) => n + 1)}
            resetTitle="X/Y 축 자동 맞춤으로 리셋"
          />
        ))}
      </div>
    </div>
  );
}
