import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { UdsDownloadStatus, UdsEvent, UdsStepInfo } from '../types';
import { SERVICE_DISPLAY_NAMES } from '../types';

interface Props {
  config: { id: string; title: string; options: Record<string, unknown> };
}

const NUM_SLOTS = 3;

const STATE_LABELS: Record<string, string> = {
  IDLE: '대기',
  LOADING: '로딩 중',
  READY: '준비',
  RUNNING_PREP: '준비 단계 실행 중',
  RUNNING_DOWNLOAD: '다운로드 중',
  RUNNING_ACTIVATE: '활성화 중',
  COMPLETED: '완료',
  ERROR: '오류',
};

const STATE_COLORS: Record<string, string> = {
  IDLE: '#6b7280',
  LOADING: '#f59e0b',
  READY: '#3b82f6',
  RUNNING_PREP: '#f59e0b',
  RUNNING_DOWNLOAD: '#f59e0b',
  RUNNING_ACTIVATE: '#f59e0b',
  COMPLETED: '#10b981',
  ERROR: '#ef4444',
};

interface SlotState {
  xmlFile: File | null;
  binFile: File | null;
  status: UdsDownloadStatus | null;
  steps: UdsStepInfo[] | null;
  paramOverrides: Record<string, Record<string, string>>;
  uploading: boolean;
  error: string | null;
}

