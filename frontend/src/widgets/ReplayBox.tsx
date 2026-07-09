// CAN log replay box: load a .blf/.asc file, pick DBC messages for the
// filter and start/stop. Filter semantics: Pass = replay only the selected
// messages, Stop = replay everything except them; nothing selected = replay
// every frame. The message picker is enabled once a DBC is loaded.

import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import { groupedMessages, useApp } from '../store/appContext';
import { MessageFilter, type MessageFilterMode } from './MessageOptions';
import type { DbcMessage, WidgetConfig } from '../types';

export function ReplayBox(_: { config: WidgetConfig }) {
  useCanVersion();
  const { dbc } = useApp();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<'pass' | 'stop'>('pass');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const replay = canStore.status?.replay;
  const progress = replay?.progress;
  const percent =
    progress && progress.total > 0
      ? Math.round(((progress.sent + progress.skipped) / progress.total) * 100)
      : 0;

  const onFile = async (file: File) => {
    try {
      await api.uploadReplay(file);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const start = async () => {
    try {
      await api.replayStart(mode, selectedIds);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const filterHint =
    selectedIds.length === 0
      ? '선택 없음 → 전체 재생'
      : mode === 'pass'
        ? `선택한 ${selectedIds.length}개 메시지만 재생`
        : `선택한 ${selectedIds.length}개 메시지 제외`;

  return (
    <div className="replay-box">
      <div className="replay-row">
        <button className="small-btn" onClick={() => fileInput.current?.click()}>
          파일 열기 (.blf / .asc)
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".blf,.asc"
          hidden
          onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
        />
        <span className="replay-fileinfo">
          {replay?.loaded
            ? `${replay.filename} — ${replay.message_count} frames, ${replay.duration_s}s`
            : '로드된 파일 없음'}
        </span>
      </div>
      <div className="replay-row">
        <span>필터:</span>
        <label>
          <input type="radio" checked={mode === 'pass'} onChange={() => setMode('pass')} />
          Pass
        </label>
        <label>
          <input type="radio" checked={mode === 'stop'} onChange={() => setMode('stop')} />
          Stop
        </label>
        <button
          className="small-btn"
          disabled={!dbc.loaded}
          title={dbc.loaded ? '필터에 적용할 메시지 선택' : 'DBC를 먼저 업로드하세요'}
          onClick={() => setShowPicker(true)}
        >
          메시지 선택 ({selectedIds.length})
        </button>
        <span className="hint">{filterHint}</span>
        <span className="spacer" />
        <button
          className="small-btn primary"
          onClick={start}
          disabled={!replay?.loaded || progress?.running}
        >
          ▶ Replay Start
        </button>
        <button
          className="small-btn danger"
          onClick={() => api.replayStop()}
          disabled={!progress?.running}
        >
          ■ Stop
        </button>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <div className="replay-row hint">
        {progress
          ? `${progress.sent} sent / ${progress.skipped} skipped / ${progress.total} total ${
              progress.running ? '(재생 중)' : ''
            }`
          : ''}
      </div>
      {error && <div className="error">{error}</div>}
      {showPicker && (
        <MessagePicker
          selectedIds={selectedIds}
          onSave={(ids) => {
            setSelectedIds(ids);
            setShowPicker(false);
          }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}

function MessagePicker({
  selectedIds,
  onSave,
  onClose,
}: {
  selectedIds: number[];
  onSave: (ids: number[]) => void;
  onClose: () => void;
}) {
  const { dbc } = useApp();
  const [draft, setDraft] = useState<Set<number>>(new Set(selectedIds));
  const [msgFilter, setMsgFilter] = useState<MessageFilterMode>('all');
  const groups = groupedMessages(dbc, canStore.getRxNode());
  const allMessages = [...groups.tx, ...groups.rx];
  const visibleMessages =
    msgFilter === 'tx' ? groups.tx : msgFilter === 'rx' ? groups.rx : allMessages;

  const toggle = (id: number) =>
    setDraft((d) => {
      const next = new Set(d);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const renderItem = (m: DbcMessage) => (
    <label key={m.frame_id} className="picker-item">
      <input
        type="checkbox"
        checked={draft.has(m.frame_id)}
        onChange={() => toggle(m.frame_id)}
      />
      <span className="mono">0x{m.frame_id.toString(16).toUpperCase().padStart(3, '0')}</span>
      <span>{m.name}</span>
    </label>
  );

  // portal to <body> so the picker always renders above every widget
  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>필터 메시지 선택</h3>
        <p className="hint">
          Pass 필터: 선택한 메시지만 재생 / Stop 필터: 선택한 메시지 제외
        </p>
        <div className="replay-row">
          <MessageFilter value={msgFilter} onChange={setMsgFilter} />
        </div>
        <div className="picker-list">
          {msgFilter === 'all' && groups.grouped ? (
            <>
              <div className="picker-group-label">TX 메시지</div>
              {groups.tx.map(renderItem)}
              <div className="picker-group-label">RX 메시지</div>
              {groups.rx.map(renderItem)}
            </>
          ) : (
            visibleMessages.map(renderItem)
          )}
        </div>
        <div className="modal-buttons">
          <button className="small-btn" onClick={() => setDraft(new Set())}>
            전체 해제
          </button>
          <button
            className="small-btn"
            onClick={() => setDraft(new Set(visibleMessages.map((m) => m.frame_id)))}
          >
            전체 선택
          </button>
          <span className="spacer" />
          <button onClick={() => onSave([...draft])}>적용</button>
          <button onClick={onClose}>취소</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
