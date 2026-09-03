// ISO-TP (ISO 15765-2) message send box — multi-line data input.
// 한 줄 = 한 번의 ISO-TP 전송. 커서가 위치한 라인의 메시지만 전송하고,
// 해당 라인은 연한 파란색으로 강조. 빈 줄은 무시+전송 비활성화, # 또는 // 로
// 시작하는 줄은 주석으로 무시 (인라인 주석도 지원: 데이터 뒤 #// 이후는 주석).

import { useEffect, useRef, useState } from 'react';
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

function splitLines(raw: string): string[] {
  return raw.split(/\r?\n/);
}

function getDataPart(line: string): string {
  // 인라인 주석 지원: # 또는 // 이후는 주석
  const hashIdx = line.indexOf('#');
  const slashIdx = line.indexOf('//');
  let cut = -1;
  if (hashIdx !== -1 && slashIdx !== -1) cut = Math.min(hashIdx, slashIdx);
  else if (hashIdx !== -1) cut = hashIdx;
  else if (slashIdx !== -1) cut = slashIdx;
  if (cut !== -1) return line.slice(0, cut);
  return line;
}

function isCommentLine(line: string): boolean {
  const t = line.trim();
  return t.startsWith('#') || t.startsWith('//');
}

interface IsoTpOptions {
  txId?: string;
  fcId?: string;
  dataHex?: string;
  isExtended?: boolean;
  fcTimeoutMs?: number;
  waitForResponse?: boolean;
  respId?: string;
  respTimeoutMs?: number;
  respFcBlockSize?: number;
}

