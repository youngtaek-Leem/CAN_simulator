// CAN evaluation environment - main app shell.
//
// Top bar: bus connection, DBC upload, layout save/load, settings, edit mode.
// Canvas: react-grid-layout based free-form GUI builder; widgets can always be
// dragged (via the title bar) and resized, regardless of edit mode. Edit mode
// only gates the per-widget config/remove buttons and stops TX/RX.

import { useCallback, useEffect, useMemo, useState } from 'react';
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

interface SavedLayout {
  layout: LayoutItem[];
  widgets: WidgetConfig[];
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
  const [widgets, setWidgets] = useState<WidgetConfig[]>([]);
  const [layout, setLayout] = useState<LayoutItem[]>([]);
  const [layoutName, setLayoutName] = useState('default');
  const [layoutList, setLayoutList] = useState<string[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  const { width, containerRef, mounted } = useContainerWidth();

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

  // ---- widget management -------------------------------------------------

  const addWidget = (type: WidgetType) => {
    const meta = WIDGET_REGISTRY[type];
    const id = `w${Date.now()}`;
    setWidgets((w) => [...w, { id, type, title: meta.label, options: {} }]);
    // cascade new widgets from the top-left (no auto-compaction)
    setLayout((l) => {
      const n = l.length;
      return [...l, { i: id, x: (n % 6) * 2, y: n, ...meta.defaultSize }];
    });
  };

  const updateWidget = useCallback((cfg: WidgetConfig) => {
    setWidgets((w) => w.map((x) => (x.id === cfg.id ? cfg : x)));
  }, []);

  const removeWidget = useCallback(
    (id: string) => {
      const target = widgets.find((w) => w.id === id);
      if (target) {
        const remaining = widgets.filter((w) => w.id !== id);
        for (const messageName of sendableMessages(target)) {
          const stillBound = remaining.some((w) => sendableMessages(w).includes(messageName));
          if (!stillBound) api.txAutoStop(messageName).catch(() => {});
        }
      }
      setWidgets((w) => w.filter((x) => x.id !== id));
      setLayout((l) => l.filter((x) => x.i !== id));
    },
    [widgets],
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
      layout.map((item) => {
        const widget = widgets.find((w) => w.id === item.i);
        if (!widget) return item;
        const { minW, minH } = WIDGET_REGISTRY[widget.type].defaultSize;
        return { ...item, minW, minH };
      }),
    [layout, widgets],
  );

  // ---- layout persistence --------------------------------------------------

  const saveLayout = async () => {
    try {
      await api.saveLayout(layoutName, { layout, widgets } satisfies SavedLayout);
      const r = await api.listLayouts();
      setLayoutList(r.layouts);
      notify(`레이아웃 "${layoutName}" 저장됨`);
    } catch (e) {
      notify(`저장 실패: ${(e as Error).message}`);
    }
  };

  const loadLayout = async (name: string) => {
    try {
      const saved = (await api.getLayout(name)) as SavedLayout;
      setWidgets(saved.widgets ?? []);
      setLayout(saved.layout ?? []);
      setLayoutName(name);
      notify(`레이아웃 "${name}" 불러옴`);
    } catch (e) {
      notify(`불러오기 실패: ${(e as Error).message}`);
    }
  };

  // auto-arrange: tile (바둑판) keeps sizes and packs rows left→right,
  // cascade (계단식) staggers widgets diagonally
  const arrange = (mode: 'tile' | 'cascade') => {
    setLayout((l) => {
      if (mode === 'cascade') {
        return l.map((it, n) => ({ ...it, x: Math.min(n, Math.max(0, 12 - it.w)), y: n }));
      }
      let x = 0;
      let y = 0;
      let rowH = 0;
      return l.map((it) => {
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
    });
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
          saveLayout={saveLayout}
          loadLayout={loadLayout}
          openSettings={() => setShowSettings(true)}
          notify={notify}
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
              onLayoutChange={(l: Layout) => setLayout([...l])}
            >
              {widgets.map((w) => {
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
          {widgets.length === 0 && (
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
  saveLayout: () => void;
  loadLayout: (name: string) => void;
  openSettings: () => void;
  notify: (text: string) => void;
}

function TopBar(props: TopBarProps) {
  useCanVersion();
  const [iface, setIface] = useState('virtual');
  const [channel, setChannel] = useState('ch0');
  const [bitrate, setBitrate] = useState(500000);
  const [fd, setFd] = useState(false);
  const [dataBitrate, setDataBitrate] = useState(2_000_000);
  const connected = canStore.status?.can.connected ?? false;

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
      props.notify(`함수 마스터 스크립트 로드됨: ${file.name}`);
    } catch (e) {
      props.notify(`함수 스크립트 오류: ${(e as Error).message}`);
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

      <span className="group">
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
        <label className="toggle fd-toggle" title="CAN-FD 활성화 (최대 64바이트 페이로드 + 데이터 위상 고속 전송)">
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
      </span>

      <span className="group">
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
        {props.dbc.loaded && props.dbc.nodes && props.dbc.nodes.length > 0 && (
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
        )}
      </span>

      <span className="group">
        <label className="small-btn file-btn">
          함수 마스터 스크립트
          <input
            type="file"
            accept=".json"
            hidden
            onChange={(e) => e.target.files?.[0] && uploadFunctions(e.target.files[0])}
          />
        </label>
        <span className="hint">
          {functions?.loaded ? `${functions.filename} — ${functions.names.length}개 기능` : '로드 안 됨'}
        </span>
      </span>

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

      <span className="group">
        <input
          className="layout-input"
          value={props.layoutName}
          onChange={(e) => props.setLayoutName(e.target.value)}
          placeholder="레이아웃 이름"
        />
        <button className="small-btn" onClick={props.saveLayout}>
          저장
        </button>
        <select value="" onChange={(e) => e.target.value && props.loadLayout(e.target.value)}>
          <option value="">불러오기…</option>
          {props.layoutList.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </span>

      <span className="spacer" />
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
