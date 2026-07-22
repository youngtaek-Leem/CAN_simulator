// CAN evaluation environment - main app shell.
//
// Top bar: bus connection, DBC upload, layout save/load, settings, edit mode.
// Canvas: react-grid-layout based free-form GUI builder; widgets can always be
// dragged (via the title bar) and resized, regardless of edit mode. Edit mode
// only gates the per-widget config/remove buttons and stops TX/RX.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  GridLayout,
  useContainerWidth,
  type Compactor,
  type Layout,
  type LayoutItem,
} from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import { api } from './api/client';
import { connectWebSocket } from './api/ws';
import { canStore, useCanVersion } from './store/canStore';
import { AppContext } from './store/appContext';
import { WIDGET_REGISTRY } from './widgets/registry';
import { WidgetFrame } from './widgets/WidgetFrame';
import type { DbcSummary, MultiCell, WidgetConfig, WidgetType } from './types';

interface Page {
  id: string;
  name: string;
  widgets: WidgetConfig[];
  layout: LayoutItem[];
}

interface SavedFile {
  filename: string;
  content: string;
}

interface CanConfig {
  iface: string;
  channel: string;
  bitrate: number;
  fd: boolean;
  dataBitrate: number;
}

const DEFAULT_CAN_CONFIG: CanConfig = {
  iface: 'virtual',
  channel: 'ch0',
  bitrate: 500000,
  fd: false,
  dataBitrate: 2_000_000,
};

interface SavedLayout {
  pages: Page[];
  dbc?: SavedFile;
  functionScript?: SavedFile;
  scenarioScript?: SavedFile;
  canConfig?: CanConfig;
}

/** Legacy single-page save format (before multi-page tabs) -- still
 * readable so old saved layouts keep working. */
interface LegacySavedLayout {
  layout: LayoutItem[];
  widgets: WidgetConfig[];
}

function makePage(id: string, name: string): Page {
  return { id, name, widgets: [], layout: [] };
}

/** DBC message names a widget could have armed an auto-periodic sender for
 * (via POST /api/tx/signal), so we know what to stop_auto() on removal. */
function sendableMessages(config: WidgetConfig): string[] {
  if (config.binding?.message) return [config.binding.message];
  const cells = config.options.cells as MultiCell[] | undefined;
  if (cells) return cells.map((c) => c.binding?.message).filter((m): m is string => !!m);
  return [];
}

// free placement: no compaction, widgets keep their position and may overlap
const freeCompactor: Compactor = {
  type: null,
  allowOverlap: true,
  compact: (layout) => [...layout],
};

