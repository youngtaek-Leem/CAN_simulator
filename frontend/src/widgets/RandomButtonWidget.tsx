// Sends a Random or Range-cycled value for a bound CAN signal instead of a
// fixed one. The value-generation logic lives entirely on the backend
// (tx_scheduler.py's per-signal generators). Clicks toggle transmitting
// (light-blue button) <-> stopped:
// - press 1 starts transmitting Random values (Periodic: generator +
//   one generated send, then a fresh value on every auto-resend tick;
//   Event: periodic Random sends at the user-set period, each following
//   the Event rule -- valid now, invalid 30ms later, server-side).
// - press 2 stops and sends a final value (Event: Invalid frame once;
//   Periodic: raw 0x0 once, persisted), restoring the button color.
// Running flags persist in options so they survive page switches and
// layout saves.

import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { findSignal, useApp } from '../store/appContext';
import { canStore, useCanVersion } from '../store/canStore';
import type { WidgetConfig } from '../types';

const EVENT_PERIOD_MIN_MS = 10;
const EVENT_PERIOD_MAX_MS = 60000;
const EVENT_PERIOD_DEFAULT_MS = 1000;

const clampPeriodMs = (v: number) =>
  Math.min(EVENT_PERIOD_MAX_MS, Math.max(EVENT_PERIOD_MIN_MS, Math.round(v) || EVENT_PERIOD_DEFAULT_MS));

export function RandomButtonWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const [error, setError] = useState<string | null>(null);
  const binding = config.binding;
  const mode = (config.options.mode as string | undefined) ?? 'random';
  const rangeMin = config.options.rangeMin as number | undefined;
  const rangeMax = config.options.rangeMax as number | undefined;
  const step = config.options.step as number | undefined;
  const isPeriodic = findSignal(dbc, binding)?.signal.send_type === 'periodic';
  const isEvent = findSignal(dbc, binding)?.signal.send_type === 'event';
  const eventPeriodMs = clampPeriodMs(Number(config.options.eventPeriodMs ?? EVENT_PERIOD_DEFAULT_MS));
  const eventRunning = Boolean(config.options.eventRunning ?? false);
  const generating = Boolean(config.options.generating ?? false);

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
    if (isEvent) {
      try {
        if (eventRunning) {
          await canStore.stopEventPeriodic(binding.message, binding.signal, true);
          updateWidget({ ...config, options: { ...config.options, eventRunning: false } });
        } else {
          await canStore.startEventPeriodic(binding.message, binding.signal, eventPeriodMs);
          updateWidget({ ...config, options: { ...config.options, eventRunning: true } });
        }
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    if (isPeriodic) {
      try {
        if (generating) {
          await canStore.stopGenerated(binding.message, binding.signal);
          updateWidget({ ...config, options: { ...config.options, generating: false } });
        } else {
          // re-register: stopGenerated() cleared it the last time we stopped
          await api.setValueGenerator(binding.message, binding.signal, mode, rangeMin, rangeMax, step);
          await canStore.sendGenerated(binding.message, binding.signal);
          updateWidget({ ...config, options: { ...config.options, generating: true } });
        }
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    // Unclassified send type: momentary one-shot generate per click.
    try {
      await canStore.sendGenerated(binding.message, binding.signal);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const running = eventRunning || generating;
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
        className={`big-btn${running ? ' random-running' : ''}`}
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
          ? isEvent
            ? `${binding.signal} [${modeLabel}] ${eventRunning ? '■' : '▶'} ${eventPeriodMs}ms`
            : `${binding.signal} [${modeLabel}]${generating ? ' ■' : ''}`
          : '신호 미할당'}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
