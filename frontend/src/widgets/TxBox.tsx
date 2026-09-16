// CAN message TX box: up to 20 messages with ID / period / payload,
// applied to the backend scheduler and started/stopped as a group.
// A row either carries a raw hex payload, or references a DBC message --
// in signal mode each signal gets an editor (defaults from the DBC) whose
// values are stored on apply and sent on Start/Send, or the row can switch
// to manual hex override mode (payload then sent raw under the DBC frame_id).

import { useRef, useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { useApp } from '../store/appContext';
import { MessageFilter, MessageOptions, type MessageFilterMode } from './MessageOptions';
import type { DbcMessage, DbcSignal, TxRow, WidgetConfig } from '../types';

const MAX_ROWS = 20;
const MAX_CLASSIC_LEN = 8;
const MAX_FD_LEN = 64;
// CAN-FD에서 실제로 전송 가능한 DLC 길이 (ISO 11898-1)
const FD_VALID_LENGTHS = new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]);
const FD_VALID_LIST_TEXT = '0~8, 12, 16, 20, 24, 32, 48, 64';

/** Hex를 바이트 단위 "XX XX ..."로 포맷하고, 8바이트마다 줄바꿈한다.
 * 데이터 입력칸의 폭(8바이트 분량)은 그대로 두고, FD 64바이트까지는
 * 최대 8줄로 입력받기 위함이다. 전송 시에는 공백/개행을 모두 제거한다. */
function formatHexBytes(raw: string): string {
  const hex = raw.replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  const bytes = hex.match(/.{1,2}/g);
  if (!bytes) return hex;
  const lines: string[] = [];
  for (let i = 0; i < bytes.length; i += 8) {
    lines.push(bytes.slice(i, i + 8).join(' '));
  }
  return lines.join('\n');
}

/** 포맷된 문자열에서 캐럿 위치: 캐럿 앞 hex 자릿수(k) 기준.
 * 바이트 사이 구분자(공백/개행, 모두 1자)를 더하고, 바이트 경계에서
 * 입력을 이어가면 뒤따르는 구분자를 건너뛰도록 +1 한다. */
function caretAfterFormat(hexDigitsBeforeCaret: number, totalHexDigits: number): number {
  const b = Math.floor(hexDigitsBeforeCaret / 2);
  const hasHalf = hexDigitsBeforeCaret % 2 === 1;
  const seps = hasHalf ? b : Math.max(0, b - 1);
  let pos = hexDigitsBeforeCaret + seps;
  if (!hasHalf && hexDigitsBeforeCaret > 0 && hexDigitsBeforeCaret < totalHexDigits) pos += 1;
  return pos;
}

/** dataHex의 바이트 수 (공백/개행 제외, 홀수 자릿수는 버림) */
function dataByteLen(dataHex: string): number {
  return Math.floor(dataHex.replace(/\s/g, '').length / 2);
}

/** raw 페이로드 검증 -- 어긋나면 한글 에러 문구, 정상이면 null.
 * FD 해제 시 초과분은 자동 절삭하지 않고 에러로 알려 수정하도록 유도한다. */
function validateRawData(dataHex: string, isFd: boolean): string | null {
  const clean = dataHex.replace(/\s/g, '');
  if (clean.length === 0) return null;
  if (!/^[0-9a-fA-F]*$/.test(clean)) return '잘못된 hex 문자열입니다';
  if (clean.length % 2 !== 0) return 'hex 문자열 길이가 홀수입니다 (2자리=1바이트로 입력하세요)';
  const n = clean.length / 2;
  if (!isFd && n > MAX_CLASSIC_LEN) {
    return `${n}바이트는 classic CAN 최대 ${MAX_CLASSIC_LEN}바이트를 초과합니다. FD를 체크하면 최대 ${MAX_FD_LEN}바이트까지 전송할 수 있습니다`;
  }
  if (isFd) {
    if (n > MAX_FD_LEN) return `${n}바이트는 CAN-FD 최대 ${MAX_FD_LEN}바이트를 초과합니다`;
    if (!FD_VALID_LENGTHS.has(n)) {
      return `CAN-FD에서는 ${n}바이트 길이를 사용할 수 없습니다 (가능한 길이: ${FD_VALID_LIST_TEXT})`;
    }
  }
  return null;
}

/** textarea 표시 줄 수: 8바이트당 1줄, 1~8줄 */
function rowsForData(dataHex: string): number {
  return Math.min(8, Math.max(1, Math.ceil(Math.max(1, dataByteLen(dataHex)) / 8)));
}

