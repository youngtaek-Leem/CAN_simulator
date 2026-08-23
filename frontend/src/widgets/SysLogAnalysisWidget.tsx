// sysLog 분석 위젯: 바이너리 sysLog 파일 + logDB(ID;NAME) 텍스트 파일을 업로드하면
// 백엔드(syslog_service.py)가 파싱해서 log ID별 시계열을 만들어준다. 좌측에는 스크롤
// 가능한 ID 체크박스 목록(ID가 많으므로 창 크기에 맞춰 스크롤), 우측에는 선택된 ID들의
// 계단형(step) 미니 차트를 세로로 쌓아 보여준다(역시 스크롤).
//
// x축(시간)은 모든 차트가 부모의 공유 ref(sharedXRef)를 통해 동기화된다 -- 어느
// 차트를 휠 줌/드래그 팬해도 나머지 차트가 같이 움직인다. 이 패턴은
// CanAudioLatencyWidget.tsx가 CAN 신호 차트 + 오디오 채널 차트들을 동기화하는 데 쓴
// 것과 동일해서, 그 위젯이 export하는 AudioWaveformChart.tsx의 공용 타입/헬퍼와
// DiffCursor.ts의 커서 기능을 그대로 재사용한다. y축은 차트마다 독립(값 스케일이
// ID마다 크게 다르므로)이고, 값이 항상 0 이상이라는 사양에 맞춰 auto-fit 최소값을
// 항상 0으로 고정한다.
//
// x축 좌표(plot_x)는 세그먼트 이어붙이기 결과(syslog_service.py 참고, 역주행 시
// 새 세그먼트로 이어붙임)라 실제 달력 시간과 선형이 아니다 -- 임의의 plot_x를 실제
// day/hour/min/ms로 되돌리려면 백엔드가 함께 내려주는 세그먼트 경계 목록
// (`/api/syslog/timeline`)이 필요하다 (plotXToAbsMs 참고).
//
// log 파일/DB 파일은 백엔드에 세션당 1개씩만 유지되는 교체 방식이라(syslog_service.py의
// SysLogService), 이 위젯의 여러 인스턴스가 같은 데이터를 공유한다 -- 새로 업로드하면
// 이전 파일을 덮어쓴다. 선택된 ID 목록만 위젯별로 config.options.selectedIds에 저장된다.

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import { niceTicks, orFallback, type AudioChartXView, type Geom } from './AudioWaveformChart';
import {
  drawDiffCursors,
  nearestCursor,
  CURSOR_A_COLOR,
  CURSOR_B_COLOR,
  type DiffCursorState,
} from './DiffCursor';
import type {
  SysLogIdInfo,
  SysLogPoint,
  SysLogScriptResult,
  SysLogSeries,
  SysLogStatus,
  SysLogTimeline,
  SysLogTimelineSegment,
  WidgetConfig,
} from '../types';

const PALETTE = [
  '#3b82f6',
  '#f87171',
  '#34d399',
  '#fbbf24',
  '#a78bfa',
  '#f472b6',
  '#22d3ee',
  '#fb923c',
];

const MARGIN = { left: 58, right: 10, top: 8, bottom: 22 }; // left는 16진수 y라벨("0xFFFF")도 넉넉히 들어가게
const WHEEL_ZOOM_STEP = 1.1;
const BUTTON_ZOOM_FACTOR = 1.3;
const DOT_RADIUS = 2.5;

type SharedXView = AudioChartXView; // { xMin: number | null; xMax: number | null } -- 여기선 plot_x 단위

interface YView {
  yMin: number | null;
  yMax: number | null;
}

function getSelectedIds(config: WidgetConfig): number[] {
  return (config.options.selectedIds as number[] | undefined) ?? [];
}

// log ID를 고정 범위로 나누는 그룹 -- 사용자가 지정한 구간표.
const ID_GROUPS: { label: string; min: number; max: number }[] = [
  { label: '0~399', min: 0, max: 399 },
  { label: '400~499', min: 400, max: 499 },
  { label: '500~599', min: 500, max: 599 },
  { label: '600~699', min: 600, max: 699 },
  { label: '700~799', min: 700, max: 799 },
  { label: '800~899', min: 800, max: 899 },
  { label: '900~999', min: 900, max: 999 },
  { label: '1000~1299', min: 1000, max: 1299 },
  { label: '1300~1399', min: 1300, max: 1399 },
  { label: '1400~2000', min: 1400, max: 2000 },
];

/** plot_x(세그먼트 이어붙인 좌표) -> 실제 절대ms. 세그먼트 경계 중 plot_x_start가
 * x 이하인 마지막 것을 찾아, 그 세그먼트 시작점 기준 상대 경과를 더한다
 * (syslog_service.py의 build_global_timeline과 동일한 역연산). */
function plotXToAbsMs(segments: SysLogTimelineSegment[], x: number): number {
  if (segments.length === 0) return x;
  let seg = segments[0];
  for (const s of segments) {
    if (s.plot_x_start <= x) seg = s;
    else break;
  }
  return seg.abs_ms_start + (x - seg.plot_x_start);
}

/** 절대ms -> "D<day> HH:MM:SS.mmm" (디코딩한 day/hour/min/millisec 표현). */
function fmtAbsTime(absMs: number): string {
  const sign = absMs < 0 ? '-' : '';
  absMs = Math.abs(absMs);
  const day = Math.floor(absMs / 86_400_000);
  let rem = absMs % 86_400_000;
  const hour = Math.floor(rem / 3_600_000);
  rem %= 3_600_000;
  const minute = Math.floor(rem / 60_000);
  rem %= 60_000;
  const sec = Math.floor(rem / 1000);
  const millis = Math.floor(rem % 1000);
  const p2 = (n: number) => String(n).padStart(2, '0');
  const p3 = (n: number) => String(n).padStart(3, '0');
  return `${sign}D${day} ${p2(hour)}:${p2(minute)}:${p2(sec)}.${p3(millis)}`;
}

/** step-held 시맨틱(값이 다음 샘플까지 유지)으로, 주어진 plot_x 시점에 표시돼야
 * 할 값을 가진 점을 찾는다 -- x_ms <= plotX인 마지막 점. plotX가 첫 점보다
 * 앞이면 아직 값이 없으므로 null. */
