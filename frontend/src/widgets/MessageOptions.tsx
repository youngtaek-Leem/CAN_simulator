// Renders a DBC message list as <option>s for a <select>, alphabetically
// sorted. `filter` controls which messages are shown: 'all' groups them into
// TX/RX <optgroup>s (or a flat list if no RX node is configured), while 'tx'
// / 'rx' show only that group as a flat list. Pair with <MessageFilter> for
// the TX/RX/전체 toggle buttons.

import { groupedMessages } from '../store/appContext';
import type { DbcMessage, DbcSummary } from '../types';

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
