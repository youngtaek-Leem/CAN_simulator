// Control widgets that send a bound DBC signal: button, checkbox, dropdown,
// slider and manual value entry. Sending goes through POST /api/tx/signal,
// where the backend applies the Event(valid → 30ms → invalid) / Periodic rule.

import { useEffect, useRef, useState } from 'react';
import { findSignal, signalBitMax, signalBitMin, signalRawBounds, useApp } from '../store/appContext';
import { canStore } from '../store/canStore';
import type { DbcSummary, ExtraBinding, SignalBinding, WidgetConfig } from '../types';

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

/** 버튼 눌림 fan-out: entries(바인딩+전송값)를 메시지별로 묶어 전송한다.
 * 그룹 내 전부 periodic → ZeroAfter pulse 1회, 그 외 일반 단발
 * (ZeroAfter는 Event 신호를 거부하므로 혼합 그룹은 periodic/event로
 * 나눠 2프레임 — 각 신호의 단건 버튼 동작과 동일 보장). */
export async function sendButtonBindings(
  entries: { binding: SignalBinding | undefined; value: number }[],
  dbc: DbcSummary,
): Promise<void> {
  const seen = new Set<string>();
  const byMessage = new Map<string, { signal: string; value: number; periodic: boolean }[]>();
  for (const e of entries) {
    if (!e.binding?.message || !e.binding?.signal) continue;
    const k = `${e.binding.message}.${e.binding.signal}`;
    if (seen.has(k)) continue;
    seen.add(k);
    const sig = findSignal(dbc, e.binding)?.signal;
    let v = e.value;
    if (sig) {
      const lo = sig.minimum ?? signalBitMin(sig);
      const hi = sig.maximum ?? signalBitMax(sig);
      v = Math.min(hi, Math.max(lo, e.value));
    }
    const list = byMessage.get(e.binding.message) ?? [];
    list.push({ signal: e.binding.signal, value: v, periodic: sig?.send_type === 'periodic' });
    byMessage.set(e.binding.message, list);
  }
  if (byMessage.size === 0) throw new Error('신호 미할당');
  const toValues = (list: { signal: string; value: number }[]) =>
    Object.fromEntries(list.map((s) => [s.signal, s.value]));
  for (const [message, list] of byMessage) {
    if (list.every((s) => s.periodic)) {
      await canStore.sendSignalZeroAfter(message, toValues(list));
    } else if (list.every((s) => !s.periodic)) {
      await canStore.sendSignal(message, toValues(list));
    } else {
      const per = list.filter((s) => s.periodic);
      const ev = list.filter((s) => !s.periodic);
      await canStore.sendSignalZeroAfter(message, toValues(per));
      await canStore.sendSignal(message, toValues(ev));
    }
  }
}

/** 여러 바인딩 신호에 동일 물리값을 전송 -- 메시지별로 묶어 메시지당 1회
 * 호출한다. 각 신호는 자신의 선언 범위(minimum/maximum 우선, 없으면
 * bit폭)로 클램프된다. 바인딩이 하나도 없으면 throw (호출자가 표시). */
export async function sendValueToBindings(
  bindings: SignalBinding[],
  value: number,
  dbc: DbcSummary,
): Promise<void> {
  const seen = new Set<string>();
  const byMessage = new Map<string, Record<string, number>>();
  for (const b of bindings) {
    if (!b?.message || !b?.signal) continue;
    const k = `${b.message}.${b.signal}`;
    if (seen.has(k)) continue;
    seen.add(k);
    let v = value;
    const sig = findSignal(dbc, b)?.signal;
    if (sig) {
      const lo = sig.minimum ?? signalBitMin(sig);
      const hi = sig.maximum ?? signalBitMax(sig);
      v = Math.min(hi, Math.max(lo, value));
    }
    const values = byMessage.get(b.message) ?? {};
    values[b.signal] = v;
    byMessage.set(b.message, values);
  }
  if (byMessage.size === 0) throw new Error('신호 미할당');
  for (const [message, values] of byMessage) {
    await canStore.sendSignal(message, values);
  }
}