function findHeldPoint(points: SysLogPoint[], plotX: number): SysLogPoint | null {
  let held: SysLogPoint | null = null;
  for (const p of points) {
    if (p.x_ms <= plotX) held = p;
    else break;
  }
  return held;
}

function fmtValue(v: number, mode: 'hex' | 'dec'): string {
  const n = Math.round(v);
  if (mode === 'dec') return n.toString();
  const abs = Math.abs(n).toString(16).toUpperCase();
  return n < 0 ? `-0x${abs}` : `0x${abs}`;
}

/** 커서 간격(Δ) 표시용 -- plot_x 차이를 실제 경과 시간처럼 day/hour/min/sec로 분해. */
function fmtDeltaDuration(ms: number): string {
  const sign = ms < 0 ? '-' : '';
  ms = Math.abs(ms);
  if (ms < 1000) return `${sign}${ms.toFixed(0)}ms`;
  const day = Math.floor(ms / 86_400_000);
  let rem = ms % 86_400_000;
  const hour = Math.floor(rem / 3_600_000);
  rem %= 3_600_000;
  const minute = Math.floor(rem / 60_000);
  rem %= 60_000;
  const sec = rem / 1000;
  const parts: string[] = [];
  if (day) parts.push(`${day}d`);
  if (day || hour) parts.push(`${hour}h`);
  if (day || hour || minute) parts.push(`${minute}m`);
  parts.push(`${sec.toFixed(3)}s`);
  return sign + parts.join(' ');
}

/** 생성된 JSON을 브라우저 다운로드로 전달한다(서버가 아니라 클라이언트에서
 * blob 링크를 만들어 클릭하는 표준 패턴 -- 이 앱에 아직 이런 다운로드가
 * 없어서 새로 추가). */
function downloadTextFile(filename: string, content: string, mime = 'application/json'): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** test_script_Rev01.json과 같은 스타일로 포맷: 배열 자체는 줄바꿈하되, 각
 * 스텝 객체는 (그 안의 "type" 등 키 줄바꿈 없이) 한 줄로 -- `JSON.stringify`의
 * 들여쓰기 옵션은 중첩 객체까지 전부 펼쳐버려서 이 스타일을 못 만들기 때문에
 * 배열 원소 단위로 직접 이어붙인다. */
/** 스텝 하나를 한 줄로("Signal"/"Value" 단일 신호) 또는, 같은 메시지의 여러
 * 신호가 딜레이 없이 묶인 경우("Signals" 배열, 백엔드
 * syslog_script_generator.py가 만듦) 레퍼런스처럼 신호마다 한 줄씩 펼쳐서
 * 표시한다. */
function formatStep(step: Record<string, unknown>): string {
  const signals = step['Signals'] as { Signal: string; Value: string }[] | undefined;
  if (!signals) return '\t' + JSON.stringify(step);
  const sigLines = signals.map((s) => '\t\t' + JSON.stringify(s)).join(',\n');
  return `\t{ "type": ${JSON.stringify(step['type'])}, "Message": ${JSON.stringify(step['Message'])}, "Signals": [\n${sigLines}\n\t] }`;
}

function formatStepsAsJson(steps: Record<string, unknown>[]): string {
  if (steps.length === 0) return '[]\n';
  const lines = steps.map(formatStep);
  return '[\n' + lines.join(',\n') + '\n]\n';
}

