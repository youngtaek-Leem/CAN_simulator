// Control widgets that send a bound DBC signal: button, checkbox, dropdown,
// slider and manual value entry. Sending goes through POST /api/tx/signal,
// where the backend applies the Event(valid → 30ms → invalid) / Periodic rule.

import { useEffect, useRef, useState } from 'react';
import { findSignal, signalBitMax, signalRawBounds, useApp } from '../store/appContext';
import { canStore } from '../store/canStore';
import type { WidgetConfig } from '../types';

function useSendSignal(config: WidgetConfig) {
  const [error, setError] = useState<string | null>(null);
  const send = async (value: number) => {
    if (!config.binding?.signal) {
      setError('신호 미할당');
      return;
    }
    try {
      await canStore.sendSignal(config.binding.message, { [config.binding.signal]: value });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return { send, error, setError };
}

/** Periodic signals: press 1 starts sending the configured value, press 2
 * switches to sending the invalid value (bit-max) continuously, press 3
 * switches back, etc. Event signals are unaffected -- they keep the plain
 * single-value-per-click behavior (the backend's own 30ms-later invalid
 * follow-up already applies there). */
function usePeriodicInvalidToggle(config: WidgetConfig, value: number) {
  const { dbc } = useApp();
  const bound = findSignal(dbc, config.binding);
  const isPeriodic = bound?.signal.send_type === 'periodic';
  // what the *next* click will send, and what the *last* click actually
  // sent (null before the first click, so the label starts out neutral)
  const [pending, setPending] = useState<'valid' | 'invalid'>('valid');
  const [lastSent, setLastSent] = useState<'valid' | 'invalid' | null>(null);
  const { send, error, setError } = useSendSignal(config);

  const activate = async () => {
    if (!config.binding?.signal) {
      send(value);
      return;
    }
    if (!isPeriodic) {
      send(value);
      return;
    }
    if (pending === 'invalid') {
      try {
        await canStore.sendInvalid(config.binding.message, config.binding.signal);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    } else {
      await send(value);
    }
    // advance the toggle regardless of send success -- consistent with the
    // rest of this app's optimistic (no-rollback-on-failure) UI updates
    setLastSent(pending);
    setPending(pending === 'invalid' ? 'valid' : 'invalid');
  };

  const invalidActive = isPeriodic && lastSent === 'invalid';
  return { activate, error, invalidActive };
}

export function ButtonWidget({ config }: { config: WidgetConfig }) {
  const value = Number(config.options.value ?? 1);
  const { activate, error, invalidActive } = usePeriodicInvalidToggle(config, value);
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
        {config.binding?.signal
          ? invalidActive
            ? `${config.binding.signal} = INVALID`
            : `${config.binding.signal} = ${value}`
          : '신호 미할당'}
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
  const defaultValue = Math.min(max, Math.max(min, Number(config.options.default ?? min)));
  const [value, setValue] = useState(defaultValue);
  const valueRef = useRef(defaultValue);
  const lastSent = useRef(0);

  // Re-apply the configured default whenever it (or min/max, which it's
  // clamped into) changes in the config modal -- otherwise a slider that's
  // already on screen would only pick up a new default on the next full
  // page load, which reads as "the setting didn't do anything."
  useEffect(() => {
    setValue(defaultValue);
    valueRef.current = defaultValue;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultValue]);

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

/** "0x1A" / "0b00011010" / "26" -> 26 (raw, unscaled). null if unparseable. */
export function parseFlexibleInt(input: string): number | null {
  const s = input.trim();
  if (/^0[xX][0-9a-fA-F]+$/.test(s)) return parseInt(s.slice(2), 16);
  if (/^0[bB][01]+$/.test(s)) return parseInt(s.slice(2), 2);
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  return null;
}

/** Assign a single CAN signal and type its raw value directly (hex/binary/
 * decimal), then send with the same Event/Periodic rule as every other
 * control widget: Event sends the value then auto-invalidates 30ms later,
 * Periodic keeps resending the value at the signal's cycle time. */
export function ManualValueWidget({ config }: { config: WidgetConfig }) {
  const { dbc } = useApp();
  const bound = findSignal(dbc, config.binding);
  const defaultText = String(config.options.default ?? '');
  const [text, setText] = useState(defaultText);
  const { send, error, setError } = useSendSignal(config);

  // Re-apply the configured default whenever it changes in the config modal
  // -- same reasoning as SliderWidget's equivalent effect: otherwise a
  // widget already on screen wouldn't pick up a new default until reload.
  useEffect(() => {
    setText(defaultText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultText]);

  const submit = async () => {
    if (!bound) {
      setError('신호 미할당');
      return;
    }
    const raw = parseFlexibleInt(text);
    if (raw === null) {
      setError('잘못된 값 (0x1A, 0b00011010, 26 형식만 지원)');
      return;
    }
    const { min, max } = signalRawBounds(bound.signal);
    if (raw < min || raw > max) {
      setError(`범위 초과 (raw ${min} ~ ${max})`);
      return;
    }
    await send(raw * bound.signal.scale + bound.signal.offset);
  };

  return (
    <div className="control-widget manual-value-widget">
      <div className="slider-header">
        <span>{config.binding?.signal ?? '신호 미할당'}</span>
        {bound && (
          <span className="hint mono">
            raw {signalRawBounds(bound.signal).min}~{signalRawBounds(bound.signal).max}
          </span>
        )}
      </div>
      <div className="manual-value-row">
        <input
          className="mono"
          value={text}
          placeholder="0x1A / 0b00011010 / 26"
          disabled={!config.binding?.signal}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit();
          }}
        />
        <button className="small-btn primary" disabled={!config.binding?.signal} onClick={submit}>
          전송
        </button>
      </div>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
