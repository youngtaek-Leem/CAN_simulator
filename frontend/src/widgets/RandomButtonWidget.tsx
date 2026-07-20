// Sends a Random or Range-cycled value for a bound CAN signal instead of a
// fixed one. The value-generation logic lives entirely on the backend
// (tx_scheduler.py's per-signal generators), because a Periodic signal's
// auto-resend ticks happen server-side with no frontend involvement -- this
// widget only registers the generator (mode/range/step) with the backend and
// triggers one generated send per click. Periodic signals then keep
// generating a fresh value on every subsequent auto-resend tick on their
// own; Event signals only get a new value when this button is clicked.
//
// For Periodic signals specifically, clicks also toggle generating<->invalid
// (mirroring ButtonWidget's valid<->invalid toggle): press 1 starts
// generating, press 2 sends the invalid value continuously (the backend's
// send_invalid() clears the registered generator so it can't overwrite
// invalid on the next tick), press 3 re-registers the generator and resumes.

import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { findSignal, useApp } from '../store/appContext';
import { canStore, useCanVersion } from '../store/canStore';
import type { WidgetConfig } from '../types';

export function RandomButtonWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc } = useApp();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<'generate' | 'invalid'>('generate');
  const [lastSent, setLastSent] = useState<'generate' | 'invalid' | null>(null);
  const binding = config.binding;
  const mode = (config.options.mode as string | undefined) ?? 'random';
  const rangeMin = config.options.rangeMin as number | undefined;
  const rangeMax = config.options.rangeMax as number | undefined;
  const step = config.options.step as number | undefined;
  const isPeriodic = findSignal(dbc, binding)?.signal.send_type === 'periodic';

  // Re-register on every mount / config change so a backend restart or a
  // config edit elsewhere always leaves the server-side generator in sync
  // with what this widget currently displays.
  useEffect(() => {
    if (!binding?.signal || !dbc.loaded) return;
    api.setValueGenerator(binding.message, binding.signal, mode, rangeMin, rangeMax, step).catch(() => {});
  }, [binding?.message, binding?.signal, mode, rangeMin, rangeMax, step, dbc.loaded]);

  const activate = async () => {
    if (!binding?.signal) {
      setError('신호 미할당');
      return;
    }
    try {
      const next = isPeriodic ? pending : 'generate';
      if (next === 'invalid') {
        await canStore.sendInvalid(binding.message, binding.signal);
      } else {
        if (isPeriodic) {
          // re-register: send_invalid() cleared it the last time we toggled
          await api.setValueGenerator(binding.message, binding.signal, mode, rangeMin, rangeMax, step);
        }
        await canStore.sendGenerated(binding.message, binding.signal);
      }
      if (isPeriodic) {
        setLastSent(next);
        setPending(next === 'invalid' ? 'generate' : 'invalid');
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const invalidActive = isPeriodic && lastSent === 'invalid';
  const hasRange = rangeMin !== undefined || rangeMax !== undefined;
  const modeLabel =
    mode === 'range'
      ? `Range ${rangeMin ?? ''}~${rangeMax ?? ''}`
      : hasRange
        ? `Random ${rangeMin ?? ''}~${rangeMax ?? ''}`
        : 'Random';

  return (
    <div className="control-widget">
      <button
        className="big-btn"
        onClick={activate}
        onKeyDown={(e) => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            activate();
          }
        }}
        disabled={!binding?.signal}
      >
        {binding?.signal
          ? invalidActive
            ? `${binding.signal} = INVALID`
            : `${binding.signal} [${modeLabel}]`
          : '신호 미할당'}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