export default function UdsSwdlWidget({ config: _config }: Props) {
  const makeEmptySlot = (): SlotState => ({
    xmlFile: null,
    binFile: null,
    status: null,
    steps: null,
    paramOverrides: {},
    uploading: false,
    error: null,
  });

  const [slots, setSlots] = useState<SlotState[]>(() =>
    Array.from({ length: NUM_SLOTS }, () => makeEmptySlot()),
  );
  // Per-slot selected step indices — using step index (not service name) to distinguish duplicate steps
  const [selectedSteps, setSelectedSteps] = useState<Record<number, Set<number>>>({
    0: new Set(),
    1: new Set(),
    2: new Set(),
  });
  const [slotExecState, setSlotExecState] = useState<Record<number, string>>({ 0: 'idle', 1: 'idle', 2: 'idle' });
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [stminTxEnabled, setStminTxEnabled] = useState(false);
  const [globalStminTx, setGlobalStminTx] = useState('0A');
  const [autoLoadMsg, setAutoLoadMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  // Poll status while any slot is running
  useEffect(() => {
    const anyRunning = Object.values(slotExecState).some(s => s === 'running');
    if (anyRunning) {
      pollRef.current = setInterval(async () => {
        try {
          const all = await api.udsStatus();
          setSlots(prev => prev.map((s, i) => ({ ...s, status: all[i] || s.status })));
        } catch { /* ignore */ }
      }, 500);
    } else {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [slotExecState]);

  // Scroll events
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [slots]);

  const toggleStep = (slotIdx: number, stepIdx: number) => {
    setSelectedSteps(prev => {
      const current = new Set(prev[slotIdx] ?? []);
      if (current.has(stepIdx)) {
        current.delete(stepIdx);
      } else {
        current.add(stepIdx);
      }
      return { ...prev, [slotIdx]: current };
    });
  };

  const handleSelectAll = () => {
    setSelectedSteps(_prev => {
      const next: Record<number, Set<number>> = {};
      slots.forEach((slot, i) => {
        next[i] = slot.steps ? new Set(slot.steps.map((_, si) => si)) : new Set();
      });
      return next;
    });
  };

  const handleDeselectAll = () => {
    setSelectedSteps({ 0: new Set(), 1: new Set(), 2: new Set() });
  };

  const uploadXml = async (slotIdx: number) => {
    const file = slots[slotIdx].xmlFile;
    if (!file) return;
    setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, uploading: true, error: null } : s));
    try {
      await api.udsUploadXml(file, slotIdx);
      const steps = await api.udsSteps(slotIdx);
      const allStatus = await api.udsStatus();
      setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, status: allStatus[i], steps, uploading: false } : s));
    } catch (e: unknown) {
      setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, error: String(e instanceof Error ? e.message : e), uploading: false } : s));
    }
  };

  const uploadBin = async (slotIdx: number) => {
    const file = slots[slotIdx].binFile;
    if (!file) return;
    setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, uploading: true, error: null } : s));
    try {
      await api.udsUploadBinary(file, slotIdx);
      const all = await api.udsStatus();
      setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, status: all[i], uploading: false } : s));
    } catch (e: unknown) {
      setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, error: String(e instanceof Error ? e.message : e), uploading: false } : s));
    }
  };

  const startAll = async () => {
    setGlobalError(null);
    const readySlots: number[] = [];
    slots.forEach((s, i) => {
      if (s.status?.procedure_loaded && s.status?.binary_loaded) readySlots.push(i);
    });
    if (readySlots.length === 0) { setGlobalError('준비된 슬롯이 없습니다.'); return; }

    for (const idx of readySlots) {
      setSlotExecState(prev => ({ ...prev, [idx]: 'running' }));
      try {
        const slotStepIndices = Array.from(selectedSteps[idx] ?? []);
        const slotStepServices = slotStepIndices.map(si => slots[idx].steps![si].service);
        await api.udsStart([idx], slotStepServices, slots[idx].paramOverrides, stminTxEnabled ? parseInt(globalStminTx, 16) : undefined);
        setSlotExecState(prev => ({ ...prev, [idx]: 'done' }));
      } catch (e: unknown) {
        setSlotExecState(prev => ({ ...prev, [idx]: 'error' }));
        setGlobalError(`파일 ${idx + 1} 실패: ${String(e instanceof Error ? e.message : e)}`);
        break;
      }
    }
    const final = await api.udsStatus();
    setSlots(prev => prev.map((s, i) => ({ ...s, status: final[i] || s.status })));
  };

  const stopSlot = async (idx: number) => {
    try {
      await api.udsStop(idx);
      setSlotExecState(prev => ({ ...prev, [idx]: 'idle' }));
      const all = await api.udsStatus();
      setSlots(prev => prev.map((s, i) => i === idx ? { ...s, status: all[i] } : s));
    } catch { /* ignore */ }
  };

  const refresh = async () => {
    try {
      const all = await api.udsStatus();
      setSlots(prev => prev.map((s, i) => ({ ...s, status: all[i] || s.status })));
    } catch { /* ignore */ }
  };

  const setParam = (slotIdx: number, svc: string, key: string, val: string) => {
    setSlots(prev => prev.map((s, i) => {
      if (i !== slotIdx) return s;
      const overrides = { ...s.paramOverrides };
      if (!overrides[svc]) overrides[svc] = {};
      overrides[svc][key] = val;
      return { ...s, paramOverrides: overrides };
    }));
  };

  const anyRunning = Object.values(slotExecState).some(s => s === 'running');
  const allReady = slots.every(s => s.status?.procedure_loaded && s.status?.binary_loaded);

  return (
    <div style={{ padding: '8px', fontSize: '13px', height: '100%', display: 'flex', flexDirection: 'column', gap: '8px', overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', position: 'sticky', top: 0, background: 'var(--panel)', zIndex: 10, paddingBottom: '4px' }}>
        <span style={{ fontWeight: 600, fontSize: '14px', whiteSpace: 'nowrap' }}>CAN-SWDL</span>
        <button onClick={handleSelectAll} disabled={!allReady} style={{ fontSize: '10px', padding: '2px 6px', whiteSpace: 'nowrap' }}>전체 선택</button>
        <button onClick={handleDeselectAll} style={{ fontSize: '10px', padding: '2px 6px', whiteSpace: 'nowrap' }}>전체 해제</button>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', padding: '0', background: 'none', borderRadius: '0', border: 'none' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '11px', whiteSpace: 'nowrap', color: 'inherit' }}>
            <input type="checkbox" checked={stminTxEnabled} onChange={e => setStminTxEnabled(e.target.checked)} style={{ margin: 0, width: '14px', height: '14px' }} />
            <span style={{ fontWeight: 500, fontSize: '13px', color: 'inherit' }}>STmin</span>
          </label>
          <input
            value={globalStminTx}
            onChange={e => setGlobalStminTx(e.target.value)}
            disabled={!stminTxEnabled}
            placeholder="0A"
            style={{
              width: '32px',
              fontSize: '11px',
              padding: '1px 3px',
              border: '1px solid var(--border)',
              borderRadius: '2px',
              backgroundColor: stminTxEnabled ? 'var(--input-bg)' : 'var(--panel)',
              color: 'inherit',
              flexShrink: 0,
            }}
            title="Flow Control STmin (hex), unit 0.1ms"
          />
          <span style={{ fontSize: '9px', color: '#6b7280', whiteSpace: 'nowrap' }}>×0.1ms</span>
        </span>
        {/* Folder auto-load */}
        <label style={{ fontSize: '10px', padding: '2px 6px', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer', whiteSpace: 'nowrap', background: 'var(--panel)', color: 'inherit' }}>
          📁 폴더 선택
          <input type="file" /* @ts-ignore */ {...{ webkitdirectory: '' }} style={{ display: 'none' }}
            onChange={async (e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length === 0) return;
              setAutoLoadMsg('폴더 분석 중...');
              try {
                // Match XML files by _01 / _02 / _03 suffix pattern
                const slotXmlFiles: (File | undefined)[] = [undefined, undefined, undefined];
                for (const f of files) {
                  if (!f.name.toLowerCase().endsWith('.xml')) continue;
                  if (f.name.includes('_01')) slotXmlFiles[0] = f;
                  else if (f.name.includes('_02')) slotXmlFiles[1] = f;
                  else if (f.name.includes('_03')) slotXmlFiles[2] = f;
                }
                if (!slotXmlFiles[0] && !slotXmlFiles[1] && !slotXmlFiles[2]) {
                  setAutoLoadMsg('_01/_02/_03 패턴의 XML 파일을 찾을 수 없습니다.');
                  return;
                }

                const newSlots = Array.from({ length: NUM_SLOTS }, () => makeEmptySlot());
                const uploadPromises: Promise<void>[] = [];

                for (let slotIdx = 0; slotIdx < NUM_SLOTS; slotIdx++) {
                  const xmlFile = slotXmlFiles[slotIdx];
                  if (!xmlFile) continue;
                  newSlots[slotIdx].xmlFile = xmlFile;

                  // Read XML content to extract <information:romInfo>
                  const xmlText = await xmlFile.text();
                  const parser = new DOMParser();
                  const doc = parser.parseFromString(xmlText, 'text/xml');
                  const romInfoNodes = doc.getElementsByTagNameNS('http://gitauto.com/information/', 'romInfo');
                  if (romInfoNodes.length > 0 && romInfoNodes[0].textContent) {
                    const romPath = romInfoNodes[0].textContent.trim();
                    const binName = romPath.replace(/\\/g, '/').split('/').pop()!;
                    const binFile = files.find(ff => ff.name === binName);
                    if (binFile) {
                      newSlots[slotIdx].binFile = binFile;
                    }
                  }

                  // Upload XML and Binary
                  uploadPromises.push(
                    (async () => {
                      if (newSlots[slotIdx].xmlFile) {
                        await api.udsUploadXml(newSlots[slotIdx].xmlFile!, slotIdx);
                        const steps = await api.udsSteps(slotIdx);
                        setSlots(prev => prev.map((s, si) => si === slotIdx ? { ...s, steps, status: null } : s));
                      }
                      if (newSlots[slotIdx].binFile) {
                        await api.udsUploadBinary(newSlots[slotIdx].binFile!, slotIdx);
                      }
                    })()
                  );
                }
                setSlots(newSlots);

                await Promise.all(uploadPromises);

                // Final status refresh
                const allStatus = await api.udsStatus();
                setSlots(prev => prev.map((s, i) => ({ ...s, status: allStatus[i] || s.status })));

                const loadedCount = slotXmlFiles.filter(Boolean).length;
                setAutoLoadMsg(`완료: ${loadedCount}개 XML/BIN 로딩됨`);
                setTimeout(() => setAutoLoadMsg(null), 3000);
              } catch (err: unknown) {
                setAutoLoadMsg(`오류: ${String(err instanceof Error ? err.message : err)}`);
              }
            }}
          />
        </label>
        <button onClick={refresh} style={{ fontSize: '11px', padding: '2px 6px', marginLeft: 'auto', flexShrink: 0 }}>⟳</button>
        {autoLoadMsg && <span style={{ fontSize: '10px', color: autoLoadMsg.startsWith('오류') ? '#ef4444' : '#10b981', whiteSpace: 'nowrap' }}>{autoLoadMsg}</span>}
      </div>

      {globalError && <div style={{ padding: '4px 8px', background: '#fee2e2', color: '#dc2626', borderRadius: '4px', fontSize: '12px' }}>{globalError}</div>}

      {/* 3 Slots Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px', flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {slots.map((slot, idx) => (
          <div key={idx} style={{ border: '1px solid #e5e7eb', borderRadius: '4px', padding: '6px', display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11px', minWidth: 0 }}>
            {/* Slot Title + Status Badge */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span style={{ fontWeight: 600 }}>{`파일 ${idx + 1}`}</span>
              {slot.status && (
                <span style={{ fontSize: '9px', padding: '1px 6px', borderRadius: '3px', color: '#fff', backgroundColor: STATE_COLORS[slot.status.state] }}>{STATE_LABELS[slot.status.state]}</span>
              )}
              {slot.status?.running && (
                <button onClick={() => stopSlot(idx)} style={{ marginLeft: 'auto', fontSize: '9px', padding: '1px 4px', backgroundColor: '#ef4444', color: '#fff', border: 'none', borderRadius: '2px' }}>■</button>
              )}
            </div>

            {/* Error */}
            {slot.error && <div style={{ color: '#dc2626', fontSize: '10px' }}>{slot.error}</div>}

            {/* File pickers */}
            <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="file" accept=".xml" style={{ flex: 1, minWidth: 0 }}
                onChange={e => setSlots(prev => prev.map((s, i) => i === idx ? { ...s, xmlFile: e.target.files?.[0] ?? null } : s))}
                disabled={slot.status?.running} />
              <button disabled={!slot.xmlFile || slot.uploading || slot.status?.running}
                onClick={() => uploadXml(idx)}
                style={{
                  padding: '3px 8px',
                  backgroundColor: slot.xmlFile ? '#3b82f6' : '#d1d5db',
                  color: slot.xmlFile ? '#fff' : '#9ca3af',
                  border: 'none',
                  borderRadius: '3px',
                  fontSize: '11px',
                  fontWeight: 600,
                  cursor: slot.xmlFile ? 'pointer' : 'not-allowed',
                }}>▲ XML 업로드</button>
            </div>
            <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="file" accept=".bin" style={{ flex: 1, minWidth: 0 }}
                onChange={e => setSlots(prev => prev.map((s, i) => i === idx ? { ...s, binFile: e.target.files?.[0] ?? null } : s))} />
              <button onClick={() => uploadBin(idx)}
                disabled={!slot.binFile || slot.uploading || slot.status?.running}
                style={{
                  padding: '3px 8px',
                  backgroundColor: slot.binFile ? '#3b82f6' : '#d1d5db',
                  color: slot.binFile ? '#fff' : '#9ca3af',
                  border: 'none',
                  borderRadius: '3px',
                  fontSize: '11px',
                  fontWeight: 600,
                  cursor: slot.binFile ? 'pointer' : 'not-allowed',
                }}>BIN Upload</button>
            </div>

            {/* File info */}
            {slot.status && (
              <div style={{ fontSize: '9px', color: '#6b7280' }}>
                {slot.status.xml_filename ?? '-'} | {slot.status.binary_filename ?? '-'} ({slot.status.binary_size > 0 ? (slot.status.binary_size / 1024).toFixed(1) + 'KB' : '-'})
              </div>
            )}

            {/* Progress */}
            {slot.status?.progress && slot.status.running && (
              <div>
                <div style={{ height: '4px', backgroundColor: '#e5e7eb', borderRadius: '2px' }}>
                  <div style={{ width: `${Math.min(100, slot.status.progress.percent)}%`, height: '100%', backgroundColor: '#3b82f6', borderRadius: '2px' }} />
                </div>
                <div style={{ fontSize: '9px', color: '#9ca3af' }}>{slot.status.progress.current_step} {slot.status.progress.percent.toFixed(1)}%</div>
              </div>
            )}

            {/* Per-slot Select All / Deselect All */}
            {slot.steps && slot.steps.length > 0 && (
              <div style={{ display: 'flex', gap: '4px' }}>
                <button
                  onClick={() => setSelectedSteps(prev => ({ ...prev, [idx]: new Set(slot.steps!.map((_, si) => si)) }))}
                  style={{ fontSize: '9px', padding: '1px 4px', border: '1px solid #d1d5db', borderRadius: '2px', background: '#f9fafb' }}>
                  이 슬롯 전체 선택
                </button>
                <button
                  onClick={() => setSelectedSteps(prev => ({ ...prev, [idx]: new Set() }))}
                  style={{ fontSize: '9px', padding: '1px 4px', border: '1px solid #d1d5db', borderRadius: '2px', background: '#f9fafb' }}>
                  이 슬롯 전체 해제
                </button>
              </div>
            )}

            {/* Step Checklist */}
            {slot.steps && (
              <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
                {slot.steps.map((step, stepIdx) => {
                  const isChecked = (selectedSteps[idx] ?? new Set()).has(stepIdx);
                  const displayName = SERVICE_DISPLAY_NAMES[step.service] || step.service;

                  return (
                    <div key={`${idx}-${stepIdx}`} style={{ border: '1px solid #f0f0f0', borderRadius: '3px', padding: '3px', marginBottom: '3px', background: isChecked ? '#f0fdf4' : '#fafafa' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <input type="checkbox" checked={isChecked} onChange={() => toggleStep(idx, stepIdx)} style={{ margin: 0 }} />
                        <span style={{ fontSize: '10px', fontWeight: 500 }}>{displayName}</span>
                      </div>
                       {/* Session Type Selector for diagnosticSessionControl */}
                       {step.service === 'diagnosticSessionControl' && (() => {
                         const sessionTypes: string[] = [];
                         // Collect all available session type options from params
                         const paramKeys = Object.keys(step.params);
                         for (const pk of paramKeys) {
                           if (pk === 'diagnosticSessionType' || pk === 'background_diagnosticSessionType') {
                             if (step.params[pk]) sessionTypes.push(pk);
                           }
                         }
                         // If there are multiple session types to choose from, show a dropdown
                         if (sessionTypes.length > 1) {
                           // Determine current selection
                           const overrideVal = slot.paramOverrides[step.service]?.[`_sessionType_${stepIdx}`] ?? '';
                           // Check if a param override for _selectedSessionType exists (the actual type string)
                           return (
                             <div style={{ marginLeft: '14px', marginTop: '2px' }}>
                               {/* Dropdown for session type selection */}
                               <div style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '9px', marginTop: '1px' }}>
                                 <span style={{ color: '#6b7280', width: '60px' }}>세션 타입</span>
                                 <select
                                   value={overrideVal || sessionTypes[0]}
                                   onChange={e => setParam(idx, step.service, `_sessionType_${stepIdx}`, e.target.value)}
                                   style={{
                                     flex: 1,
                                     fontSize: '10px',
                                     padding: '1px 4px',
                                     border: '1px solid #3b82f6',
                                     borderRadius: '2px',
                                     backgroundColor: '#eff6ff',
                                     color: '#1d4ed8',
                                   }}>
                                   {sessionTypes.map(st => {
                                     const val = step.params[st];
                                     const label = st === 'diagnosticSessionType' ? `진단 세션 (${val})` : `백그라운드 세션 (${val})`;
                                     return <option key={st} value={st}>{label}</option>;
                                   })}
                                 </select>
                               </div>
                             </div>
                           );
                         }
                         // Single type: show as regular text input
                         return null;
                       })()}

                       {/* Parameter inputs (non-session-selector params) */}
                       {Object.keys(step.params).length > 0 && (
                         <div style={{ marginLeft: '14px', marginTop: '2px' }}>
                           {Object.entries(step.params).map(([pk, pv]) => {
                             // Skip diagnosticSessionType params that are handled by the dropdown above
                             if (step.service === 'diagnosticSessionControl' && (pk === 'diagnosticSessionType' || pk === 'background_diagnosticSessionType')) {
                               // Only show as plain text if there's only one type
                               const sessionTypes = Object.keys(step.params).filter(k => k === 'diagnosticSessionType' || k === 'background_diagnosticSessionType');
                               if (sessionTypes.length > 1) return null; // handled by dropdown
                             }
                             // For skipTask/skipConfirm etc, still show as input
                             if (pk === 'skipTask' || pk === 'confirmPositiveResponse' || pk === 'skipTask') {
                               const overrideVal = slot.paramOverrides[step.service]?.[pk] ?? '';
                               const isModified = overrideVal !== '';
                               return (
                                 <div key={pk} style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '9px', marginTop: '1px' }}>
                                   <span style={{ color: '#6b7280', width: '70px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={pk}>{pk}</span>
                                   <input
                                     value={overrideVal}
                                     placeholder={pv}
                                     onChange={e => setParam(idx, step.service, pk, e.target.value)}
                                     style={{
                                       flex: 1,
                                       fontSize: '10px',
                                       padding: '1px 4px',
                                       border: isModified ? '1px solid #3b82f6' : '1px solid #d1d5db',
                                       borderRadius: '2px',
                                       backgroundColor: isModified ? '#eff6ff' : '#fff',
                                       color: isModified ? '#1d4ed8' : '#374151',
                                     }}
                                   />
                                 </div>
                               );
                             }
                             // Regular params (memoryAddress, etc)
                             const overrideVal = slot.paramOverrides[step.service]?.[pk] ?? '';
                             const isModified = overrideVal !== '';
                             return (
                               <div key={pk} style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '9px', marginTop: '1px' }}>
                                 <span style={{ color: '#6b7280', width: '70px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={pk}>{pk}</span>
                                 <input
                                   value={overrideVal}
                                   placeholder={pv}
                                   onChange={e => setParam(idx, step.service, pk, e.target.value)}
                                   style={{
                                     flex: 1,
                                     fontSize: '10px',
                                     padding: '1px 4px',
                                     border: isModified ? '1px solid #3b82f6' : '1px solid #d1d5db',
                                     borderRadius: '2px',
                                     backgroundColor: isModified ? '#eff6ff' : '#fff',
                                     color: isModified ? '#1d4ed8' : '#374151',
                                   }}
                                 />
                               </div>
                             );
                           })}
                         </div>
                       )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Event log (compact) */}
            {slot.status?.events && slot.status.events.length > 0 && (
              <div style={{ maxHeight: '60px', overflowY: 'auto', fontSize: '9px', backgroundColor: '#1f2937', color: '#e5e7eb', borderRadius: '3px', padding: '2px' }}>
                {slot.status.events.map((evt: UdsEvent, i: number) => (
                  <div key={i} style={{ color: evt.level === 'ERROR' ? '#fca5a5' : evt.level === 'WARN' ? '#fcd34d' : '#e5e7eb' }}>
                    {evt.service && <span style={{ color: '#60a5fa' }}>[{evt.service}]</span>} {evt.msg}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Start All button */}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <button onClick={startAll} disabled={!allReady || anyRunning}
          style={{ padding: '4px 16px', backgroundColor: anyRunning ? '#d1d5db' : '#10b981', color: '#fff', border: 'none', borderRadius: '4px', cursor: anyRunning ? 'not-allowed' : 'pointer', fontSize: '12px', fontWeight: 600 }}>
          ▶ 전체 시작
        </button>
      </div>
      <div ref={eventsEndRef} />
    </div>
  );
}