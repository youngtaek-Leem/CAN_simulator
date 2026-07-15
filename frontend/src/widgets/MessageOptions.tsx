// Renders a DBC message list as <option>s for a <select>, alphabetically
// sorted. `filter` controls which messages are shown: 'all' groups them into
// TX/RX <optgroup>s (or a flat list if no RX node is configured), while 'tx'
// / 'rx' show only that group as a flat list. Pair with <MessageFilter> for
// the TX/RX/전체 toggle buttons.

import { useMemo, useState } from 'react';
import { groupedMessages, sortedMessages } from '../store/appContext';
import type { DbcMessage, DbcSignal, DbcSummary, SignalBinding } from '../types';

export type MessageFilterMode = 'all' | 'tx' | 'rx';

export function MessageOptions({
  dbc,
  rxNode,
  filter = 'all',
  labelFor,
}: {
  dbc: DbcSummary;
  rxNode: string;
  filter?: MessageFilterMode;
  labelFor: (m: DbcMessage) => string;
}) {
  const { tx, rx, grouped } = groupedMessages(dbc, rxNode);
  const option = (m: DbcMessage) => (
    <option key={m.name} value={m.name}>
      {labelFor(m)}
    </option>
  );

  if (filter === 'tx') return <>{tx.map(option)}</>;
  if (filter === 'rx') return <>{rx.map(option)}</>;
  if (!grouped) return <>{tx.map(option)}</>;
  return (
    <>
      <optgroup label="TX 메시지">{tx.map(option)}</optgroup>
      <optgroup label="RX 메시지">{rx.map(option)}</optgroup>
    </>
  );
}

/** TX/RX/전체 toggle buttons that drive a MessageOptions `filter` prop. */
export function MessageFilter({
  value,
  onChange,
}: {
  value: MessageFilterMode;
  onChange: (mode: MessageFilterMode) => void;
}) {
  const options: { mode: MessageFilterMode; label: string }[] = [
    { mode: 'all', label: '전체' },
    { mode: 'tx', label: 'TX' },
    { mode: 'rx', label: 'RX' },
  ];
  return (
    <span className="seg msg-filter">
      {options.map((o) => (
        <button
          key={o.mode}
          type="button"
          className={`small-btn ${value === o.mode ? 'seg-active' : ''}`}
          onClick={() => onChange(o.mode)}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}

const SIGNAL_SEARCH_MAX = 30;

/**
 * Unified CAN-signal binding picker: a free-text search box (type any
 * substring of a signal name -> matching signals across every message are
 * listed, pick one to set both message+signal at once) alongside the
 * existing message-select -> signal-select cascade, both wired to the same
 * `binding` state so either input method works interchangeably.
 */
export function SignalPicker({
  dbc,
  rxNode,
  binding,
  onChange,
  messageLabelFor = (m) => m.name,
}: {
  dbc: DbcSummary;
  rxNode: string;
  binding: SignalBinding | undefined;
  onChange: (b: SignalBinding | undefined) => void;
  messageLabelFor?: (m: DbcMessage) => string;
}) {
  const [query, setQuery] = useState('');
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const message = dbc.messages?.find((m) => m.name === binding?.message);

  const allSignals = useMemo(() => {
    const list: { message: DbcMessage; signal: DbcSignal }[] = [];
    for (const m of sortedMessages(dbc)) {
      for (const s of m.signals) list.push({ message: m, signal: s });
    }
    return list;
  }, [dbc]);

  const matches =
    query.trim() === ''
      ? []
      : allSignals
          .filter(({ signal }) => signal.name.toLowerCase().includes(query.trim().toLowerCase()))
          .slice(0, SIGNAL_SEARCH_MAX);

  const pick = (m: DbcMessage, s: DbcSignal) => {
    onChange({ message: m.name, signal: s.name });
    setQuery('');
  };

  return (
    <>
      <label>
        신호 검색 (이름 일부 입력)
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="예: Speed"
        />
      </label>
      {matches.length > 0 && (
        <div className="signal-search-list">
          {matches.map(({ message: m, signal: s }) => (
            <div
              key={`${m.name}.${s.name}`}
              className="signal-search-item"
              // onMouseDown (not onClick) fires before the input's onBlur,
              // so the selection registers before any blur-driven close
              onMouseDown={(e) => {
                e.preventDefault();
                pick(m, s);
              }}
            >
              <span>{s.name}</span>
              <span className="hint">{m.name}</span>
            </div>
          ))}
        </div>
      )}

      <label>
        CAN 메시지
        <span className="select-with-filter">
          <select
            value={binding?.message ?? ''}
            onChange={(e) =>
              onChange(e.target.value ? { message: e.target.value, signal: '' } : undefined)
            }
          >
            <option value="">— 선택 —</option>
            <MessageOptions dbc={dbc} rxNode={rxNode} filter={msgFilter} labelFor={messageLabelFor} />
          </select>
          <MessageFilter value={msgFilter} onChange={setMsgFilter} />
        </span>
      </label>
      {message && (
        <label>
          신호
          <select
            value={binding?.signal ?? ''}
            onChange={(e) => onChange({ message: message.name, signal: e.target.value })}
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
  );
}
