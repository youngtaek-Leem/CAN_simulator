// 오디오 파형비교: 2개 WAV 업로드 → 시작점 매칭 + 구간별 종합 비교.
//
// 백엔드 backend/wav_compare_service.py가 파일 보관·분석을 담당하고, 이
// 위젯은 업로드 UI + 알고리즘/파라미터 설정 + 결과(판정·구간표·파형) 표시만
// 한다. 파라미터는 config.options에 영속 (레이아웃 저장/불러오기 유지).

import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import type {
  WavCompareResult,
  WavCompareSeries,
  WavCompareStatus,
  WidgetConfig,
} from '../types';
import { niceTicks } from './AudioWaveformChart';
import { WAVCMP_ALGORITHMS } from './wavcmpAlgorithms';

const SIM_LABELS: Record<string, string> = {
  mfcc_dtw: 'MFCC-DTW',
  fft: 'FFT',
  fft_band: '대역FFT',
  xcorr: '상호상관',
  rms: 'RMS',
  zcr: 'ZCR',
  centroid: 'Centroid',
};

function WaveChart({
  title,
  color,
  series,
  xMaxMs,
  offsetMs,
  failRanges,
}: {
  title: string;
  color: string;
  series: WavCompareSeries | null;
  xMaxMs: number;
  offsetMs: number;
  failRanges: { s: number; e: number }[];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const css = getComputedStyle(canvas);
    ctx.fillStyle = css.getPropertyValue('--panel') || '#fff';
    ctx.fillRect(0, 0, w, h);

    const padL = 44;
    const padR = 8;
    const padT = 6;
    const padB = 16;
    const plotW = Math.max(1, w - padL - padR);
    const plotH = Math.max(1, h - padT - padB);
    const xMax = Math.max(xMaxMs, 1);
    const xToPx = (ms: number) => padL + (ms / xMax) * plotW;

    // 실패 구간 음영 (B 차트는 오프셋 적용 좌표계이므로 그대로 그림)
    ctx.fillStyle = 'rgba(255, 60, 60, 0.12)';
    for (const r of failRanges) {
      ctx.fillRect(xToPx(r.s), padT, Math.max(1, xToPx(r.e) - xToPx(r.s)), plotH);
    }

    // 파형 (B는 offsetMs만큼 오른쪽으로 이동 표시)
    if (series) {
      // 전체 min/max로 y 정규화
      let peak = 0;
      for (const p of series.points) {
        peak = Math.max(peak, Math.abs(p.min), Math.abs(p.max));
      }
      if (peak === 0) peak = 1;
      const yToPx = (v: number) => padT + plotH / 2 - (v / peak) * (plotH / 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (const p of series.points) {
        const x = xToPx(p.t_ms + offsetMs);
        if (x < padL - 1 || x > padL + plotW + 1) continue;
        ctx.moveTo(x, yToPx(p.min));
        ctx.lineTo(x, yToPx(p.max));
      }
      ctx.stroke();
      // 0선
      ctx.strokeStyle = 'rgba(128,128,128,0.4)';
      ctx.beginPath();
      ctx.moveTo(padL, yToPx(0));
      ctx.lineTo(padL + plotW, yToPx(0));
      ctx.stroke();
    }

    // x축 눈금 (ms)
    ctx.fillStyle = '#888';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    for (const t of niceTicks(0, xMax, 6)) {
      const x = xToPx(t);
      ctx.fillText(`${Math.round(t)}`, x, h - 4);
      ctx.strokeStyle = 'rgba(128,128,128,0.25)';
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + plotH);
      ctx.stroke();
    }
  }, [series, xMaxMs, offsetMs, failRanges, color]);

  return (
    <div className="wavcmp-chart">
      <div className="wavcmp-chart-title">{title}</div>
      <canvas ref={canvasRef} className="wavcmp-canvas" />
    </div>
  );
}

