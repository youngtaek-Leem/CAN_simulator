import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore } from '../store/canStore';
import { useApp } from '../store/appContext';
import { UdsGlobalControls, getGlobalStminOverride } from './UdsGlobalControls';
import type { UdsDownloadStatus, UdsStepInfo, WidgetConfig } from '../types';
import { SERVICE_DISPLAY_NAMES } from '../types';

interface Props {
  config: WidgetConfig;
}

const NUM_SLOTS = 3;

// diagnosticSessionType is the immediate session switch; background_diagnosticSessionType
// is a secondary follow-up switch. When an XML step carries both, the immediate one must
// win as the default choice regardless of XML attribute order.
const SESSION_TYPE_ORDER = ['diagnosticSessionType', 'background_diagnosticSessionType'] as const;

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
  uploading: boolean;
  error: string | null;
}

export default function UdsSwdlWidget({ config }: Props) {
  const { updateWidget } = useApp();
  const makeEmptySlot = (): SlotState => ({
    xmlFile: null,
    binFile: null,
    status: null,
    steps: null,
    uploading: false,
    error: null,
  });

  const [slots, setSlots] = useState<SlotState[]>(() =>
    Array.from({ length: NUM_SLOTS }, () => makeEmptySlot()),
  );

  // Per-slot selected step indices and parameter overrides -- persisted in
  // config.options (not local useState) so they survive switching to
  // another page and back; App.tsx only mounts the active page's widgets,
  // so a value kept only in local useState resets on remount. Stored as
  // plain arrays/objects (not Set, which doesn't survive JSON.stringify)
  // since config.options is also what gets serialized when saving a layout.
  const selectedStepsArr = (config.options.selectedSteps as Record<number, number[]> | undefined) ?? {};
  const selectedSteps: Record<number, Set<number>> = {
    0: new Set(selectedStepsArr[0] ?? []),
    1: new Set(selectedStepsArr[1] ?? []),
    2: new Set(selectedStepsArr[2] ?? []),
  };
  const setSelectedSteps = (
    updater: (prev: Record<number, Set<number>>) => Record<number, Set<number>>,
  ) => {
    const next = updater(selectedSteps);
    const nextArr: Record<number, number[]> = {};
    for (let i = 0; i < NUM_SLOTS; i++) nextArr[i] = Array.from(next[i] ?? []);
    updateWidget({ ...config, options: { ...config.options, selectedSteps: nextArr } });
  };

  const allParamOverrides =
    (config.options.paramOverrides as Record<number, Record<string, Record<string, string>>> | undefined) ?? {};
  const paramOverridesFor = (slotIdx: number) => allParamOverrides[slotIdx] ?? {};
  const setSlotParamOverrides = (slotIdx: number, overrides: Record<string, Record<string, string>>) => {
    updateWidget({
      ...config,
      options: { ...config.options, paramOverrides: { ...allParamOverrides, [slotIdx]: overrides } },
    });
  };
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [autoLoadMsg, setAutoLoadMsg] = useState<string | null>(null);
  // Polling control: a single on/off flag. Turned on right before start, turned
  // off once polling observes NO slot is running anymore. Decoupling the poll
  // loop from the (instantly-resolving) start() HTTP call is the key fix -- the
  // backend keeps the worker thread alive long after the request returns, so
  // the only reliable "still running" signal is the backend status itself.
  const [polling, setPolling] = useState(false);
  // Local "starting" flag purely for disabling the ▶ button while the start
  // POST is in flight; the long-running state is driven by `polling`, not this.
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastEventCountRef = useRef<Record<number, number>>({ 0: 0, 1: 0, 2: 0 });

  // Restore status + steps on mount -- otherwise switching to another page
  // and back (App.tsx only mounts the active page's widgets) shows every
  // slot as blank/unloaded even though the backend still has the XML/binary
  // loaded and remembers it, until the user manually clicks the ⟳ button.
  useEffect(() => {
    (async () => {
      try {
        const all = await api.udsStatus();
        setSlots(prev => prev.map((s, i) => ({ ...s, status: all[i] || s.status })));
        for (let i = 0; i < NUM_SLOTS; i++) {
          if (!all[i]?.procedure_loaded) continue;
          try {
            const steps = await api.udsSteps(i);
            setSlots(prev => prev.map((s, si) => (si === i ? { ...s, steps } : s)));
          } catch { /* ignore -- this slot just stays without a step list */ }
        }
      } catch { /* ignore -- widget still usable, just starts from a blank state */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll status while `polling` is on -- also pipe CAN-SWDL events into the
  // shared TextDisplay activity log (canStore.activityLog). The loop turns
  // itself off once every slot's `running` flag goes false, so it stays alive
  // for the whole background download even though the start HTTP call returned
  // immediately (the backend runs the procedure in a daemon thread).
  useEffect(() => {
    if (!polling) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const all = await api.udsStatus();
        setSlots(prev => prev.map((s, i) => ({ ...s, status: all[i] || s.status })));

        // Push new CAN-SWDL events into the shared TextDisplay activity log.
        let stillRunning = false;
        for (let i = 0; i < NUM_SLOTS; i++) {
          const slotStatus = all[i];
          if (!slotStatus) continue;
          if (slotStatus.running) stillRunning = true;
          if (!slotStatus.events) continue;
          const oldCount = lastEventCountRef.current[i] ?? 0;
          const newEvents = slotStatus.events.slice(oldCount);
          lastEventCountRef.current[i] = slotStatus.events.length;
          for (const evt of newEvents) {
            let label = '';
            if (evt.service) label = `[${evt.service}] `;
            if (evt.level === 'ERROR') label += '❌ ';
            else if (evt.level === 'WARN') label += '⚠️ ';
            canStore.pushActivity(`SWDL${i + 1}: ${label}${evt.msg ?? ''}`, (evt.ts ?? Date.now() / 1000) * 1000);
          }
        }
        // Backend finished (no slot running) -> stop polling. The status
        // snapshot has already been refreshed above, so the UI shows the final
        // COMPLETED/ERROR state.
        if (!stillRunning) {
          setPolling(false);
        }
      } catch { /* ignore */ }
    }, 500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [polling]);

  const toggleStep = (slotIdx: number, stepIdx: number) => {
    // Determined from the pre-click state (not inside the setState updater
    // below) -- React only runs a batched updater eagerly for the first call
    // in a tick, so a variable mutated inside it and read right after was
    // stale for every subsequent rapid click, always logging "OFF" even when
    // the box had just been checked.
    const nowChecked = !(selectedSteps[slotIdx] ?? new Set()).has(stepIdx);
    setSelectedSteps(prev => {
      const current = new Set(prev[slotIdx] ?? []);
      if (nowChecked) current.add(stepIdx); else current.delete(stepIdx);
      return { ...prev, [slotIdx]: current };
    });
    // Debug visibility: log every checkbox change to the TextDisplay so the
    // user can confirm their selection was actually captured by the frontend.
    const slot = slots[slotIdx];
    const step = slot.steps?.[stepIdx];
    const name = step ? (SERVICE_DISPLAY_NAMES[step.service] || step.service) : `step ${stepIdx}`;
    canStore.pushActivity(`SWDL${slotIdx + 1}: 체크${nowChecked ? ' ON' : ' OFF'} → [${stepIdx}] ${name}`);
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
    setSelectedSteps(() => ({ 0: new Set(), 1: new Set(), 2: new Set() }));
  };

  const uploadXml = async (slotIdx: number) => {
    const file = slots[slotIdx].xmlFile;
    if (!file) return;
    setSlots(prev => prev.map((s, i) => i === slotIdx ? { ...s, uploading: true, error: null } : s));
    try {
      await api.udsUploadXml(file, slotIdx);
      const steps = await api.udsSteps(slotIdx);
      const allStatus = await api.udsStatus();

      // Auto-initialize paramOverrides for diagnosticSessionControl steps with multiple session types
      const autoParams: Record<string, string> = {};
      if (steps) {
        steps.forEach((step, stepIdx) => {
          if (step.service === 'diagnosticSessionControl') {
            const sessionKeys = SESSION_TYPE_ORDER.filter(k => k in step.params);
            if (sessionKeys.length > 1) {
              autoParams[`_sessionType_${stepIdx}`] = sessionKeys[0]; // diagnosticSessionType wins by default
            }
          }
        });
      }

      if (Object.keys(autoParams).length > 0) {
        setSlotParamOverrides(slotIdx, {
          ...paramOverridesFor(slotIdx),
          diagnosticSessionControl: { ...autoParams },
        });
      }
      setSlots(prev => prev.map((s, i) => (i === slotIdx ? { ...s, status: allStatus[i], steps, uploading: false } : s)));
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
    for (let i = 0; i < NUM_SLOTS; i++) {
      if (slots[i].status?.procedure_loaded && slots[i].status?.binary_loaded) readySlots.push(i);
    }
    if (readySlots.length === 0) { setGlobalError('준비된 슬롯이 없습니다.'); return; }

    // Build per-slot payloads. The backend `start_all` consumes
    // `per_slot_selected_steps` (slot_index -> list[int] | None) and runs each
    // ready slot in sequence. We use the per-slot form (not the shared
    // `selected_steps`) so each slot only runs its own checked steps:
    //   - no checks   -> []     (none)
    //   - all checks  -> undefined (all)
    //   - partial     -> [idx, ...]
    const perSlotSelected: Record<number, number[] | undefined> = {};
    const perSlotParams: Record<number, Record<string, Record<string, string>> | undefined> = {};
    for (const idx of readySlots) {
      const totalSteps = slots[idx].steps?.length ?? 0;
      const checkedSet = selectedSteps[idx] ?? new Set<number>();
      const arr = Array.from(checkedSet).sort((a, b) => a - b);
      perSlotSelected[idx] =
        arr.length === 0 ? [] :
        arr.length === totalSteps ? undefined :
        arr;
      const slotParams = paramOverridesFor(idx);
      perSlotParams[idx] = Object.keys(slotParams).length > 0 ? slotParams : undefined;
      // Debug log: announce what we are about to run for this slot.
      const describe = arr.length === 0 ? '선택 없음(건너뜀)'
        : arr.length === totalSteps ? `전체 ${totalSteps}스텝`
        : `선택 ${arr.length}/${totalSteps}스텝 [${arr.join(',')}]`;
      canStore.pushActivity(`SWDL${idx + 1}: 시작 요청 — ${describe}`);
    }

    setStarting(true);
    canStore.pushActivity(`[CAN-SWDL] 전체 시작 (슬롯 ${readySlots.map(i => i + 1).join(', ')})`);
    try {
      // Turn polling ON *before* the POST so the very first tick after the
      // (instant) response starts streaming events -- otherwise the previous
      // race (start returns, UI flips to "done", poll never runs) would hide
      // the whole background run from the TextDisplay.
      setPolling(true);
      await api.udsStart(
        readySlots,
        undefined,            // selected_steps (shared) -- unused with per-slot
        undefined,            // modified_params (shared) -- unused with per-slot
        getGlobalStminOverride(),
        perSlotSelected,
        perSlotParams,
      );
    } catch (e: unknown) {
      setGlobalError(`시작 실패: ${String(e instanceof Error ? e.message : e)}`);
      setPolling(false);
    } finally {
      setStarting(false);
    }
  };

  const stopSlot = async (idx: number) => {
    try {
      await api.udsStop(idx);
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
    const overrides = { ...paramOverridesFor(slotIdx) };
    overrides[svc] = { ...(overrides[svc] ?? {}), [key]: val };
    setSlotParamOverrides(slotIdx, overrides);
  };

  // Running is driven by backend status (kept fresh by polling), not by a
  // local flag that flips to "done" the instant the start POST returns.
  const anyRunning = slots.some(s => s.status?.running) || starting;
  const allReady = slots.every(s => s.status?.procedure_loaded && s.status?.binary_loaded);

  return (
    <div style={{ padding: '8px', fontSize: '13px', height: '100%', display: 'flex', flexDirection: 'column', gap: '8px', overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', position: 'sticky', top: 0, background: 'var(--panel)', zIndex: 10, paddingBottom: '4px' }}>
        <span style={{ fontWeight: 600, fontSize: '14px', whiteSpace: 'nowrap' }}>CAN-SWDL</span>
        <button onClick={handleSelectAll} disabled={!allReady} style={{ fontSize: '10px', padding: '2px 6px', whiteSpace: 'nowrap' }}>전체 선택</button>
        <button onClick={handleDeselectAll} style={{ fontSize: '10px', padding: '2px 6px', whiteSpace: 'nowrap' }}>전체 해제</button>
        {/* STmin override + SeedKey DLL loader -- shared with OTA Tester, see UdsGlobalControls.tsx */}
        <UdsGlobalControls />
        {/* Folder auto-load */}
        <label style={{ fontSize: '10px', padding: '2px 6px', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer', whiteSpace: 'nowrap', background: 'var(--panel)', color: 'inherit' }}>
          📁 패키지 폴더선택
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
                // Collected here instead of calling setSlotParamOverrides() from inside
                // each per-slot closure below -- all 3 slots upload concurrently, and
                // setSlotParamOverrides() reads `allParamOverrides` (captured once from
                // this render's `config` prop) to build its merged write. Three
                // concurrent closures all reading that same stale snapshot means
                // whichever slot's upload settles last overwrites the whole
                // paramOverrides object with only its own entry, silently discarding
                // the other two slots' auto-selected session type (this is exactly
                // what a real multi-slot XML load hit: only the last-resolving slot's
                // diagnosticSessionControl override survived, so the others fell back
                // to sending *both* the main and background session -- see
                // Requirement.md). Stashing each slot's result here and writing once
                // after every upload has settled makes it a single atomic update.
                const autoParamsBySlot: Record<number, Record<string, string>> = {};

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

                        // Auto-initialize paramOverrides for diagnosticSessionControl with multiple session types
                        const autoParams: Record<string, string> = {};
                        if (steps) {
                          steps.forEach((step, stepIdx) => {
                            if (step.service === 'diagnosticSessionControl') {
                              const sessionKeys = SESSION_TYPE_ORDER.filter(k => k in step.params);
                              if (sessionKeys.length > 1) {
                                autoParams[`_sessionType_${stepIdx}`] = sessionKeys[0];
                              }
                            }
                          });
                        }

                        // Stashed for the single merged write after Promise.all below
                        // (see autoParamsBySlot's comment above) -- not written here.
                        autoParamsBySlot[slotIdx] = autoParams;
                        setSlots(prev => prev.map((s, si) => (si === slotIdx ? { ...s, steps, status: null } : s)));
                      }
                      if (newSlots[slotIdx].binFile) {
                        await api.udsUploadBinary(newSlots[slotIdx].binFile!, slotIdx);
                      }
                    })()
                  );
                }
                setSlots(newSlots);

                await Promise.all(uploadPromises);

                // Single atomic write of every slot's auto-selected params, now that
                // every upload has settled -- see autoParamsBySlot's comment above.
                // Fresh package load -- reset each touched slot's overrides rather
                // than merging with whatever a previously loaded package left behind.
                if (Object.keys(autoParamsBySlot).length > 0) {
                  const mergedOverrides = { ...allParamOverrides };
                  for (const [slotIdxStr, autoParams] of Object.entries(autoParamsBySlot)) {
                    mergedOverrides[Number(slotIdxStr)] =
                      Object.keys(autoParams).length > 0 ? { diagnosticSessionControl: { ...autoParams } } : {};
                  }
                  updateWidget({ ...config, options: { ...config.options, paramOverrides: mergedOverrides } });
                }

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
                  style={{ fontSize: '9px', padding: '1px 4px', border: 'none', borderRadius: '2px', background: '#3b82f6', color: '#fff' }}>
                  이 슬롯 전체 선택
                </button>
                <button
                  onClick={() => setSelectedSteps(prev => ({ ...prev, [idx]: new Set() }))}
                  style={{ fontSize: '9px', padding: '1px 4px', border: 'none', borderRadius: '2px', background: '#3b82f6', color: '#fff' }}>
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
                  const isRunningStep = !!slot.status?.running && slot.status?.progress?.current_step_idx === stepIdx;

                  return (
                    <div key={`${idx}-${stepIdx}`} style={{ border: '1px solid #f0f0f0', borderRadius: '3px', padding: '3px', marginBottom: '3px', background: isChecked ? '#f0fdf4' : '#fafafa' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <input type="checkbox" checked={isChecked} onChange={() => toggleStep(idx, stepIdx)} style={{ margin: 0 }} />
                        <span style={{ fontSize: '10px', fontWeight: 700, color: isRunningStep ? '#dc2626' : '#000' }}>{displayName}</span>
                      </div>
                       {/* Session Type Selector for diagnosticSessionControl */}
                       {step.service === 'diagnosticSessionControl' && (() => {
                         // Collect available session type options, diagnosticSessionType first
                         const sessionTypes = SESSION_TYPE_ORDER.filter(k => step.params[k]);
                         // If there are multiple session types to choose from, show a dropdown
                         if (sessionTypes.length > 1) {
                           // Determine current selection
                           const overrideVal = paramOverridesFor(idx)[step.service]?.[`_sessionType_${stepIdx}`] ?? '';
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

                       {/* accessMode readout for securityAccess -- sourced from the requestSeed
                           sub-step, since it's what the backend actually sends [27 <accessMode>] with. */}
                       {step.service === 'securityAccess' && (() => {
                         const accessMode = step.sub_steps.find(s => s.service === 'requestSeed')?.params?.accessMode;
                         if (!accessMode) return null;
                         return (
                           <div style={{ marginLeft: '14px', marginTop: '2px', display: 'flex', alignItems: 'center', gap: '3px', fontSize: '9px' }}>
                             <span style={{ color: '#6b7280', width: '70px' }} title="accessMode">accessMode</span>
                             <span style={{ flex: 1, padding: '1px 4px', color: '#374151' }}>{accessMode}</span>
                           </div>
                         );
                       })()}

                       {/* Parameter inputs (non-session-selector params) */}
                       {Object.keys(step.params).length > 0 && (
                         <div style={{ marginLeft: '14px', marginTop: '2px' }}>
                           {Object.entries(step.params).map(([pk, pv]) => {
                             // Skip diagnosticSessionType params that are handled by the dropdown above
                             if (step.service === 'diagnosticSessionControl' && (pk === 'diagnosticSessionType' || pk === 'background_diagnosticSessionType')) {
                               // Only show as plain text if there's only one type
                               const sessionTypes = SESSION_TYPE_ORDER.filter(k => step.params[k]);
                               if (sessionTypes.length > 1) return null; // handled by dropdown
                             }
                             // skipTask is not used by the backend and is not user-configurable -- hide it.
                             if (pk === 'skipTask') return null;
                             // Regular params (memoryAddress, etc)
                             const overrideVal = paramOverridesFor(idx)[step.service]?.[pk] ?? '';
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
    </div>
  );
}