export function SysLogAnalysisWidget({ config }: { config: WidgetConfig }) {
  const { updateWidget } = useApp();
  const selectedIds = getSelectedIds(config);
  const [status, setStatus] = useState<SysLogStatus | null>(null);
  const [ids, setIds] = useState<SysLogIdInfo[]>([]);
  const [timeline, setTimeline] = useState<SysLogTimeline | null>(null);
  const [seriesMap, setSeriesMap] = useState<Record<number, SysLogSeries>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setSelectedIds = (next: number[]) =>
    updateWidget({ ...config, options: { ...config.options, selectedIds: next } });

  // status + timeline만 갱신한다(ids는 timeline.segments 개수를 알아야 "체크된
  // 구간" 기본값(전부 체크)을 계산할 수 있어서 별도 effect로 분리 -- 아래
  // "ID 목록 + 구간별 count" effect 참고).
  const refreshStatusAndTimeline = async () => {
    const [st, tl] = await Promise.all([api.syslogStatus(), api.syslogTimeline()]);
    setStatus(st);
    setTimeline(tl);
    return tl;
  };

  const fetchSeries = async (targetIds: number[]) => {
    if (targetIds.length === 0) {
      setSeriesMap({});
      return;
    }
    const res = await api.syslogSeries(targetIds);
    const parsed: Record<number, SysLogSeries> = {};
    for (const [k, v] of Object.entries(res)) parsed[Number(k)] = v;
    setSeriesMap(parsed);
  };

  useEffect(() => {
    // 백엔드가 세션당 1개만 유지하므로, 마운트 시 이미 업로드된 데이터가 있으면 그대로 반영
    refreshStatusAndTimeline()
      .then(() => fetchSeries(selectedIds))
      .catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchSeries(selectedIds).catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds.join(',')]);

  // ID 목록(이름/count) 갱신 -- timeline이 바뀌거나(새 로그/DB 업로드) 체크된
  // 시간 구간이 바뀔 때마다, 그 구간 안의 count로 다시 받아온다. 구간 체크
  // 상태를 저장해두지 않았으면(undefined) 전체 구간이 기본 체크.
  useEffect(() => {
    if (!timeline) return;
    const arr = config.options.checkedSegments as number[] | undefined;
    const indices = arr !== undefined ? arr : timeline.segments.map((_, i) => i);
    api.syslogIds(indices).then(setIds).catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline, JSON.stringify(config.options.checkedSegments)]);

  const uploadLog = async (file: File) => {
    setError(null);
    setBusy(true);
    try {
      await api.syslogUploadLog(file);
      await refreshStatusAndTimeline();
      await fetchSeries(selectedIds);
      // 새 log 파일은 구간 구성 자체가 달라지므로, 이전 파일 기준으로 저장해둔
      // 구간 체크 상태를 지워서 새 구간 전체가 기본(전부 체크)이 되게 한다.
      updateWidget({ ...config, options: { ...config.options, checkedSegments: undefined } });
      resetEverything();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const uploadDb = async (file: File) => {
    setError(null);
    setBusy(true);
    try {
      await api.syslogUploadDb(file);
      await refreshStatusAndTimeline(); // DB 재업로드로 이름이 바뀌므로 ID 목록도 다시 받아옴(effect가 처리)
      await fetchSeries(selectedIds);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleId = (id: number) => {
    const next = selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id];
    setSelectedIds(next);
  };

  // ---- ID 정렬(이름순/ID순) + ID 범위 그룹 ----------------------------------
  const sortMode = (config.options.sortMode as 'name' | 'id' | undefined) ?? 'name';
  const setSortMode = (mode: 'name' | 'id') => updateWidget({ ...config, options: { ...config.options, sortMode: mode } });

  const sortedIds = [...ids].sort((a, b) =>
    sortMode === 'id' ? a.id - b.id : a.name.localeCompare(b.name),
  );
  const idGroups = ID_GROUPS.map((g) => ({
    ...g,
    items: sortedIds.filter((info) => info.id >= g.min && info.id <= g.max),
  })).filter((g) => g.items.length > 0);

  const setGroupSelected = (items: SysLogIdInfo[], selected: boolean) => {
    const groupIdSet = new Set(items.map((i) => i.id));
    const next = selected
      ? Array.from(new Set([...selectedIds, ...groupIdSet]))
      : selectedIds.filter((id) => !groupIdSet.has(id));
    setSelectedIds(next);
  };

  // ---- 모든 차트가 공유하는 X(시간)뷰 ----------------------------------------
  const sharedXRef = useRef<SharedXView>({ xMin: null, xMax: null });
  const [sharedVersion, setSharedVersion] = useState(0);
  const notifyChange = () => setSharedVersion((n) => n + 1);
  // 부모의 "전체 리셋"이 각 차트에게 "네 Y뷰도 지워라"라고 알리는 신호
  const [resetToken, setResetToken] = useState(0);

  const plotXMin = timeline?.plot_x_min ?? 0;
  const plotXMax = timeline && timeline.plot_x_max > timeline.plot_x_min ? timeline.plot_x_max : plotXMin + 1;

  const resetEverything = () => {
    sharedXRef.current = { xMin: null, xMax: null };
    setResetToken((n) => n + 1);
    notifyChange();
  };

  const zoomSharedX = (factor: number) => {
    const v = sharedXRef.current;
    const xMin = v.xMin ?? plotXMin;
    const xMax = v.xMax ?? plotXMax;
    const center = (xMin + xMax) / 2;
    const halfWidth = ((xMax - xMin) / 2) * factor;
    v.xMin = center - halfWidth;
    v.xMax = center + halfWidth;
    notifyChange();
  };

  // ---- 커서 on/off (CanAudioLatencyWidget.tsx + DiffCursor.ts와 동일한 방식) ----
  const [cursorMode, setCursorMode] = useState(false);
  const [cursorA, setCursorA] = useState<number | null>(null);
  const [cursorB, setCursorB] = useState<number | null>(null);
  const onCursorMove = (which: 'a' | 'b', x: number) => {
    if (which === 'a') setCursorA(x);
    else setCursorB(x);
    notifyChange();
  };
  const toggleCursorMode = () => {
    if (!cursorMode && cursorA === null && cursorB === null) {
      const v = sharedXRef.current;
      const xMax = v.xMax ?? plotXMax;
      const xMin = v.xMin ?? plotXMin;
      setCursorA(xMin + (xMax - xMin) / 3);
      setCursorB(xMin + ((xMax - xMin) * 2) / 3);
    }
    setCursorMode((m) => !m);
  };
  const cursor: DiffCursorState = { mode: cursorMode, a: cursorA, b: cursorB, onMove: onCursorMove };
  const cursorDeltaMs = cursorA !== null && cursorB !== null ? Math.abs(cursorB - cursorA) : null;

  const segments = timeline?.segments ?? [];

  // ---- 시간 구간(역주행 경계 기준) 체크박스 -- 체크된 구간의 데이터만 그래프에
  // 표시한다. selectedIds와 마찬가지로 config.options에 저장해서 레이아웃
  // 저장/불러오기 후에도 유지되게 한다(레이아웃을 불러오면 위젯이 새로
  // 마운트돼 로컬 state는 초기화되므로, 로컬 state로만 두면 사라진다 --
  // 실사용 확인된 버그).
  //
  // 체크 상태를 저장하지 않은 적(undefined)은 "전부 체크"로 간주한다 --
  // 새 log 파일을 업로드해 구간 자체가 바뀌면(uploadLog에서) 이 값을 다시
  // undefined로 되돌려 새 구간 전체가 기본으로 체크되게 한다.
  const checkedSegmentsArr = config.options.checkedSegments as number[] | undefined;
  const checkedSegments =
    checkedSegmentsArr !== undefined ? new Set(checkedSegmentsArr) : new Set(segments.map((_, i) => i));
  const setCheckedSegments = (next: Set<number>) =>
    updateWidget({ ...config, options: { ...config.options, checkedSegments: Array.from(next) } });

  const toggleSegment = (i: number) => {
    const next = new Set(checkedSegments);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setCheckedSegments(next);
    resetEverything(); // 체크 상태가 바뀌면 이전 줌/팬이 무의미해질 수 있어 리셋
  };
  const setAllSegments = (checked: boolean) => {
    setCheckedSegments(checked ? new Set(segments.map((_, i) => i)) : new Set());
    resetEverything();
  };

  const isPlotXInCheckedSegments = (x: number): boolean => {
    if (checkedSegments.size === segments.length) return true; // 전부 체크 = 필터 없음
    for (let i = 0; i < segments.length; i++) {
      if (!checkedSegments.has(i)) continue;
      const seg = segments[i];
      if (x >= seg.plot_x_start && x <= seg.plot_x_end) return true;
    }
    return false;
  };

  // 체크된 구간만 반영한 x축 기본(전체 맞춤) 범위 -- 언체크된 구간이 있으면
  // 굳이 그만큼 빈 공간을 넓게 잡을 필요가 없다.
  const checkedSegmentList = segments.filter((_, i) => checkedSegments.has(i));
  const effectiveXMin = checkedSegmentList.length > 0 ? Math.min(...checkedSegmentList.map((s) => s.plot_x_start)) : plotXMin;
  const effectiveXMax = checkedSegmentList.length > 0 ? Math.max(...checkedSegmentList.map((s) => s.plot_x_end)) : plotXMax;

  const filterSeriesToCheckedSegments = (series: SysLogSeries): SysLogSeries => {
    if (checkedSegments.size === segments.length) return series;
    return { ...series, points: series.points.filter((p) => isPlotXInCheckedSegments(p.x_ms)) };
  };

  // ---- CAN 테스트 스크립트 생성 (체크된 시간 구간의 log ID 0~399를
  // CAN-DB와 매칭해 CANReq/CANEv .json으로) ----------------------------------
  const [scriptBusy, setScriptBusy] = useState(false);
  const [scriptError, setScriptError] = useState<string | null>(null);
  const [scriptResult, setScriptResult] = useState<SysLogScriptResult | null>(null);

  const generateScript = async () => {
    setScriptError(null);
    setScriptBusy(true);
    try {
      const result = await api.syslogGenerateScript(Array.from(checkedSegments));
      setScriptResult(result);
      if (result.steps.length > 0) {
        const base = (status?.log_filename ?? 'syslog').replace(/\.[^.]+$/, '');
        downloadTextFile(`${base}_script.json`, formatStepsAsJson(result.steps));
      }
    } catch (e) {
      setScriptError((e as Error).message);
    } finally {
      setScriptBusy(false);
    }
  };

  // 그래프 영역(오른쪽 컬럼)은 휠 = 확대/축소 전용, 스크롤은 오른쪽 스크롤바를
  // 직접 드래그해서만 하도록 한다. React의 합성 onWheel은 브라우저에 따라
  // passive 리스너로 등록돼 preventDefault가 조용히 무시될 수 있어서(캔버스
  // 바깥, 예: 차트 헤더 위에서 휠을 굴리면 이 컨테이너의 overflow-y 스크롤이
  // 새어나가는 것을 실측으로 확인함), 네이티브 addEventListener를
  // { passive: false }로 직접 등록해 항상 확실하게 막는다.
  const graphsColRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = graphsColRef.current;
    if (!el) return;
    const blockWheelScroll = (e: WheelEvent) => e.preventDefault();
    el.addEventListener('wheel', blockWheelScroll, { passive: false });
    return () => el.removeEventListener('wheel', blockWheelScroll);
  }, []);

  // ---- 그래프 드래그 재정렬 (차트 헤더를 드래그 핸들로 사용) ----------------
  const dragIdRef = useRef<number | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null);
  const handleChartDragStart = (id: number) => (e: React.DragEvent) => {
    dragIdRef.current = id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(id));
  };
  const handleChartDragOver = (id: number) => (e: React.DragEvent) => {
    if (dragIdRef.current === null || dragIdRef.current === id) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverId(id);
  };
  const handleChartDrop = (id: number) => (e: React.DragEvent) => {
    e.preventDefault();
    const draggedId = dragIdRef.current;
    dragIdRef.current = null;
    setDragOverId(null);
    if (draggedId === null || draggedId === id) return;
    const from = selectedIds.indexOf(draggedId);
    const to = selectedIds.indexOf(id);
    if (from === -1 || to === -1) return;
    const next = [...selectedIds];
    next.splice(from, 1);
    next.splice(to, 0, draggedId);
    setSelectedIds(next);
  };
  const handleChartDragEnd = () => {
    dragIdRef.current = null;
    setDragOverId(null);
  };

  return (
    <div className="syslog-widget">
      <div className="graph-toolbar">
        <label className="small-btn">
          📄 log 파일 업로드
          <input
            type="file"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) uploadLog(f);
            }}
          />
        </label>
        <label className="small-btn">
          📄 logDB 파일 업로드
          <input
            type="file"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) uploadDb(f);
            }}
          />
        </label>
        <span className="hint">
          {status?.log_filename ? `log: ${status.log_filename} (${status.record_count}건)` : 'log 파일 없음'}
          {' / '}
          {status?.db_filename ? `DB: ${status.db_filename}` : 'DB 파일 없음'}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="X축 확대" onClick={() => zoomSharedX(1 / BUTTON_ZOOM_FACTOR)}>
          X+
        </button>
        <button className="icon-btn" title="X축 축소" onClick={() => zoomSharedX(BUTTON_ZOOM_FACTOR)}>
          X−
        </button>
        <button className="icon-btn" title="모든 그래프의 X/Y 축을 자동 맞춤으로 리셋" onClick={resetEverything}>
          ⟲
        </button>
        <button
          className={`small-btn ${cursorMode ? 'primary' : ''}`}
          title="차트에서 드래그해 두 커서를 움직이면 그 사이의 x축 간격을 읽을 수 있습니다"
          onClick={toggleCursorMode}
        >
          커서 {cursorMode ? 'ON' : 'OFF'}
        </button>
        {cursorMode && cursorDeltaMs !== null && (
          <span className="graph-xwindow mono">
            <span style={{ color: CURSOR_A_COLOR }}>A</span>
            {' - '}
            <span style={{ color: CURSOR_B_COLOR }}>B</span>
            {`: Δ ${fmtDeltaDuration(cursorDeltaMs)}`}
          </span>
        )}
        <span className="spacer" />
        <button
          className="small-btn"
          disabled={scriptBusy || segments.length === 0}
          title="체크된 시간 구간의 log ID(0~399)를 현재 로드된 CAN-DB와 매칭해 CANReq/CANEv 테스트 스크립트(.json)를 생성/다운로드합니다"
          onClick={generateScript}
        >
          {scriptBusy ? '생성 중…' : '📝 테스트 스크립트 생성'}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {scriptError && <div className="error">{scriptError}</div>}
      {scriptResult && (
        <div className="syslog-script-result">
          <div className="syslog-script-summary">
            스크립트 생성 완료 — 매칭 {scriptResult.matched_count}개 / 경고{' '}
            {scriptResult.warnings.length}개 / 오류 {scriptResult.errors.length}개
            {scriptResult.steps.length === 0 && ' (다운로드할 스텝 없음)'}
          </div>
          {scriptResult.warnings.length > 0 && (
            <details className="syslog-script-details">
              <summary>⚠ 유사 신호명으로 대치된 항목 {scriptResult.warnings.length}개</summary>
              {scriptResult.warnings.map((w, i) => (
                <div key={i} className="syslog-script-row">
                  log ID {w.log_id}({w.log_name}) → {w.matched_message}.{w.matched_signal}
                </div>
              ))}
            </details>
          )}
          {scriptResult.errors.length > 0 && (
            <details className="syslog-script-details">
              <summary>✕ 매칭 실패로 제외된 log ID {scriptResult.errors.length}개</summary>
              {scriptResult.errors.map((e, i) => (
                <div key={i} className="syslog-script-row">
                  log ID {e.log_id}
                  {e.log_name ? `(${e.log_name})` : ''} — {e.reason}
                </div>
              ))}
            </details>
          )}
        </div>
      )}
      <div className="syslog-body">
        <div className="syslog-idlist">
          {segments.length > 0 && (
            <div className="syslog-segment-box">
              <div className="syslog-section-title">
                시간 구간 ({checkedSegments.size}/{segments.length})
                <span className="spacer" />
                <button className="icon-btn" title="전체 체크" onClick={() => setAllSegments(true)}>
                  전체
                </button>
                <button className="icon-btn" title="전체 해제" onClick={() => setAllSegments(false)}>
                  해제
                </button>
              </div>
              <div className="syslog-segment-list">
                {segments.map((seg, i) => (
                  <label key={i} className="syslog-segment-row">
                    <input type="checkbox" checked={checkedSegments.has(i)} onChange={() => toggleSegment(i)} />
                    <span className="syslog-segment-range">
                      구간{i + 1}: {fmtAbsTime(seg.abs_ms_start)} ~ {fmtAbsTime(seg.abs_ms_end)}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="syslog-section-title">
            log ID
            <span className="spacer" />
            <button
              className={`small-btn ${sortMode === 'name' ? 'primary' : ''}`}
              title="이름 알파벳순 정렬"
              onClick={() => setSortMode('name')}
            >
              이름순
            </button>
            <button
              className={`small-btn ${sortMode === 'id' ? 'primary' : ''}`}
              title="ID 번호순 정렬"
              onClick={() => setSortMode('id')}
            >
              ID순
            </button>
          </div>
          <div className="syslog-id-list">
            {ids.length === 0 && <div className="hint">log/DB 파일을 업로드하세요.</div>}
            {idGroups.map((g) => (
              <div key={g.label} className="syslog-id-group">
                <div className="syslog-id-group-header">
                  <span>
                    ID {g.label} ({g.items.length})
                  </span>
                  <span className="spacer" />
                  <button className="icon-btn" title="이 그룹 전체 선택" onClick={() => setGroupSelected(g.items, true)}>
                    전체
                  </button>
                  <button className="icon-btn" title="이 그룹 전체 해제" onClick={() => setGroupSelected(g.items, false)}>
                    해제
                  </button>
                </div>
                {g.items.map((info) => (
                  <label key={info.id} className="syslog-id-row">
                    <input type="checkbox" checked={selectedIds.includes(info.id)} onChange={() => toggleId(info.id)} />
                    <span className="syslog-id-name" title={`${info.id}: ${info.name}`}>
                      {info.name}
                    </span>
                    <span className="syslog-id-count">{info.count}</span>
                  </label>
                ))}
              </div>
            ))}
          </div>
        </div>
        <div className="syslog-graphs-wrap">
          <div className="graph-charts-col syslog-graphs" ref={graphsColRef}>
            {selectedIds.length === 0 && <div className="hint">왼쪽에서 log ID를 선택하세요.</div>}
            {selectedIds.map((id, i) => {
              const series = seriesMap[id];
              if (!series) return null;
              return (
                <SysLogChart
                  key={id}
                  id={id}
                  series={filterSeriesToCheckedSegments(series)}
                  color={PALETTE[i % PALETTE.length]}
                  segments={segments}
                  xViewRef={sharedXRef}
                  xVersion={sharedVersion}
                  notifyChange={notifyChange}
                  defaultXMin={effectiveXMin}
                  defaultXMax={effectiveXMax}
                  showXAxis={i === selectedIds.length - 1}
                  resetToken={resetToken}
                  cursor={cursor}
                  onRemove={() => toggleId(id)}
                  isDragOver={dragOverId === id}
                  onDragStart={handleChartDragStart(id)}
                  onDragOver={handleChartDragOver(id)}
                  onDrop={handleChartDrop(id)}
                  onDragEnd={handleChartDragEnd}
                />
              );
            })}
          </div>
          <VerticalScrollbar targetRef={graphsColRef} />
        </div>
      </div>
    </div>
  );
}

// 오른쪽에 항상 일정하게 두껍고 확실히 동작하는 커스텀 스크롤바. OS/브라우저마다
// 제각각인 네이티브 스크롤바(얇거나, macOS 오버레이처럼 잡기 어렵거나, 헤드리스
// 환경에서 자동화로 조작이 안 되는 등)에 기대지 않고 직접 그린다 -- scrollTop을
// 100% 우리 코드가 제어하므로 어디서나 동일하게 동작하고 자동화 테스트도 가능하다.
function VerticalScrollbar({ targetRef }: { targetRef: MutableRefObject<HTMLDivElement | null> }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [trackH, setTrackH] = useState(1);
  const [metrics, setMetrics] = useState({ scrollTop: 0, scrollHeight: 1, clientHeight: 1 });
  const draggingRef = useRef<{ startY: number; startScrollTop: number } | null>(null);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const measure = () => setTrackH(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;
    const readMetrics = () =>
      setMetrics({ scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    readMetrics();
    el.addEventListener('scroll', readMetrics);
    const ro = new ResizeObserver(readMetrics);
    ro.observe(el);
    // 그래프 카드가 추가/삭제되면 el 자신의 크기는 안 바뀌어도 scrollHeight가
    // 바뀌므로(콘텐츠 변화), 자식 트리 변화도 함께 감시한다.
    const mo = new MutationObserver(readMetrics);
    mo.observe(el, { childList: true, subtree: true });
    return () => {
      el.removeEventListener('scroll', readMetrics);
      ro.disconnect();
      mo.disconnect();
    };
  }, [targetRef]);

  const { scrollTop, scrollHeight, clientHeight } = metrics;
  const scrollableH = scrollHeight - clientHeight;
  const canScroll = scrollableH > 1;
  const thumbH = canScroll ? Math.max(24, (clientHeight / scrollHeight) * trackH) : trackH;
  const maxThumbTop = Math.max(0, trackH - thumbH);
  const thumbTop = canScroll && maxThumbTop > 0 ? (scrollTop / scrollableH) * maxThumbTop : 0;

  const onThumbPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation(); // 트랙의 "클릭한 위치로 점프" 핸들러가 같이 발동하지 않도록
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = { startY: e.clientY, startScrollTop: scrollTop };
  };
  const onThumbPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = draggingRef.current;
    const el = targetRef.current;
    if (!drag || !el || maxThumbTop <= 0) return;
    const dy = e.clientY - drag.startY;
    const deltaScroll = (dy / maxThumbTop) * scrollableH;
    el.scrollTop = Math.min(scrollableH, Math.max(0, drag.startScrollTop + deltaScroll));
  };
  const onThumbPointerUp = () => {
    draggingRef.current = null;
  };

  // 트랙(썸 바깥) 클릭: 클릭한 위치로 바로 스크롤 이동(썸 중앙이 클릭 지점에 오도록).
  const onTrackPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || !canScroll) return;
    const el = targetRef.current;
    if (!el) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickY = e.clientY - rect.top;
    const targetTop = Math.min(maxThumbTop, Math.max(0, clickY - thumbH / 2));
    el.scrollTop = maxThumbTop > 0 ? (targetTop / maxThumbTop) * scrollableH : 0;
  };

  return (
    <div className="syslog-scrollbar-track" ref={trackRef} onPointerDown={onTrackPointerDown}>
      {canScroll && (
        <div
          className="syslog-scrollbar-thumb"
          style={{ top: thumbTop, height: thumbH }}
          onPointerDown={onThumbPointerDown}
          onPointerMove={onThumbPointerMove}
          onPointerUp={onThumbPointerUp}
          onPointerLeave={onThumbPointerUp}
        />
      )}
    </div>
  );
}

