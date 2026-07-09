// Common widget chrome: title bar (always a drag handle, position/size can
// be changed regardless of edit mode), config and remove buttons shown only
// in edit mode, and a config modal with signal binding + per-type options.

import { useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { findSignal, useApp } from '../store/appContext';
import { canStore } from '../store/canStore';
import { MessageFilter, MessageOptions, type MessageFilterMode } from './MessageOptions';
import type { WidgetConfig } from '../types';

const BINDABLE = new Set(['textDisplay', 'button', 'checkbox', 'dropdown', 'slider']);

export function WidgetFrame({ config, children }: { config: WidgetConfig; children: ReactNode }) {
  const { editMode, removeWidget } = useApp();
  const [showConfig, setShowConfig] = useState(false);
  return (
    <div className="widget">
      <div className="widget-titlebar drag-handle">
        <span className="widget-title">{config.title}</span>
        {editMode && (
          <span className="widget-actions">
            <button className="icon-btn" title="설정" onClick={() => setShowConfig(true)}>
              ⚙
            </button>
            <button className="icon-btn" title="삭제" onClick={() => removeWidget(config.id)}>
              ✕
            </button>
          </span>
        )}
      </div>
      <div className="widget-body">{children}</div>
      {showConfig && <ConfigModal config={config} onClose={() => setShowConfig(false)} />}
    </div>
  );
}

function ConfigModal({ config, onClose }: { config: WidgetConfig; onClose: () => void }) {
  const { dbc, updateWidget, refreshDbc } = useApp();
  const [draft, setDraft] = useState<WidgetConfig>({
    ...config,
    options: { ...config.options },
  });
  const bindable = BINDABLE.has(config.type);
  const message = dbc.messages?.find((m) => m.name === draft.binding?.message);
  const bound = findSignal(dbc, draft.binding);
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');

  const setOption = (key: string, value: unknown) =>
    setDraft((d) => ({ ...d, options: { ...d.options, [key]: value } }));

  const save = () => {
    updateWidget(draft);
    onClose();
  };

  // portal to <body>: grid items create transform stacking contexts, so an
  // in-place fixed modal can end up behind neighbouring widgets
  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>위젯 설정 — {config.type}</h3>
        <label>
          제목
          <input
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        </label>

        {bindable && (
          <>
            {!dbc.loaded && <p className="hint">신호 할당을 하려면 먼저 DBC를 업로드하세요.</p>}
            {dbc.loaded && (
              <>
                <label>
                  CAN 메시지
                  <span className="select-with-filter">
                    <select
                      value={draft.binding?.message ?? ''}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          binding: e.target.value
                            ? { message: e.target.value, signal: '' }
                            : undefined,
                        })
                      }
                    >
                      <option value="">— 선택 —</option>
                      <MessageOptions
                        dbc={dbc}
                        rxNode={canStore.getRxNode()}
                        filter={msgFilter}
                        labelFor={(m) => `${m.name} (0x${m.frame_id.toString(16).toUpperCase()})`}
                      />
                    </select>
                    <MessageFilter value={msgFilter} onChange={setMsgFilter} />
                  </span>
                </label>
                {message && (
                  <label>
                    신호
                    <select
                      value={draft.binding?.signal ?? ''}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          binding: { message: message.name, signal: e.target.value },
                        })
                      }
                    >
                      <option value="">— 선택 —</option>
                      {message.signals.map((s) => (
                        <option key={s.name} value={s.name}>
                          {s.name} ({s.length}bit, {s.send_type})
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {bound && (
                  <label>
                    송신 속성 (Event: 30ms 후 invalid 값 자동 송신)
                    <select
                      value={bound.signal.send_type}
                      onChange={async (e) => {
                        await api.overrideSendType(
                          bound.message.name,
                          bound.signal.name,
                          e.target.value,
                        );
                        refreshDbc();
                      }}
                    >
                      <option value="periodic">Periodic</option>
                      <option value="event">Event</option>
                    </select>
                  </label>
                )}
              </>
            )}
          </>
        )}

        {config.type === 'button' && (
          <label>
            전송 값
            <input
              type="number"
              value={String(draft.options.value ?? 1)}
              onChange={(e) => setOption('value', Number(e.target.value))}
            />
          </label>
        )}
        {config.type === 'checkbox' && (
          <div className="row-2">
            <label>
              ON 값
              <input
                type="number"
                value={String(draft.options.onValue ?? 1)}
                onChange={(e) => setOption('onValue', Number(e.target.value))}
              />
            </label>
            <label>
              OFF 값
              <input
                type="number"
                value={String(draft.options.offValue ?? 0)}
                onChange={(e) => setOption('offValue', Number(e.target.value))}
              />
            </label>
          </div>
        )}
        {(config.type === 'multiButton' || config.type === 'multiCheckbox') && (
          <div className="row-2">
            <label>
              가로 개수(열)
              <input
                type="number"
                min={1}
                max={10}
                value={String(draft.options.cols ?? 4)}
                onChange={(e) => setOption('cols', Number(e.target.value))}
              />
            </label>
            <label>
              세로 개수(행)
              <input
                type="number"
                min={1}
                max={10}
                value={String(draft.options.rows ?? 3)}
                onChange={(e) => setOption('rows', Number(e.target.value))}
              />
            </label>
          </div>
        )}
        {config.type === 'slider' && (
          <div className="row-2">
            <label>
              최소
              <input
                type="number"
                value={String(draft.options.min ?? bound?.signal.minimum ?? 0)}
                onChange={(e) => setOption('min', Number(e.target.value))}
              />
            </label>
            <label>
              최대
              <input
                type="number"
                value={String(draft.options.max ?? bound?.signal.maximum ?? 100)}
                onChange={(e) => setOption('max', Number(e.target.value))}
              />
            </label>
            <label>
              간격
              <input
                type="number"
                value={String(draft.options.step ?? 1)}
                onChange={(e) => setOption('step', Number(e.target.value))}
              />
            </label>
          </div>
        )}

        <div className="modal-buttons">
          <button onClick={save}>저장</button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