/** 신호 raw bit幅의 물리값 범위 */
function signalPhysBounds(s: DbcSignal): { min: number; max: number } {
  const rawMin = s.is_signed ? -(2 ** (s.length - 1)) : 0;
  const rawMax = s.is_signed ? 2 ** (s.length - 1) - 1 : 2 ** s.length - 1;
  return { min: rawMin * s.scale + s.offset, max: rawMax * s.scale + s.offset };
}

/** 에디터 한 신호의 표시 범위 (DBC minimum/maximum 우선, 없으면 bit幅) */
function signalDisplayRange(s: DbcSignal): { min: number; max: number } {
  const b = signalPhysBounds(s);
  return { min: s.minimum ?? b.min, max: s.maximum ?? b.max };
}

/** 신호 에디터 기본 텍스트: DBC 정의 default, 없으면 Invalid(최대값).
 * VAL_ 선택지가 있는 신호는 선택지에 없는 값으로 두면 전송이 실패하므로,
 * 기본값이 선택지에 없을 때는 첫 번째 선택지로 둔다. */
function defaultSignalText(s: DbcSignal): string {
  if (s.choices) {
    const keys = Object.keys(s.choices).map(Number).sort((a, b) => a - b);
    if (keys.length === 0) return '';
    const d = s.default_value;
    return String(d != null && keys.includes(d) ? d : keys[0]);
  }
  if (s.default_value != null) return String(s.default_value);
  return String(signalPhysBounds(s).max); // Invalid raw의 물리값
}

/** 행의 신호별 입력 텍스트를 전송용 물리값 dict로 변환 + 검증 */
function parseSignalValues(
  msg: DbcMessage,
  texts: Record<string, string> | undefined,
): { values: Record<string, number>; error: string | null } {
  const values: Record<string, number> = {};
  for (const s of msg.signals) {
    const t = (texts?.[s.name] ?? defaultSignalText(s)).trim();
    const num = Number(t);
    if (t === '' || !Number.isFinite(num)) {
      return { values, error: `"${s.name}": 숫자 값을 입력하세요` };
    }
    if (s.choices) {
      const keys = Object.keys(s.choices).map(Number);
      if (!keys.includes(num)) {
        return { values, error: `"${s.name}": 선택지 [${keys.join(', ')}] 중 입력하세요` };
      }
    } else {
      const { min, max } = signalDisplayRange(s);
      if (num < min || num > max) {
        return { values, error: `"${s.name}": 범위 초과 (${min} ~ ${max})` };
      }
    }
    values[s.name] = num;
  }
  return { values, error: null };
}

