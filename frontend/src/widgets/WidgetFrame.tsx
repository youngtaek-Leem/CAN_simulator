// Common widget chrome: title bar (always a drag handle, position/size can
// be changed regardless of edit mode), config and remove buttons shown only
// in edit mode, and a config modal with signal binding + per-type options.

import { useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { findSignal, signalBitMax, signalBitMin, signalRawBounds, useApp } from '../store/appContext';
import { canStore } from '../store/canStore';
import { SignalPicker } from './MessageOptions';
import type { DbcSignal, WidgetConfig } from '../types';

const BINDABLE = new Set(['button', 'checkbox', 'dropdown', 'slider', 'randomButton', 'manualValue']);

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

/** Physical value range for a "전송 값" input: the signal's own declared
 * DBC minimum/maximum when present (may be tighter than the bit width,
 * e.g. a 4-bit signal documented as only using 0..14), falling back to the
 * full bit-width range otherwise -- same convention as SliderWidget. */
function signalValueRange(signal: DbcSignal): { min: number; max: number } {
  return {
    min: signal.minimum ?? signalBitMin(signal),
    max: signal.maximum ?? signalBitMax(signal),
  };
}

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
  const bound = findSignal(dbc, draft.binding);

  const setOption = (key: string, value: unknown) =>
    setDraft((d) => ({ ...d, options: { ...d.options, [key]: value } }));

  const save = () => {
    updateWidget(draft);
    if (config.type === 'randomButton' && draft.binding?.signal) {
      const mode = (draft.options.mode as string | undefined) ?? 'random';
      api.setValueGenerator(
        draft.binding.message,
        draft.binding.signal,
        mode,
        draft.options.rangeMin as number | undefined,
        draft.options.rangeMax as number | undefined,
        draft.options.step as number | undefined,
      ).catch(() => {});
    }
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
                <SignalPicker
                  dbc={dbc}
                  rxNode={canStore.getRxNode()}
                  binding={draft.binding}
                  onChange={(b) => setDraft({ ...draft, binding: b })}
                  messageLabelFor={(m) => `${m.name} (0x${m.frame_id.toString(16).toUpperCase()})`}
                />
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
            {bound && (
              <span className="hint">
                범위: {signalValueRange(bound.signal).min} ~ {signalValueRange(bound.signal).max}
              </span>
            )}
            <input
              type="number"
              min={bound ? signalValueRange(bound.signal).min : undefined}
              max={bound ? signalValueRange(bound.signal).max : undefined}
              value={String(draft.options.value ?? 1)}
              onChange={(e) => {
                const raw = Number(e.target.value);
                const { min, max } = bound ? signalValueRange(bound.signal) : { min: -Infinity, max: Infinity };
                setOption('value', bound ? clamp(raw, min, max) : raw);
              }}
            />
          </label>
        )}
        {config.type === 'randomButton' && (
          <>
            <label>
              값 모드
              <select
                value={(draft.options.mode as string | undefined) ?? 'random'}
                onChange={(e) => setOption('mode', e.target.value)}
              >
                <option value="random">Random (기본: 전체 bit 범위)</option>
                <option value="range">Range (순차 순환)</option>
              </select>
            </label>
            <div className="row-2">
              {bound && (
                <p className="hint">
                  raw 범위: {signalRawBounds(bound.signal).min} ~ {signalRawBounds(bound.signal).max}
                </p>
              )}
              <label>
                최소값 (raw, 비우면 전체 범위)
                <input
                  type="number"
                  min={bound ? signalRawBounds(bound.signal).min : undefined}
                  max={bound ? signalRawBounds(bound.signal).max : undefined}
                  value={String(draft.options.rangeMin ?? (bound ? signalRawBounds(bound.signal).min : 0))}
                  onChange={(e) => {
                    const raw = Number(e.target.value);
                    const v = bound
                      ? clamp(raw, signalRawBounds(bound.signal).min, signalRawBounds(bound.signal).max)
                      : raw;
                    setOption('rangeMin', v);
                  }}
                />
              </label>
              <label>
                최대값 (raw, 비우면 전체 범위)
                <input
                  type="number"
                  min={bound ? signalRawBounds(bound.signal).min : undefined}
                  max={bound ? signalRawBounds(bound.signal).max : undefined}
                  value={String(draft.options.rangeMax ?? (bound ? signalRawBounds(bound.signal).max : 1))}
                  onChange={(e) => {
                    const raw = Number(e.target.value);
                    const v = bound
                      ? clamp(raw, signalRawBounds(bound.signal).min, signalRawBounds(bound.signal).max)
                      : raw;
                    setOption('rangeMax', v);
                  }}
                />
              </label>
              {draft.options.mode === 'range' && (
                <label>
                  step
                  <input
                    type="number"
                    min={1}
                    value={String(draft.options.step ?? 1)}
                    onChange={(e) => setOption('step', Math.max(1, Number(e.target.value)))}
                  />
                </label>
              )}
            </div>
          </>
        )}
        {config.type === 'functionButton' && (
          <label>
            실행할 함수
            {!canStore.status?.test_runner.functions.loaded && (
              <p className="hint">먼저 상단 툴바에서 함수 마스터 스크립트를 업로드하세요.</p>
            )}
            <select
              value={(draft.options.funcName as string | undefined) ?? ''}
              onChange={(e) => setOption('funcName', e.target.value || undefined)}
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
        {config.type === 'checkbox' && (
          <div className="row-2">
            {bound && (
              <p className="hint">
                범위: {signalValueRange(bound.signal).min} ~ {signalValueRange(bound.signal).max}
              </p>
            )}
            <label>
              ON 값
              <input
                type="number"
                min={bound ? signalValueRange(bound.signal).min : undefined}
                max={bound ? signalValueRange(bound.signal).max : undefined}
                value={String(draft.options.onValue ?? 1)}
                onChange={(e) => {
                  const raw = Number(e.target.value);
                  const { min, max } = bound ? signalValueRange(bound.signal) : { min: -Infinity, max: Infinity };
                  setOption('onValue', bound ? clamp(raw, min, max) : raw);
                }}
              />
            </label>
            <label>
              OFF 값
              <input
                type="number"
                min={bound ? signalValueRange(bound.signal).min : undefined}
                max={bound ? signalValueRange(bound.signal).max : undefined}
                value={String(draft.options.offValue ?? 0)}
                onChange={(e) => {
                  const raw = Number(e.target.value);
                  const { min, max } = bound ? signalValueRange(bound.signal) : { min: -Infinity, max: Infinity };
                  setOption('offValue', bound ? clamp(raw, min, max) : raw);
                }}
              />
            </label>
          </div>
        )}
        {(config.type === 'multiButton' ||
          config.type === 'multiCheckbox' ||
          config.type === 'multiDropdown' ||
          config.type === 'multiSlider' ||
          config.type === 'multiManualValue') && (
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
                value={String(
                  draft.options.max ?? bound?.signal.maximum ?? (bound ? signalBitMax(bound.signal) : 100),
                )}
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
            <label>
              기본값
              <input
                type="number"
                value={String(draft.options.default ?? draft.options.min ?? bound?.signal.minimum ?? 0)}
                onChange={(e) => setOption('default', Number(e.target.value))}
              />
            </label>
          </div>
        )}
        {config.type === 'manualValue' && (
          <label>
            기본값 (hex/binary/decimal, 비우면 빈 칸)
            <input
              className="mono"
              value={String(draft.options.default ?? '')}
              placeholder="0x1A / 0b00011010 / 26"
              onChange={(e) => setOption('default', e.target.value)}
            />
          </label>
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
