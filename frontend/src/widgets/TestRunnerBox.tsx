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
import type { AudioLevel, TestRunnerStatus, WavcmpParams, WidgetConfig } from '../types';
import { WAVCMP_ALGORITHMS } from './wavcmpAlgorithms';

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
  const setOpt = (patch: Record<string, unknown>) => updateWidget({ ...config, options: { ...opts, ...patch } });
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

  // 파형비교 설정 -- 오디오 파형비교 위젯과 백엔드 단일 진실(/api/wavcmp/params)
  // 공유. 실행기 compWAV는 항상 전채널 AND로 비교하므로 channel은 제외.
  // 2초 주기로 서버값을 다시 읽어 상대 위젯의 변경을 반영한다.
  const [wavcmp, setWavcmp] = useState<WavcmpParams | null>(null);
  const [thrText, setThrText] = useState('0.8');
  const [segText, setSegText] = useState('100');
  // 마지막으로 서버에서 반영한 값 -- 폴링값이 이것과 같으면 "내 편집 중"이므로
  // 텍스트를 덮지 않는다. 상대 위젯이 바꾸면 서버값이 달라져 정상 반영된다.
  const wavcmpSyncedRef = useRef<WavcmpParams | null>(null);
  const wavcmpEditingRef = useRef(false);
  const sameWavcmp = (a: WavcmpParams | null, b: WavcmpParams | null) =>
    !!a &&
    !!b &&
    a.algorithm === b.algorithm &&
    a.threshold === b.threshold &&
    a.segment_ms === b.segment_ms &&
    a.align === b.align;
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const p = await api.wavcmpGetParams();
        if (cancelled || sameWavcmp(p, wavcmpSyncedRef.current)) return;
        wavcmpSyncedRef.current = p;
        setWavcmp(p);
        if (!wavcmpEditingRef.current) {
          setThrText(String(p.threshold));
          setSegText(String(p.segment_ms));
        }
      } catch {}
    };
    load();
    const id = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  const saveWavcmp = async (patch: Partial<WavcmpParams>) => {
    try {
      const p = await api.wavcmpSetParams(patch);
      wavcmpSyncedRef.current = p;
      setWavcmp(p);
      setThrText(String(p.threshold));
      setSegText(String(p.segment_ms));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const commitThr = () => {
    const v = Number(thrText);
    if (thrText.trim() !== '' && Number.isFinite(v)) void saveWavcmp({ threshold: v });
    else if (wavcmp) setThrText(String(wavcmp.threshold));
  };
  const commitSeg = () => {
    const v = Math.round(Number(segText));
    if (segText.trim() !== '' && Number.isFinite(v) && v > 0) void saveWavcmp({ segment_ms: v });
    else if (wavcmp) setSegText(String(wavcmp.segment_ms));
  };
  // 파형비교옵션 팝오버 개폐 + 외부클릭/Escape 닫기
  const [wavcmpOpen, setWavcmpOpen] = useState(false);
  const wavcmpWrapRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!wavcmpOpen) return;
    const onDown = (e: PointerEvent) => {
      if (!wavcmpWrapRef.current?.contains(e.target as Node)) setWavcmpOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setWavcmpOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [wavcmpOpen]);
  const wavcmpAlgoLabel =
    WAVCMP_ALGORITHMS.find((a) => a.value === wavcmp?.algorithm)?.label ?? wavcmp?.algorithm ?? '…';

  // 케이스 결과 / 실행 로그 좌우 폭 드래그 조절 (SerialMonitorWidget과 동일
  // 패턴: 드래그 중 로컬 상태, pointerup 때 레이아웃에 영속, 0.2–0.8 클램프).
  const bodyRef = useRef<HTMLDivElement>(null);
  const splitRatio = Math.min(0.8, Math.max(0.2, Number(opts.splitRatio ?? 0.5) || 0.5));
  const [dragRatio, setDragRatio] = useState<number | null>(null);
  const ratio = dragRatio ?? splitRatio;
  const onSplitDown = (e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const move = (ev: PointerEvent) => {
      const el = bodyRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      setDragRatio(Math.min(0.8, Math.max(0.2, (ev.clientX - rect.left) / rect.width)));
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

  const clearGolden = async () => {
    try {
      await api.clearTestGolden();
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
        <span className="testrunner-wavcmp-wrap" ref={wavcmpWrapRef}>
          <button
            className="small-btn"
            title={`파형비교 설정 (오디오 파형비교 위젯과 공유, 실행기는 전채널 비교)\n알고리즘[${wavcmpAlgoLabel}] 임계값[${thrText}] 구간(ms)[${segText}] 시작점매칭[${(wavcmp?.align ?? true) ? 'ON' : 'OFF'}]`}
            onClick={() => setWavcmpOpen((o) => !o)}
          >
            파형비교옵션
          </button>
          {wavcmpOpen && (
            <span className="testrunner-wavcmp-panel">
              <label title="비교 알고리즘">
                알고리즘
                <select
                  value={wavcmp?.algorithm ?? 'comprehensive'}
                  onChange={(e) => void saveWavcmp({ algorithm: e.target.value })}
                >
                  {WAVCMP_ALGORITHMS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <label title="임계값 (0~1, Enter/포커스아웃 시 적용)">
                임계값
                <input
                  className="mono"
                  style={{ width: 52 }}
                  value={thrText}
                  onChange={(e) => setThrText(e.target.value)}
                  onFocus={() => {
                    wavcmpEditingRef.current = true;
                  }}
                  onBlur={() => {
                    wavcmpEditingRef.current = false;
                    commitThr();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                  }}
                />
              </label>
              <label title="구간 길이 ms (Enter/포커스아웃 시 적용)">
                구간(ms)
                <input
                  className="mono"
                  style={{ width: 64 }}
                  value={segText}
                  onChange={(e) => setSegText(e.target.value)}
                  onFocus={() => {
                    wavcmpEditingRef.current = true;
                  }}
                  onBlur={() => {
                    wavcmpEditingRef.current = false;
                    commitSeg();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                  }}
                />
              </label>
              <label title="시작점 매칭 (B 지연 정렬)">
                <input
                  type="checkbox"
                  checked={wavcmp?.align ?? true}
                  onChange={(e) => void saveWavcmp({ align: e.target.checked })}
                />
                시작점매칭
              </label>
            </span>
          )}
        </span>
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
        <span
          className="testrunner-fileinfo"
          title={
            audio?.active_golden
              ? `업로드된 gold (${audio.active_golden}) -- 파일명 없는 compWAV가 이 파일과 비교합니다`
              : 'gold 없음 -- 파일명 없는 compWAV는 직전/현재 녹음을 비교합니다'
          }
        >
          {audio?.active_golden ? `🥇 gold: ${audio.active_golden}` : 'gold 없음'}
        </span>
        {audio?.active_golden && (
          <button className="icon-btn" title="기억된 gold 해제" onClick={() => void clearGolden()}>
            ✕
          </button>
        )}
        {audio?.recording && <span className="testrunner-fileinfo">🔴 녹음 중</span>}
      </div>
      {error && <div className="error">{error}</div>}
      {paused && <div className="hint">⏸ 일시정지 — 재개 시 남은 시간부터 계속</div>}
      <div className="testrunner-body" ref={bodyRef}>
        <div className="testrunner-results" style={{ width: `${ratio * 100}%`, flex: 'none' }}>
          <div className="testrunner-section-title">케이스 결과</div>
          {!detail?.results.length && <div className="hint">아직 결과 없음</div>}
          {detail?.results.map((r, i) => (
            <div key={i} className={`testrunner-result ${r.status === 'OK' ? 'ok' : 'fail'}`}>
              케이스 {r.case} · 반복 {r.cycle} · {r.status === 'OK' ? '✅ OK' : '❌ Fail'}
              {r.status !== 'OK' && r.reason && (
                <div className="testrunner-result-reason" title={r.reason}>
                  {r.reason}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="testrunner-split-drag" onPointerDown={onSplitDown} title="드래그해서 좌우 폭 조절" />
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
