// CAN message TX box: up to 20 messages with ID / period / payload,
// applied to the backend scheduler and started/stopped as a group.
// A row can either carry a raw hex payload or reference a DBC message
// (payload then follows the live signal state on the backend).

import { useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { useApp } from '../store/appContext';
import { MessageFilter, MessageOptions, type MessageFilterMode } from './MessageOptions';
import type { TxRow, WidgetConfig } from '../types';

const MAX_ROWS = 20;

/** Group hex digits into "XX XX XX ..." as the user types, so a payload
 * reads byte-by-byte instead of as one long run of digits. */
function formatHexBytes(raw: string): string {
  const hex = raw.replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  return hex.match(/.{1,2}/g)?.join(' ') ?? hex;
}

/** Where the caret should land in the reformatted string, given how many hex
 * digits (spaces excluded) preceded it in the raw input -- every full byte
 * boundary after the first adds one space before it. */
function caretAfterFormat(hexDigitsBeforeCaret: number): number {
  return hexDigitsBeforeCaret + Math.floor(hexDigitsBeforeCaret / 2);
}

export function TxBox({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const rows = (config.options.rows as TxRow[] | undefined) ?? [];
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const txStatus = canStore.status?.tx;

  const setRows = (next: TxRow[]) => {
    updateWidget({ ...config, options: { ...config.options, rows: next } });
    setApplied(false);
  };

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
        isFd: false,
        bitrateSwitch: false,
      },
    ]);
  };

  const patchRow = (key: string, patch: Partial<TxRow>) =>
    setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const apply = async () => {
    try {
      await api.txConfigure(
        rows.map((r) => ({
          key: r.key,
          arbitration_id: r.messageName
            ? dbc.messages?.find((m) => m.name === r.messageName)?.frame_id ?? 0
            : parseInt(r.idHex, 16),
          period_ms: r.periodMs,
          data: r.messageName ? null : r.dataHex.replace(/\s/g, ''),
          message_name: r.messageName,
          enabled: r.enabled,
          periodic: r.periodic,
          is_fd: r.messageName ? false : r.isFd,
          bitrate_switch: r.messageName ? false : r.bitrateSwitch,
        })),
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

  /** One-shot immediate send of a row's current ID/data -- the row's Send
   * button, and (from the data input's onChange) a live edit while running. */
  const sendOnce = async (r: TxRow) => {
    if (r.messageName) return; // DBC-bound rows have no raw id/data to send directly
    try {
      await api.txSendOnce({
        key: r.key,
        arbitration_id: parseInt(r.idHex, 16),
        data: r.dataHex.replace(/\s/g, ''),
        is_fd: r.isFd,
        bitrate_switch: r.bitrateSwitch,
      });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleDataInputChange = (r: TxRow, e: React.ChangeEvent<HTMLInputElement>) => {
    const input = e.target;
    const caret = input.selectionStart ?? input.value.length;
    const hexDigitsBeforeCaret = input.value.slice(0, caret).replace(/[^0-9a-fA-F]/g, '').length;
    const formatted = formatHexBytes(input.value);
    patchRow(r.key, { dataHex: formatted });
    const newCaret = caretAfterFormat(hexDigitsBeforeCaret);
    requestAnimationFrame(() => input.setSelectionRange(newCaret, newCaret));
    // Start 상태(전체 실행 중)에서 데이터 값을 바꾸면 다음 주기 tick을 기다리지
    // 않고 바로 그 값을 한 번 전송한다.
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
                    onChange={(e) =>
                      patchRow(r.key, { messageName: e.target.value || null })
                    }
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
                {r.messageName ? (
                  <span className="hint">신호 상태값 사용</span>
                ) : (
                  <input
                    className="mono data-input"
                    value={r.dataHex}
                    onChange={(e) => handleDataInputChange(r, e)}
                  />
                )}
              </td>
              <td>
                {r.messageName ? (
                  <span className="hint" title="DBC 메시지의 FD 속성을 따름">
                    {dbc.messages?.find((m) => m.name === r.messageName)?.is_fd ? 'FD' : '-'}
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
                  disabled={!!r.messageName}
                  title="현재 ID/데이터 값을 즉시 한 번 전송"
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
