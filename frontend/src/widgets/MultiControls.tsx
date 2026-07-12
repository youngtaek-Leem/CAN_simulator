// Multi-button / multi-checkbox widgets: an NxM grid of individually
// bindable buttons or checkboxes inside a single widget. Grid size (rows x
// cols) is set in the widget's own config modal (WidgetFrame.tsx); each cell
// is bound to its own CAN signal via a small per-cell edit popup shown only
// in edit mode.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { findSignal, signalRawBounds, useApp } from '../store/appContext';
import { canStore, useCanVersion } from '../store/canStore';
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
  const { editMode, dbc } = useApp();
  const { rows, cols, cells } = getGrid(config);
  const updateCell = useCellUpdater(config);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // per-cell toggle state -- only meaningful for cells bound to a Periodic
  // signal, see ButtonWidget's usePeriodicInvalidToggle for the same design
  const [pending, setPending] = useState<Record<number, 'valid' | 'invalid'>>({});
  const [lastSent, setLastSent] = useState<Record<number, 'valid' | 'invalid'>>({});

  const send = async (cell: MultiCell) => {
    if (!cell.binding?.signal) return;
    try {
      await api.txSignal(cell.binding.message, { [cell.binding.signal]: cell.value ?? 1 });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const activate = async (i: number, cell: MultiCell) => {
    if (!cell.binding?.signal) return;
    const isPeriodic = findSignal(dbc, cell.binding)?.signal.send_type === 'periodic';
    if (!isPeriodic) {
      send(cell);
      return;
    }
    const next = pending[i] ?? 'valid';
    if (next === 'invalid') {
      try {
        await api.sendInvalid(cell.binding.message, cell.binding.signal);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    } else {
      await send(cell);
    }
    setLastSent((s) => ({ ...s, [i]: next }));
    setPending((s) => ({ ...s, [i]: next === 'invalid' ? 'valid' : 'invalid' }));
  };

  return (
    <div className="multi-widget control-widget">
      <div
        className="multi-grid"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${rows}, 1fr)` }}
      >
        {Array.from({ length: rows * cols }, (_, i) => {
          const cell = cells[i] ?? {};
          const invalidActive = lastSent[i] === 'invalid';
          const label = cell.label || (invalidActive ? `${cell.binding?.signal} = INVALID` : cell.binding?.signal) || `#${i + 1}`;
          return (
            <div className="multi-cell" key={i}>
              <button
                className="big-btn multi-cell-btn"
                disabled={!cell.binding?.signal}
                title={cell.binding?.signal ? `${cell.binding.message}.${cell.binding.signal} = ${invalidActive ? 'INVALID' : (cell.value ?? 1)}` : '신호 미할당'}
                onClick={() => activate(i, cell)}
                onKeyDown={(e) => {
                  if (e.key === ' ' || e.key === 'Enter') {
                    e.preventDefault();
                    activate(i, cell);
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

// Grid of buttons that each trigger one FUNC block from the loaded function
// master script (see FunctionButtonWidget.tsx for the single-button version
// and its design notes -- same shared test_runner_service engine, same
// "실행 중" gray highlight, no per-cell result display).
export function FunctionMultiButtonWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { editMode } = useApp();
  const { rows, cols, cells } = getGrid(config);
  const updateCell = useCellUpdater(config);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = canStore.status?.test_runner.running ?? false;
  const runningCase = canStore.status?.test_runner.running_case ?? null;

  const trigger = async (cell: MultiCell) => {
    if (!cell.funcName) return;
    try {
      await api.functionStart(cell.funcName);
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
          const label = cell.label || cell.funcName || `#${i + 1}`;
          const isActive = running && cell.funcName !== undefined && runningCase === cell.funcName;
          return (
            <div className="multi-cell" key={i}>
              <button
                className={`big-btn multi-cell-btn ${isActive ? 'func-running' : ''}`}
                disabled={!cell.funcName || running}
                title={cell.funcName ?? '함수 미할당'}
                onClick={() => trigger(cell)}
                onKeyDown={(e) => {
                  if (e.key === ' ' || e.key === 'Enter') {
                    e.preventDefault();
                    trigger(cell);
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
          kind="function"
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

// Grid of buttons that each send a Random/Range-generated value for their
// own bound signal, one cell = one independent RandomButtonWidget (see that
// file for the generating<->invalid periodic toggle design). Every
// periodic-bound cell's generator is (re-)registered on mount so it survives
// independently of any single cell being clicked.
export function RandomMultiButtonWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { editMode, dbc } = useApp();
  const { rows, cols, cells } = getGrid(config);
  const updateCell = useCellUpdater(config);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<number, 'generate' | 'invalid'>>({});
  const [lastSent, setLastSent] = useState<Record<number, 'generate' | 'invalid'>>({});

  useEffect(() => {
    if (!dbc.loaded) return;
    cells.forEach((cell) => {
      if (!cell.binding?.signal) return;
      api
        .setValueGenerator(
          cell.binding.message,
          cell.binding.signal,
          cell.mode ?? 'random',
          cell.rangeMin,
          cell.rangeMax,
          cell.step,
        )
        .catch(() => {});
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dbc.loaded, JSON.stringify(cells)]);

  const activate = async (i: number, cell: MultiCell) => {
    if (!cell.binding?.signal) return;
    const isPeriodic = findSignal(dbc, cell.binding)?.signal.send_type === 'periodic';
    try {
      const next = isPeriodic ? (pending[i] ?? 'generate') : 'generate';
      if (next === 'invalid') {
        await api.sendInvalid(cell.binding.message, cell.binding.signal);
      } else {
        if (isPeriodic) {
          await api.setValueGenerator(
            cell.binding.message,
            cell.binding.signal,
            cell.mode ?? 'random',
            cell.rangeMin,
            cell.rangeMax,
            cell.step,
          );
        }
        await api.sendGenerated(cell.binding.message, cell.binding.signal);
      }
      if (isPeriodic) {
        setLastSent((s) => ({ ...s, [i]: next }));
        setPending((s) => ({ ...s, [i]: next === 'invalid' ? 'generate' : 'invalid' }));
      }
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
          const invalidActive = lastSent[i] === 'invalid';
          const modeLabel = cell.mode === 'range' ? `Range ${cell.rangeMin ?? ''}~${cell.rangeMax ?? ''}` : 'Random';
          const label =
            cell.label ||
            (cell.binding?.signal ? (invalidActive ? `${cell.binding.signal} = INVALID` : `${cell.binding.signal} [${modeLabel}]`) : `#${i + 1}`);
          return (
            <div className="multi-cell" key={i}>
              <button
                className="big-btn multi-cell-btn"
                disabled={!cell.binding?.signal}
                title={cell.binding?.signal ? `${cell.binding.message}.${cell.binding.signal}` : '신호 미할당'}
                onClick={() => activate(i, cell)}
                onKeyDown={(e) => {
                  if (e.key === ' ' || e.key === 'Enter') {
                    e.preventDefault();
                    activate(i, cell);
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
          kind="random"
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
  kind: 'button' | 'checkbox' | 'function' | 'random';
  cell: MultiCell;
  onSave: (patch: MultiCell) => void;
  onClose: () => void;
}) {
  const { dbc } = useApp();
  const [draft, setDraft] = useState<MultiCell>({ ...cell });
  const message = dbc.messages?.find((m) => m.name === draft.binding?.message);
  const bound = findSignal(dbc, draft.binding);
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const kindLabel =
    kind === 'button' ? '버튼' : kind === 'checkbox' ? '체크박스' : kind === 'function' ? 'Function 버튼' : 'Random 버튼';

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>셀 설정 — {kindLabel}</h3>
        <label>
          라벨 (비우면 {kind === 'function' ? '함수명' : '신호명'} 표시)
          <input
            value={draft.label ?? ''}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
        </label>
        {kind === 'function' && (
          <label>
            실행할 함수
            {!canStore.status?.test_runner.functions.loaded && (
              <p className="hint">먼저 상단 툴바에서 함수 마스터 스크립트를 업로드하세요.</p>
            )}
            <select
              value={draft.funcName ?? ''}
              onChange={(e) => setDraft({ ...draft, funcName: e.target.value || undefined })}
            >
              <option value="">— 선택 —</option>
              {canStore.status?.test_runner.functions.names.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind !== 'function' && !dbc.loaded && <p className="hint">신호 할당을 하려면 먼저 DBC를 업로드하세요.</p>}
        {kind !== 'function' && dbc.loaded && (
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
        {kind === 'random' && (
          <>
            <label>
              값 모드
              <select
                value={draft.mode ?? 'random'}
                onChange={(e) => setDraft({ ...draft, mode: e.target.value as 'random' | 'range' })}
              >
                <option value="random">Random (전체 bit 범위)</option>
                <option value="range">Range (순차 순환)</option>
              </select>
            </label>
            {draft.mode === 'range' && (
              <div className="row-2">
                {bound && (
                  <p className="hint">
                    raw 범위: {signalRawBounds(bound.signal).min} ~ {signalRawBounds(bound.signal).max}
                  </p>
                )}
                <label>
                  최소값 (raw)
                  <input
                    type="number"
                    min={bound ? signalRawBounds(bound.signal).min : undefined}
                    max={bound ? signalRawBounds(bound.signal).max : undefined}
                    value={String(draft.rangeMin ?? (bound ? signalRawBounds(bound.signal).min : 0))}
                    onChange={(e) => {
                      const raw = Number(e.target.value);
                      const v = bound
                        ? Math.min(signalRawBounds(bound.signal).max, Math.max(signalRawBounds(bound.signal).min, raw))
                        : raw;
                      setDraft({ ...draft, rangeMin: v });
                    }}
                  />
                </label>
                <label>
                  최대값 (raw)
                  <input
                    type="number"
                    min={bound ? signalRawBounds(bound.signal).min : undefined}
                    max={bound ? signalRawBounds(bound.signal).max : undefined}
                    value={String(draft.rangeMax ?? (bound ? signalRawBounds(bound.signal).max : 1))}
                    onChange={(e) => {
                      const raw = Number(e.target.value);
                      const v = bound
                        ? Math.min(signalRawBounds(bound.signal).max, Math.max(signalRawBounds(bound.signal).min, raw))
                        : raw;
                      setDraft({ ...draft, rangeMax: v });
                    }}
                  />
                </label>
                <label>
                  step
                  <input
                    type="number"
                    min={1}
                    value={String(draft.step ?? 1)}
                    onChange={(e) => setDraft({ ...draft, step: Math.max(1, Number(e.target.value)) })}
                  />
                </label>
              </div>
            )}
          </>
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