export function TxBox({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const rows = (config.options.rows as TxRow[] | undefined) ?? [];
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const txStatus = canStore.status?.tx;

  // rows의 최신값 미러 -- 비동기 연속부(await 이후)에서 클로저의 오래된
  // rows로 상태를 덮어써 선택이 되돌아가는 버그 방지 (raw ID 선택 후
  // 메시지 선택이 raw ID로 되돌아갔던 원인).
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  const setRows = (next: TxRow[] | ((prev: TxRow[]) => TxRow[])) => {
    updateWidget({
      ...config,
      options: {
        ...config.options,
        rows: typeof next === 'function' ? next(rowsRef.current) : next,
      },
    });
    setApplied(false);
  };

  /** 실제 연결된 버스의 FD 상태 -- 새 행의 FD 기본값으로 쓴다.
   * canConfig(연결 설정 UI 값)가 아닌 live 상태 기준: classic 버스에
   * FD 프레임을 보내면 드라이버 오류/손실로 이어질 수 있기 때문이다. */
  const busFd = canStore.status?.can.config.fd === true;

  const addRow = () => {
    if (rows.length >= MAX_ROWS) return;
    setRows([
      ...rows,
      {
        key: `${Date.now()}-${rows.length}`,
        idHex: '100',
        periodMs: 100,
        dataHex: '00 00 00 00 00 00 00 00',
        messageName: null,
        enabled: true,
        periodic: true,
        isFd: busFd,
        bitrateSwitch: false,
        rawOverride: false,
      },
    ]);
  };

  const patchRow = (key: string, patch: Partial<TxRow>) =>
    setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  /** DBC 메시지 조회 (길이/FD 표시 및 frame_id 고정용) */
  const dbcMessageOf = (name: string | null) =>
    name ? (dbc.messages?.find((m) => m.name === name) ?? null) : null;

  /** DBC 메시지 선택: 신호 편집 모드를 기본으로 한다. hex 직접 입력용
   * 데이터칸에는 DBC 정의 초기값(미정의 신호는 Invalid)을 백그라운드로
   * 프리필해 둔다 (직접 입력으로 전환 시 사용). FD 기본값은 연결된 버스
   * 기준. 초기값 조회 실패 시 DBC 길이만큼 00으로 채운다. */
  const selectMessage = async (key: string, name: string | null) => {
    const row = rowsRef.current.find((r) => r.key === key);
    if (!row) return;
    if (!name) {
      patchRow(key, { messageName: null, rawOverride: false });
      return;
    }
    const msg = dbcMessageOf(name);
    patchRow(key, { messageName: name, rawOverride: false, isFd: busFd, bitrateSwitch: false });
    setApplied(false);
    let initialHex: string | null = null;
    try {
      const res = await api.getDbcMessageInitial(name);
      initialHex = res.data_hex;
    } catch {
      initialHex = null;
    }
    if (initialHex == null && msg) {
      initialHex = Array(msg.length).fill('00').join(' ');
    }
    if (initialHex != null) {
      setRows((prev) => {
        const cur = prev.find((r) => r.key === key);
        // 사용자가 그 사이 다른 메시지로 바꿨으면 덮어쓰지 않는다
        if (!cur || cur.messageName !== name) return prev;
        return prev.map((r) => (r.key === key ? { ...r, dataHex: formatHexBytes(initialHex as string) } : r));
      });
      setApplied(false);
    }
  };

  /** 행 라벨 (검증 에러 표시용) */
  const rowLabel = (r: TxRow) => r.messageName ?? `ID ${r.idHex || '(미입력)'}`;

  const apply = async () => {
    try {
      // 신호 모드 행의 에디터 값은 전송 없이 백엔드 상태로 저장(preset) --
      // 이후 Start(주기 재전송)와 Send(1회 전송)가 이 값을 사용한다.
      // raw 행은 기존대로 길이 검증만 한다.
      for (const r of rows) {
        if (!r.enabled) continue;
        const rawMode = !r.messageName || r.rawOverride;
        if (!rawMode) {
          const msg = dbcMessageOf(r.messageName);
          if (!msg) throw new Error(`"${rowLabel(r)}": DBC에 메시지가 없습니다`);
          const { values, error } = parseSignalValues(msg, r.signalValues);
          if (error) throw new Error(`"${rowLabel(r)}": ${error}`);
          await api.txSignalPreset(msg.name, values);
          continue;
        }
        const err = validateRawData(r.dataHex, r.isFd);
        if (err) throw new Error(`"${rowLabel(r)}": ${err}`);
        if (!r.messageName && !/^[0-9a-fA-Fa-f]+$/.test(r.idHex.trim())) {
          throw new Error(`"${rowLabel(r)}": ID(hex)를 입력하세요`);
        }
      }
      await api.txConfigure(
        rows.map((r) => {
          const msg = dbcMessageOf(r.messageName);
          const rawMode = !r.messageName || r.rawOverride;
          return {
            key: r.key,
            // DBC 행(신호/오버라이드 모두)의 ID는 DBC frame_id로 고정
            arbitration_id: msg ? msg.frame_id : parseInt(r.idHex, 16),
            period_ms: r.periodMs,
            data: rawMode ? r.dataHex.replace(/\s/g, '') : null,
            message_name: rawMode ? null : r.messageName,
            enabled: r.enabled,
            periodic: r.periodic,
            is_fd: rawMode ? r.isFd : (msg?.is_fd ?? false),
            bitrate_switch: rawMode ? r.bitrateSwitch : (msg?.is_fd ?? false),
          };
        }),
      );
      setApplied(true);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const start = async () => {
    try {
      if (!applied) await apply();
      await api.txStart();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  /** 행별 Send 버튼: raw 행은 현재 ID/데이터를, DBC 신호 모드 행은
   * 에디터 값을 즉시 한 번 전송한다 (Event/Periodic 규칙 따름).
   * 수동 오버라이드 행은 DBC의 frame_id로 고정해 전송한다. */
  const sendOnce = async (r: TxRow) => {
    const rawMode = !r.messageName || r.rawOverride;
    if (!rawMode) {
      const msg = dbcMessageOf(r.messageName);
      if (!msg) {
        setError(`"${rowLabel(r)}": DBC에 메시지가 없습니다`);
        return;
      }
      const { values, error } = parseSignalValues(msg, r.signalValues);
      if (error) {
        setError(`"${rowLabel(r)}": ${error}`);
        return;
      }
      try {
        await api.txSignal(msg.name, values);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    const err = validateRawData(r.dataHex, r.isFd);
    if (err) {
      setError(`"${rowLabel(r)}": ${err}`);
      return;
    }
    const msg = dbcMessageOf(r.messageName);
    try {
      await api.txSendOnce({
        key: r.key,
        arbitration_id: msg ? msg.frame_id : parseInt(r.idHex, 16),
        data: r.dataHex.replace(/\s/g, ''),
        is_fd: r.isFd,
        bitrate_switch: r.bitrateSwitch,
      });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleDataInputChange = (r: TxRow, e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const input = e.target;
    const caret = input.selectionStart ?? input.value.length;
    const hexDigitsBeforeCaret = input.value.slice(0, caret).replace(/[^0-9a-fA-F]/g, '').length;
    const formatted = formatHexBytes(input.value);
    const totalHexDigits = formatted.replace(/[^0-9a-fA-F]/g, '').length;
    patchRow(r.key, { dataHex: formatted });
    const newCaret = caretAfterFormat(hexDigitsBeforeCaret, totalHexDigits);
    requestAnimationFrame(() => input.setSelectionRange(newCaret, newCaret));
    // Start 상태(전체 실행 중)에서 데이터 값을 바꾸면 다음 주기 tick을 기다리지
    // 않고 바로 그 값을 한 번 전송한다. 입력 중인 값(홀수 자릿수 등)은
    // sendOnce 내부 검증에서 차단되고 에러로 표시된다.
    if (txStatus?.running) sendOnce({ ...r, dataHex: formatted });
  };

  const txCount = (key: string) =>
    txStatus?.entries.find((e) => e.key === key)?.tx_count ?? 0;

  return (
    <div className="tx-box">
      <div className="tx-toolbar">
        <button className="small-btn" onClick={addRow} disabled={rows.length >= MAX_ROWS}>
          + 메시지 추가 ({rows.length}/{MAX_ROWS})
        </button>
        <MessageFilter value={msgFilter} onChange={setMsgFilter} />
        <span className="spacer" />
        <button
          className={`small-btn ${txStatus?.running ? '' : 'primary'}`}
          onClick={start}
          disabled={txStatus?.running}
        >
          ▶ Start
        </button>
        <button
          className={`small-btn ${txStatus?.running ? 'danger' : ''}`}
          onClick={() => api.txStop()}
          disabled={!txStatus?.running}
        >
          ■ Stop
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      <table className="tx-table">
        <thead>
          <tr>
            <th></th>
            <th>ID(hex) / DBC 메시지</th>
            <th title="체크하면 Start 상태에서 주기(ms)마다 자동 재전송, 해제하면 Send 버튼이나 데이터 값 변경 시에만 전송">주기</th>
            <th>주기(ms)</th>
            <th>데이터(hex)</th>
            <th>FD</th>
            <th></th>
            <th>Cnt</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>
                <input
                  type="checkbox"
                  checked={r.enabled}
                  onChange={(e) => patchRow(r.key, { enabled: e.target.checked })}
                />
              </td>
              <td>
                {dbc.loaded ? (
                  <select
                    value={r.messageName ?? ''}
                    onChange={(e) => selectMessage(r.key, e.target.value || null)}
                  >
                    <option value="">raw ID</option>
                    <MessageOptions
                      dbc={dbc}
                      rxNode={canStore.getRxNode()}
                      filter={msgFilter}
                      labelFor={(m) => m.name}
                    />
                  </select>
                ) : null}
                {!r.messageName && (
                  <input
                    className="mono id-input"
                    value={r.idHex}
                    onChange={(e) => patchRow(r.key, { idHex: e.target.value })}
                  />
                )}
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={r.periodic}
                  title="주기 재전송 여부"
                  onChange={(e) => patchRow(r.key, { periodic: e.target.checked })}
                />
              </td>
              <td>
                <input
                  type="number"
                  className="period-input"
                  min={1}
                  value={r.periodMs}
                  disabled={!r.periodic}
                  onChange={(e) => patchRow(r.key, { periodMs: Number(e.target.value) })}
                />
              </td>
              <td>
                {r.messageName && !r.rawOverride ? (
                  <div className="tx-signal-cell">
                    {(() => {
                      const msg = dbcMessageOf(r.messageName);
                      if (!msg) return <span className="hint">DBC에 메시지가 없습니다</span>;
                      const len = msg.length;
                      const fd = msg.is_fd ? 'FD' : 'Classic';
                      return (
                        <>
                          <div className="tx-override-note">
                            <span className="hint">
                              {msg.name} ({len}B · {fd})
                            </span>
                            <button
                              className="small-btn"
                              title="신호 상태값 대신 hex 데이터를 직접 입력해 전송 (최대 64B, FD 시 8바이트 단위 줄바꿈)"
                              onClick={() => patchRow(r.key, { rawOverride: true })}
                            >
                              직접 입력
                            </button>
                          </div>
                          {msg.signals.map((s) => {
                            const text = r.signalValues?.[s.name] ?? defaultSignalText(s);
                            const setSignalText = (v: string) =>
                              patchRow(r.key, {
                                signalValues: { ...(r.signalValues ?? {}), [s.name]: v },
                              });
                            const { min, max } = signalDisplayRange(s);
                            return (
                              <label
                                key={s.name}
                                className="tx-signal-row"
                                title={`${s.name} (${s.length}bit${s.is_signed ? ', signed' : ''}, ${s.send_type}) 범위 ${min} ~ ${max}`}
                              >
                                <span className="tx-signal-name mono">{s.name}</span>
                                {s.choices ? (
                                  <select
                                    className="tx-signal-input"
                                    value={text}
                                    onChange={(e) => setSignalText(e.target.value)}
                                  >
                                    {Object.entries(s.choices)
                                      .map(([k, v]) => ({ k: Number(k), v }))
                                      .sort((a, b) => a.k - b.k)
                                      .map(({ k, v }) => (
                                        <option key={k} value={String(k)}>
                                          {v} ({k})
                                        </option>
                                      ))}
                                  </select>
                                ) : (
                                  <input
                                    className="mono tx-signal-input"
                                    value={text}
                                    onChange={(e) => setSignalText(e.target.value)}
                                  />
                                )}
                                <span className="hint">{s.unit ?? ''}</span>
                              </label>
                            );
                          })}
                        </>
                      );
                    })()}
                  </div>
                ) : (
                  <div className="tx-data-cell">
                    <textarea
                      className="mono data-input data-input--fd"
                      rows={rowsForData(r.dataHex)}
                      value={r.dataHex}
                      onChange={(e) => handleDataInputChange(r, e)}
                    />
                    {(() => {
                      const err = validateRawData(r.dataHex, r.isFd);
                      const n = dataByteLen(r.dataHex);
                      const max = r.isFd ? MAX_FD_LEN : MAX_CLASSIC_LEN;
                      return (
                        <div className={err ? 'byte-count byte-count--error' : 'byte-count'}>
                          {n}/{max}B
                        </div>
                      );
                    })()}
                    {r.messageName && (
                      <div className="tx-override-note">
                        <span className="hint">
                          ID는 DBC(0x
                          {dbcMessageOf(r.messageName)?.frame_id.toString(16).toUpperCase()})로 고정
                        </span>
                        <button
                          className="small-btn"
                          title="신호 상태값 전송 모드로 복귀"
                          onClick={() => patchRow(r.key, { rawOverride: false })}
                        >
                          신호 사용
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </td>
              <td>
                {r.messageName && !r.rawOverride ? (
                  <span className="hint" title="신호 에디터 값은 Start/Send 시점에 전송되며 DBC 메시지의 FD 속성을 따름">
                    {dbcMessageOf(r.messageName)?.is_fd ? 'FD' : '-'}
                  </span>
                ) : (
                  <span className="fd-controls">
                    <label title="CAN-FD (최대 64바이트)">
                      <input
                        type="checkbox"
                        checked={r.isFd}
                        onChange={(e) =>
                          patchRow(r.key, {
                            isFd: e.target.checked,
                            bitrateSwitch: e.target.checked && r.bitrateSwitch,
                          })
                        }
                      />
                      F
                    </label>
                    <label title="Bitrate switch (데이터 위상 고속 전송)">
                      <input
                        type="checkbox"
                        checked={r.bitrateSwitch}
                        disabled={!r.isFd}
                        onChange={(e) => patchRow(r.key, { bitrateSwitch: e.target.checked })}
                      />
                      B
                    </label>
                  </span>
                )}
              </td>
              <td>
                <button
                  className="small-btn"
                  title="현재 값을 즉시 한 번 전송 (신호 모드: 에디터 값, raw: ID/데이터)"
                  onClick={() => sendOnce(r)}
                >
                  Send
                </button>
              </td>
              <td>{txCount(r.key)}</td>
              <td>
                <button
                  className="icon-btn"
                  onClick={() => setRows(rows.filter((x) => x.key !== r.key))}
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={9} className="empty">
                "+ 메시지 추가"로 전송할 메시지를 등록하세요
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
