// CAN message TX box: up to 20 messages with ID / period / payload.
// Split view: the top table lists messages, the bottom panel edits the
// selected message's signals (or raw hex payload). Each row transmits
// independently through its own Send button -- one exact frame per click,
// or a periodic toggle when the row's 주기 box is checked (button turns
// red while transmitting, pressing again stops just that row). A row
// either carries a raw hex payload, or references a DBC message -- in
// signal mode each signal gets an editor (defaults from the DBC), or the
// row can switch to manual hex override mode (payload then sent raw under
// the DBC frame_id). A signal search box adds the owning message as a new
// row.

import { useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { sortedMessages, useApp } from '../store/appContext';
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

/** 행의 신호별 입력 텍스트를 전송용 물리값 dict로 변환 + 검증.
 * VAL_ 선택지가 있어도 직접 입력을 허용한다 (에디터가 datalist 제안 +
 * 자유 타이핑): 선택지 멤버십이 아니라 표시 범위(min/max) 검사만 한다. */
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
    const { min, max } = signalDisplayRange(s);
    if (num < min || num > max) {
      return { values, error: `"${s.name}": 범위 초과 (${min} ~ ${max})` };
    }
    values[s.name] = num;
  }
  return { values, error: null };
}

/** VAL_ 선택지 + 직접 입력 콤보 (TxBox 신호 에디터 전용). 네이티브
 * datalist는 입력값 기준으로 제안을 필터링해서(기본값이 Invalid인 신호가
 * 많음) 전체 목록이 안 보이기 때문에 커스텀 드롭다운을 쓴다: 클릭/포커스·▼
 * 버튼에서는 입력값과 무관하게 VAL_ 전체를 항상 표시하고(현재값 하이라이트),
 * 타이핑할 때만 포함 필터가 걸린다. 입력값은 그대로 유지된다.
 * openKey는 호출자가 행·신호·칸까지 포함해 유일하게 만든다. */
