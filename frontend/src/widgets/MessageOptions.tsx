// Renders a DBC message list as <option>s for a <select>, alphabetically
// sorted. `filter` controls which messages are shown: 'all' groups them into
// TX/RX <optgroup>s (or a flat list if no RX node is configured), while 'tx'
// / 'rx' show only that group as a flat list. Pair with <MessageFilter> for
// the TX/RX/전체 toggle buttons.

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import { findSignal, groupedMessages, signalBitMax, signalBitMin, sortedMessages } from '../store/appContext';
import type { DbcMessage, DbcSignal, DbcSummary, ExtraBinding, SignalBinding } from '../types';

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

function bindingKey(b: SignalBinding | undefined): string {
  return b?.signal ? `${b.message}.${b.signal}` : '';
}

/** 한 줄형 바인딩 피커: [신호검색][CAN 메시지][전체|TX|RX][신호]를 1라인에
 * 배치한다 (SignalPicker와 동일 동작). 검색 매치 목록은 아래 오버레이로
 * 표시해 행 높이를 유지한다. */
export function CompactSignalPicker({
  dbc,
  rxNode,
  binding,
  onChange,
  messageLabelFor = (m) => m.name,
  showFilter = true,
}: {
  dbc: DbcSummary;
  rxNode: string;
  binding: SignalBinding | undefined;
  onChange: (b: SignalBinding | undefined) => void;
  messageLabelFor?: (m: DbcMessage) => string;
  showFilter?: boolean;
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
    <div className="binding-inline">
      <div className="binding-search-wrap">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="신호검색"
          title="신호 이름 일부 입력"
        />
        {matches.length > 0 && (
          <div className="signal-search-list signal-search-overlay">
            {matches.map(({ message: m, signal: s }) => (
              <div
                key={`${m.name}.${s.name}`}
                className="signal-search-item"
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
      </div>
      <select
        value={binding?.message ?? ''}
        title="CAN 메시지"
        onChange={(e) =>
          onChange(e.target.value ? { message: e.target.value, signal: '' } : undefined)
        }
      >
        <option value="">— 메시지 —</option>
        <MessageOptions dbc={dbc} rxNode={rxNode} filter={msgFilter} labelFor={messageLabelFor} />
      </select>
      {showFilter && <MessageFilter value={msgFilter} onChange={setMsgFilter} />}
      <select
        value={binding?.signal ?? ''}
        title="신호"
        disabled={!message}
        onChange={(e) => {
          if (message) onChange({ message: message.name, signal: e.target.value });
        }}
      >
        <option value="">— 신호 —</option>
        {message?.signals.map((s) => (
          <option key={s.name} value={s.name}>
            {s.name} ({s.length}bit, {s.send_type})
          </option>
        ))}
      </select>
    </div>
  );
}

/** 추가 바인딩 목록 편집기 (슬라이더/버튼 다중 신호용): 행마다 CompactSignalPicker +
 * 삭제 (+ 선택적 행별 추가 UI), 맨 아래 + 신호 추가. 미완성 행은 무시하고,
 * primary(본 바인딩) 및 행 간 중복 선택은 해당 행을 제거한다.
 * 행 업데이트 시 message/signal 외 필드(value 등)는 보존된다. */
export function ExtraBindingsEditor({
  dbc,
  rxNode,
  primary,
  value,
  onChange,
  messageLabelFor = (m) => `${m.name} (0x${m.frame_id.toString(16).toUpperCase()})`,
  renderRowExtra,
  pickerFilter = true,
}: {
  dbc: DbcSummary;
  rxNode: string;
  primary: SignalBinding | undefined;
  value: ExtraBinding[];
  onChange: (next: ExtraBinding[]) => void;
  messageLabelFor?: (m: DbcMessage) => string;
  pickerFilter?: boolean;
  renderRowExtra?: (
    b: ExtraBinding,
    i: number,
    patch: (p: Partial<ExtraBinding>) => void,
  ) => ReactNode;
}) {
  const rows = value ?? [];
  const patchRow = (i: number, p: Partial<ExtraBinding>) => {
    const next = [...rows];
    next[i] = { ...next[i], ...p };
    onChange(next);
  };
  const updateRow = (i: number, nb: SignalBinding | undefined) => {
    const taken = new Set<string>();
    if (primary?.signal) taken.add(bindingKey(primary));
    rows.forEach((x, j) => {
      if (j !== i) {
        const k = bindingKey(x);
        if (k) taken.add(k);
      }
    });
    const next = [...rows];
    const nbMsg = nb?.message ?? '';
    const nbSig = nb?.signal ?? '';
    if (!nbMsg && !nbSig) {
      // 메시지 선택 해제 -- 빈 행으로 유지 (저장 시 정리됨)
      next[i] = { ...next[i], message: '', signal: '' };
    } else if (!nbSig) {
      // 메시지 먼저 선택 (신호는 아직) -- 행 유지, 신호 선택 대기
      next[i] = { ...next[i], message: nbMsg, signal: '' };
    } else if (taken.has(`${nbMsg}.${nbSig}`)) {
      next.splice(i, 1);
    } else {
      next[i] = { ...next[i], message: nbMsg, signal: nbSig };
    }
    onChange(next);
  };
  return (
    <div>
      <p className="hint">추가 신호 (본 신호와 함께 전송됨 — 본 바인딩과 중복 불가)</p>
      {rows.map((b, i) => (
        <div key={i} className="binding-row">
          <CompactSignalPicker
            dbc={dbc}
            rxNode={rxNode}
            binding={b.message || b.signal ? b : undefined}
            onChange={(nb) => updateRow(i, nb)}
            messageLabelFor={messageLabelFor}
            showFilter={pickerFilter}
          />
          {renderRowExtra?.(b, i, (p) => patchRow(i, p))}
          <button
            className="icon-btn"
            title="추가 신호 삭제"
            onClick={() => {
              const next = [...rows];
              next.splice(i, 1);
              onChange(next);
            }}
          >
            ✕
          </button>
        </div>
      ))}
      <button className="small-btn" onClick={() => onChange([...rows, { message: '', signal: '' }])}>
        + 신호 추가
      </button>
    </div>
  );
}

/** 송신 속성 select (Event/Periodic 전역 override — 호출자가 DBC 새로고침).
 * 미바인딩이면 비활성화 표시만. */
export function SendTypeSelect({
  dbc,
  binding,
  onDbcRefresh,
}: {
  dbc: DbcSummary;
  binding: SignalBinding | undefined;
  onDbcRefresh: () => void;
}) {
  const bound = findSignal(dbc, binding);
  return (
    <select
      value={bound?.signal.send_type ?? ''}
      title="송신 속성 (Event: 30ms 후 invalid 값 자동 송신)"
      disabled={!bound}
      style={{ width: 84, flexShrink: 0 }}
      onChange={async (e) => {
        if (!bound) return;
        await api.overrideSendType(bound.message.name, bound.signal.name, e.target.value);
        onDbcRefresh();
      }}
    >
      {!bound && <option value="">—</option>}
      <option value="periodic">Periodic</option>
      <option value="event">Event</option>
    </select>
  );
}

/** 전송값 숫자 입력 (해당 신호의 선언 범위로 클램프). */
export function BindingValueInput({
  dbc,
  binding,
  value,
  onValue,
}: {
  dbc: DbcSummary;
  binding: SignalBinding | undefined;
  value: number;
  onValue: (v: number) => void;
}) {
  const bound = findSignal(dbc, binding);
  const lo = bound ? (bound.signal.minimum ?? signalBitMin(bound.signal)) : undefined;
  const hi = bound ? (bound.signal.maximum ?? signalBitMax(bound.signal)) : undefined;
  return (
    <input
      type="number"
      title="전송값"
      style={{ width: 76, flexShrink: 0 }}
      min={lo}
      max={hi}
      value={String(value)}
      onChange={(e) => {
        const raw = Number(e.target.value);
        onValue(bound ? Math.min(hi!, Math.max(lo!, raw)) : raw);
      }}
    />
  );
}

/** 물리값 파싱: 10진 정수/소수(음수 포함), 0x 16진수, 0b 2진수. 실패 시 null.
 * 버튼 전송값용 -- ManualValue의 raw 파서와 달리 소수를 허용한다. */
export function parsePhysicalValue(input: string): number | null {
  const s = input.trim();
  if (/^0[xX][0-9a-fA-F]+$/.test(s)) return parseInt(s.slice(2), 16);
  if (/^0[bB][01]+$/.test(s)) return parseInt(s.slice(2), 2);
  if (/^-?(\d+(\.\d+)?|\.\d+)$/.test(s)) return parseFloat(s);
  return null;
}

/** 전송값 텍스트 입력 (dec/0x/0b 표기, 물리값 의미): 입력 중 텍스트 유지,
 * 파싱 성공 시 범위 클램프 후 전파, 실패 시 빨간 테두리+타이틀 안내만
 * (1라인 유지를 위해 별도 에러행 없음). 바인딩 변경 시 텍스트 리셋.
 * value 미지정 시 빈칸 + 플레이스홀더 표시 (전송 시 호출자가 폴백).
 * savedText: 저장된 입력 표기 -- 재오픈 시 hex/binary 그대로 표시.
 * 유효 파싱·빈칸만 저장되고 파싱 실패 입력은 로컬에만 남는다. */
export function FlexibleValueInput({
  dbc,
  binding,
  value,
  onValue,
  placeholder,
  savedText,
  onText,
}: {
  dbc: DbcSummary;
  binding: SignalBinding | undefined;
  value?: number;
  onValue: (v: number) => void;
  placeholder?: string;
  savedText?: string;
  onText?: (t: string) => void;
}) {
  const bound = findSignal(dbc, binding);
  const lo = bound ? (bound.signal.minimum ?? signalBitMin(bound.signal)) : undefined;
  const hi = bound ? (bound.signal.maximum ?? signalBitMax(bound.signal)) : undefined;
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
  }, [binding?.message, binding?.signal]);
  const empty = text === null ? savedText === undefined && value === undefined : text.trim() === '';
  const parsed = text === null ? null : parsePhysicalValue(text);
  const invalid = !empty && text !== null && parsed === null;
  const shownText = text ?? savedText ?? (value !== undefined ? String(value) : '');
  const applyText = (t: string) => {
    setText(t);
    if (t.trim() === '') {
      onText?.('');
      return;
    }
    const n = parsePhysicalValue(t);
    if (n !== null) {
      onValue(bound ? Math.min(hi!, Math.max(lo!, n)) : n);
      onText?.(t);
    }
  };
  // VAL_ 선택지가 있으면 TxBox와 동일한 콤보 동작: 포커스/▼ = 입력값 무관
  // 전체 목록 + 현재값 하이라이트, 타이핑 = 포함 필터, 항목 선택 = 숫자 확정.
  const [comboOpen, setComboOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const choiceItems = (() => {
    if (!bound?.signal.choices) return null;
    const q = (query ?? '').trim().toLowerCase();
    return Object.entries(bound.signal.choices)
      .map(([k, v]) => ({ k: Number(k), v }))
      .sort((a, b) => a.k - b.k)
            .filter(({ k, v }) => query === null || q === '' || String(k).includes(q) || v.toLowerCase().includes(q));
  })();
  const closeCombo = () => {
    setComboOpen(false);
    setQuery(null);
  };
  return (
    <span className="tx-combo" style={{ width: 112, flexShrink: 0 }}>
      <input
        className="mono"
        title={
          bound
            ? `전송값 (dec/0x/0b, 범위 ${lo} ~ ${hi})${choiceItems ? ' — 클릭하면 목록 표시' : ''}${invalid ? ' — 잘못된 값 형식' : ''}`
            : '전송값 (dec/0x/0b)'
        }
        placeholder={placeholder}
        style={{
          flex: 1,
          minWidth: 0,
          ...(invalid ? { borderColor: 'var(--bad)' } : {}),
        }}
        value={shownText}
        onChange={(e) => {
          applyText(e.target.value);
          if (choiceItems) setQuery(e.target.value);
        }}
        onFocus={() => {
          if (!choiceItems) return;
          setQuery(null);
          setComboOpen(true);
        }}
        onBlur={() => {
          if (!choiceItems) return;
          // 항목 mousedown이 먼저 처리되도록 blur 닫힘을 한 tick 미룬다
          setTimeout(closeCombo, 120);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            closeCombo();
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      {choiceItems && comboOpen && (
        <div className="tx-combo-list">
          {choiceItems.length === 0 ? (
            <div className="hint">일치 없음 — 직접 입력값을 그대로 사용</div>
          ) : (
            choiceItems.map(({ k, v }) => (
              <div
                key={k}
                className={`tx-combo-hit${String(k) === shownText.trim() ? ' active' : ''}`}
                title={`${v} (${k}) 입력`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  applyText(String(k));
                  closeCombo();
                }}
              >
                <span className="mono">{k}</span>
                <span className="hint">{v}</span>
              </div>
            ))
          )}
        </div>
      )}
    </span>
  );
}

/** 버튼용 1라인 바인딩 행: [신호검색][메시지][신호][전송타입][전송값]. */
export function ButtonBindingRow({
  dbc,
  rxNode,
  binding,
  value,
  onBinding,
  onValue,
  onDbcRefresh,
  messageLabelFor,
}: {
  dbc: DbcSummary;
  rxNode: string;
  binding: SignalBinding | undefined;
  value: number;
  onBinding: (b: SignalBinding | undefined) => void;
  onValue: (v: number) => void;
  onDbcRefresh: () => void;
  messageLabelFor?: (m: DbcMessage) => string;
}) {
  return (
    <div className="binding-row">
      <CompactSignalPicker
        dbc={dbc}
        rxNode={rxNode}
        binding={binding}
        onChange={onBinding}
        messageLabelFor={messageLabelFor}
        showFilter={false}
      />
      <SendTypeSelect dbc={dbc} binding={binding} onDbcRefresh={onDbcRefresh} />
      <FlexibleValueInput dbc={dbc} binding={binding} value={value} onValue={onValue} />
    </div>
  );
}
