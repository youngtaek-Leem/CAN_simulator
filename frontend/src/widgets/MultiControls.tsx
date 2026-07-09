// Multi-button / multi-checkbox widgets: an NxM grid of individually
// bindable buttons or checkboxes inside a single widget. Grid size (rows x
// cols) is set in the widget's own config modal (WidgetFrame.tsx); each cell
// is bound to its own CAN signal via a small per-cell edit popup shown only
// in edit mode.

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import { canStore } from '../store/canStore';
import { MessageFilter, MessageOptions, type MessageFilterMode } from './MessageOptions';
import type { MultiCell, WidgetConfig } from '../types';

function getGrid(config: WidgetConfig): { rows: number; cols: number; cells: MultiCell[] } {
  const rows = Math.max(1, Math.min(10, Number(config.options.rows) || 3));
  const cols = Math.max(1, Math.min(10, Number(config.options.cols) || 4));
  const cells = (config.options.cells as MultiCell[] | undefined) ?? [];
  return { rows, cols, cells };
}

function useCellUpdater(config: WidgetConfig) {
  const { updateWidget } = useApp();
  return (index: number, patch: MultiCell) => {
    const { cells } = getGrid(config);
    const next = [...cells];
    while (next.length <= index) next.push({});
    next[index] = patch;
    updateWidget({ ...config, options: { ...config.options, cells: next } });
  };
}

export function MultiButtonWidget({ config }: { config: WidgetConfig }) {
  const { editMode } = useApp();
  const { rows, cols, cells } = getGrid(config);
  const updateCell = useCellUpdater(config);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async (cell: MultiCell) => {
    if (!cell.binding?.signal) return;
    try {
      await api.txSignal(cell.binding.message, { [cell.binding.signal]: cell.value ?? 1 });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="multi-widget control-widget">
      <div
        className="multi-grid"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${rows}, 1fr)` }}
      >
        {Array.from({ length: rows * cols }, (_, i) => {
          const cell = cells[i] ?? {};
          const label = cell.label || cell.binding?.signal || `#${i + 1}`;
          return (
            <div className="multi-cell" key={i}>
              <button
                className="big-btn multi-cell-btn"
                disabled={!cell.binding?.signal}
                title={cell.binding?.signal ? `${cell.binding.message}.${cell.binding.signal} = ${cell.value ?? 1}` : '신호 미할당'}
                onClick={() => send(cell)}
                onKeyDown={(e) => {
                  if (e.key === ' ' || e.key === 'Enter') {
                    e.preventDefault();
                    send(cell);
                  }
                }}
              >
                {label}
              </button>
              {editMode && (
                <button
                  className="icon-btn multi-cell-edit"
                  title="셀 설정"
                  onClick={() => setEditingIndex(i)}
                >
                  ⚙
                </button>
              )}
            </div>
          );
        })}
      </div>
      {error && <span className="error">{error}</span>}
      {editingIndex !== null && (
        <CellEditModal
          kind="button"
          cell={cells[editingIndex] ?? {}}
          onSave={(patch) => {
            updateCell(editingIndex, patch);
            setEditingIndex(null);
          }}
          onClose={() => setEditingIndex(null)}
        />
      )}
    </div>
  );
}

export function MultiCheckboxWidget({ config }: { config: WidgetConfig }) {
  const { editMode } = useApp();
  const { rows, cols, cells } = getGrid(config);
  const updateCell = useCellUpdater(config);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [checkedState, setCheckedState] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const toggle = async (i: number, cell: MultiCell, checked: boolean) => {
    setCheckedState((s) => ({ ...s, [i]: checked }));
    if (!cell.binding?.signal) return;
    const value = checked ? (cell.onValue ?? 1) : (cell.offValue ?? 0);
    try {
      await api.txSignal(cell.binding.message, { [cell.binding.signal]: value });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="multi-widget control-widget">
      <div
        className="multi-grid"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${rows}, 1fr)` }}
      >
        {Array.from({ length: rows * cols }, (_, i) => {
          const cell = cells[i] ?? {};
          const label = cell.label || cell.binding?.signal || `#${i + 1}`;
          return (
            <div className="multi-cell" key={i}>
              <label className="check-label multi-cell-check">
                <input
                  type="checkbox"
                  checked={checkedState[i] ?? false}
                  disabled={!cell.binding?.signal}
                  onChange={(e) => toggle(i, cell, e.target.checked)}
                  onKeyDown={(e) => {
                    if (e.key === ' ') {
                      e.preventDefault();
                      toggle(i, cell, !(checkedState[i] ?? false));
                    }
                  }}
                />
                {label}
              </label>
              {editMode && (
                <button
                  className="icon-btn multi-cell-edit"
                  title="셀 설정"
                  onClick={() => setEditingIndex(i)}
                >
                  ⚙
                </button>
              )}
            </div>
          );
        })}
      </div>
      {error && <span className="error">{error}</span>}
      {editingIndex !== null && (
        <CellEditModal
          kind="checkbox"
          cell={cells[editingIndex] ?? {}}
          onSave={(patch) => {
            updateCell(editingIndex, patch);
            setEditingIndex(null);
          }}
          onClose={() => setEditingIndex(null)}
        />
      )}
    </div>
  );
}

function CellEditModal({
  kind,
  cell,
  onSave,
  onClose,
}: {
  kind: 'button' | 'checkbox';
  cell: MultiCell;
  onSave: (patch: MultiCell) => void;
  onClose: () => void;
}) {
  const { dbc } = useApp();
  const [draft, setDraft] = useState<MultiCell>({ ...cell });
  const message = dbc.messages?.find((m) => m.name === draft.binding?.message);
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>셀 설정 — {kind === 'button' ? '버튼' : '체크박스'}</h3>
        <label>
          라벨 (비우면 신호명 표시)
          <input
            value={draft.label ?? ''}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
        </label>
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
                    setDraft({ ...draft, binding: { message: message.name, signal: e.target.value } })
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
          </>
        )}
        {kind === 'button' && (
          <label>
            전송 값
            <input
              type="number"
              value={String(draft.value ?? 1)}
              onChange={(e) => setDraft({ ...draft, value: Number(e.target.value) })}
            />
          </label>
        )}
        {kind === 'checkbox' && (
          <div className="row-2">
            <label>
              ON 값
              <input
                type="number"
                value={String(draft.onValue ?? 1)}
                onChange={(e) => setDraft({ ...draft, onValue: Number(e.target.value) })}
              />
            </label>
            <label>
              OFF 값
              <input
                type="number"
                value={String(draft.offValue ?? 0)}
                onChange={(e) => setDraft({ ...draft, offValue: Number(e.target.value) })}
              />
            </label>
          </div>
        )}
        <div className="modal-buttons">
          <button onClick={() => onSave(draft)}>저장</button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
