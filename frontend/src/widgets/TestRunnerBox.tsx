// Test scenario runner: upload a JSON step script (see
// Automation/test_script_Rev01.json for the original format this ports),
// optionally upload referenced .blf/.asc log files for CANlogReplay steps,
// then Start/Stop and watch the step-by-step log and per-case pass/fail
// results. The interpreter itself lives entirely in the backend
// (test_runner_service.py) -- this widget only uploads, starts/stops, and
// polls /api/testrunner/status for the live event/result log.

import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore, formatTestRunnerEvent, useCanVersion } from '../store/canStore';
import { useApp } from '../store/appContext';
import type { AudioLevel, TestRunnerStatus, WidgetConfig } from '../types';

const POLL_MS = 400;

function parseNumbers(values: string[]): number[] | null {
  const nums = values.map(Number);
  return nums.every(Number.isFinite) ? nums : null;
}

function UnitField({
  value,
  onChange,
  unit,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  unit: string;
  disabled?: boolean;
}) {
  return (
    <span className="power-unit-field">
      <input className="mono power-num" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      <span className="power-unit">{unit}</span>
    </span>
  );
}

export function TestRunnerBox({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { updateWidget } = useApp();
  const scriptInput = useRef<HTMLInputElement>(null);
  const logInput = useRef<HTMLInputElement>(null);
  const goldenInput = useRef<HTMLInputElement>(null);
  const logListRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<TestRunnerStatus | null>(null);
  const summary = canStore.status?.test_runner;
  const running = summary?.running ?? false;
  const paused = summary?.paused ?? false;
  const power = canStore.status?.power;
  const audio = canStore.status?.audio;

  // Battery voltage/current — 공유: PowerControlWidget과 동일한 config.options 키(voltage/current)
  // TestRunnerBox와 PowerControlWidget은 별개 위젯 인스턴스라 config는 분리되지만,
  // 동일한 키/동일 UI/동일 API로 “공유” 동작을 보장. 필요 시 localStorage로 완전 공유 가능.
  const opts = config.options;
  const setOpt = (patch: Record<string, string>) => updateWidget({ ...config, options: { ...opts, ...patch } });
  const voltage = String(opts.voltage ?? '14.4');
  const current = String(opts.current ?? '10');
  const setVoltage = (v: string) => setOpt({ voltage: v });
  const setCurrent = (v: string) => setOpt({ current: v });
  const connected = power?.initialized ?? false;
  const onoffEnabled = power?.onoff.enabled ?? false;
  const sweepEnabled = power?.sweep.enabled ?? false;
  const autoActive = onoffEnabled || sweepEnabled;

  const submitBattery = () => {
    const parsed = parseNumbers([voltage, current]);
    if (!parsed) {
      setError('전압/전류 값이 올바르지 않습니다');
      return;
    }
    void runBattery(() => api.powerSetBattery(parsed[0], parsed[1]));
  };
  const submitOff = () => {
    void runBattery(() => api.powerSetBattery(0, 0));
  };
  const runBattery = async (fn: () => Promise<{ ok: boolean; reason?: string }>) => {
    try {
      const r = await fn();
      setError(r.ok ? null : (r.reason ?? '실패'));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Audio recording status — Pause/Resume 오른쪽 아이콘
  const [audioLevel, setAudioLevel] = useState<AudioLevel | null>(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const lvl = await api.audioLevel();
        if (!cancelled) setAudioLevel(lvl);
      } catch {}
      if (!cancelled) setTimeout(poll, 500);
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, []);
  const isRecording = audioLevel?.recording ?? audio?.recording ?? false;
  const stopRecording = async () => {
    try {
      const r = await api.audioRecordingStop();
      setError(r.ok ? null : (r.reason ?? '정지 실패'));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Auto-scroll to the newest log line, same as the TextDisplay widget.
  useEffect(() => {
    const el = logListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail?.events.length]);

  // Poll the full event/result log while the widget is mounted -- the
  // lightweight summary in the general WS status broadcast doesn't carry it
  // (see backend/main.py's _status(), which deliberately keeps that payload
  // small since it goes out to every client twice a second).
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api
        .testRunnerStatus()
        .then((s) => {
          if (!cancelled) setDetail(s);
        })
        .catch(() => {});
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const uploadScript = async (file: File) => {
    try {
      await api.uploadTestScript(file);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const uploadLogfiles = async (files: FileList) => {
    try {
      for (const file of Array.from(files)) await api.uploadTestLogfile(file);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const start = async () => {
    try {
      await api.testRunnerStart();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const pause = async () => {
    try {
      await api.testRunnerPause();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const resume = async () => {
    try {
      await api.testRunnerResume();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const stop = async () => {
    try {
      await api.testRunnerStop();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const togglePower = async () => {
    try {
      if (power?.initialized) await api.powerDisconnect();
      else await api.powerConnect();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const uploadGolden = async (file: File) => {
    try {
      await api.uploadTestGolden(file);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="testrunner-box">
      <div className="testrunner-toolbar">
        <button className="small-btn" onClick={() => scriptInput.current?.click()}>
          시나리오 파일 열기 (JSON/Excel)
        </button>
        <input
          ref={scriptInput}
          type="file"
          accept=".json,.xlsx"
          hidden
          onChange={(e) => e.target.files?.[0] && uploadScript(e.target.files[0])}
        />
        <button className="small-btn" onClick={() => logInput.current?.click()}>
          로그 파일 추가 (.blf/.asc)
        </button>
        <input
          ref={logInput}
          type="file"
          accept=".blf,.asc"
          multiple
          hidden
          onChange={(e) => e.target.files && uploadLogfiles(e.target.files)}
        />
        <span className="testrunner-fileinfo">
          {summary?.loaded ? `${summary.filename} — ${summary.case_count}개 케이스` : '로드된 시나리오 없음'}
        </span>
        <span className="spacer" />
        <button className="small-btn primary" onClick={start} disabled={!summary?.loaded || running}>
          ▶ Start
        </button>
        {paused ? (
          <button className="small-btn primary" onClick={resume} disabled={!running}>
            ▶ Resume
          </button>
        ) : (
          <button className="small-btn" onClick={pause} disabled={!running} title={running ? '일시정지 — delay/CANResp 타임아웃이 pause 시간만큼 연장됩니다' : ''}>
            ⏸ Pause
          </button>
        )}
        <button
          className={`small-btn ${isRecording ? 'danger' : ''}`}
          onClick={stopRecording}
          disabled={!isRecording}
          title={isRecording ? '오디오 레코딩 중 — 클릭 시 정지 (러너 녹음 포함)' : '레코딩 중 아님'}
        >
          {isRecording ? '🔴 Rec' : '⚪ Rec'}
        </button>
        <button className="small-btn danger" onClick={stop} disabled={!running}>
          ■ Stop
        </button>
      </div>
      <div className="testrunner-toolbar">
        <button className={`small-btn ${power?.initialized ? 'danger' : ''}`} onClick={togglePower}>
          {power?.initialized ? '전원 연결 해제' : '전원 연결'}
        </button>
        <span className="testrunner-fileinfo" title={power?.error ?? undefined}>
          {power?.initialized ? '✅ 파워서플라이 연결됨' : `⚠️ 파워서플라이 없음${power?.error ? ` (${power.error})` : ''}`}
        </span>
        <UnitField value={voltage} unit="V" disabled={!connected || autoActive} onChange={setVoltage} />
        <UnitField value={current} unit="A" disabled={!connected || autoActive} onChange={setCurrent} />
        <button className="small-btn primary" disabled={!connected || autoActive} onClick={submitBattery}>
          OK
        </button>
        <button className="small-btn" disabled={!connected || autoActive} onClick={submitOff} title="출력만 0V, 0A로 설정 (입력 값은 유지 — OK로 원래 값 복귀)">
          OFF
        </button>
        {connected && <span className="hint mono">현재: {power!.battery_voltage}V / {power!.battery_current}A</span>}
        <span className="spacer" />
        <select
          value={audio?.device_index ?? ''}
          onChange={(e) => e.target.value && api.audioSelectDevice(Number(e.target.value))}
        >
          <option value="">오디오 장치 선택…</option>
          {audio?.devices.map((d) => (
            <option key={d.index} value={d.index}>
              {d.name} ({d.channels}ch)
            </option>
          ))}
        </select>
        <button className="icon-btn" title="오디오 장치 목록 새로고침" onClick={() => api.audioDevices()}>
          ⟲
        </button>
        <button className="small-btn" onClick={() => goldenInput.current?.click()}>
          기준(golden) WAV 업로드
        </button>
        <input
          ref={goldenInput}
          type="file"
          accept=".wav"
          hidden
          onChange={(e) => e.target.files?.[0] && uploadGolden(e.target.files[0])}
        />
        {audio?.recording && <span className="testrunner-fileinfo">🔴 녹음 중</span>}
      </div>
      {error && <div className="error">{error}</div>}
      {paused && <div className="hint">⏸ 일시정지 — 재개 시 남은 시간부터 계속</div>}
      <div className="testrunner-body">
        <div className="testrunner-results">
          <div className="testrunner-section-title">케이스 결과</div>
          {!detail?.results.length && <div className="hint">아직 결과 없음</div>}
          {detail?.results.map((r, i) => (
            <div key={i} className={`testrunner-result ${r.status === 'OK' ? 'ok' : 'fail'}`}>
              케이스 {r.case} · 반복 {r.cycle} · {r.status === 'OK' ? '✅ OK' : '❌ Fail'}
            </div>
          ))}
        </div>
        <div className="testrunner-log">
          <div className="testrunner-section-title">실행 로그</div>
          <div className="testrunner-log-list" ref={logListRef}>
            {detail?.events.map((ev, i) => (
              <div key={i} className="testrunner-log-line mono">
                {formatTestRunnerEvent(ev)}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
