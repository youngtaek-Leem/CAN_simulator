// CAN log 분석 위젯: BLF/ASC CAN 로그 + 필수설정 DBC로 신호 시계열을 보여준다.
// sysLog 분석 위젯과 동일한 공유 X 동기화/스텝 차트 패턴을 재사용한다.
// x축은 단순 선형: (timestamp - t0)*1000 ms.

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import { niceTicks, orFallback, type AudioChartXView, type Geom } from './AudioWaveformChart';
import {
  drawDiffCursors,
  nearestCursor,
  type DiffCursorState,
} from './DiffCursor';
import type {
  CanLogMessageInfo,
  CanLogPoint,
  CanLogSeries,
  CanLogSignalInfo,
  CanLogStatus,
  CanLogTimeline,
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

const MARGIN = { left: 62, right: 10, top: 8, bottom: 22 };
const WHEEL_ZOOM_STEP = 1.1;
const BUTTON_ZOOM_FACTOR = 1.3;
const DOT_RADIUS = 2.5;

type SharedXView = AudioChartXView;
interface YView { yMin: number | null; yMax: number | null; }

function getSelectedKeys(config: WidgetConfig): string[] {
  return (config.options.selectedKeys as string[] | undefined) ?? [];
}

function fmtTimeMs(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(3)}s`;
}

function findHeldPoint(points: CanLogPoint[], plotX: number): CanLogPoint | null {
  let held: CanLogPoint | null = null;
  for (const p of points) {
    if (p.x_ms <= plotX) held = p;
    else break;
  }
  return held;
}

export function CanLogAnalysisWidget({ config }: { config: WidgetConfig }) {
  const { updateWidget } = useApp();
  const selectedKeys = getSelectedKeys(config);
  const [status, setStatus] = useState<CanLogStatus | null>(null);
  const [signals, setSignals] = useState<CanLogSignalInfo[]>([]);
  const [messages, setMessages] = useState<CanLogMessageInfo[]>([]);
  const [timeline, setTimeline] = useState<CanLogTimeline | null>(null);
  const [seriesMap, setSeriesMap] = useState<Record<string, CanLogSeries>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState('');
  const viewMode = (config.options.viewMode as 'byMessage' | 'bySignal' | undefined) ?? 'byMessage';
  const setViewMode = (m: 'byMessage' | 'bySignal') => updateWidget({ ...config, options: { ...config.options, viewMode: m } });

  const setSelectedKeys = (next: string[]) =>
    updateWidget({ ...config, options: { ...config.options, selectedKeys: next } });

  const refresh = async () => {
    const [st, tl, sigs, msgs] = await Promise.all([
      api.canlogStatus().catch(() => null),
      api.canlogTimeline().catch(() => null),
      api.canlogSignals().catch(() => [] as CanLogSignalInfo[]),
      api.canlogMessages().catch(() => [] as CanLogMessageInfo[]),
    ]);
    if (st) setStatus(st as CanLogStatus);
    if (tl) setTimeline(tl as CanLogTimeline);
    setSignals(sigs as CanLogSignalInfo[]);
    setMessages(msgs as CanLogMessageInfo[]);
    return { st, tl };
  };

  const fetchSeries = async (keys: string[]) => {
    if (keys.length === 0) { setSeriesMap({}); return; }
    const res = await api.canlogSeries(keys);
    const parsed: Record<string, CanLogSeries> = {};
    for (const [k, v] of Object.entries(res)) parsed[k] = v as CanLogSeries;
    setSeriesMap(parsed);
  };

  useEffect(() => {
    refresh().then(() => fetchSeries(selectedKeys)).catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchSeries(selectedKeys).catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKeys.join(',')]);

  const uploadLog = async (file: File) => {
    setError(null); setBusy(true);
    try {
      await api.canlogUpload(file);
      await refresh();
      await fetchSeries(selectedKeys);
      resetEverything();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const toggleKey = (key: string) => {
    const next = selectedKeys.includes(key) ? selectedKeys.filter((x) => x !== key) : [...selectedKeys, key];
    setSelectedKeys(next);
  };

  // filtered by search (signal name only) — Signal별은 시그널명 순 정렬
  const searchLower = search.trim().toLowerCase();
  const filteredSignalsBase = searchLower ? signals.filter((s) => s.signal.toLowerCase().includes(searchLower)) : signals;
  const filteredSignals = [...filteredSignalsBase].sort((a, b) => a.signal.toLowerCase().localeCompare(b.signal.toLowerCase()));
  const filteredMessages = searchLower
    ? messages.map((m) => ({ ...m, signals: m.signals.filter((s) => s.signal.toLowerCase().includes(searchLower)) })).filter((m) => m.signals.length > 0)
    : messages;

  // shared X
  const sharedXRef = useRef<SharedXView>({ xMin: null, xMax: null });
  const [sharedVersion, setSharedVersion] = useState(0);
  const notifyChange = () => setSharedVersion((n) => n + 1);
  const [resetToken, setResetToken] = useState(0);
  const plotXMin = timeline?.plot_x_min ?? 0;
  const plotXMax = timeline && timeline.plot_x_max > timeline.plot_x_min ? timeline.plot_x_max : plotXMin + 1;
  const resetEverything = () => { sharedXRef.current = { xMin: null, xMax: null }; setResetToken((n) => n + 1); notifyChange(); };
  const zoomSharedX = (factor: number) => {
    const v = sharedXRef.current;
    const xMin = v.xMin ?? plotXMin; const xMax = v.xMax ?? plotXMax;
    const center = (xMin + xMax) / 2; const halfWidth = ((xMax - xMin) / 2) * factor;
    v.xMin = center - halfWidth; v.xMax = center + halfWidth; notifyChange();
  };

  const [cursorMode, setCursorMode] = useState(false);
  const [cursorA, setCursorA] = useState<number | null>(null);
  const [cursorB, setCursorB] = useState<number | null>(null);
  const onCursorMove = (which: 'a' | 'b', x: number) => { if (which === 'a') setCursorA(x); else setCursorB(x); notifyChange(); };
  const toggleCursorMode = () => {
    if (!cursorMode && cursorA === null && cursorB === null) {
      const v = sharedXRef.current; const xMax = v.xMax ?? plotXMax; const xMin = v.xMin ?? plotXMin;
      setCursorA(xMin + (xMax - xMin) / 3); setCursorB(xMin + ((xMax - xMin) * 2) / 3);
    }
    setCursorMode((m) => !m);
  };
  const cursor: DiffCursorState = { mode: cursorMode, a: cursorA, b: cursorB, onMove: onCursorMove };
  const cursorDeltaMs = cursorA !== null && cursorB !== null ? Math.abs(cursorB - cursorA) : null;

  const hoverXRef = useRef<number | null>(null);
  const setHoverX = (x: number | null) => { hoverXRef.current = x; notifyChange(); };
  const hoveredGraphIdxRef = useRef<number | null>(null);

  const graphsColRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = graphsColRef.current; if (!el) return;
    const blockPageZoomOnCtrlWheel = (e: WheelEvent) => { if (e.ctrlKey) e.preventDefault(); };
    el.addEventListener('wheel', blockPageZoomOnCtrlWheel, { passive: false });
    return () => el.removeEventListener('wheel', blockPageZoomOnCtrlWheel);
  }, []);

  const dragIdRef = useRef<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const handleChartDragStart = (key: string) => (e: React.DragEvent) => { dragIdRef.current = key; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', key); };
  const handleChartDragOver = (key: string) => (e: React.DragEvent) => { if (dragIdRef.current === null || dragIdRef.current === key) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverId(key); };
  const handleChartDrop = (key: string) => (e: React.DragEvent) => {
    e.preventDefault(); const dragged = dragIdRef.current; dragIdRef.current = null; setDragOverId(null);
    if (dragged === null || dragged === key) return;
    const from = selectedKeys.indexOf(dragged); const to = selectedKeys.indexOf(key);
    if (from === -1 || to === -1) return;
    const next = [...selectedKeys]; next.splice(from, 1); next.splice(to, 0, dragged); setSelectedKeys(next);
  };
  const handleChartDragEnd = () => { dragIdRef.current = null; setDragOverId(null); };

  return (
    <div className="syslog-widget">
      <div className="graph-toolbar">
        <label className="small-btn">📄 CAN log 업로드
          <input type="file" style={{ display: 'none' }} accept=".blf,.asc" disabled={busy}
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value=''; if(f) uploadLog(f); }} />
        </label>
        <span className="hint">{status?.log_filename ? `${status.log_filename} (${status.record_count}건, ${status.duration_s}s)` : 'CAN log 없음'} {status?.log_filename && !signals.length ? ' — DBC 로드 필요' : ''}{busy && <span className="spinner" title="불러오는 중" />}</span>
        <span className="spacer" />
        <button className="icon-btn" title="X축 확대" onClick={() => zoomSharedX(1 / BUTTON_ZOOM_FACTOR)}>X+</button>
        <button className="icon-btn" title="X축 축소" onClick={() => zoomSharedX(BUTTON_ZOOM_FACTOR)}>X−</button>
        <button className="icon-btn" title="모든 그래프 X/Y 리셋" onClick={resetEverything}>⟲</button>
        <button className={`small-btn ${cursorMode ? 'primary' : ''}`} onClick={toggleCursorMode}>커서 {cursorMode ? 'ON' : 'OFF'}</button>
        {cursorMode && cursorDeltaMs !== null && <span className="graph-xwindow mono">Δ {fmtTimeMs(cursorDeltaMs)}</span>}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="syslog-body">
        <div className="syslog-idlist">
          <div className="syslog-section-title">CAN Signal 검색</div>
          <input className="layout-input" style={{ width: '100%', marginBottom: 6 }} placeholder="signal 이름 직접 입력 (부분 일치)" value={search} onChange={(e) => setSearch(e.target.value)} />
          {searchLower && (
            <div className="syslog-id-list" style={{ maxHeight: 140, marginBottom: 8 }}>
              {filteredSignals.length === 0 && <div className="hint">일치하는 signal 없음</div>}
              {filteredSignals.map((s) => (
                <label key={s.key} className="syslog-id-row">
                  <input type="checkbox" checked={selectedKeys.includes(s.key)} onChange={() => toggleKey(s.key)} />
                  <span className="syslog-id-name" title={`${s.message}.${s.signal}`}>{s.signal}</span>
                  <span className="hint" style={{ fontSize: 10 }}>{s.message}</span>
                  <span className="syslog-id-count">{s.count}</span>
                </label>
              ))}
            </div>
          )}
          <div className="syslog-section-title">
            <label><input type="radio" checked={viewMode==='byMessage'} onChange={()=>setViewMode('byMessage')} /> 메시지별</label>
            <label style={{ marginLeft: 8 }}><input type="radio" checked={viewMode==='bySignal'} onChange={()=>setViewMode('bySignal')} /> Signal별</label>
          </div>
          <div className="syslog-id-list">
            {viewMode === 'byMessage' ? (
              filteredMessages.length === 0 ? <div className="hint">DBC 로드 또는 CAN log 업로드 필요</div> :
              filteredMessages.map((m) => {
                const allChecked = m.signals.length > 0 && m.signals.every((s) => selectedKeys.includes(s.key));
                const someChecked = m.signals.some((s) => selectedKeys.includes(s.key));
                return (
                <div key={m.message} className="syslog-id-group">
                  <div className="syslog-id-group-header">
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                      <input type="checkbox" checked={allChecked}
                        ref={(el) => { if (el) el.indeterminate = !allChecked && someChecked; }}
                        onChange={(e) => {
                          const keys = m.signals.map((s) => s.key);
                          if (e.target.checked) {
                            const next = Array.from(new Set([...selectedKeys, ...keys]));
                            setSelectedKeys(next);
                          } else {
                            const set = new Set(keys);
                            setSelectedKeys(selectedKeys.filter((k) => !set.has(k)));
                          }
                        }} />
                      <span>{m.message} (0x{m.frame_id.toString(16).toUpperCase()})</span>
                    </label>
                    <span className="spacer" /><span className="hint">{m.count} pts</span></div>
                  {m.signals.map((s) => (
                    <label key={s.key} className="syslog-id-row">
                      <input type="checkbox" checked={selectedKeys.includes(s.key)} onChange={() => toggleKey(s.key)} />
                      <span className="syslog-id-name" title={s.key}>{s.signal}</span>
                      <span className="syslog-id-count">{s.count}</span>
                    </label>
                  ))}
                </div>
                );
              })
            ) : (
              filteredSignals.length === 0 ? <div className="hint">신호 없음</div> :
              filteredSignals.map((s) => (
                <label key={s.key} className="syslog-id-row">
                  <input type="checkbox" checked={selectedKeys.includes(s.key)} onChange={() => toggleKey(s.key)} />
                  <span className="syslog-id-name" title={s.key}>{s.signal}</span>
                  <span className="hint" style={{ fontSize: 10 }}>{s.message}</span>
                  <span className="syslog-id-count">{s.count}</span>
                </label>
              ))
            )}
          </div>
        </div>
        <div className="syslog-graphs-wrap">
          <div className="graph-charts-col syslog-graphs" ref={graphsColRef}>
            {selectedKeys.length === 0 && <div className="hint">왼쪽에서 signal을 선택하세요.</div>}
            {selectedKeys.map((key, i) => {
              const series = seriesMap[key]; if (!series) return null;
              return (
                <CanLogChart key={key} series={series} color={PALETTE[i % PALETTE.length]}
                  xViewRef={sharedXRef} xVersion={sharedVersion} notifyChange={notifyChange}
                  defaultXMin={plotXMin} defaultXMax={plotXMax}
                  showXAxis={i === selectedKeys.length - 1} resetToken={resetToken}
                  cursor={cursor} hoverXRef={hoverXRef} setHoverX={setHoverX}
                  graphIndex={i} hoveredGraphIdxRef={hoveredGraphIdxRef}
                  onRemove={() => toggleKey(key)} isDragOver={dragOverId===key}
                  onDragStart={handleChartDragStart(key)} onDragOver={handleChartDragOver(key)} onDrop={handleChartDrop(key)} onDragEnd={handleChartDragEnd}
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

function VerticalScrollbar({ targetRef }: { targetRef: MutableRefObject<HTMLDivElement | null> }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [trackH, setTrackH] = useState(1);
  const [metrics, setMetrics] = useState({ scrollTop: 0, scrollHeight: 1, clientHeight: 1 });
  const draggingRef = useRef<{ startY: number; startScrollTop: number } | null>(null);
  useEffect(() => { const el = trackRef.current; if (!el) return; const m=()=>setTrackH(el.clientHeight); m(); const ro=new ResizeObserver(m); ro.observe(el); return()=>ro.disconnect(); },[]);
  useEffect(() => {
    const el = targetRef.current; if (!el) return;
    const read=()=>setMetrics({ scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    read(); el.addEventListener('scroll', read); const ro=new ResizeObserver(read); ro.observe(el); const mo=new MutationObserver(read); mo.observe(el,{childList:true,subtree:true});
    return ()=>{ el.removeEventListener('scroll', read); ro.disconnect(); mo.disconnect(); };
  },[targetRef]);
  const { scrollTop, scrollHeight, clientHeight } = metrics; const scrollableH = scrollHeight - clientHeight; const canScroll = scrollableH>1; const thumbH = canScroll ? Math.max(24,(clientHeight/scrollHeight)*trackH) : trackH; const maxThumbTop=Math.max(0,trackH-thumbH); const thumbTop=canScroll&&maxThumbTop>0 ? (scrollTop/scrollableH)*maxThumbTop :0;
  const onThumbDown=(e: React.PointerEvent<HTMLDivElement>)=>{ e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); draggingRef.current={startY:e.clientY,startScrollTop:scrollTop}; };
  const onThumbMove=(e: React.PointerEvent<HTMLDivElement>)=>{ const d=draggingRef.current; const el=targetRef.current; if(!d||!el||maxThumbTop<=0) return; const dy=e.clientY-d.startY; const delta=(dy/maxThumbTop)*scrollableH; el.scrollTop=Math.min(scrollableH,Math.max(0,d.startScrollTop+delta)); };
  const onThumbUp=()=>{ draggingRef.current=null; };
  const onTrackDown=(e: React.PointerEvent<HTMLDivElement>)=>{ if(e.target!==e.currentTarget||!canScroll) return; const el=targetRef.current; if(!el) return; const rect=e.currentTarget.getBoundingClientRect(); const clickY=e.clientY-rect.top; const targetTop=Math.min(maxThumbTop,Math.max(0,clickY-thumbH/2)); el.scrollTop=maxThumbTop>0?(targetTop/maxThumbTop)*scrollableH:0; };
  return (<div className="syslog-scrollbar-track" ref={trackRef} onPointerDown={onTrackDown}>{canScroll && <div className="syslog-scrollbar-thumb" style={{top:thumbTop,height:thumbH}} onPointerDown={onThumbDown} onPointerMove={onThumbMove} onPointerUp={onThumbUp} onPointerLeave={onThumbUp} />}</div>);
}

function CanLogChart({ series, color, xViewRef, xVersion, notifyChange, defaultXMin, defaultXMax, showXAxis, resetToken, cursor, hoverXRef, setHoverX, graphIndex, hoveredGraphIdxRef, onRemove, isDragOver, onDragStart, onDragOver, onDrop, onDragEnd }: {
  series: CanLogSeries; color: string; xViewRef: MutableRefObject<SharedXView>; xVersion: number; notifyChange: ()=>void; defaultXMin:number; defaultXMax:number; showXAxis:boolean; resetToken:number; cursor: DiffCursorState; hoverXRef: MutableRefObject<number|null>; setHoverX:(x:number|null)=>void; graphIndex:number; hoveredGraphIdxRef: MutableRefObject<number|null>; onRemove:()=>void; isDragOver:boolean; onDragStart:(e:React.DragEvent)=>void; onDragOver:(e:React.DragEvent)=>void; onDrop:(e:React.DragEvent)=>void; onDragEnd:()=>void;
}) {
  const canvasRef=useRef<HTMLCanvasElement>(null); const wrapRef=useRef<HTMLDivElement>(null);
  const yViewRef=useRef<YView>({yMin:null,yMax:null}); const dragRef=useRef<{x:number;y:number;xView:SharedXView;yView:YView}|null>(null); const cursorDragRef=useRef<'a'|'b'|null>(null);
  const [valueMode, setValueMode]=useState<'dec'|'hex'|'desc'>('dec');
  const lastGeomRef=useRef<Geom>({xMin:0,xMax:1,yMin:0,yMax:1,plotLeft:MARGIN.left,plotTop:MARGIN.top,plotW:1,plotH:1});
  const [size,setSize]=useState({w:260,h:200}); const [localTick,bump]=useState(0); const redraw=()=>bump(n=>n+1);
  useEffect(()=>{ const el=wrapRef.current; if(!el) return; const m=()=>setSize({w:el.clientWidth,h:el.clientHeight}); m(); const ro=new ResizeObserver(m); ro.observe(el); return()=>ro.disconnect(); },[]);
  useEffect(()=>{ yViewRef.current={yMin:null,yMax:null}; },[resetToken]);
  const resetYOnly=()=>{ yViewRef.current={yMin:null,yMax:null}; redraw(); };
  const zoomY=(factor:number)=>{ const g=lastGeomRef.current; const v=yViewRef.current; const yMin=v.yMin??g.yMin; const yMax=v.yMax??g.yMax; const center=(yMin+yMax)/2; const half=(yMax-yMin)/2*factor; v.yMin=center-half; v.yMax=center+half; redraw(); };
  useEffect(()=>{
    const canvas=canvasRef.current; if(!canvas) return; const dpr=window.devicePixelRatio||1; const w=Math.max(1,size.w); const h=Math.max(1,size.h);
    canvas.width=w*dpr; canvas.height=h*dpr; canvas.style.width=`${w}px`; canvas.style.height=`${h}px`; const ctx=canvas.getContext('2d'); if(!ctx) return; ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h);
    const bottomMargin=showXAxis?MARGIN.bottom:4; const plotLeft=MARGIN.left; const plotTop=MARGIN.top; const plotW=Math.max(1,w-MARGIN.left-MARGIN.right); const plotH=Math.max(1,h-MARGIN.top-bottomMargin);
    const points=series.points;
    let xMin=xViewRef.current.xMin, xMax=xViewRef.current.xMax; if(xMin===null||xMax===null){ xMin=defaultXMin; xMax=defaultXMax; }
    const visible=points.filter(p=>p.x_ms>=xMin! && p.x_ms<=xMax!);
    let yMin=yViewRef.current.yMin, yMax=yViewRef.current.yMax;
    if(yMin===null||yMax===null){
      if(visible.length>0){ const vals=visible.map(p=>p.value); const lo=Math.min(...vals), hi=Math.max(...vals); if(lo===hi){ yMin=lo-1; yMax=hi+1; } else { const pad=(hi-lo)*0.1||1; yMin=lo-pad; yMax=hi+pad; } }
      else { yMin=0; yMax=1; }
    }
    const xToPx=(x:number)=>plotLeft+((x-xMin!)/(xMax!-xMin!))*plotW;
    const yToPx=(v:number)=>plotTop+plotH-((v-yMin!)/(yMax!-yMin!))*plotH;
    const distinct=[...new Set(visible.map(p=>p.value))].sort((a,b)=>a-b);
    const yTickValues= distinct.length>0 && distinct.length<=12 ? distinct : niceTicks(yMin,yMax,4);
    ctx.strokeStyle='#363b47'; ctx.fillStyle='#8b909c'; ctx.font='9px monospace'; ctx.lineWidth=1;
    const xTicks=niceTicks(xMin,xMax,20);
    xTicks.forEach((t,i)=>{ const px=xToPx(t); ctx.beginPath(); ctx.moveTo(px,plotTop); ctx.lineTo(px,plotTop+plotH); ctx.stroke(); if(!showXAxis) return; const label=fmtTimeMs(t); const tw=ctx.measureText(label).width; let lx=px-tw/2; if(i===0) lx=Math.max(plotLeft,lx); if(i===xTicks.length-1) lx=Math.min(plotLeft+plotW-tw,lx); ctx.fillText(label,lx,h-6); });
    for(const t of yTickValues){ const py=yToPx(t); if(py<plotTop-0.5||py>plotTop+plotH+0.5) continue; ctx.beginPath(); ctx.moveTo(plotLeft,py); ctx.lineTo(plotLeft+plotW,py); ctx.stroke(); ctx.fillText(fmtValueRaw(t,valueMode,series.choices),2,py+3); }
    ctx.strokeStyle='#4b5160'; ctx.strokeRect(plotLeft,plotTop,plotW,plotH);
    let start=points.findIndex(p=>p.x_ms>=xMin!); let drawPoints:CanLogPoint[]=[]; if(start!==-1){ if(start>0) start-=1; let end=points.length-1; while(end>=0 && points[end].x_ms>xMax!) end-=1; if(end<points.length-1) end+=1; if(start<=end) drawPoints=points.slice(start,end+1); }
    if (drawPoints.length > plotW * 2) { const step=Math.ceil(drawPoints.length/(plotW*2)); drawPoints=drawPoints.filter((_,i)=>i%step===0); }
    if(drawPoints.length>0){
      ctx.save(); ctx.beginPath(); ctx.rect(plotLeft,plotTop,plotW,plotH); ctx.clip();
      ctx.strokeStyle=color; ctx.fillStyle=color; ctx.lineWidth=1.5; ctx.beginPath();
      let prevPy=0; drawPoints.forEach((p,i)=>{ const px=xToPx(p.x_ms), py=yToPx(p.value); if(i===0) ctx.moveTo(px,py); else { ctx.lineTo(px,prevPy); ctx.lineTo(px,py); } prevPy=py; }); ctx.stroke();
      for(const p of drawPoints){ const px=xToPx(p.x_ms), py=yToPx(p.value); if(px<plotLeft-5||px>plotLeft+plotW+5) continue; ctx.beginPath(); ctx.arc(px,py,DOT_RADIUS,0,Math.PI*2); ctx.fill(); }
      ctx.restore();
    }
    drawDiffCursors(ctx,cursor,xMin,xMax,plotTop,plotH,xToPx);
    const hoverPlotX=hoverXRef.current;
    if(hoverPlotX!==null){ const px=xToPx(hoverPlotX); ctx.save(); ctx.strokeStyle='#ffffff88'; ctx.setLineDash([2,2]); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(px,plotTop); ctx.lineTo(px,plotTop+plotH); ctx.stroke(); ctx.restore(); }
    if(hoverPlotX!==null && hoveredGraphIdxRef.current===graphIndex){
      const px=xToPx(hoverPlotX); const held=findHeldPoint(points,hoverPlotX);
      const tooltipText=`${fmtTimeMs(hoverPlotX)}  ${held? fmtValueRaw(held.value,valueMode,series.choices):'-'}`;
      ctx.font='10px monospace'; const tw=ctx.measureText(tooltipText).width; let tx=px+6; if(tx+tw+6>w) tx=px-tw-6; const ty=plotTop+12; ctx.fillStyle='rgba(0,0,0,0.8)'; ctx.fillRect(tx-3,ty-10,tw+6,14); ctx.fillStyle='#ffffff'; ctx.fillText(tooltipText,tx,ty);
    }
    lastGeomRef.current={xMin,xMax,yMin,yMax,plotLeft,plotTop,plotW,plotH};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[size,xVersion,localTick,series,showXAxis,valueMode]);

  const onWheel=(e:React.WheelEvent<HTMLCanvasElement>)=>{
    if(!e.ctrlKey) return; const rect=canvasRef.current!.getBoundingClientRect(); const px=e.clientX-rect.left, py=e.clientY-rect.top; const g=lastGeomRef.current; const factor=e.deltaY>0?WHEEL_ZOOM_STEP:1/WHEEL_ZOOM_STEP;
    const inX=px>=g.plotLeft&&px<=g.plotLeft+g.plotW; const inY=py>=g.plotTop&&py<=g.plotTop+g.plotH; const overX=px>=g.plotLeft&&px<=g.plotLeft+g.plotW&&py>g.plotTop+g.plotH; const overY=py>=g.plotTop&&py<=g.plotTop+g.plotH&&px<g.plotLeft;
    const zoomX=overX||(inX&&inY); const zoomY=overY; const xv=xViewRef.current, yv=yViewRef.current;
    if(zoomX){ const cx=g.xMin+((px-g.plotLeft)/g.plotW)*(g.xMax-g.xMin); const xMin=xv.xMin??g.xMin, xMax=xv.xMax??g.xMax; xv.xMin=cx-(cx-xMin)*factor; xv.xMax=cx+(xMax-cx)*factor; notifyChange(); return; }
    if(zoomY){ const cy=g.yMax-((py-g.plotTop)/g.plotH)*(g.yMax-g.yMin); const yMin=yv.yMin??g.yMin, yMax=yv.yMax??g.yMax; yv.yMin=cy-(cy-yMin)*factor; yv.yMax=cy+(yMax-cy)*factor; redraw(); }
  };
  const onPointerDown=(e:React.PointerEvent<HTMLCanvasElement>)=>{
    const g=lastGeomRef.current; const rect=canvasRef.current!.getBoundingClientRect(); const px=e.clientX-rect.left, py=e.clientY-rect.top;
    if(px<g.plotLeft||px>g.plotLeft+g.plotW||py<g.plotTop||py>g.plotTop+g.plotH) return; (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
    if(cursor.mode){ const msToPx=(ms:number)=>g.plotLeft+((ms-g.xMin)/(g.xMax-g.xMin))*g.plotW; const which=nearestCursor(cursor,px,msToPx); cursorDragRef.current=which; cursor.onMove(which,g.xMin+((px-g.plotLeft)/g.plotW)*(g.xMax-g.xMin)); return; }
    dragRef.current={x:e.clientX,y:e.clientY,xView:{xMin:orFallback(xViewRef.current.xMin,g.xMin),xMax:orFallback(xViewRef.current.xMax,g.xMax)},yView:{yMin:orFallback(yViewRef.current.yMin,g.yMin),yMax:orFallback(yViewRef.current.yMax,g.yMax)}};
  };
  const onPointerMove=(e:React.PointerEvent<HTMLCanvasElement>)=>{
    if(cursorDragRef.current){ const g=lastGeomRef.current; const rect=canvasRef.current!.getBoundingClientRect(); const px=e.clientX-rect.left; cursor.onMove(cursorDragRef.current,g.xMin+((px-g.plotLeft)/g.plotW)*(g.xMax-g.xMin)); return; }
    const drag=dragRef.current; if(drag){ const g=lastGeomRef.current; const dxPx=e.clientX-drag.x, dyPx=e.clientY-drag.y; const dataDx=(dxPx/g.plotW)*(drag.xView.xMax!-drag.xView.xMin!); const dataDy=(dyPx/g.plotH)*(drag.yView.yMax!-drag.yView.yMin!); xViewRef.current={xMin:drag.xView.xMin!-dataDx,xMax:drag.xView.xMax!-dataDx}; yViewRef.current={yMin:drag.yView.yMin!+dataDy,yMax:drag.yView.yMax!+dataDy}; notifyChange(); return; }
    const g=lastGeomRef.current; const rect=canvasRef.current!.getBoundingClientRect(); const px=e.clientX-rect.left, py=e.clientY-rect.top;
    if(px<g.plotLeft||px>g.plotLeft+g.plotW||py<g.plotTop||py>g.plotTop+g.plotH){ if(hoverXRef.current!==null) setHoverX(null); hoveredGraphIdxRef.current=null; return; }
    setHoverX(g.xMin+((px-g.plotLeft)/g.plotW)*(g.xMax-g.xMin)); const idx=parseInt((e.target as HTMLCanvasElement).dataset.index??'',10); if(!isNaN(idx)) hoveredGraphIdxRef.current=idx;
  };
  const onPointerUp=()=>{ dragRef.current=null; cursorDragRef.current=null; };
  const onPointerLeave=()=>{ onPointerUp(); if(hoverXRef.current!==null) setHoverX(null); hoveredGraphIdxRef.current=null; };

  return (
    <div className={`graph-chart${isDragOver?' syslog-chart-dragover':''}`} onDragOver={onDragOver} onDrop={onDrop}>
      <div className="graph-chart-header syslog-chart-header-draggable" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} title="드래그해서 그래프 순서 바꾸기">
        <span className="graph-swatch" style={{background:color}} />
        <span className="graph-chart-title" title={series.key}>{series.key}</span>
        <span className="spacer" />
        <button className="icon-btn" title="값 표시 형식 전환 (DEC/HEX/설명)" onClick={()=>setValueMode(m=> m==='dec'? 'hex' : m==='hex' ? 'desc' : 'dec')}>{valueMode==='dec'?'DEC':valueMode==='hex'?'HEX':'DESC'}</button>
        <button className="icon-btn" title="Y축 확대" onClick={()=>zoomY(1/BUTTON_ZOOM_FACTOR)}>Y+</button>
        <button className="icon-btn" title="Y축 축소" onClick={()=>zoomY(BUTTON_ZOOM_FACTOR)}>Y−</button>
        <button className="icon-btn" title="Y축 리셋" onClick={resetYOnly}>⟲</button>
        <button className="icon-btn" title="선택 해제" onClick={onRemove}>✕</button>
      </div>
      <div className="graph-canvas-wrap" ref={wrapRef}>
        <canvas ref={canvasRef} data-index={graphIndex} onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerLeave} />
      </div>
    </div>
  );
}

function fmtValueRaw(v:number, mode:'dec'|'hex'|'desc', choices: Record<string,string>|null){
  if(mode==='hex'){ const abs=Math.abs(v).toString(16).toUpperCase(); return v<0?`-0x${abs}`:`0x${abs}`; }
  if(mode==='desc' && choices){ const lbl=choices[String(v)]; if(lbl!==undefined) return `${lbl} (${v})`; }
  return String(v);
}