// 정적(업로드 시점 고정) 데이터용 미니 차트. X(시간)뷰는 부모가 준 공유 ref를 쓰므로
// 어느 차트에서 줌/팬해도 다른 차트가 같이 움직인다. Y뷰는 차트별 로컬(값 스케일이
// ID마다 다르므로) -- auto-fit 최소값은 항상 0(값에 음수가 없다는 사양).
function SysLogChart({
  id,
  series,
  color,
  segments,
  xViewRef,
  xVersion,
  notifyChange,
  defaultXMin,
  defaultXMax,
  showXAxis,
  resetToken,
  cursor,
  onRemove,
  isDragOver,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  id: number;
  series: SysLogSeries;
  color: string;
  segments: SysLogTimelineSegment[];
  xViewRef: MutableRefObject<SharedXView>;
  xVersion: number;
  notifyChange: () => void;
  defaultXMin: number;
  defaultXMax: number;
  showXAxis: boolean;
  resetToken: number;
  cursor: DiffCursorState;
  onRemove: () => void;
  isDragOver: boolean;
  onDragStart: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onDragEnd: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const yViewRef = useRef<YView>({ yMin: null, yMax: null });
  const dragRef = useRef<{ x: number; y: number; xView: SharedXView; yView: YView } | null>(null);
  const cursorDragRef = useRef<'a' | 'b' | null>(null);
  const hoverRef = useRef<{ px: number; plotX: number } | null>(null);
  const [valueMode, setValueMode] = useState<'hex' | 'dec'>('hex');
  const lastGeomRef = useRef<Geom>({
    xMin: 0,
    xMax: 1,
    yMin: 0,
    yMax: 1,
    plotLeft: MARGIN.left,
    plotTop: MARGIN.top,
    plotW: 1,
    plotH: 1,
  });
  const [size, setSize] = useState({ w: 260, h: 200 });
  const [localTick, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 부모의 "전체 리셋" -- 이 차트의 Y뷰도 지운다(공유 X는 부모가 직접 지움).
  useEffect(() => {
    yViewRef.current = { yMin: null, yMax: null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken]);

  // 이 차트만 Y를 자동 맞춤으로 되돌림 (공유 X뷰는 건드리지 않음).
  const resetYOnly = () => {
    yViewRef.current = { yMin: null, yMax: null };
    redraw();
  };

  const zoomY = (factor: number) => {
    const g = lastGeomRef.current;
    const v = yViewRef.current;
    const yMin = v.yMin ?? g.yMin;
    const yMax = v.yMax ?? g.yMax;
    const center = (yMin + yMax) / 2;
    const halfHeight = ((yMax - yMin) / 2) * factor;
    v.yMin = center - halfHeight;
    v.yMax = center + halfHeight;
    redraw();
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, size.w);
    const h = Math.max(1, size.h);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const bottomMargin = showXAxis ? MARGIN.bottom : 4;
    const plotLeft = MARGIN.left;
    const plotTop = MARGIN.top;
    const plotW = Math.max(1, w - MARGIN.left - MARGIN.right);
    const plotH = Math.max(1, h - MARGIN.top - bottomMargin);

    const points = series.points;

    let xMin = xViewRef.current.xMin;
    let xMax = xViewRef.current.xMax;
    if (xMin === null || xMax === null) {
      xMin = defaultXMin;
      xMax = defaultXMax;
    }

    const visible = points.filter((p) => p.x_ms >= xMin! && p.x_ms <= xMax!);

    let yMin = yViewRef.current.yMin;
    let yMax = yViewRef.current.yMax;
    if (yMin === null || yMax === null) {
      if (visible.length > 0) {
        const hi = Math.max(...visible.map((p) => p.value));
        const pad = hi * 0.1 || 1;
        yMin = 0; // 값에 음수가 없으므로 auto-fit 최소값은 항상 0
        yMax = hi + pad;
      } else {
        yMin = 0;
        yMax = 1;
      }
    }

    const xToPx = (xMs: number) => plotLeft + ((xMs - xMin!) / (xMax! - xMin!)) * plotW;
    const yToPx = (v: number) => plotTop + plotH - ((v - yMin!) / (yMax! - yMin!)) * plotH;

    // Y축 눈금: sysLog 값은 대부분 상태/열거값(예: 1, 2)처럼 종류가 적은데,
    // niceTicks(균등 보간)로 뽑으면 로그에 실제로 없는 근사값(예: 0, 0.55, 1.1...
    // 반올림 시 "1, 1, 2, 2" 중복)이 찍혀서 "Y축 값이 로그값과 안 맞는다"는
    // 혼동을 일으켰다(사용자 실사용 확인). 화면에 보이는 값의 종류가 적으면
    // 그 실제 값들을 그대로 눈금으로 쓰고, 종류가 너무 많으면(연속값에 가까움)
    // 기존 보간 방식으로 되돌아간다.
    const distinctVisibleValues = Array.from(new Set(visible.map((p) => p.value))).sort((a, b) => a - b);
    const yTickValues =
      distinctVisibleValues.length > 0 && distinctVisibleValues.length <= 12
        ? distinctVisibleValues
        : niceTicks(yMin, yMax, 4);

    ctx.strokeStyle = '#363b47';
    ctx.fillStyle = '#8b909c';
    ctx.font = '9px monospace';
    ctx.lineWidth = 1;
    // 시간 축은 21군데(20 간격)로 촘촘히 눈금을 그린다 -- 라벨 텍스트는 맨 아래
    // 차트(showXAxis)에만 그려서 차트마다 중복 표기하지 않는다. 21개 라벨은
    // 폭이 좁으면 서로 겹칠 수 있는데(의도된 트레이드오프), 정확한 값은 마우스
    // 호버 툴팁(아래)으로 확인한다.
    const xTicks = niceTicks(xMin, xMax, 20);
    xTicks.forEach((t, i) => {
      const px = xToPx(t);
      ctx.beginPath();
      ctx.moveTo(px, plotTop);
      ctx.lineTo(px, plotTop + plotH);
      ctx.stroke();
      if (!showXAxis) return;
      const label = fmtAbsTime(plotXToAbsMs(segments, t));
      const textW = ctx.measureText(label).width;
      let lx = px - textW / 2;
      if (i === 0) lx = Math.max(plotLeft, lx);
      if (i === xTicks.length - 1) lx = Math.min(plotLeft + plotW - textW, lx);
      ctx.fillText(label, lx, h - 6);
    });
    for (const t of yTickValues) {
      const py = yToPx(t);
      if (py < plotTop - 0.5 || py > plotTop + plotH + 0.5) continue; // Y 확대/축소로 화면 밖이면 건너뜀
      ctx.beginPath();
      ctx.moveTo(plotLeft, py);
      ctx.lineTo(plotLeft + plotW, py);
      ctx.stroke();
      ctx.fillText(fmtValue(t, valueMode), 2, py + 3);
    }
    ctx.strokeStyle = '#4b5160';
    ctx.strokeRect(plotLeft, plotTop, plotW, plotH);

    // points inside [xMin, xMax] plus one just outside each edge, so the step
    // line draws smoothly up to the clip boundary (same as GraphWidget)
    let start = points.findIndex((p) => p.x_ms >= xMin!);
    let drawPoints: SysLogPoint[] = [];
    if (start !== -1) {
      if (start > 0) start -= 1;
      let end = points.length - 1;
      while (end >= 0 && points[end].x_ms > xMax!) end -= 1;
      if (end < points.length - 1) end += 1;
      if (start <= end) drawPoints = points.slice(start, end + 1);
    }

    if (drawPoints.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotW, plotH);
      ctx.clip();

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      // step-after: 값이 다음 샘플까지 유지되는 계단형
      let prevPy = 0;
      drawPoints.forEach((p, i) => {
        const px = xToPx(p.x_ms);
        const py = yToPx(p.value);
        if (i === 0) {
          ctx.moveTo(px, py);
        } else {
          ctx.lineTo(px, prevPy);
          ctx.lineTo(px, py);
        }
        prevPy = py;
      });
      ctx.stroke();
      for (const p of drawPoints) {
        const px = xToPx(p.x_ms);
        const py = yToPx(p.value);
        if (px < plotLeft - 5 || px > plotLeft + plotW + 5) continue;
        ctx.beginPath();
        ctx.arc(px, py, DOT_RADIUS, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawDiffCursors(ctx, cursor, xMin, xMax, plotTop, plotH, xToPx);

    // 마우스 호버 크로스헤어 + 툴팁 -- 21군데 눈금 라벨이 서로 겹쳐 읽기 어려울
    // 수 있어서(위), 커서를 올리면 정확한 시간과 그 시점의 값을 항상 읽을 수
    // 있게 한다.
    if (hoverRef.current) {
      const { px, plotX } = hoverRef.current;
      ctx.save();
      ctx.strokeStyle = '#ffffff88';
      ctx.setLineDash([2, 2]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, plotTop);
      ctx.lineTo(px, plotTop + plotH);
      ctx.stroke();
      ctx.restore();

      const heldPoint = findHeldPoint(points, plotX);
      const tooltipText = `${fmtAbsTime(plotXToAbsMs(segments, plotX))}  ${heldPoint ? fmtValue(heldPoint.value, valueMode) : '-'}`;
      ctx.font = '10px monospace';
      const textW = ctx.measureText(tooltipText).width;
      let tx = px + 6;
      if (tx + textW + 6 > w) tx = px - textW - 6;
      const ty = plotTop + 12;
      ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
      ctx.fillRect(tx - 3, ty - 10, textW + 6, 14);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(tooltipText, tx, ty);
    }

    lastGeomRef.current = { xMin, xMax, yMin, yMax, plotLeft, plotTop, plotW, plotH };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, xVersion, localTick, series, showXAxis, valueMode]);

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const g = lastGeomRef.current;
    const factor = e.deltaY > 0 ? WHEEL_ZOOM_STEP : 1 / WHEEL_ZOOM_STEP;
    const inX = px >= g.plotLeft && px <= g.plotLeft + g.plotW;
    const inY = py >= g.plotTop && py <= g.plotTop + g.plotH;
    const overXAxisStrip = px >= g.plotLeft && px <= g.plotLeft + g.plotW && py > g.plotTop + g.plotH;
    const overYAxisStrip = py >= g.plotTop && py <= g.plotTop + g.plotH && px < g.plotLeft;

    const zoomX = overXAxisStrip || (inX && inY);
    const zoomYAxis = overYAxisStrip;
    const xv = xViewRef.current;
    const yv = yViewRef.current;

    if (zoomX) {
      const cursorX = g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin);
      const xMin = xv.xMin ?? g.xMin;
      const xMax = xv.xMax ?? g.xMax;
      xv.xMin = cursorX - (cursorX - xMin) * factor;
      xv.xMax = cursorX + (xMax - cursorX) * factor;
      notifyChange(); // 공유 X뷰라 모든 차트를 다시 그려야 함
      return;
    }
    if (zoomYAxis) {
      const cursorY = g.yMax - ((py - g.plotTop) / g.plotH) * (g.yMax - g.yMin);
      const yMin = yv.yMin ?? g.yMin;
      const yMax = yv.yMax ?? g.yMax;
      yv.yMin = cursorY - (cursorY - yMin) * factor;
      yv.yMax = cursorY + (yMax - cursorY) * factor;
      redraw();
    }
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const g = lastGeomRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < g.plotLeft || px > g.plotLeft + g.plotW || py < g.plotTop || py > g.plotTop + g.plotH) return;
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
    if (cursor.mode) {
      const msToPx = (x: number) => g.plotLeft + ((x - g.xMin) / (g.xMax - g.xMin)) * g.plotW;
      const which = nearestCursor(cursor, px, msToPx);
      cursorDragRef.current = which;
      cursor.onMove(which, g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin));
      return;
    }
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      xView: {
        xMin: orFallback(xViewRef.current.xMin, g.xMin),
        xMax: orFallback(xViewRef.current.xMax, g.xMax),
      },
      yView: {
        yMin: orFallback(yViewRef.current.yMin, g.yMin),
        yMax: orFallback(yViewRef.current.yMax, g.yMax),
      },
    };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (cursorDragRef.current) {
      const g = lastGeomRef.current;
      const rect = canvasRef.current!.getBoundingClientRect();
      const px = e.clientX - rect.left;
      cursor.onMove(cursorDragRef.current, g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin));
      return;
    }
    const drag = dragRef.current;
    if (drag) {
      const g = lastGeomRef.current;
      const dxPx = e.clientX - drag.x;
      const dyPx = e.clientY - drag.y;
      const dataDx = (dxPx / g.plotW) * (drag.xView.xMax! - drag.xView.xMin!);
      const dataDy = (dyPx / g.plotH) * (drag.yView.yMax! - drag.yView.yMin!);
      xViewRef.current = { xMin: drag.xView.xMin! - dataDx, xMax: drag.xView.xMax! - dataDx };
      yViewRef.current = { yMin: drag.yView.yMin! + dataDy, yMax: drag.yView.yMax! + dataDy };
      notifyChange(); // X가 바뀌었으니 모든 차트를 다시 그려야 함 (이 차트의 Y도 같이 반영됨)
      return;
    }
    // 드래그 중이 아니면 호버 크로스헤어/툴팁 위치만 갱신 (draw effect에서 그림)
    const g = lastGeomRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < g.plotLeft || px > g.plotLeft + g.plotW || py < g.plotTop || py > g.plotTop + g.plotH) {
      if (hoverRef.current) {
        hoverRef.current = null;
        redraw();
      }
      return;
    }
    hoverRef.current = { px, plotX: g.xMin + ((px - g.plotLeft) / g.plotW) * (g.xMax - g.xMin) };
    redraw();
  };
  const onPointerUp = () => {
    dragRef.current = null;
    cursorDragRef.current = null;
  };
  const onPointerLeave = () => {
    onPointerUp();
    if (hoverRef.current) {
      hoverRef.current = null;
      redraw();
    }
  };

  return (
    <div className={`graph-chart${isDragOver ? ' syslog-chart-dragover' : ''}`} onDragOver={onDragOver} onDrop={onDrop}>
      <div
        className="graph-chart-header syslog-chart-header-draggable"
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        title="드래그해서 그래프 순서 바꾸기"
      >
        <span className="graph-swatch" style={{ background: color }} />
        <span className="graph-chart-title" title={`${id}: ${series.name}`}>
          {series.name}
        </span>
        <span className="spacer" />
        <button
          className="icon-btn"
          title="Y값 표시 형식(16진수/10진수) 전환"
          onClick={() => setValueMode((m) => (m === 'hex' ? 'dec' : 'hex'))}
        >
          {valueMode === 'hex' ? 'HEX' : 'DEC'}
        </button>
        <button className="icon-btn" title="Y축 확대" onClick={() => zoomY(1 / BUTTON_ZOOM_FACTOR)}>
          Y+
        </button>
        <button className="icon-btn" title="Y축 축소" onClick={() => zoomY(BUTTON_ZOOM_FACTOR)}>
          Y−
        </button>
        <button className="icon-btn" title="이 차트의 Y축만 자동 맞춤으로 리셋" onClick={resetYOnly}>
          ⟲
        </button>
        <button className="icon-btn" title="목록에서 선택 해제" onClick={onRemove}>
          ✕
        </button>
      </div>
      <div className="graph-canvas-wrap" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerLeave}
        />
      </div>
    </div>
  );
}
