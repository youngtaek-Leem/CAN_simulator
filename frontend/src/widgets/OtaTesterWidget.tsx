import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import type { OtaTesterCase, OtaTesterStatus, OtaTesterStepInfo, WidgetConfig } from '../types';
import { SERVICE_DISPLAY_NAMES } from '../types';
import { UdsGlobalControls, getGlobalStminOverride } from './UdsGlobalControls';

interface Props {
  config: WidgetConfig;
}

const STATE_LABELS: Record<string, string> = {
  IDLE: '대기',
  READY: '준비',
  RUNNING: '실행 중',
  COMPLETED: '완료',
  ERROR: '오류',
};

const STATE_COLORS: Record<string, string> = {
  IDLE: '#6b7280',
  READY: '#3b82f6',
  RUNNING: '#f59e0b',
  COMPLETED: '#10b981',
  ERROR: '#ef4444',
};

const XFRM_NS = 'http://gitauto.com/xfrm/';

// Every path we compare (webkitRelativePath, and binaryPath embedded in the
// XML) is prefixed with the selected root folder's own name -- browsers
// always emit forward-slash-separated webkitRelativePath regardless of host
// OS, but the reference tooling's own path strings (project.json, binaryPath)
// mix '\\' and '/'. Normalizing both before comparing, and matching only the
// suffix *after* that root segment, keeps this working on Windows and
// tolerant of the root folder having been renamed/copied elsewhere.
function normalizeSlashes(p: string): string {
  return p.replace(/\\/g, '/');
}

function stripRootSegment(relPath: string): string {
  const norm = normalizeSlashes(relPath);
  const idx = norm.indexOf('/');
  return idx === -1 ? norm : norm.slice(idx + 1);
}

interface FileIndexEntry {
  file: File;
  suffix: string; // path relative to the selected root folder, forward-slash
}

function buildFileIndex(files: File[]): FileIndexEntry[] {
  return files.map(f => ({
    file: f,
    suffix: stripRootSegment((f as any).webkitRelativePath || f.name),
  }));
}

function findBySuffix(index: FileIndexEntry[], suffix: string): File | undefined {
  const target = normalizeSlashes(suffix);
  return index.find(e => e.suffix === target)?.file;
}

function findByPrefixAndExt(index: FileIndexEntry[], prefix: string, ext: string): File | undefined {
  const p = normalizeSlashes(prefix);
  return index.find(e => e.suffix.startsWith(p) && e.suffix.toLowerCase().endsWith(ext))?.file;
}

interface ManifestEntry {
  id: string;
  fileName: string;
  enable: boolean;
  label: string;
  kind: 'hook' | 'testBlock';
}

