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

/** Largest physical value representable by the signal's bit width (raw max * scale + offset). */
export function signalBitMax(signal: DbcSignal): number {
  const rawMax = signal.is_signed ? 2 ** (signal.length - 1) - 1 : 2 ** signal.length - 1;
  return rawMax * signal.scale + signal.offset;
}

/** DBC messages sorted alphabetically by name, for signal-picker dropdowns. */
export function sortedMessages(dbc: DbcSummary): DbcMessage[] {
  return [...(dbc.messages ?? [])].sort((a, b) => a.name.localeCompare(b.name));
}

export interface MessageGroups {
  tx: DbcMessage[];
  rx: DbcMessage[];
  /** false when no RX node is configured -- caller should render `tx` as a flat list. */
  grouped: boolean;
}

/**
 * Split the (alphabetically sorted) message list into TX/RX groups based on
 * the DBC's declared sender node(s) for each message, relative to `rxNode`
 * (the real DUT node on the bus). Messages that `rxNode` sends are what the
 * simulator receives ("RX"); every other message is something the simulator
 * must transmit ("TX") to stand in for the rest of the bus. With no RX node
 * configured, nothing is confirmed as RX, so everything is returned under
 * `tx` and `grouped` is false so callers can render a flat, ungrouped list.
 */
export function groupedMessages(dbc: DbcSummary, rxNode: string): MessageGroups {
  const all = sortedMessages(dbc);
  if (!rxNode) return { tx: all, rx: [], grouped: false };
  return {
    rx: all.filter((m) => m.senders.includes(rxNode)),
    tx: all.filter((m) => !m.senders.includes(rxNode)),
    grouped: true,
  };
}
