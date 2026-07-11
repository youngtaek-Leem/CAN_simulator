// Test scenario runner: upload a JSON step script (see
// Automation/test_script_Rev01.json for the original format this ports),
// optionally upload referenced .blf/.asc log files for CANlogReplay steps,
// then Start/Stop and watch the step-by-step log and per-case pass/fail
// results. The interpreter itself lives entirely in the backend
// (test_runner_service.py) -- this widget only uploads, starts/stops, and
// polls /api/testrunner/status for the live event/result log.

import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import type { TestRunnerStatus, WidgetConfig } from '../types';

const POLL_MS = 400;

export function TestRunnerBox(_: { config: WidgetConfig }) {
  useCanVersion();
  const scriptInput = useRef<HTMLInputElement>(null);
  const logInput = useRef<HTMLInputElement>(null);
  const goldenInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<TestRunnerStatus | null>(null);
  const summary = canStore.status?.test_runner;
  const running = summary?.running ?? false;
  const power = canStore.status?.power;
  const audio = canStore.status?.audio;

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
          시나리오 JSON 열기
        </button>
        <input
          ref={scriptInput}
          type="file"
          accept=".json"
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
        <button className="small-btn danger" onClick={() => api.testRunnerStop()} disabled={!running}>
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
          <div className="testrunner-log-list">
            {detail?.events.map((ev, i) => (
              <div key={i} className="testrunner-log-line mono">
                {ev.msg ?? `[${ev.type}] ${ev.message ?? ''} ${ev.signal ?? ''} → ${ev.status ?? ''}`}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