export function WavCompareWidget({ config }: { config: WidgetConfig }) {
  const { updateWidget } = useApp();
  const opts = config.options;
  const setOpt = (patch: Record<string, unknown>) =>
    updateWidget({ ...config, options: { ...opts, ...patch } });

  const algorithm = String(opts.algorithm ?? 'comprehensive');
  const threshold = String(opts.threshold ?? '0.8');
  const segmentMs = String(opts.segmentMs ?? '100');
  const align = (opts.align as boolean | undefined) ?? true;
  const channel = String(opts.channel ?? 'mix');

  const [status, setStatus] = useState<WavCompareStatus | null>(null);
  const [seriesA, setSeriesA] = useState<WavCompareSeries | null>(null);
  const [seriesB, setSeriesB] = useState<WavCompareSeries | null>(null);
  const [result, setResult] = useState<WavCompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 마지막으로 표시한 비교 결과 ts -- 실행기 등 외부 비교가 새로 생기면
  // 파형+결과를 자동 갱신한다 (다음 비교 시 다시 업데이트).
  const lastTsRef = useRef<number | null>(null);

  const fetchSeries = async (which: 'a' | 'b', ch: string) => {
    try {
      const s = await api.wavcmpSeries(which, 1500, ch);
      if (which === 'a') setSeriesA(s);
      else setSeriesB(s);
    } catch {
      // 업로드 직후가 아니면 무시
    }
  };

  // 마지막으로 서버에서 반영한 공유 설정 -- 폴링값이 이것과 같으면
  // "내 편집 중"이므로 옵션을 덮지 않는다 (입력 중 이전값 복원 방지).
  const paramsSyncedRef = useRef<{ algorithm: string; threshold: string; segment_ms: string; align: boolean } | null>(
    null,
  );
  const paramsEditingRef = useRef(false);
  // 폴링 클로저가 첫 렌더의 opts를 계속 물고 있어 외부 변경 반영 시
  // 그 사이 바뀐 옵션(예: 채널)을 덮어쓰지 않도록 최신 opts 미러 유지.
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const channelRef = useRef(channel);
  channelRef.current = channel;

  const applyServerParams = (st: WavCompareStatus) => {
    const srv = {
      algorithm: st.params.algorithm,
      threshold: String(st.params.threshold),
      segment_ms: String(st.params.segment_ms),
      align: st.params.align,
    };
    const prev = paramsSyncedRef.current;
    if (prev && prev.algorithm === srv.algorithm && prev.threshold === srv.threshold && prev.segment_ms === srv.segment_ms && prev.align === srv.align)
      return;
    paramsSyncedRef.current = srv;
    if (paramsEditingRef.current) return; // 포커스 중에는 덮지 않음
    const cur = optsRef.current;
    const patch: Record<string, unknown> = {};
    if (String(cur.algorithm ?? 'comprehensive') !== srv.algorithm) patch.algorithm = srv.algorithm;
    if (String(cur.threshold ?? '0.8') !== srv.threshold) patch.threshold = srv.threshold;
    if (String(cur.segmentMs ?? '100') !== srv.segment_ms) patch.segmentMs = srv.segment_ms;
    if (((cur.align as boolean | undefined) ?? true) !== srv.align) patch.align = srv.align;
    if (Object.keys(patch).length > 0) {
      updateWidget({ ...config, options: { ...cur, ...patch } });
    }
  };

  const applyLastResult = async (st: WavCompareStatus) => {
    if (!st.last || st.last.ts === lastTsRef.current) return;
    lastTsRef.current = st.last.ts;
    try {
      const full = await api.wavcmpLastResult();
      setResult(full.result);
      lastTsRef.current = full.ts;
      await Promise.all([fetchSeries('a', channelRef.current), fetchSeries('b', channelRef.current)]);
    } catch {
      // 결과 조회 실패 시 무시 (다음 폴링에서 재시도)
      lastTsRef.current = null;
    }
  };

  const pollStatus = async () => {
    try {
      const st = await api.wavcmpStatus();
      setStatus(st);
      applyServerParams(st);
      await applyLastResult(st);
    } catch {
      // 서버 미기동 등은 error 패널을 오염시키지 않고 무시
    }
  };

  useEffect(() => {
    api
      .wavcmpStatus()
      .then((st) => {
        setStatus(st);
        applyServerParams(st);
        if (st.a) void fetchSeries('a', channel);
        if (st.b) void fetchSeries('b', channel);
        void applyLastResult(st);
      })
      .catch((e) => setError((e as Error).message));
    const id = setInterval(() => void pollStatus(), 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 채널 변경 시 표시 파형도 다시 로드
  useEffect(() => {
    if (status?.a) void fetchSeries('a', channel);
    if (status?.b) void fetchSeries('b', channel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  const upload = async (which: 'a' | 'b', file: File) => {
    setError(null);
    setBusy(true);
    try {
      if (which === 'a') await api.wavcmpUploadA(file);
      else await api.wavcmpUploadB(file);
      const st = await api.wavcmpStatus();
      setStatus(st);
      setResult(null);
      await fetchSeries(which, channel);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runCompare = async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await api.wavcmpCompare({
        algorithm,
        threshold: Number(threshold) || 0.8,
        segment_ms: Math.max(1, Math.round(Number(segmentMs) || 100)),
        align,
        channel,
      });
      setResult(r);
      // 서버 last_result와 동기화해 폴링이 같은 결과를 다시 가져오지 않게 한다
      const st = await api.wavcmpStatus();
      setStatus(st);
      if (st.last) lastTsRef.current = st.last.ts;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // 공유 설정 수정 -- 로컬(options) 저장 + 서버에 게시 (실행기와 동기화).
  // channel은 위젯 표시 전용이라 서버에 보내지 않는다.
  const postShared = async (patch: Partial<{ algorithm: string; threshold: number; segment_ms: number; align: boolean }>) => {
    try {
      const p = await api.wavcmpSetParams(patch);
      paramsSyncedRef.current = {
        algorithm: p.algorithm,
        threshold: String(p.threshold),
        segment_ms: String(p.segment_ms),
        align: p.align,
      };
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const maxChannels = Math.max(status?.a?.channels ?? 0, status?.b?.channels ?? 0, 1);
  const fmtFile = (m: WavCompareStatus['a']) =>
    m ? `${m.filename} (${m.duration_s}s · ${m.samplerate}Hz · ${m.channels}ch)` : '없음';

  const offset = result?.offset_ms ?? 0;
  // 양 차트 모두 "정렬 좌표계"로 표시: offset>0(B 지연)이면 B를 오른쪽으로,
  // offset<0이면 A를 오른쪽으로 이동. 실패 구간은 정렬 좌표 그대로 양쪽에 표시.
  const shiftA = Math.min(0, offset);
  const shiftB = Math.min(0, -offset);
  const xMaxMs = Math.max(
    (status?.a?.duration_s ?? 0) * 1000 + shiftA,
    (status?.b?.duration_s ?? 0) * 1000 + shiftB,
    1,
  );
  const failRanges = (result?.segments ?? [])
    .filter((s) => !s.pass)
    .map((s) => ({ s: s.t_start_ms, e: s.t_end_ms }));

  return (
    <div className="wavcmp-widget">
      <div className="graph-toolbar">
        <label className="small-btn">
          📄 A 파일
          <input
            type="file"
            accept=".wav"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) void upload('a', f);
            }}
          />
        </label>
        <label className="small-btn">
          📄 B 파일
          <input
            type="file"
            accept=".wav"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) void upload('b', f);
            }}
          />
        </label>
        <span className="hint">
          A: {fmtFile(status?.a ?? null)} / B: {fmtFile(status?.b ?? null)}
        </span>
        <span className="spacer" />
        <button className="small-btn primary" disabled={busy || !status?.a || !status?.b} onClick={() => void runCompare()}>
          {busy ? '비교 중…' : '🔍 비교 실행'}
        </button>
      </div>

      <div className="wavcmp-params">
        <label>
          알고리즘
          <select
            value={algorithm}
            onChange={(e) => {
              setOpt({ algorithm: e.target.value });
              void postShared({ algorithm: e.target.value });
            }}
          >
            {WAVCMP_ALGORITHMS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label title="통과 기준값 (0~1, 종합은 7지표 평균과 비교, Enter/포커스아웃 시 공유 적용)">
          임계값
          <input
            className="mono"
            value={threshold}
            onChange={(e) => setOpt({ threshold: e.target.value })}
            onFocus={() => {
              paramsEditingRef.current = true;
            }}
            onBlur={() => {
              paramsEditingRef.current = false;
              const v = Number(threshold);
              if (threshold.trim() !== '' && Number.isFinite(v)) void postShared({ threshold: v });
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            }}
            style={{ width: 56 }}
          />
        </label>
        <label title="구간 분할 단위 ms (Enter/포커스아웃 시 공유 적용)">
          구간(ms)
          <input
            className="mono"
            value={segmentMs}
            onChange={(e) => setOpt({ segmentMs: e.target.value })}
            onFocus={() => {
              paramsEditingRef.current = true;
            }}
            onBlur={() => {
              paramsEditingRef.current = false;
              const v = Math.round(Number(segmentMs));
              if (segmentMs.trim() !== '' && Number.isFinite(v) && v > 0) void postShared({ segment_ms: v });
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            }}
            style={{ width: 64 }}
          />
        </label>
        <label title="B 파일의 시작 지연을 전역 탐색으로 맞춰 정렬">
          <input
            type="checkbox"
            checked={align}
            onChange={(e) => {
              setOpt({ align: e.target.checked });
              void postShared({ align: e.target.checked });
            }}
          />
          시작점 매칭
        </label>
        <label title="비교·표시할 채널 (mix=모노 믹스다운)">
          채널
          <select value={channel} onChange={(e) => setOpt({ channel: e.target.value })}>
            <option value="mix">mix</option>
            {Array.from({ length: maxChannels }, (_, i) => (
              <option key={i} value={String(i)}>
                ch{i}
              </option>
            ))}
          </select>
        </label>
        {result && (
          <span className={`wavcmp-verdict ${result.overall_pass ? 'ok' : 'fail'}`}>
            {result.overall_pass ? '✅ 일치' : '❌ 불일치'}
          </span>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      {result && (
        <div className="wavcmp-summary mono">
          offset {result.offset_ms}ms · {result.passed_count}/{result.segment_count} 구간 통과
          ({(result.pass_rate * 100).toFixed(1)}%) · 대표값 {result.overall_value} (임계{' '}
          {result.threshold})
          {result.per_channel && (
            <span className="wavcmp-sims" title="전채널 비교 (실행기)">
              {result.per_channel.map((p) => (
                <span key={p.channel}>
                  ch{p.channel}={p.overall_pass ? '✅' : '❌'}
                </span>
              ))}
            </span>
          )}
          <span className="wavcmp-sims">
            {Object.entries(result.overall_sims).map(([k, v]) => (
              <span key={k} title={SIM_LABELS[k] ?? k}>
                {SIM_LABELS[k] ?? k}={v.toFixed(3)}
              </span>
            ))}
          </span>
        </div>
      )}

      <div className="wavcmp-charts">
        <WaveChart title="A" color="#2f7cf6" series={seriesA} xMaxMs={xMaxMs} offsetMs={shiftA} failRanges={failRanges} />
        <WaveChart
          title={`B${result ? ` (offset ${result.offset_ms}ms)` : ''}`}
          color="#e06c2f"
          series={seriesB}
          xMaxMs={xMaxMs}
          offsetMs={shiftB}
          failRanges={failRanges}
        />
      </div>

      {result && result.segments.length > 0 && (
        <div className="wavcmp-segments">
          <div className="wavcmp-section-title">구간별 결과 ({result.segment_count}개)</div>
          <div className="wavcmp-segment-list">
            {result.segments.map((s) => (
              <div
                key={s.index}
                className={`wavcmp-segment-row ${s.pass ? 'ok' : 'fail'}`}
                title={
                  s.error ??
                  Object.entries(s.sims)
                    .map(([k, v]) => `${SIM_LABELS[k] ?? k}=${v.toFixed(3)}`)
                    .join(' ')
                }
              >
                <span className="mono">
                  #{s.index} {s.t_start_ms}–{s.t_end_ms}ms
                </span>
                <span>{s.pass ? '✅' : '❌'}</span>
                <span className="mono">{s.value.toFixed(3)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