/** 체크박스 토글 fan-out: entries(바인딩+ON/OFF값)를 메시지별로 묶어
 * checked ? ON값 : OFF값으로 일반 단발 전송한다 (체크박스는 pulse 없음).
 * 각 값은 신호별 선언 범위로 클램프. 바인딩이 하나도 없으면 throw. */
export async function sendCheckboxBindings(
  entries: { binding: SignalBinding | undefined; onValue: number; offValue: number }[],
  checked: boolean,
  dbc: DbcSummary,
): Promise<void> {
  const seen = new Set<string>();
  const byMessage = new Map<string, Record<string, number>>();
  for (const e of entries) {
    if (!e.binding?.message || !e.binding?.signal) continue;
    const k = `${e.binding.message}.${e.binding.signal}`;
    if (seen.has(k)) continue;
    seen.add(k);
    const sig = findSignal(dbc, e.binding)?.signal;
    const raw = checked ? e.onValue : e.offValue;
    let v = raw;
    if (sig) {
      const lo = sig.minimum ?? signalBitMin(sig);
      const hi = sig.maximum ?? signalBitMax(sig);
      v = Math.min(hi, Math.max(lo, raw));
    }
    const values = byMessage.get(e.binding.message) ?? {};
    values[e.binding.signal] = v;
    byMessage.set(e.binding.message, values);
  }
  if (byMessage.size === 0) throw new Error('신호 미할당');
  for (const [message, values] of byMessage) {
    await canStore.sendSignal(message, values);
  }
}