export default function App() {
  useCanVersion();
  const [dbc, setDbc] = useState<DbcSummary>({ loaded: false });
  const [editMode, setEditMode] = useState(true);
  const [pages, setPages] = useState<Page[]>([makePage('p1', 'Page 1')]);
  const [activePageId, setActivePageId] = useState('p1');
  const [layoutName, setLayoutName] = useState('default');
  const [layoutList, setLayoutList] = useState<string[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [canConfig, setCanConfig] = useState<CanConfig>(DEFAULT_CAN_CONFIG);

  const { width, containerRef, mounted } = useContainerWidth();

  const activePage = pages.find((p) => p.id === activePageId) ?? pages[0];

  // if the active page was just removed, fall back to the first remaining one
  useEffect(() => {
    if (!pages.some((p) => p.id === activePageId)) {
      setActivePageId(pages[0]?.id ?? 'p1');
      setActiveId(null);
    }
  }, [pages, activePageId]);

  // requirement: while in edit mode all TX/RX activity must be stopped
  useEffect(() => {
    if (editMode) api.runStop().catch(() => {});
  }, [editMode]);

  const refreshDbc = useCallback(() => {
    api.getDbc().then((d) => {
      setDbc(d as DbcSummary);
      canStore.setDbc(d as DbcSummary);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    connectWebSocket();
    refreshDbc();
    api.listLayouts().then((r) => setLayoutList(r.layouts)).catch(() => {});
  }, [refreshDbc]);

  const notify = (text: string) => {
    setBanner(text);
    window.setTimeout(() => setBanner(null), 3000);
  };

  // ---- widget management (scoped to the active page) --------------------

  const updateActivePage = useCallback(
    (fn: (p: Page) => Page) => {
      setPages((ps) => ps.map((p) => (p.id === activePageId ? fn(p) : p)));
    },
    [activePageId],
  );

  const addWidget = (type: WidgetType) => {
    const meta = WIDGET_REGISTRY[type];
    const id = `w${Date.now()}`;
    updateActivePage((p) => {
      // cascade new widgets from the top-left (no auto-compaction)
      const n = p.layout.length;
      return {
        ...p,
        widgets: [...p.widgets, { id, type, title: meta.label, options: {} }],
        layout: [...p.layout, { i: id, x: (n % 6) * 2, y: n, ...meta.defaultSize }],
      };
    });
  };

  const updateWidget = useCallback(
    (cfg: WidgetConfig) => {
      updateActivePage((p) => ({
        ...p,
        widgets: p.widgets.map((x) => (x.id === cfg.id ? cfg : x)),
      }));
    },
    [updateActivePage],
  );

  const removeWidget = useCallback(
    (id: string) => {
      // a signal a removed widget was driving may still be in active use by
      // a widget sitting on a different (currently hidden) page, so this
      // must scan every page, not just the active one
      const allWidgets = pages.flatMap((p) => p.widgets);
      const target = allWidgets.find((w) => w.id === id);
      if (target) {
        const remaining = allWidgets.filter((w) => w.id !== id);
        for (const messageName of sendableMessages(target)) {
          const stillBound = remaining.some((w) => sendableMessages(w).includes(messageName));
          if (!stillBound) api.txAutoStop(messageName).catch(() => {});
        }
      }
      setPages((ps) =>
        ps.map((p) => ({
          ...p,
          widgets: p.widgets.filter((x) => x.id !== id),
          layout: p.layout.filter((x) => x.i !== id),
        })),
      );
    },
    [pages],
  );

  const ctx = useMemo(
    () => ({ dbc, editMode, updateWidget, removeWidget, refreshDbc }),
    [dbc, editMode, updateWidget, removeWidget, refreshDbc],
  );

  // Resize limits (minW/minH) always come live from the registry rather than
  // whatever was baked into a layout item at creation time or loaded from an
  // older saved layout — so tightening/loosening a widget's limits in
  // registry.tsx takes effect immediately for existing widgets too.
  const effectiveLayout = useMemo(
    () =>
      activePage.layout.map((item) => {
        const widget = activePage.widgets.find((w) => w.id === item.i);
        if (!widget) return item;
        const { minW, minH } = WIDGET_REGISTRY[widget.type].defaultSize;
        return { ...item, minW, minH };
      }),
    [activePage.layout, activePage.widgets],
  );

  // ---- layout persistence (all pages) ---------------------------------------

  const saveLayout = async () => {
    try {
      const [dbcRaw, funcRaw, scriptRaw] = await Promise.all([
        api.getDbcRaw().catch(() => null),
        api.getFunctionScriptRaw().catch(() => null),
        api.getTestScriptRaw().catch(() => null),
      ]);
      const body: SavedLayout = { pages, canConfig };
      if (dbcRaw && 'content' in dbcRaw) body.dbc = dbcRaw;
      if (funcRaw && 'content' in funcRaw) body.functionScript = funcRaw;
      if (scriptRaw && 'content' in scriptRaw) body.scenarioScript = scriptRaw;
      await api.saveLayout(layoutName, body);
      const r = await api.listLayouts();
      setLayoutList(r.layouts);
      const parts = [
        body.dbc ? 'DBC' : null,
        body.functionScript ? 'Function Script' : null,
        body.scenarioScript ? 'Test Sequence' : null,
      ].filter(Boolean);
      notify(`레이아웃 "${layoutName}" 저장됨${parts.length > 0 ? ` (${parts.join(', ')} 포함)` : ''}`);
    } catch (e) {
      notify(`저장 실패: ${(e as Error).message}`);
    }
  };

  const loadLayout = async (name: string) => {
    try {
      const saved = (await api.getLayout(name)) as Partial<SavedLayout> & Partial<LegacySavedLayout>;
      if (saved.dbc?.content) {
        try {
          await api.uploadDbc(new File([saved.dbc.content], saved.dbc.filename, { type: 'text/plain' }));
        } catch (e) {
          notify(`DBC 복원 실패: ${(e as Error).message}`);
        }
      }
      if (saved.functionScript?.content) {
        try {
          await api.uploadFunctionScript(
            new File([saved.functionScript.content], saved.functionScript.filename, {
              type: 'application/json',
            }),
          );
        } catch (e) {
          notify(`Function Script 복원 실패: ${(e as Error).message}`);
        }
      }
      if (saved.scenarioScript?.content) {
        try {
          await api.uploadTestScript(
            new File([saved.scenarioScript.content], saved.scenarioScript.filename, {
              type: 'application/json',
            }),
          );
        } catch (e) {
          notify(`Test Sequence 복원 실패: ${(e as Error).message}`);
        }
      }
      refreshDbc();
      // CAN 설정값만 복원하고 실제 연결은 자동으로 하지 않는다 -- 연결은 사용자가
      // "연결" 버튼으로 직접 트리거해야 하는 부수효과 있는 동작이다.
      if (saved.canConfig) setCanConfig(saved.canConfig);
      // legacy saves (before multi-page tabs) have no `pages` key -- wrap
      // their single layout/widgets pair into one page
      const loadedPages: Page[] =
        saved.pages && saved.pages.length > 0
          ? saved.pages
          : [{ ...makePage('p1', 'Page 1'), widgets: saved.widgets ?? [], layout: saved.layout ?? [] }];
      setPages(loadedPages);
      setActivePageId(loadedPages[0].id);
      setActiveId(null);
      setLayoutName(name);
      notify(`레이아웃 "${name}" 불러옴`);
    } catch (e) {
      notify(`불러오기 실패: ${(e as Error).message}`);
    }
  };

  // 편집 환경(페이지/위젯/CAN 설정)만 빈 상태로 초기화한다 -- 업로드된
  // DBC/Function Script/Test Sequence는 별도 파일이므로 건드리지 않는다.
  const newFile = () => {
    if (!window.confirm('현재 편집 화면을 초기화하고 새로 시작할까요? 저장하지 않은 변경사항은 사라집니다.')) {
      return;
    }
    setPages([makePage('p1', 'Page 1')]);
    setActivePageId('p1');
    setActiveId(null);
    setLayoutName('');
    setCanConfig(DEFAULT_CAN_CONFIG);
    notify('새 편집 화면으로 초기화되었습니다');
  };

  // auto-arrange: tile (바둑판) keeps sizes and packs rows left→right,
  // cascade (계단식) staggers widgets diagonally
  const arrange = (mode: 'tile' | 'cascade') => {
    updateActivePage((p) => {
      let newLayout: LayoutItem[];
      if (mode === 'cascade') {
        newLayout = p.layout.map((it, n) => ({ ...it, x: Math.min(n, Math.max(0, 12 - it.w)), y: n }));
      } else {
        let x = 0;
        let y = 0;
        let rowH = 0;
        newLayout = p.layout.map((it) => {
          if (x + it.w > 12) {
            x = 0;
            y += rowH;
            rowH = 0;
          }
          const placed = { ...it, x, y };
          x += it.w;
          rowH = Math.max(rowH, it.h);
          return placed;
        });
      }
      return { ...p, layout: newLayout };
    });
  };

  // ---- page (tab) management -------------------------------------------------

  const addPage = () => {
    const id = `p${Date.now()}`;
    setPages((ps) => [...ps, makePage(id, `Page ${ps.length + 1}`)]);
    setActivePageId(id);
    setActiveId(null);
  };

  const renamePage = (id: string, name: string) => {
    setPages((ps) => ps.map((p) => (p.id === id ? { ...p, name } : p)));
  };

  const removePage = (id: string) => {
    setPages((ps) => (ps.length <= 1 ? ps : ps.filter((p) => p.id !== id)));
  };

  const switchPage = (id: string) => {
    setActivePageId(id);
    setActiveId(null);
  };

  // ---- render ----------------------------------------------------------------

  return (
    <AppContext.Provider value={ctx}>
      <div className="app">
        <TopBar
          dbc={dbc}
          refreshDbc={refreshDbc}
          editMode={editMode}
          setEditMode={setEditMode}
          addWidget={addWidget}
          arrange={arrange}
          layoutName={layoutName}
          setLayoutName={setLayoutName}
          layoutList={layoutList}
          newFile={newFile}
          saveLayout={saveLayout}
          loadLayout={loadLayout}
          openSettings={() => setShowSettings(true)}
          notify={notify}
          canConfig={canConfig}
          setCanConfig={setCanConfig}
        />
        <PageTabs
          pages={pages}
          activePageId={activePageId}
          editMode={editMode}
          onSwitch={switchPage}
          onAdd={addPage}
          onRename={renamePage}
          onRemove={removePage}
        />
        {banner && <div className="banner">{banner}</div>}
        <div className="canvas" ref={containerRef}>
          {mounted && (
            <GridLayout
              width={width}
              layout={effectiveLayout}
              gridConfig={{ cols: 12, rowHeight: 60, margin: [8, 8] }}
              compactor={freeCompactor}
              dragConfig={{ enabled: true, handle: '.drag-handle' }}
              resizeConfig={{ enabled: true, handles: ['se', 'e', 's'] }}
              onLayoutChange={(l: Layout) => updateActivePage((p) => ({ ...p, layout: [...l] }))}
            >
              {activePage.widgets.map((w) => {
                const Comp = WIDGET_REGISTRY[w.type].component;
                return (
                  <div
                    key={w.id}
                    style={activeId === w.id ? { zIndex: 10 } : undefined}
                    onMouseDownCapture={() => setActiveId(w.id)}
                  >
                    <WidgetFrame config={w}>
                      <Comp config={w} />
                    </WidgetFrame>
                  </div>
                );
              })}
            </GridLayout>
          )}
          {activePage.widgets.length === 0 && (
            <div className="empty-canvas">
              상단의 "+ 위젯 추가"에서 컴포넌트를 배치해 GUI를 구성하세요.
            </div>
          )}
        </div>
        <StatusBar />
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </div>
    </AppContext.Provider>
  );
}

// ---- page tabs -----------------------------------------------------------------

interface PageTabsProps {
  pages: Page[];
  activePageId: string;
  editMode: boolean;
  onSwitch: (id: string) => void;
  onAdd: () => void;
  onRename: (id: string, name: string) => void;
  onRemove: (id: string) => void;
}

function PageTabs({ pages, activePageId, editMode, onSwitch, onAdd, onRename, onRemove }: PageTabsProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const startRename = (p: Page) => {
    setEditingId(p.id);
    setDraft(p.name);
  };

  const commitRename = (id: string) => {
    const name = draft.trim();
    if (name) onRename(id, name);
    setEditingId(null);
  };

  return (
    <div className="page-tabs">
      {pages.map((p) => (
        <div key={p.id} className={`page-tab ${p.id === activePageId ? 'page-tab-active' : ''}`}>
          {editingId === p.id ? (
            <input
              className="page-tab-rename"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => commitRename(p.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename(p.id);
                if (e.key === 'Escape') setEditingId(null);
              }}
            />
          ) : (
            <button className="page-tab-label" onClick={() => onSwitch(p.id)}>
              {p.name}
            </button>
          )}
          {editMode && editingId !== p.id && (
            <span className="page-tab-actions">
              <button className="icon-btn" title="이름 변경" onClick={() => startRename(p)}>
                ✎
              </button>
              {pages.length > 1 && (
                <button className="icon-btn" title="페이지 삭제" onClick={() => onRemove(p.id)}>
                  ✕
                </button>
              )}
            </span>
          )}
        </div>
      ))}
      {editMode && (
        <button className="small-btn page-tab-add" onClick={onAdd}>
          + 페이지
        </button>
      )}
    </div>
  );
}

// ---- top bar -----------------------------------------------------------------

interface TopBarProps {
  dbc: DbcSummary;
  refreshDbc: () => void;
  editMode: boolean;
  setEditMode: (v: boolean) => void;
  addWidget: (t: WidgetType) => void;
  arrange: (mode: 'tile' | 'cascade') => void;
  layoutName: string;
  setLayoutName: (v: string) => void;
  layoutList: string[];
  newFile: () => void;
  saveLayout: () => void;
  loadLayout: (name: string) => void;
  openSettings: () => void;
  notify: (text: string) => void;
  canConfig: CanConfig;
  setCanConfig: (c: CanConfig) => void;
}

function TopBar(props: TopBarProps) {
  useCanVersion();
  const { iface, channel, bitrate, fd, dataBitrate } = props.canConfig;
  const setIface = (v: string) => props.setCanConfig({ ...props.canConfig, iface: v });
  const setChannel = (v: string) => props.setCanConfig({ ...props.canConfig, channel: v });
  const setBitrate = (v: number) => props.setCanConfig({ ...props.canConfig, bitrate: v });
  const setFd = (v: boolean) => props.setCanConfig({ ...props.canConfig, fd: v });
  const setDataBitrate = (v: number) => props.setCanConfig({ ...props.canConfig, dataBitrate: v });
  const [showMore, setShowMore] = useState(false);
  const moreRef = useRef<HTMLSpanElement>(null);
  const connected = canStore.status?.can.connected ?? false;

  // "더보기" 드롭다운 바깥을 클릭하면 닫는다
  useEffect(() => {
    if (!showMore) return;
    const onMouseDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false);
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [showMore]);

  const connect = async () => {
    try {
      await api.connect(iface, channel, bitrate, fd, dataBitrate);
      props.notify(`${iface}:${channel} 연결됨${fd ? ' (CAN-FD)' : ''}`);
    } catch (e) {
      props.notify(`연결 실패: ${(e as Error).message}`);
    }
  };

  const uploadDbc = async (file: File) => {
    try {
      await api.uploadDbc(file);
      props.refreshDbc();
      props.notify(`DBC 로드됨: ${file.name}`);
    } catch (e) {
      props.notify(`DBC 오류: ${(e as Error).message}`);
    }
  };

  const functions = canStore.status?.test_runner.functions;
  const uploadFunctions = async (file: File) => {
    try {
      await api.uploadFunctionScript(file);
      props.notify(`Function Script 로드됨: ${file.name}`);
    } catch (e) {
      props.notify(`Function Script 오류: ${(e as Error).message}`);
    }
  };

  const running = canStore.status?.run?.running ?? false;
  const toggleRun = async () => {
    try {
      if (running) await api.runStop();
      else await api.runStart();
    } catch (e) {
      props.notify((e as Error).message);
    }
  };

  const periodicOn = (canStore.status?.tx.auto_entries.length ?? 0) > 0;
  const toggleEnableMsg = async () => {
    try {
      if (periodicOn) {
        await api.txAutoStop();
        props.notify('Periodic 메시지 주기 송신 중지');
      } else {
        const r = await api.enableAllPeriodic(canStore.getRxNode());
        props.notify(`Periodic 메시지 ${r.armed.length}개 주기 송신 시작`);
      }
    } catch (e) {
      props.notify((e as Error).message);
    }
  };

  return (
    <header className="topbar">
      <span className="logo">CAN Simulator</span>
      <button
        className={`small-btn ${running ? 'danger' : 'primary'}`}
        disabled={props.editMode}
        title={props.editMode ? '편집 모드에서는 송수신이 정지됩니다' : '전체 메시지 송수신 Start/Stop'}
        onClick={toggleRun}
      >
        {running ? '■ Stop' : '▶ Start'}
      </button>
      <button
        className={`small-btn ${periodicOn ? 'danger' : 'primary'}`}
        disabled={props.editMode || !running || !props.dbc.loaded}
        title="DBC의 Periodic Tx 메시지 전체를 각자의 cycle time으로 주기 송신 시작/중지 (기본값으로 시작, 이후 위젯에서 보낸 값으로 계속 전송)"
        onClick={toggleEnableMsg}
      >
        {periodicOn ? '■ Enable Msg' : '▶ Enable Msg'}
      </button>

      <span className="group">
        <select
          value=""
          onChange={(e) => e.target.value && props.addWidget(e.target.value as WidgetType)}
        >
          <option value="">+ 위젯 추가</option>
          {Object.entries(WIDGET_REGISTRY).map(([type, meta]) => (
            <option key={type} value={type}>
              {meta.label}
            </option>
          ))}
        </select>
        <label className="toggle">
          <input
            type="checkbox"
            checked={props.editMode}
            onChange={(e) => props.setEditMode(e.target.checked)}
          />
          편집 모드
        </label>
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) props.arrange(e.target.value as 'tile' | 'cascade');
          }}
        >
          <option value="">자동 정렬…</option>
          <option value="tile">바둑판 정렬</option>
          <option value="cascade">계단식 정렬</option>
        </select>
      </span>

      <span className="spacer" />

      <span className="topbar-more" ref={moreRef}>
        <button className="small-btn" onClick={() => setShowMore((v) => !v)}>
          ⋯ 더보기
        </button>
        {showMore && (
          <div className="topbar-more-panel">
            <div className="topbar-more-section">
              <div className="topbar-more-heading">CAN 설정</div>
              <div className="topbar-more-row">
                <select value={iface} onChange={(e) => setIface(e.target.value)} disabled={connected}>
                  <option value="virtual">Virtual</option>
                  <option value="pcan">PCAN</option>
                  <option value="vector">Vector (CANcase)</option>
                </select>
                <input
                  className="channel-input"
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  disabled={connected}
                  placeholder="channel"
                />
                <select
                  value={bitrate}
                  onChange={(e) => setBitrate(Number(e.target.value))}
                  disabled={connected || iface === 'virtual'}
                >
                  {[125000, 250000, 500000, 1000000].map((b) => (
                    <option key={b} value={b}>
                      {b / 1000} kbit/s
                    </option>
                  ))}
                </select>
              </div>
              <div className="topbar-more-row">
                <label
                  className="toggle fd-toggle"
                  title="CAN-FD 활성화 (최대 64바이트 페이로드 + 데이터 위상 고속 전송)"
                >
                  <input
                    type="checkbox"
                    checked={fd}
                    disabled={connected}
                    onChange={(e) => setFd(e.target.checked)}
                  />
                  FD
                </label>
                {fd && (
                  <select
                    value={dataBitrate}
                    onChange={(e) => setDataBitrate(Number(e.target.value))}
                    disabled={connected}
                    title="CAN-FD 데이터 위상 비트레이트"
                  >
                    {[1000000, 2000000, 4000000, 5000000, 8000000].map((b) => (
                      <option key={b} value={b}>
                        data {b / 1_000_000}Mbit/s
                      </option>
                    ))}
                  </select>
                )}
                {!connected ? (
                  <button className="small-btn primary" onClick={connect}>
                    연결
                  </button>
                ) : (
                  <button className="small-btn danger" onClick={() => api.disconnect()}>
                    해제
                  </button>
                )}
              </div>
            </div>

            <div className="topbar-more-section">
              <div className="topbar-more-heading">DBC 업로드</div>
              <div className="topbar-more-row">
                <label className="small-btn file-btn">
                  DBC 업로드
                  <input
                    type="file"
                    accept=".dbc"
                    hidden
                    onChange={(e) => e.target.files?.[0] && uploadDbc(e.target.files[0])}
                  />
                </label>
                <span className="hint">{props.dbc.loaded ? props.dbc.filename : 'DBC 없음'}</span>
              </div>
              {props.dbc.loaded && props.dbc.nodes && props.dbc.nodes.length > 0 && (
                <div className="topbar-more-row">
                  <select
                    value={canStore.getRxNode()}
                    onChange={(e) => canStore.setRxNode(e.target.value)}
                    title="실제 DUT(실기) 노드를 선택하세요. 이 노드가 보내는 메시지는 신호 선택 목록에서 RX(시뮬레이터가 수신)로, 나머지는 TX(시뮬레이터가 대신 송신)로 분류됩니다"
                  >
                    <option value="">RX 노드 미설정</option>
                    {props.dbc.nodes.map((n) => (
                      <option key={n} value={n}>
                        RX 노드: {n}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            <div className="topbar-more-section">
              <div className="topbar-more-heading">Function Script</div>
              <div className="topbar-more-row">
                <label className="small-btn file-btn">
                  Function Script
                  <input
                    type="file"
                    accept=".json"
                    hidden
                    onChange={(e) => e.target.files?.[0] && uploadFunctions(e.target.files[0])}
                  />
                </label>
                <span className="hint">
                  {functions?.loaded
                    ? `${functions.filename} — ${functions.names.length}개 기능`
                    : '로드 안 됨'}
                </span>
              </div>
            </div>

            <div className="topbar-more-section">
              <div className="topbar-more-heading">설정저장/불러오기</div>
              <div className="topbar-more-row">
                <input
                  className="layout-input"
                  value={props.layoutName}
                  onChange={(e) => props.setLayoutName(e.target.value)}
                  placeholder="레이아웃 이름"
                />
                <button className="small-btn" onClick={props.saveLayout}>
                  저장
                </button>
                <select
                  value=""
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === '__new__') props.newFile();
                    else if (v) props.loadLayout(v);
                  }}
                >
                  <option value="">불러오기…</option>
                  <option value="__new__">새 파일</option>
                  {props.layoutList.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}
      </span>

      <button className="small-btn" onClick={props.openSettings}>
        ⚙ 설정
      </button>
    </header>
  );
}

// ---- status bar / settings ---------------------------------------------------

function StatusBar() {
  useCanVersion();
  const status = canStore.status;
  return (
    <footer className="statusbar">
      <span className={canStore.wsConnected ? 'ok' : 'bad'}>
        서버 {canStore.wsConnected ? '연결됨' : '끊김(재시도 중)'}
      </span>
      <span className={status?.can.connected ? 'ok' : ''}>
        CAN{' '}
        {status?.can.connected
          ? `${String(status.can.config.interface)}:${String(status.can.config.channel)}${
              status.can.config.fd ? ' [FD]' : ''
            }`
          : '미연결'}
      </span>
      <span>RX {status?.can.counters.rx ?? 0}</span>
      <span>TX {status?.can.counters.tx ?? 0}</span>
      <span className={status?.run?.running ? 'ok' : 'bad'}>
        송수신 {status?.run?.running ? 'RUN' : 'STOP'}
      </span>
      <span>
        주기송신 {status?.tx.running ? 'ON' : 'OFF'}
        {status && status.tx.auto_entries.length > 0
          ? ` (+auto ${status.tx.auto_entries.length})`
          : ''}
      </span>
      <span className="spacer" />
      <span>UI {canStore.getFps()} fps</span>
    </footer>
  );
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  const [fps, setFps] = useState(canStore.getFps());
  const [flushMs, setFlushMs] = useState(canStore.status?.settings.ws_flush_ms ?? 30);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>설정</h3>
        <label>
          UI 갱신 속도: {fps} fps (10~60, requestAnimationFrame throttle)
          <input
            type="range"
            min={10}
            max={60}
            value={fps}
            onChange={(e) => setFps(Number(e.target.value))}
          />
        </label>
        <label>
          서버 전송 묶음 주기: {flushMs} ms
          <input
            type="range"
            min={10}
            max={200}
            step={10}
            value={flushMs}
            onChange={(e) => setFlushMs(Number(e.target.value))}
          />
        </label>
        <div className="modal-buttons">
          <button
            onClick={async () => {
              canStore.setFps(fps);
              try {
                await api.updateSettings(flushMs);
              } catch {
                /* backend offline; fps still applies */
              }
              onClose();
            }}
          >
            적용
          </button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>
  );
}
