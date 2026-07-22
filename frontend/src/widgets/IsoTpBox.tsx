// ISO-TP (ISO 15765-2) message send box.
//
// ID and data are entered in separate fields (hex). Payloads up to 7 bytes
// go out as a single Single Frame; longer payloads are automatically split
// into a First Frame + Consecutive Frame(s) by the backend, which waits for
// a Flow Control frame from the receiver (on the configured FC ID) and
// honors its Block Size / STmin, per ISO 15765-2.

import { useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../store/appContext';
import type { WidgetConfig } from '../types';

const SF_MAX_LEN = 7;
const FF_DATA_LEN = 6;
const CF_DATA_LEN = 7;

function parseHexBytes(input: string): Uint8Array | null {
  const clean = input.replace(/\s+/g, '');
  if (clean.length === 0) return new Uint8Array();
  if (clean.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(clean)) return null;
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function framePreview(byteLen: number): string {
  if (byteLen === 0) return '';
  if (byteLen <= SF_MAX_LEN) return `${byteLen}바이트 → Single Frame`;
  const cfCount = Math.ceil((byteLen - FF_DATA_LEN) / CF_DATA_LEN);
  return `${byteLen}바이트 → First Frame + Consecutive Frame ${cfCount}개 (총 ${1 + cfCount}프레임)`;
}

interface IsoTpOptions {
  txId?: string;
  fcId?: string;
  dataHex?: string;
  isExtended?: boolean;
  fcTimeoutMs?: number;
}

export function IsoTpBox({ config }: { config: WidgetConfig }) {
  const { updateWidget } = useApp();
  const opts = config.options as IsoTpOptions;
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const txId = opts.txId ?? '783';
  const fcId = opts.fcId ?? '78B';
  const dataHex = opts.dataHex ?? '';
  const isExtended = opts.isExtended ?? false;
  const fcTimeoutMs = opts.fcTimeoutMs ?? 1000;

  const setOpt = (patch: Partial<IsoTpOptions>) =>
    updateWidget({ ...config, options: { ...config.options, ...patch } });

  const dataBytes = parseHexBytes(dataHex);
  const txIdNum = txId.trim() ? parseInt(txId, 16) : NaN;
  const fcIdNum = fcId.trim() ? parseInt(fcId, 16) : NaN;
  const needsFc = (dataBytes?.length ?? 0) > SF_MAX_LEN;
  const canSend =
    !sending &&
    dataBytes !== null &&
    dataBytes.length > 0 &&
    Number.isInteger(txIdNum) &&
    txIdNum >= 0 &&
    (!needsFc || (Number.isInteger(fcIdNum) && fcIdNum >= 0));

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    setError(null);
    setResult(null);
    try {
      const r = (await api.isotpSend(txIdNum, needsFc ? fcIdNum : 0, dataHex, {
        is_extended_id: isExtended,
        fc_timeout_ms: fcTimeoutMs,
      })) as { frame_type: string; frames_sent: number; bytes_sent: number; duration_ms: number };
      setResult(
        `${r.frame_type === 'single' ? 'Single Frame' : 'Multi Frame'} 전송 완료 — ` +
          `${r.frames_sent}프레임, ${r.bytes_sent}바이트, ${r.duration_ms}ms`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="isotp-box">
      <div className="isotp-row">
        <label className="isotp-field">
          TX ID (hex)
          <input
            className="mono"
            value={txId}
            placeholder="783"
            onChange={(e) => setOpt({ txId: e.target.value })}
          />
        </label>
        <label className="isotp-field">
          FC ID (hex){!needsFc && <span className="hint"> — 8바이트 이하는 불필요</span>}
          <input
            className="mono"
            value={fcId}
            placeholder="78B"
            disabled={!needsFc}
            onChange={(e) => setOpt({ fcId: e.target.value })}
          />
        </label>
        <label className="isotp-field isotp-field-narrow">
          FC 타임아웃(ms)
          <input
            type="number"
            min={100}
            step={100}
            value={fcTimeoutMs}
            onChange={(e) => setOpt({ fcTimeoutMs: Number(e.target.value) })}
          />
        </label>
        <label className="toggle isotp-ext-toggle">
          <input
            type="checkbox"
            checked={isExtended}
            onChange={(e) => setOpt({ isExtended: e.target.checked })}
          />
          확장 ID
        </label>
      </div>
      <label className="isotp-field isotp-data-field">
        데이터 (hex, 공백 허용)
        <textarea
          className="mono isotp-data-input"
          value={dataHex}
          placeholder="01 02 03 04 05 06 07 08 09 10 11 12 13 14 15"
          onChange={(e) => setOpt({ dataHex: e.target.value })}
        />
      </label>
      <div className="isotp-row">
        <span className="hint">
          {dataBytes === null ? '잘못된 hex 문자열입니다' : framePreview(dataBytes.length)}
        </span>
        <span className="spacer" />
        <button className="small-btn primary" disabled={!canSend} onClick={send}>
          {sending ? '전송 중…' : '▶ 전송'}
        </button>
      </div>
      {result && <div className="isotp-result ok">{result}</div>}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
