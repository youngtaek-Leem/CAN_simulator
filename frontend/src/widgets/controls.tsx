// Control widgets that send a bound DBC signal: button, checkbox,
// dropdown and slider. Sending goes through POST /api/tx/signal, where the
// backend applies the Event(valid → 30ms → invalid) / Periodic rule.

import { useRef, useState } from 'react';
import { api } from '../api/client';
import { findSignal, signalBitMax, useApp } from '../store/appContext';
import type { WidgetConfig } from '../types';

function useSendSignal(config: WidgetConfig) {
  const [error, setError] = useState<string | null>(null);
  const send = async (value: number) => {
    if (!config.binding?.signal) {
      setError('신호 미할당');
      return;
    }
    try {
      await api.txSignal(config.binding.message, { [config.binding.signal]: value });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return { send, error };
}

export function ButtonWidget({ config }: { config: WidgetConfig }) {
  const { send, error } = useSendSignal(config);
  const value = Number(config.options.value ?? 1);
  const activate = () => send(value);
  return (
    <div className="control-widget">
      <button
        className="big-btn"
        onClick={activate}
        onKeyDown={(e) => {
          // Don't rely solely on the browser's native Space/Enter activation
          // (which some hosting environments swallow) -- drive it explicitly
          // and cancel the native path to avoid a double send.
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            activate();
          }
        }}
        disabled={!config.binding?.signal}
      >
        {config.binding?.signal ? `${config.binding.signal} = ${value}` : '신호 미할당'}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export function CheckboxWidget({ config }: { config: WidgetConfig }) {
  const { send, error } = useSendSignal(config);
  const [checked, setChecked] = useState(false);
  const onValue = Number(config.options.onValue ?? 1);
  const offValue = Number(config.options.offValue ?? 0);
  const toggle = (next: boolean) => {
    setChecked(next);
    send(next ? onValue : offValue);
  };
  return (
    <div className="control-widget">
      <label className="check-label">
        <input
          type="checkbox"
          checked={checked}
          disabled={!config.binding?.signal}
          onChange={(e) => toggle(e.target.checked)}
          onKeyDown={(e) => {
            if (e.key === ' ') {
              e.preventDefault();
              toggle(!checked);
            }
          }}
        />
        {config.binding?.signal ?? '신호 미할당'}
      </label>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export function DropdownWidget({ config }: { config: WidgetConfig }) {
  const { dbc } = useApp();
  const { send, error } = useSendSignal(config);
  const [selected, setSelected] = useState('');
  const bound = findSignal(dbc, config.binding);
  const choices = bound?.signal.choices ?? null;
  return (
    <div className="control-widget">
      <select
        value={selected}
        disabled={!choices}
        onChange={(e) => {
          setSelected(e.target.value);
          if (e.target.value !== '') send(Number(e.target.value));
        }}
      >
        <option value="">
          {choices ? `${config.binding!.signal} 선택` : 'VAL_ 테이블이 있는 신호를 할당하세요'}
        </option>
        {choices &&
          Object.entries(choices).map(([raw, label]) => (
            <option key={raw} value={raw}>
              {label} ({raw})
            </option>
          ))}
      </select>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export function SliderWidget({ config }: { config: WidgetConfig }) {
  const { dbc } = useApp();
  const { send, error } = useSendSignal(config);
  const bound = findSignal(dbc, config.binding);
  const min = Number(config.options.min ?? bound?.signal.minimum ?? 0);
  const max = Number(
    config.options.max ?? bound?.signal.maximum ?? (bound ? signalBitMax(bound.signal) : 100),
  );
  const step = Number(config.options.step ?? 1);
  const [value, setValue] = useState(min);
  const valueRef = useRef(min);
  const lastSent = useRef(0);

  const onChange = (v: number) => {
    valueRef.current = v;
    setValue(v);
    const now = performance.now();
    if (now - lastSent.current >= 100) {
      lastSent.current = now;
      void send(v);
    }
  };

  // Flush the current value regardless of how the interaction ended (mouse/
  // touch pointerup or the explicit keyboard stepping below) so a value
  // swallowed by the 100ms throttle is never silently dropped.
  const flush = () => {
    lastSent.current = performance.now();
    void send(valueRef.current);
  };

  const stepBy = (delta: number) => {
    const next = Math.min(max, Math.max(min, valueRef.current + delta));
    onChange(next);
    flush();
  };

  // Explicit keyboard handling: don't rely solely on the native range
  // input's built-in arrow-key stepping (some hosting environments swallow
  // it), and prevent it here to avoid a conflicting double-adjustment.
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowUp':
        e.preventDefault();
        stepBy(step);
        break;
      case 'ArrowLeft':
      case 'ArrowDown':
        e.preventDefault();
        stepBy(-step);
        break;
      case 'Home':
        e.preventDefault();
        onChange(min);
        flush();
        break;
      case 'End':
        e.preventDefault();
        onChange(max);
        flush();
        break;
    }
  };

  return (
    <div className="control-widget slider-widget">
      <div className="slider-header">
        <span>{config.binding?.signal ?? '신호 미할당'}</span>
        <span className="mono">
          {value}
          {bound?.signal.unit ? ` ${bound.signal.unit}` : ''}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={!config.binding?.signal}
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={flush}
        onKeyDown={onKeyDown}
      />
      {error && <span className="error">{error}</span>}
    </div>
  );
}