export function IsoTpBox({ config }: { config: WidgetConfig }) {
  const { updateWidget } = useApp();
  const opts = config.options as IsoTpOptions;
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [response, setResponse] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const txId = opts.txId ?? '783';
  const fcId = opts.fcId ?? '78B';
  const dataHex = opts.dataHex ?? '';
  const isExtended = opts.isExtended ?? false;
  const fcTimeoutMs = opts.fcTimeoutMs ?? 1000;
  const waitForResponse = opts.waitForResponse ?? true;
  const respId = opts.respId ?? '78B';
  const respTimeoutMs = opts.respTimeoutMs ?? 2000;
  const respFcBlockSize = opts.respFcBlockSize ?? 0;

  const setOpt = (patch: Partial<IsoTpOptions>) =>
    updateWidget({ ...config, options: { ...config.options, ...patch } });

  // 커서 라인 추적
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  const [cursorLine, setCursorLine] = useState(0);

  const lines = splitLines(dataHex);
  const safeCursorLine = Math.min(cursorLine, Math.max(0, lines.length - 1));
  const activeRawLine = lines[safeCursorLine] ?? '';
  const activeDataPart = getDataPart(activeRawLine);
  const activeTrimmed = activeDataPart.trim();
  const activeIsEmpty = activeTrimmed === '';
  const activeIsComment = !activeIsEmpty && isCommentLine(activeRawLine);
  // 주석/빈 줄은 전송 대상 아님 — 데이터 부분만 파싱
  const activeDataBytes = activeIsEmpty || activeIsComment ? new Uint8Array() : parseHexBytes(activeDataPart);
  const activeByteLen = activeDataBytes?.length ?? 0;
  const activeIsValidHex = activeDataBytes !== null;
  const isActiveLineSendable = activeIsValidHex && activeByteLen > 0;

  const updateCursorLine = () => {
    const el = textareaRef.current;
    if (!el) return;
    const pos = el.selectionStart ?? 0;
    const before = el.value.slice(0, pos);
    const lineIdx = before.split('\n').length - 1;
    setCursorLine(Math.max(0, Math.min(lineIdx, splitLines(el.value).length - 1)));
  };

  const syncScroll = () => {
    const ta = textareaRef.current;
    const hl = highlightRef.current;
    if (ta && hl) hl.scrollTop = ta.scrollTop;
  };

  useEffect(() => {
    // dataHex가 외부에서 바뀌면(레이아웃 로드 등) 커서 범위를 보정
    setCursorLine((prev) => Math.min(prev, Math.max(0, splitLines(dataHex).length - 1)));
  }, [dataHex]);

  const txIdNum = txId.trim() ? parseInt(txId, 16) : NaN;
  const fcIdNum = fcId.trim() ? parseInt(fcId, 16) : NaN;
  const respIdNum = respId.trim() ? parseInt(respId, 16) : NaN;
  const needsFc = activeByteLen > SF_MAX_LEN;
  const canSend =
    !sending &&
    isActiveLineSendable &&
    Number.isInteger(txIdNum) &&
    txIdNum >= 0 &&
    (!needsFc || (Number.isInteger(fcIdNum) && fcIdNum >= 0)) &&
    (!waitForResponse || (Number.isInteger(respIdNum) && respIdNum >= 0));

  const send = async () => {
    if (!canSend) return;
    // 빈 줄/주석은 전송 불가 — canSend에서 이미 차단되지만 방어
    if (activeIsEmpty || activeIsComment) {
      setError('현재 라인이 비어 있거나 주석입니다');
      return;
    }
    if (!activeIsValidHex) {
      setError('잘못된 hex 문자열입니다 (현재 라인)');
      return;
    }
    setSending(true);
    setError(null);
    setResult(null);
    setResponse(null);
    try {
      // 라인 단위 전송: 데이터 부분만 전송 (인라인 주석 제거)
      const payloadHex = activeDataPart.trim();
      const r = await api.isotpSend(txIdNum, needsFc ? fcIdNum : 0, payloadHex, {
        is_extended_id: isExtended,
        fc_timeout_ms: fcTimeoutMs,
        ...(waitForResponse
          ? {
              resp_id: respIdNum,
              resp_timeout_ms: respTimeoutMs,
              resp_fc_block_size: respFcBlockSize,
            }
          : {}),
      });
      setResult(
        `${safeCursorLine + 1}번째 줄 — ${r.frame_type === 'single' ? 'Single Frame' : 'Multi Frame'} 전송 완료 — ` +
          `${r.frames_sent}프레임, ${r.bytes_sent}바이트, ${r.duration_ms}ms`,
      );
      if (r.response !== undefined) setResponse(r.response);
      else if (r.response_error !== undefined) setError(`응답 수신 실패: ${r.response_error}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  // 힌트: 현재 라인 기준 미리보기
  let hintText: string;
  if (activeIsEmpty) hintText = '현재 라인: 비어 있음 — 전송 비활성화';
  else if (activeIsComment) hintText = '현재 라인: 주석 — 전송 비활성화';
  else if (!activeIsValidHex) hintText = '잘못된 hex 문자열입니다 (현재 라인)';
  else hintText = `${safeCursorLine + 1}번째 줄: ${framePreview(activeByteLen)}`;

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
        데이터 (hex, 공백 허용) — 한 줄 = 한 번 전송, # 또는 // 이후는 주석
        <div className="isotp-data-wrap">
          <div ref={highlightRef} className="isotp-data-highlight" aria-hidden>
            {lines.map((ln, i) => (
              <div key={i} className={i === safeCursorLine ? 'isotp-line-active' : 'isotp-line'}>
                {ln || '\u00A0'}
              </div>
            ))}
          </div>
          <textarea
            ref={textareaRef}
            className="mono isotp-data-input isotp-data-input--overlay"
            value={dataHex}
            placeholder={'01 02 03\n# 주석은 전송 안 함\n02 10 01 // 인라인 주석도 가능'}
            onChange={(e) => {
              setOpt({ dataHex: e.target.value });
              requestAnimationFrame(updateCursorLine);
            }}
            onSelect={updateCursorLine}
            onClick={updateCursorLine}
            onKeyUp={updateCursorLine}
            onKeyDown={updateCursorLine}
            onScroll={syncScroll}
            onFocus={updateCursorLine}
          />
        </div>
      </label>
      <div className="isotp-row">
        <label className="toggle">
          <input
            type="checkbox"
            checked={waitForResponse}
            onChange={(e) => setOpt({ waitForResponse: e.target.checked })}
          />
          응답 대기
        </label>
        {waitForResponse && (
          <>
            <label className="isotp-field">
              응답 ID (hex)
              <input
                className="mono"
                value={respId}
                placeholder="78B"
                onChange={(e) => setOpt({ respId: e.target.value })}
              />
            </label>
            <label className="isotp-field isotp-field-narrow">
              응답 타임아웃(ms)
              <input
                type="number"
                min={100}
                step={100}
                value={respTimeoutMs}
                onChange={(e) => setOpt({ respTimeoutMs: Number(e.target.value) })}
              />
            </label>
            <label
              className="isotp-field isotp-field-narrow"
              title="응답이 여러 프레임일 때 이 값(개)마다 새 Flow Control을 보냄 (0 = 무제한, 한 번만 보냄)"
            >
              응답 FC Block Size
              <input
                type="number"
                min={0}
                step={1}
                value={respFcBlockSize}
                onChange={(e) => setOpt({ respFcBlockSize: Math.max(0, Number(e.target.value)) })}
              />
            </label>
          </>
        )}
      </div>
      <div className="isotp-row">
        <span className="hint">{hintText}</span>
        <span className="spacer" />
        <button className="small-btn primary" disabled={!canSend} onClick={send}>
          {sending ? '전송 중…' : `▶ 전송 (${safeCursorLine + 1}번째 줄)`}
        </button>
      </div>
      {result && <div className="isotp-result ok">{result}</div>}
      {response && (
        <div className="isotp-result ok">
          응답: <span className="mono">{response}</span>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
