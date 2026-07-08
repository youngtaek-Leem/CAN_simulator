import { createContext, useContext } from 'react';
import type { DbcMessage, DbcSignal, DbcSummary, SignalBinding, WidgetConfig } from '../types';

export interface AppCtx {
  dbc: DbcSummary;
  editMode: boolean;
  updateWidget: (cfg: WidgetConfig) => void;
  removeWidget: (id: string) => void;
  refreshDbc: () => void;
}

export const AppContext = createContext<AppCtx>({
  dbc: { loaded: false },
  editMode: false,
  updateWidget: () => {},
  removeWidget: () => {},
  refreshDbc: () => {},
});

export const useApp = () => useContext(AppContext);

export function findSignal(
  dbc: DbcSummary,
  binding?: SignalBinding,
): { message: DbcMessage; signal: DbcSignal } | null {
  if (!binding || !dbc.messages) return null;
  const message = dbc.messages.find((m) => m.name === binding.message);
  const signal = message?.signals.find((s) => s.name === binding.signal);
  return message && signal ? { message, signal } : null;
}