export function ButtonWidget({ config }: { config: WidgetConfig }) {
  const value = Number(config.options.value ?? 1);
  const { dbc } = useApp();
  const { error, setError } = useSendSignal(config);
  const extras = (config.options.extraBindings as ExtraBinding[] | undefined) ?? [];
  const extraCount = extras.filter((b) => b?.signal).length;
  const allLabels = [
    ...(config.binding?.signal ? [`${config.binding.message}.${config.binding.signal} = ${value}`] : []),
    ...extras.filter((b) => b?.signal).map((b) => `${b.message}.${b.signal} = ${b.value ?? 1}`),
  ].join('\n');
  // Periodic: one-shot pulse (configured value immediately, raw 0x0 30ms
  // later with 0 persisted, server-side) on every click -- no toggle.
  // Event: existing behavior, unchanged (value now + auto-invalid 30ms later).
  // 여러 신호면 메시지별로 묶어 전송 (전부 periodic인 그룹만 pulse).
  const activate = async () => {
    try {
      await sendButtonBindings(
        [
          ...(config.binding?.signal ? [{ binding: config.binding, value }] : []),
          ...extras.filter((b) => b?.signal).map((b) => ({
            binding: { message: b.message, signal: b.signal },
            value: b.value ?? 1,
          })),
        ],
        dbc,
      );
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
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
        disabled={!(config.binding?.signal || extraCount > 0)}
        title={allLabels || undefined}
      >
        {config.binding?.signal
          ? `${config.binding.signal} = ${value}${extraCount > 0 ? ` +외 ${extraCount}개` : ''}`
          : extraCount > 0
            ? `+외 ${extraCount}개`
            : '신호 미할당'}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export function CheckboxWidget({ config }: { config: WidgetConfig }) {
  const { dbc, updateWidget } = useApp();
  const { error, setError } = useSendSignal(config);
  // Persisted in config.options (not local useState) so the checked state
  // survives switching to another page and back -- App.tsx only mounts the
  // active page's widgets, so any value kept only in local useState resets
  // on remount.
  const checked = Boolean(config.options.checked ?? false);
  const extras = (config.options.extraBindings as ExtraBinding[] | undefined) ?? [];
  const extraCount = extras.filter((b) => b?.signal).length;
  const allLabels = [
    ...(config.binding?.signal
      ? [`${config.binding.message}.${config.binding.signal} ON=${Number(config.options.onValue ?? 1)} OFF=${Number(config.options.offValue ?? 0)}`]
      : []),
    ...extras
      .filter((b) => b?.signal)
      .map((b) => `${b.message}.${b.signal} ON=${b.onValue ?? 1} OFF=${b.offValue ?? 0}`),
  ].join('\n');
  // 토글 시 전체 행의 ON/OFF값을 메시지별로 묶어 전송 (일반 단발, pulse 없음).
  const toggle = (next: boolean) => {
    updateWidget({ ...config, options: { ...config.options, checked: next } });
    void (async () => {
      try {
        await sendCheckboxBindings(
          [
            ...(config.binding?.signal
              ? [{
                binding: config.binding,
                onValue: Number(config.options.onValue ?? 1),
                offValue: Number(config.options.offValue ?? 0),
              }]
              : []),
            ...extras.filter((b) => b?.signal).map((b) => ({
              binding: { message: b.message, signal: b.signal },
              onValue: b.onValue ?? 1,
              offValue: b.offValue ?? 0,
            })),
          ],
          next,
          dbc,
        );
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  };
  return (
    <div className="control-widget">
      <label className="check-label">
        <input
          type="checkbox"
          checked={checked}
          disabled={!(config.binding?.signal || extraCount > 0)}
          onChange={(e) => toggle(e.target.checked)}
          onKeyDown={(e) => {
            if (e.key === ' ') {
              e.preventDefault();
              toggle(!checked);
            }
          }}
        />
        <span title={allLabels || undefined}>
          {config.binding?.signal ?? (extraCount > 0 ? `+외 ${extraCount}개` : '신호 미할당')}
          {config.binding?.signal && extraCount > 0 ? ` +외 ${extraCount}개` : ''}
        </span>
      </label>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export function DropdownWidget({ config }: { config: WidgetConfig }) {
  const { dbc, updateWidget } = useApp();
  const { send, error } = useSendSignal(config);
  // Persisted in config.options -- same reasoning as CheckboxWidget above.
  const selected = String(config.options.selected ?? '');
  const bound = findSignal(dbc, config.binding);
  const choices = bound?.signal.choices ?? null;
  return (
    <div className="control-widget">
      <select
        value={selected}
        disabled={!choices}
        onChange={(e) => {
          updateWidget({ ...config, options: { ...config.options, selected: e.target.value } });
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
  const { dbc, updateWidget } = useApp();
  const { error, setError } = useSendSignal(config);
  const bound = findSignal(dbc, config.binding);
  const min = Number(config.options.min ?? bound?.signal.minimum ?? 0);
  const max = Number(
    config.options.max ?? bound?.signal.maximum ?? (bound ? signalBitMax(bound.signal) : 100),
  );
  const step = Number(config.options.step ?? 1);
  const defaultValue = Math.min(max, Math.max(min, Number(config.options.default ?? min)));
  // The position the user last dragged/stepped to is persisted separately
  // from `default` (config.options.currentValue) so switching to another
  // page and back doesn't snap the slider back to its configured default --
  // App.tsx only mounts the active page's widgets, so a value kept only in
  // local useState resets on remount.
  const initialValue =
    config.options.currentValue !== undefined
      ? Math.min(max, Math.max(min, Number(config.options.currentValue)))
      : defaultValue;
  const [value, setValue] = useState(initialValue);
  const valueRef = useRef(initialValue);
  const lastSent = useRef(0);
  const isFirstRender = useRef(true);

  // 본 바인딩 + 추가 바인딩 (유효·비어있지 않은 것만, 본 바인딩 중복 제외).
  // 없으면 기존 단일 바인딩 동작과 완전히 동일하다.
  const bindings = (() => {
    const list: SignalBinding[] = [];
    const seen = new Set<string>();
    const push = (b: SignalBinding | undefined) => {
      if (!b?.message || !b?.signal) return;
      const k = `${b.message}.${b.signal}`;
      if (seen.has(k)) return;
      seen.add(k);
      list.push({ message: b.message, signal: b.signal });
    };
    push(config.binding);
    for (const b of (config.options.extraBindings as SignalBinding[] | undefined) ?? []) push(b);
    return list;
  })();
  const extraCount = bindings.length - (config.binding?.signal ? 1 : 0);
  const headerLabel =
    !config.binding?.signal && extraCount <= 0
      ? '신호 미할당'
      : `${config.binding?.signal ?? ''}${config.binding?.signal && extraCount > 0 ? ' ' : ''}${extraCount > 0 ? `+외 ${extraCount}개` : ''}`;
  const headerTitle = bindings.map((b) => `${b.message}.${b.signal}`).join('\n') || undefined;

  // 같은 값을 모든 바인딩 신호에 전송 -- 메시지별로 묶어 메시지당 1회 호출.
  // 각 신호는 자신의 선언 범위(minimum/maximum 우선, 없으면 bit폭)로
  // 클램프된다.
  const sendAll = async (v: number) => {
    try {
      await sendValueToBindings(bindings, v, dbc);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Re-apply the configured default whenever it (or min/max, which it's
  // clamped into) changes in the config modal -- otherwise a slider that's
  // already on screen would only pick up a new default on the next full
  // page load, which reads as "the setting didn't do anything." Skipped on
  // mount (isFirstRender) so remounting on a page switch doesn't clobber
  // the persisted currentValue with the default every time.
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setValue(defaultValue);
    valueRef.current = defaultValue;
    updateWidget({ ...config, options: { ...config.options, currentValue: defaultValue } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultValue]);

  const onChange = (v: number) => {
    valueRef.current = v;
    setValue(v);
    const now = performance.now();
    if (now - lastSent.current >= 100) {
      lastSent.current = now;
      void sendAll(v);
    }
  };

  // Flush the current value regardless of how the interaction ended (mouse/
  // touch pointerup or the explicit keyboard stepping below) so a value
  // swallowed by the 100ms throttle is never silently dropped -- also the
  // one place that persists the settled position into config.options, so
  // dragging doesn't trigger a config write (and parent re-render) on every
  // single pointermove tick.
  const flush = () => {
    lastSent.current = performance.now();
    void sendAll(valueRef.current);
    updateWidget({ ...config, options: { ...config.options, currentValue: valueRef.current } });
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
        <span title={headerTitle}>{headerLabel}</span>
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
        disabled={bindings.length === 0}
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
 * decimal), then send: Event keeps the existing rule (value, then
 * auto-invalid 30ms later); Periodic sends the inverted pulse (INVALID
 * immediately, typed value 30ms later, server-side). */
export function ManualValueWidget({ config }: { config: WidgetConfig }) {
  const { dbc, updateWidget } = useApp();
  const bound = findSignal(dbc, config.binding);
  const defaultText = String(config.options.default ?? '');
  // The typed-in text is persisted separately from `default`
  // (config.options.currentText) so switching to another page and back
  // doesn't revert it -- App.tsx only mounts the active page's widgets, so
  // a value kept only in local useState resets on remount.
  const text = config.options.currentText !== undefined ? String(config.options.currentText) : defaultText;
  const setText = (v: string) => updateWidget({ ...config, options: { ...config.options, currentText: v } });
  const { send, error, setError } = useSendSignal(config);
  const isFirstRender = useRef(true);

  // Re-apply the configured default whenever it changes in the config modal
  // -- same reasoning as SliderWidget's equivalent effect: otherwise a
  // widget already on screen wouldn't pick up a new default until reload.
  // Skipped on mount so remounting on a page switch doesn't clobber the
  // persisted currentText with the default every time.
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
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
    const physical = raw * bound.signal.scale + bound.signal.offset;
    if (bound.signal.send_type === 'periodic') {
      try {
        await canStore.sendSignalInvalidFirst(config.binding!.message, {
          [config.binding!.signal]: physical,
        });
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    await send(physical);
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