function ChoiceComboInput({
  openKey,
  openListKey,
  setOpenListKey,
  choices,
  value,
  onChange,
}: {
  openKey: string;
  openListKey: string | null;
  setOpenListKey: Dispatch<SetStateAction<string | null>>;
  choices: Record<string, string>;
  value: string;
  onChange: (v: string) => void;
}) {
  const open = openListKey === openKey;
  // 목록 필터는 입력값(value)이 아니라 별도 query로 관리: null = 전체 표시.
  // 포커스/▼ 클릭 시 null로 리셋해 현재 숫자와 무관하게 전체를 보여주고,
  // 타이핑할 때만 query가 갱신되어 필터가 걸린다.
  const [query, setQuery] = useState<string | null>(null);
  const q = (query ?? '').trim().toLowerCase();
  const current = value.trim();
  const items = Object.entries(choices)
    .map(([k, v]) => ({ k: Number(k), v }))
    .sort((a, b) => a.k - b.k)
    .filter(({ k, v }) => query === null || q === '' || String(k).includes(q) || v.toLowerCase().includes(q));
  return (
    <div className="tx-combo">
      <input
        className="mono tx-signal-input"
        value={value}
        title="선택지에서 고르거나 직접 입력"
        onChange={(e) => {
          onChange(e.target.value);
          setQuery(e.target.value);
        }}
        onFocus={() => {
          setQuery(null);
          setOpenListKey(openKey);
        }}
        onBlur={() => {
          // 항목 mousedown이 먼저 처리되도록 blur 닫힘을 한 tick 미룬다
          setTimeout(() => {
            setOpenListKey((cur) => (cur === openKey ? null : cur));
            setQuery(null);
          }, 120);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpenListKey(null);
            setQuery(null);
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      <button
        className="small-btn"
        title="VAL_ 전체 목록 보기"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => {
          setQuery(null);
          setOpenListKey(open ? null : openKey);
        }}
      >
        ▼
      </button>
      {open && (
        <div className="tx-combo-list">
          {items.length === 0 ? (
            <div className="hint">일치 없음 — 직접 입력값을 그대로 사용</div>
          ) : (
            items.map(({ k, v }) => (
              <div
                key={k}
                className={`tx-combo-hit${String(k) === current ? ' active' : ''}`}
                title={`${v} (${k}) 입력`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onChange(String(k));
                  setQuery(null);
                  setOpenListKey(null);
                }}
              >
                <span className="mono">{k}</span>
                <span className="hint">{v}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function TxBox({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc, updateWidget } = useApp();
  const rows = (config.options.rows as TxRow[] | undefined) ?? [];
  const [error, setError] = useState<string | null>(null);
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
    // 전송 중 메시지 변경: 백엔드 행 엔트리도 새 메시지로 retarget
    pushRowLiveFor({ ...row, messageName: name, rawOverride: false, isFd: busFd, bitrateSwitch: false });

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
  
    }
  };

  /** 행 라벨 (검증 에러 표시용) */
  const rowLabel = (r: TxRow) => r.messageName ?? `ID ${r.idHex || '(미입력)'}`;

  /** 행의 주기 전송 상태: 백엔드 행별 entry 존재 여부 (remount에도 유지) */
  const isRowTransmitting = (key: string) =>
    txStatus?.auto_entries.some((e) => e.key === key) ?? false;

  /** 행별 Send 버튼 동작:
   * - 주기 미체크: Periodic/Event 상관없이 정확히 1번만 출력.
   * - 주기 체크: 설정 주기로 전송 시작 (Send 적색 표시), 다시 누르면 정지.
   * 수동 오버라이드 행은 DBC의 frame_id로 고정해 전송한다. */
  const sendOnce = async (r: TxRow) => {
    const rawMode = !r.messageName || r.rawOverride;
    // 주기 전송 중 재클릭: 해당 행만 정지
    if (r.periodic && isRowTransmitting(r.key)) {
      try {
        await api.txRowStop(r.key);
        canStore.pushActivity(`${rowLabel(r)} 주기 전송 정지`);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    if (!rawMode) {
      const msg = dbcMessageOf(r.messageName);
      if (!msg) {
        setError(`"${rowLabel(r)}": DBC에 메시지가 없습니다`);
        return;
      }
      const { values, alt, error } = parseRowValues(msg, r);
      if (error) {
        setError(`"${rowLabel(r)}": ${error}`);
        return;
      }
      try {
        if (r.periodic) {
          await api.txRowStart({
            key: r.key,
            message_name: msg.name,
            values,
            values_alt: alt,
            period_ms: Math.max(1, r.periodMs),
          });
          canStore.pushActivity(`${rowLabel(r)} 주기 전송 시작 (${Math.max(1, r.periodMs)}ms)`);
        } else {
          // once: Periodic/Event 상관없이 정확히 1번만 출력 (auto 미지정)
          await canStore.sendSignal(msg.name, values, alt, true);
        }
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
    if (!r.messageName && !/^[0-9a-fA-Fa-f]+$/.test(r.idHex.trim())) {
      setError(`"${rowLabel(r)}": ID(hex)를 입력하세요`);
      return;
    }
    const msg = dbcMessageOf(r.messageName);
    try {
      if (r.periodic) {
        await api.txRowStart({
          key: r.key,
          data_hex: r.dataHex.replace(/\s/g, ''),
          arbitration_id: msg ? msg.frame_id : parseInt(r.idHex, 16),
          is_extended: msg ? msg.is_extended : parseInt(r.idHex, 16) > 0x7FF,
          is_fd: r.isFd,
          bitrate_switch: r.bitrateSwitch,
          period_ms: Math.max(1, r.periodMs),
        });
        canStore.pushActivity(`${rowLabel(r)} 주기 전송 시작 (${Math.max(1, r.periodMs)}ms)`);
      } else {
        await api.txSendOnce({
          key: r.key,
          arbitration_id: msg ? msg.frame_id : parseInt(r.idHex, 16),
          data: r.dataHex.replace(/\s/g, ''),
          is_fd: r.isFd,
          bitrate_switch: r.bitrateSwitch,
        });
      }
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
    // 이 행이 주기 전송 중이면 데이터 값을 바꿀 때 다음 주기 tick을 기다리지
    // 않고 바로 그 값을 한 번 전송한다. 입력 중인 값(홀수 자릿수 등)은
    // sendOnce 내부 검증에서 차단되고 에러로 표시된다.
    if (isRowTransmitting(r.key)) sendOnce({ ...r, dataHex: formatted });
  };

  const txCount = (key: string) =>
    txStatus?.auto_entries.find((e) => e.key === key)?.tx_count ?? 0;

  /** 행 복제: 신호값(토글값 포함)까지 모두 동일하게 복사해 바로 아래에 추가 */
  const cloneRow = (key: string) => {
    const src = rowsRef.current.find((r) => r.key === key);
    if (!src) return;
    if (rowsRef.current.length >= MAX_ROWS) {
      setError(`최대 ${MAX_ROWS}개까지 등록할 수 있습니다`);
      return;
    }
    const idx = rowsRef.current.findIndex((r) => r.key === key);
    const copy: TxRow = {
      ...src,
      key: `${Date.now()}-${rowsRef.current.length}`,
      signalValues: src.signalValues ? { ...src.signalValues } : undefined,
      toggleValues: src.toggleValues ? { ...src.toggleValues } : undefined,
      toggleOn: src.toggleOn ? { ...src.toggleOn } : undefined,
    };
    const next = [...rowsRef.current];
    next.splice(idx + 1, 0, copy);
    setRows(next);
    setSelectedKey(copy.key);

  };

  // ---- split drag: 상/하 5:5 기본, 마우스 드래그로만 변경 (행 추가 등
  // 자동 변경 없음). 비율은 config.options에 영속.
  const boxRef = useRef<HTMLDivElement | null>(null);
  const splitRatio =
    typeof config.options.splitRatio === 'number'
      ? Math.min(0.8, Math.max(0.2, config.options.splitRatio))
      : 0.5;
  const [dragFrac, setDragFrac] = useState<number | null>(null);
  const dragFracRef = useRef<number | null>(null);
  const shownSplit = dragFrac ?? splitRatio;
  const onSplitDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const box = boxRef.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    const move = (ev: MouseEvent) => {
      const f = Math.min(0.8, Math.max(0.2, (ev.clientY - rect.top) / Math.max(1, rect.height)));
      dragFracRef.current = f;
      setDragFrac(f);
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      if (dragFracRef.current !== null) {
        updateWidget({ ...config, options: { ...config.options, splitRatio: dragFracRef.current } });
      }
      dragFracRef.current = null;
      setDragFrac(null);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  /** 행의 전송용 값 + 토글 2차값을 파싱한다 (apply/sendOnce 공용) */
  const parseRowValues = (msg: DbcMessage, r: TxRow) => {
    const { values, error } = parseSignalValues(msg, r.signalValues);
    if (error) return { values, alt: undefined as Record<string, number> | undefined, error };
    const toggleKeys = Object.keys(r.toggleOn ?? {}).filter((k) => r.toggleOn?.[k]);
    if (toggleKeys.length === 0) return { values, alt: undefined, error: null };
    const parsed2 = parseSignalValues(msg, r.toggleValues);
    if (parsed2.error) return { values, alt: undefined, error: `토글: ${parsed2.error}` };
    const alt = Object.fromEntries(Object.entries(parsed2.values).filter(([k]) => toggleKeys.includes(k)));
    return { values, alt, error: null };
  };

  /** 전송 중인 행의 값/토글/주기 변경을 백엔드에 즉시 반영한다 (즉시
   * 프레임 없이 다음 tick부터 적용, tx_count 유지). 전송 중이 아니거나
   * 파싱 실패(입력 중 빈칸 등)면 스킵 -- Send 시점에 검증 에러가 표시되므로
   * 여기서 UI를 건드리지 않고, 실패한 푸시는 다음 편집 때 재시도된다. */
  const pushRowLiveFor = (r: TxRow) => {
    if (!isRowTransmitting(r.key)) return;
    if (!r.messageName || r.rawOverride) {
      // raw 행: 주기만 live 반영 (데이터 편집은 기존대로 정지 후 재시작)
      void api
        .txRowUpdate({ key: r.key, period_ms: Math.max(1, r.periodMs) })
        .catch(() => {});
      return;
    }
    const msg = dbcMessageOf(r.messageName);
    if (!msg) return;
    const { values, alt, error } = parseRowValues(msg, r);
    if (error) return;
    void api
      .txRowUpdate({
        key: r.key,
        message_name: msg.name,
        values,
        values_alt: alt ?? null,
        period_ms: Math.max(1, r.periodMs),
      })
      .catch(() => {});
  };

  // ---- split view: message list (top) + selected message's signals (bottom)
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selectedRow = rows.find((r) => r.key === selectedKey) ?? rows[0] ?? null;
  const [sigSearch, setSigSearch] = useState('');
  const [openListKey, setOpenListKey] = useState<string | null>(null);

  // DBC 전체 signal 검색 (툴바 msgFilter 무관) -- 상위 50개만 표시
  const searchResults = (() => {
    const q = sigSearch.trim().toLowerCase();
    if (!q || !dbc.loaded) return [];
    const out: { signal: string; message: string }[] = [];
    for (const m of sortedMessages(dbc)) {
      for (const s of m.signals) {
        if (s.name.toLowerCase().includes(q)) out.push({ signal: s.name, message: m.name });
        if (out.length >= 50) return out;
      }
    }
    return out;
  })();

  /** Signal 검색에서 선택: 해당 메세지 행이 있으면 선택만, 없으면 새 행 추가
   * (초기값 프리필 포함 -- selectMessage와 동일) 후 선택한다. */
  const addMessageRow = async (messageName: string) => {
    const msg = dbcMessageOf(messageName);
    if (!msg) return;
    const existing = rowsRef.current.find((r) => r.messageName === messageName && !r.rawOverride);
    setSigSearch('');
    if (existing) {
      setSelectedKey(existing.key);
      return;
    }
    if (rowsRef.current.length >= MAX_ROWS) {
      setError(`최대 ${MAX_ROWS}개까지 등록할 수 있습니다`);
      return;
    }
    const key = `${Date.now()}-${rowsRef.current.length}`;
    setRows([
      ...rowsRef.current,
      {
        key,
        idHex: msg.frame_id.toString(16).toUpperCase(),
        periodMs: 100,
        dataHex: '',
        messageName,
        enabled: true,
        periodic: true,
        isFd: busFd,
        bitrateSwitch: false,
        rawOverride: false,
      },
    ]);
    setSelectedKey(key);

    let initialHex: string | null = null;
    try {
      const res = await api.getDbcMessageInitial(messageName);
      initialHex = res.data_hex;
    } catch {
      initialHex = null;
    }
    if (initialHex == null) {
      initialHex = Array(msg.length).fill('00').join(' ');
    }
    setRows((prev) => {
      const cur = prev.find((r) => r.key === key);
      // 사용자가 그 사이 다른 메시지로 바꿨으면 덮어쓰지 않는다
      if (!cur || cur.messageName !== messageName) return prev;
      return prev.map((r) => (r.key === key ? { ...r, dataHex: formatHexBytes(initialHex as string) } : r));
    });

  };

  return (
    <div className="tx-box" ref={boxRef}>
      <div className="tx-toolbar">
        <button className="small-btn" onClick={addRow} disabled={rows.length >= MAX_ROWS}>
          + 메시지 추가 ({rows.length}/{MAX_ROWS})
        </button>
        <MessageFilter value={msgFilter} onChange={setMsgFilter} />
        <div className="tx-signal-search">
          <input
            className="layout-input mono"
            placeholder="signal 검색 (DBC 전체)"
            title="신호 이름으로 검색 -- 선택하면 해당 메시지가 추가됩니다"
            value={sigSearch}
            onChange={(e) => setSigSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setSigSearch('');
                (e.target as HTMLInputElement).blur();
              }
            }}
            onBlur={() => setSigSearch('')}
          />
          {sigSearch.trim() !== '' && (
            <div className="tx-search-results">
              {searchResults.length === 0 ? (
                <div className="hint">일치하는 signal 없음</div>
              ) : (
                searchResults.map((hit) => (
                  <div
                    key={`${hit.message}.${hit.signal}`}
                    className="tx-search-hit"
                    title={`${hit.message}.${hit.signal} -- 클릭하면 메시지 추가`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      void addMessageRow(hit.message);
                    }}
                  >
                    <span className="mono">{hit.signal}</span>
                    <span className="hint">{hit.message}</span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        <span className="spacer" />
        <span className="hint" title="각 행의 Send 버튼으로 전송합니다 (1회 또는 주기 토글)">
          행별 Send로 전송
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="tx-split-top" style={{ height: `${Math.round(shownSplit * 1000) / 10}%` }}>
        <table className="tx-table">
          <thead>
            <tr>
              <th></th>
              <th>ID(hex) / DBC 메시지</th>
              <th title="체크하면 Send 버튼이 주기 전송 시작/정지 토글이 됩니다. 해제하면 Send 1번 누를 때마다 정확히 1번만 전송합니다">주기</th>
              <th>주기(ms)</th>
              <th>모드</th>
              <th>FD</th>
              <th></th>
              <th>Cnt</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const msg = dbcMessageOf(r.messageName);
              const signalMode = !!r.messageName && !r.rawOverride;
              return (
                <tr
                  key={r.key}
                  className={r.key === selectedRow?.key ? 'tx-row-selected' : ''}
                  onClick={() => setSelectedKey(r.key)}
                  title="클릭하면 아래에 이 메시지의 Signal을 표시합니다"
                >
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={r.enabled}
                      onChange={(e) => patchRow(r.key, { enabled: e.target.checked })}
                    />
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
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
                      onChange={(e) => {
                        const periodMs = Number(e.target.value);
                        patchRow(r.key, { periodMs });
                        pushRowLiveFor({ ...r, periodMs });
                      }}
                    />
                  </td>
                  <td>
                    <span
                      className="hint"
                      title={
                        signalMode && msg
                          ? `${msg.name} (${msg.length}B · ${msg.is_fd ? 'FD' : 'Classic'})`
                          : 'hex 직접 입력 모드'
                      }
                    >
                      {signalMode ? `신호 ${msg?.signals.length ?? 0}개` : 'raw'}
                    </span>
                  </td>
                  <td>
                    <span className="hint" title="신호 에디터 값은 Start/Send 시점에 전송되며 DBC 메시지의 FD 속성을 따름">
                      {signalMode ? (msg?.is_fd ? 'FD' : '-') : `${r.isFd ? 'F' : ''}${r.bitrateSwitch ? 'B' : ''}` || '-'}
                    </span>
                  </td>
                  <td>
                    <button
                      className={`small-btn${isRowTransmitting(r.key) ? ' danger' : ''}`}
                      title={
                        r.periodic
                          ? isRowTransmitting(r.key)
                            ? '주기 전송 중지 (이 행만)'
                            : '주기 전송 시작 (다시 누르면 정지)'
                          : '정확히 1번만 전송'
                      }
                      disabled={!r.enabled}
                      onClick={() => sendOnce(r)}
                    >
                      Send
                    </button>
                  </td>
                  <td>{txCount(r.key)}</td>
              <td>
                <button
                  className="icon-btn"
                  title="이 메시지를 신호값까지 동일하게 복제"
                  onClick={() => cloneRow(r.key)}
                >
                  ⧉
                </button>
                <button
                  className="icon-btn"
                  onClick={() => setRows(rows.filter((x) => x.key !== r.key))}
                >
                  ✕
                </button>
              </td>
                </tr>
              );
            })}
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
      <div
        className="tx-split-drag"
        title="드래그해서 상/하 크기 조절"
        onMouseDown={onSplitDown}
      />
      <div className="tx-split-bottom">
        {!selectedRow ? (
          <div className="hint">메시지를 선택하세요 (상단 목록 클릭 또는 signal 검색으로 추가)</div>
        ) : (
          <>
            <div className="tx-detail-title">
              {rowLabel(selectedRow)} — Signal
            </div>
            {(() => {
              const r = selectedRow;
              const msg = dbcMessageOf(r.messageName);
              if (r.messageName && !r.rawOverride) {
                if (!msg) return <span className="hint">DBC에 메시지가 없습니다</span>;
                const len = msg.length;
                const fd = msg.is_fd ? 'FD' : 'Classic';
                return (
                  <div className="tx-signal-cell">
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
                      const setSignalText = (v: string) => {
                        const signalValues = { ...(r.signalValues ?? {}), [s.name]: v };
                        patchRow(r.key, { signalValues });
                        pushRowLiveFor({ ...r, signalValues });
                      };
                      const toggled = r.toggleOn?.[s.name] ?? false;
                      const toggleText = r.toggleValues?.[s.name] ?? text;
                      const setToggleText = (v: string) => {
                        const toggleValues = { ...(r.toggleValues ?? {}), [s.name]: v };
                        patchRow(r.key, { toggleValues });
                        pushRowLiveFor({ ...r, toggleValues });
                      };
                      const toggleEditor = (value: string, onChange: (v: string) => void, listSuffix: string) =>
                        s.choices ? (
                          <ChoiceComboInput
                            openKey={`${r.key}.${s.name}.${listSuffix}`}
                            openListKey={openListKey}
                            setOpenListKey={setOpenListKey}
                            choices={s.choices}
                            value={value}
                            onChange={onChange}
                          />
                        ) : (
                          <input
                            className="mono tx-signal-input"
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                          />
                        );
                      const { min, max } = signalDisplayRange(s);
                      return (
                        <div key={s.name} className="tx-signal-group">
                          <label
                            className="tx-signal-row"
                            title={`${s.name} (${s.length}bit${s.is_signed ? ', signed' : ''}, ${s.send_type}) 범위 ${min} ~ ${max}`}
                          >
                            <span className="tx-signal-name mono">{s.name}</span>
                            {toggleEditor(text, setSignalText, 'a')}
                            <button
                              className={`small-btn${toggled ? ' primary' : ''}`}
                              title="값 토글: 이 신호의 두 값을 번갈아 전송 (2번째 값 입력칸 표시)"
                              onClick={() => {
                                const on = !(r.toggleOn?.[s.name] ?? false);
                                const patch: Partial<TxRow> = {
                                  toggleOn: { ...(r.toggleOn ?? {}), [s.name]: on },
                                };
                                if (on && r.toggleValues?.[s.name] === undefined) {
                                  patch.toggleValues = { ...(r.toggleValues ?? {}), [s.name]: text };
                                }
                                patchRow(r.key, patch);
                                pushRowLiveFor({ ...r, ...patch });
                              }}
                            >
                              ⇄
                            </button>
                            <span className="hint">{s.unit ?? ''}</span>
                          </label>
                          {toggled && (
                            <label
                              className="tx-signal-row tx-toggle-row"
                              title={`${s.name} 토글 2번째 값 (번갈아 전송)`}
                            >
                              <span className="tx-signal-name mono">↳ {s.name}</span>
                              {toggleEditor(toggleText, setToggleText, 'b')}
                              <span className="hint">{s.unit ?? ''}</span>
                            </label>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              }
              return (
                <>
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
                  <div className="tx-override-note">
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
                  </div>
                </>
              );
            })()}
          </>
        )}
      </div>
    </div>
  );
}
