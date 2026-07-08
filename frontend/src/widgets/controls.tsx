// Control widgets that send a bound DBC signal: button, checkbox,
// dropdown and slider. Sending goes through POST /api/tx/signal, where the
// backend applies the Event(valid → 30ms → invalid) / Periodic rule.

import { useRef, useState } from 'react';
import { api } from '../api/client';
import { findSignal, useApp } from '../store/appContext';
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
  return (
    <div className="control-widget">
      <button className="big-btn" onClick={() => send(value)} disabled={!config.binding?.signal}>
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
  return (
    <div className="control-widget">
      <label className="check-label">
        <input
          type="checkbox"
          checked={checked}
          disabled={!config.binding?.signal}
          onChange={(e) => {
            setChecked(e.target.checked);
            send(e.target.checked ? onValue : offValue);
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
  const max = Number(config.options.max ?? bound?.signal.maximum ?? 100);
  const step = Number(config.options.step ?? 1);
  const [value, setValue] = useState(min);
  const lastSent = useRef(0);

  const onChange = (v: number) => {
    setValue(v);
    const now = performance.now();
    if (now - lastSent.current >= 100) {
      lastSent.current = now;
      void send(v);
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
        onPointerUp={() => void send(value)}
      />
      {error && <span className="error">{error}</span>}
    </div>
  );
}