export default function OtaTesterWidget({ config }: Props) {
  const { updateWidget } = useApp();
  const [status, setStatus] = useState<OtaTesterStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [folderLoading, setFolderLoading] = useState(false);
  const [folderMsg, setFolderMsg] = useState<string | null>(null);
  // Persisted in config.options (not local useState) so the request/response
  // CAN IDs survive switching to another page and back -- App.tsx only
  // mounts the active page's widgets, so a value kept only in local
  // useState resets on remount.
  const reqId = String(config.options.reqId ?? '18DA00F1');
  const respId = String(config.options.respId ?? '18DA00F1');
  const setReqId = (v: string) => updateWidget({ ...config, options: { ...config.options, reqId: v } });
  const setRespId = (v: string) => updateWidget({ ...config, options: { ...config.options, respId: v } });
  // setReqId+setRespId back-to-back would both read the same stale `config`
  // snapshot from this render, so the second call's updateWidget() clobbers
  // the first's -- only whichever was called last ever actually stuck. Used
  // by the vehicleInfo.json auto-fill, which needs to set both atomically.
  const setReqRespId = (req: string, resp: string) =>
    updateWidget({ ...config, options: { ...config.options, reqId: req, respId: resp } });
  const [expandedCases, setExpandedCases] = useState<Set<string>>(new Set());
  const [stepsByCase, setStepsByCase] = useState<Record<string, OtaTesterStepInfo[]>>({});
  const [stepsLoading, setStepsLoading] = useState<Set<string>>(new Set());

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventsContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.otaTesterStatus().then(setStatus).catch(() => {});
  }, []);

  // Poll status while running
  useEffect(() => {
    if (status?.running) {
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.otaTesterStatus();
          if (s) setStatus(s);
        } catch {
          // ignore polling errors
        }
      }, 500);
    } else if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [status?.running]);

  useEffect(() => {
    // scrollTop (not scrollIntoView) so only this widget's own log area
    // scrolls -- scrollIntoView also drags the whole page's scroll
    // position to keep the target in the viewport, which hijacked focus
    // away from whatever other widget the user was looking at.
    const el = eventsContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [status?.events]);

  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ''; // allow re-selecting the same folder later
    if (files.length === 0) return;
    if (!(files[0] as any).webkitRelativePath) {
      setFolderMsg('오류: 이 브라우저는 폴더 선택을 지원하지 않습니다.');
      return;
    }

    setFolderLoading(true);
    setFolderMsg('폴더 분석 중...');
    setError(null);
    const warnings: string[] = [];
    try {
      const index = buildFileIndex(files);
      const cliConfigFile = findBySuffix(index, 'CLI/cli_config.json');
      if (!cliConfigFile) {
        setFolderMsg('오류: CLI/cli_config.json 파일을 찾을 수 없습니다.');
        return;
      }
      const cliConfig = JSON.parse(await cliConfigFile.text());
      const testcases: Array<{ id: string }> = cliConfig.testcases ?? [];
      if (testcases.length === 0) {
        setFolderMsg('오류: cli_config.json에 testcases가 없습니다.');
        return;
      }

      // VehicleInfo/vehicleInfo.json carries the vehicle's own communication
      // settings (communicationInfo.settings.requestID/responseID) -- use
      // those instead of leaving the user to type them in manually.
      const vehicleInfoFile = findBySuffix(index, 'VehicleInfo/vehicleInfo.json');
      if (vehicleInfoFile) {
        try {
          const vehicleInfo = JSON.parse(await vehicleInfoFile.text());
          const settings = vehicleInfo?.communicationInfo?.settings ?? {};
          const newReqId = settings.requestID ? String(settings.requestID).replace(/^0x/i, '').toUpperCase() : reqId;
          const newRespId = settings.responseID ? String(settings.responseID).replace(/^0x/i, '').toUpperCase() : respId;
          setReqRespId(newReqId, newRespId);
        } catch {
          warnings.push('VehicleInfo/vehicleInfo.json 파싱 실패 -- Req/Resp ID는 기존 값 유지');
        }
      } else {
        warnings.push('VehicleInfo/vehicleInfo.json을 찾을 수 없어 Req/Resp ID는 기존 값을 유지합니다.');
      }

      await api.otaTesterClearCases();
      setStepsByCase({});
      setExpandedCases(new Set());

      let order = 0;
      let loadedCount = 0;
      for (const tc of testcases) {
        const tcId = tc.id;
        const manifestFile = findByPrefixAndExt(index, `Testcases/${tcId}/`, '.json');
        if (!manifestFile) {
          warnings.push(`Testcases/${tcId}/*.json 매니페스트를 찾을 수 없습니다.`);
          continue;
        }
        const manifest = JSON.parse(await manifestFile.text());
        const entries: ManifestEntry[] = [
          ...((manifest.hooks ?? []) as any[]).map(h => ({
            id: h.id, fileName: h.fileName, enable: h.enable !== false,
            label: h.type || h.id, kind: 'hook' as const,
          })),
          ...((manifest.testBlocks ?? []) as any[]).map(b => ({
            id: b.id, fileName: b.fileName, enable: b.enable !== false,
            label: b.blockName || b.id, kind: 'testBlock' as const,
          })),
        ];

        for (const entry of entries) {
          const xmlFile = findBySuffix(index, `Testcases/${tcId}/${entry.fileName}`);
          if (!xmlFile) {
            warnings.push(`XML 파일을 찾을 수 없습니다: ${entry.fileName}`);
            continue;
          }
          await api.otaTesterCaseUploadXml(xmlFile, entry.id, entry.label, entry.kind, order, entry.enable);

          const xmlText = await xmlFile.text();
          const doc = new DOMParser().parseFromString(xmlText, 'text/xml');
          const testRuleNodes = doc.getElementsByTagNameNS(XFRM_NS, 'test-rule');
          const binaryPath = testRuleNodes.length > 0 ? testRuleNodes[0].getAttribute('binaryPath') : null;
          if (binaryPath) {
            const binFile = findBySuffix(index, stripRootSegment(binaryPath));
            if (binFile) {
              await api.otaTesterCaseUploadBinary(binFile, entry.id);
            } else {
              warnings.push(`바이너리 파일을 찾을 수 없습니다: ${binaryPath}`);
            }
          }

          order += 1;
          loadedCount += 1;
        }
      }

      const s = await api.otaTesterStatus();
      setStatus(s);
      const suffix = warnings.length > 0 ? ` (경고 ${warnings.length}건: ${warnings.join('; ')})` : '';
      setFolderMsg(`완료: ${loadedCount}개 케이스 로딩됨${suffix}`);
    } catch (err: unknown) {
      setFolderMsg(`오류: ${String(err instanceof Error ? err.message : err)}`);
    } finally {
      setFolderLoading(false);
    }
  };

  const toggleCase = async (caseId: string, enabled: boolean) => {
    try {
      const s = await api.otaTesterCaseEnable(caseId, enabled);
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const setAllEnabled = async (enabled: boolean) => {
    try {
      const s = await api.otaTesterSetAllEnabled(enabled);
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const toggleExpand = async (caseId: string) => {
    setExpandedCases(prev => {
      const next = new Set(prev);
      if (next.has(caseId)) next.delete(caseId); else next.add(caseId);
      return next;
    });
    if (stepsByCase[caseId]) return;
    setStepsLoading(prev => new Set(prev).add(caseId));
    try {
      const steps = await api.otaTesterCaseSteps(caseId);
      setStepsByCase(prev => ({ ...prev, [caseId]: steps }));
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setStepsLoading(prev => { const n = new Set(prev); n.delete(caseId); return n; });
    }
  };

  const isStepSelected = (c: OtaTesterCase, idx: number) =>
    c.selected_steps === null || c.selected_steps.includes(idx);

  const toggleStep = async (c: OtaTesterCase, idx: number, checked: boolean) => {
    const current = c.selected_steps === null
      ? Array.from({ length: c.total_steps }, (_, i) => i)
      : c.selected_steps;
    const next = checked
      ? [...current, idx].filter((v, i, arr) => arr.indexOf(v) === i).sort((a, b) => a - b)
      : current.filter(i => i !== idx);
    const payload = next.length === c.total_steps ? null : next;
    try {
      const s = await api.otaTesterSetSelectedSteps(c.id, payload);
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const setCaseStepsAll = async (caseId: string, selectAll: boolean) => {
    try {
      const s = await api.otaTesterSetSelectedSteps(caseId, selectAll ? null : []);
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const handleStart = async () => {
    setError(null);
    try {
      const rid = parseInt(reqId, 16) || 0x18DA00F1;
      const rsp = parseInt(respId, 16) || 0x18DA00F1;
      await api.otaTesterStart(rid, rsp, getGlobalStminOverride());
      const s = await api.otaTesterStatus();
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const handleStop = async () => {
    try {
      await api.otaTesterStop();
      const s = await api.otaTesterStatus();
      setStatus(s);
    } catch (e: unknown) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const st = status;
  const stateLabel = st ? (STATE_LABELS[st.state] ?? st.state) : '대기';
  const stateColor = st ? (STATE_COLORS[st.state] ?? '#6b7280') : '#6b7280';
  const cases = st?.cases ?? [];
  const enabledCount = cases.filter(c => c.enabled).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', fontSize: 13 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      {/* Control buttons */}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-start' }}>
        <button
          disabled={st?.running || enabledCount === 0}
          onClick={handleStart}
          style={{
            width: '80px', padding: 8, backgroundColor: '#10b981', color: '#fff', border: 'none', borderRadius: 4,
            cursor: (st?.running || enabledCount === 0) ? 'not-allowed' : 'pointer',
            opacity: (st?.running || enabledCount === 0) ? 0.5 : 1, fontWeight: 400,
          }}
        >
          ▶ Start
        </button>
        <button
          disabled={!st?.running}
          onClick={handleStop}
          style={{
            width: '80px', flex: 1, padding: 8, backgroundColor: '#ef4444', color: '#fff', border: 'none', borderRadius: 4,
            cursor: st?.running ? 'pointer' : 'not-allowed', opacity: st?.running ? 1 : 0.5, fontWeight: 400,
          }}
        >
          ⏹ Stop
        </button>
      </div>
	
        <span style={{
          display: 'inline-block', padding: '2px 10px', backgroundColor: stateColor,
          color: '#fff', borderRadius: 4, fontSize: 13, fontWeight: 600,
        }}>
          {stateLabel}
        </span>
      </div>

      {error && <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>}

      {/* CAN ID settings + folder picker */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Req ID
          <input type="text" value={reqId} onChange={e => setReqId(e.target.value)}
            style={{ width: 80, padding: '2px 4px', fontFamily: 'monospace' }} placeholder="18DA00F1" />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          Resp ID
          <input type="text" value={respId} onChange={e => setRespId(e.target.value)}
            style={{ width: 80, padding: '2px 4px', fontFamily: 'monospace' }} placeholder="18DA00F1" />
        </label>
        {/* STmin override + SeedKey DLL loader -- shared with CAN-SWDL, see UdsGlobalControls.tsx */}
        <UdsGlobalControls />
        <label style={{
          fontSize: 12, padding: '4px 10px', border: '1px solid var(--border)', borderRadius: 4,
          cursor: folderLoading || st?.running ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
          background: 'var(--panel)', color: 'inherit', opacity: folderLoading || st?.running ? 0.5 : 1,
        }}>
          📁 패키지 폴더선택
          <input
            type="file"
            /* @ts-ignore -- non-standard but supported by Chromium/Edge (incl. on Windows) and Firefox */
            {...{ webkitdirectory: '' }}
            disabled={folderLoading || st?.running}
            style={{ display: 'none' }}
            onChange={handleFolderSelect}
          />
        </label>
      </div>

      {folderMsg && (
        <div style={{ fontSize: 11, color: folderMsg.startsWith('오류') ? '#ef4444' : '#6b7280' }}>{folderMsg}</div>
      )}

      {/* Case checklist */}
      {cases.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: '46%', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 4, padding: 6 }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 2 }}>
            <span style={{ fontWeight: 600, fontSize: 12 }}>테스트 케이스 ({enabledCount}/{cases.length} 선택됨)</span>
            <button onClick={() => setAllEnabled(true)} disabled={st?.running} style={{ fontSize: 10, padding: '1px 6px', marginLeft: 'auto' }}>전체 선택</button>
            <button onClick={() => setAllEnabled(false)} disabled={st?.running} style={{ fontSize: 10, padding: '1px 6px' }}>전체 해제</button>
          </div>
          {cases.map(c => {
            const expanded = expandedCases.has(c.id);
            const steps = stepsByCase[c.id];
            const selectedCount = c.selected_steps === null ? c.total_steps : c.selected_steps.length;
            return (
              <div key={c.id} style={{ borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, opacity: st?.running ? 0.85 : 1 }}>
                  <button onClick={() => toggleExpand(c.id)} title="진단 명령어 목록"
                    style={{ fontSize: 10, padding: '0 4px', background: 'none', border: 'none', cursor: 'pointer', color: 'inherit' }}>
                    {expanded ? '▼' : '▶'}
                  </button>
                  <input type="checkbox" checked={c.enabled} disabled={st?.running}
                    onChange={e => toggleCase(c.id, e.target.checked)} />
                  <span style={{
                    fontSize: 9, padding: '0 5px', borderRadius: 3, color: '#fff',
                    backgroundColor: c.kind === 'hook' ? '#8b5cf6' : '#3b82f6',
                  }}>{c.kind}</span>
                  <span style={{ flex: 1 }}>{c.label}</span>
                  <span style={{ color: '#6b7280' }}>{selectedCount}/{c.total_steps} steps</span>
                  {c.binary_path_hint && (
                    <span title={c.binary_filename ?? undefined} style={{ color: c.binary_loaded ? '#10b981' : '#f59e0b' }}>
                      {c.binary_loaded ? '✓ BIN' : '⚠ BIN 없음'}
                    </span>
                  )}
                </div>
                {expanded && (
                  <div style={{ marginLeft: 22, marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button onClick={() => setCaseStepsAll(c.id, true)} disabled={st?.running} style={{ fontSize: 9, padding: '1px 5px' }}>전체 선택</button>
                      <button onClick={() => setCaseStepsAll(c.id, false)} disabled={st?.running} style={{ fontSize: 9, padding: '1px 5px' }}>전체 해제</button>
                    </div>
                    {stepsLoading.has(c.id) && <span style={{ fontSize: 11, color: '#6b7280' }}>불러오는 중...</span>}
                    {steps?.map((step, idx) => (
                      <label key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11 }}>
                        <input type="checkbox" checked={isStepSelected(c, idx)} disabled={st?.running}
                          style={{ marginTop: 2 }}
                          onChange={e => toggleStep(c, idx, e.target.checked)} />
                        <span style={{ color: '#6b7280', width: 18, textAlign: 'right', flexShrink: 0 }}>{idx + 1}</span>
                        <span style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span>{SERVICE_DISPLAY_NAMES[step.service] ?? step.service}</span>
                            {step.pdu_preview && (
                              <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#93c5fd' }}>
                                [{step.pdu_preview}]
                              </span>
                            )}
                            {!step.confirm_positive_response && (
                              <span style={{ fontSize: 9, color: '#f59e0b' }}>(음성 응답 예상)</span>
                            )}
                          </span>
                          {step.pdu_note && (
                            <span style={{ fontSize: 9, color: '#6b7280' }}>{step.pdu_note}</span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Progress */}
      {st && st.total_cases > 0 && (
        <div style={{ fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span>
              케이스 {Math.min(st.current_case_index + 1, st.total_cases)}/{st.total_cases}
              {st.total_steps_in_case > 0 && ` · Step ${Math.min(st.current_step_index + 1, st.total_steps_in_case)}/${st.total_steps_in_case}`}
            </span>
            {st.current_case_label && <span style={{ color: '#3b82f6' }}>{st.current_case_label}</span>}
          </div>
          <div style={{ background: '#374151', borderRadius: 4, height: 8 }}>
            <div style={{
              width: `${st.total_cases > 0 ? ((st.current_case_index + (st.total_steps_in_case > 0 ? (st.current_step_index + 1) / st.total_steps_in_case : 1)) / st.total_cases) * 100 : 0}%`,
              height: '100%', backgroundColor: '#3b82f6', borderRadius: 4, transition: 'width 0.3s',
            }} />
          </div>
          {/* TransferData block progress -- separate from the case/step bar above
              since one "Step" can itself be a long-running multi-block firmware
              transfer with no visible movement otherwise (matches CAN-SWDL). */}
          {st.running && st.progress && st.progress.total_blocks > 0 && (
            <div style={{ marginTop: 4 }}>
              <div style={{ height: 4, backgroundColor: '#374151', borderRadius: 2 }}>
                <div style={{
                  width: `${Math.min(100, st.progress.percent)}%`, height: '100%',
                  backgroundColor: '#3b82f6', borderRadius: 2, transition: 'width 0.3s',
                }} />
              </div>
              <div style={{ fontSize: 9, color: '#9ca3af' }}>
                {st.progress.current_step} {st.progress.current_block}/{st.progress.total_blocks} blocks ({st.progress.percent.toFixed(1)}%)
              </div>
            </div>
          )}
        </div>
      )}

      {/* Event log */}
      <div ref={eventsContainerRef} style={{
        flex: 1, overflow: 'auto', background: '#1e293b', borderRadius: 6, padding: 8,
        fontSize: 12, fontFamily: 'monospace', minHeight: 100,
      }}>
        {st?.events?.map((ev, i) => (
          <div key={i} style={{ color: ev.level === 'ERROR' ? '#ef4444' : ev.level === 'WARN' || ev.level === 'WARNING' ? '#f59e0b' : '#94a3b8', padding: '2px 0' }}>
            <span style={{ color: '#64748b', marginRight: 8 }}>{new Date(ev.ts * 1000).toLocaleTimeString()}</span>
            {ev.service && <span style={{ color: '#3b82f6', marginRight: 6 }}>[{ev.service}]</span>}
            {ev.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
