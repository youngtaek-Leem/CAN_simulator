import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import type { SeedKeyStatus } from '../types';

/** STmin override + SeedKey DLL loader, shared by CAN-SWDL and OTA Tester --
 * both widgets drive UDS SecurityAccess/transferData over the same backend,
 * so this one component (and canStore's shared stmin fields) keeps their
 * settings in sync instead of each widget having its own independent copy.
 *
 * STmin lives in canStore so both widgets show/edit the same value. The
 * SeedKey DLL is a single backend-side service already shared regardless of
 * which widget uploads it (see backend/seedkey_client.py) -- this just gives
 * whichever widget renders it the same upload UI CAN-SWDL originally had. */
export function UdsGlobalControls() {
  useCanVersion(); // re-render this widget when canStore's shared stmin fields change
  const stminTxEnabled = canStore.getGlobalStminEnabled();
  const globalStminTx = canStore.getGlobalStminTx();

  const [seedKeyFile, setSeedKeyFile] = useState<File | null>(null);
  const [seedKeyStatus, setSeedKeyStatus] = useState<SeedKeyStatus | null>(null);
  const [seedKeyUploading, setSeedKeyUploading] = useState(false);
  const [seedKeyError, setSeedKeyError] = useState<string | null>(null);

  useEffect(() => {
    api.seedkeyStatus().then(setSeedKeyStatus).catch(() => {});
  }, []);

  const uploadSeedKey = async () => {
    if (!seedKeyFile) return;
    setSeedKeyUploading(true);
    setSeedKeyError(null);
    try {
      const status = await api.seedkeyUpload(seedKeyFile);
      setSeedKeyStatus(status);
      canStore.pushActivity(`SeedKey DLL 로드됨: ${status.filename} (v${status.version?.version ?? '?'})`);
    } catch (e: unknown) {
      const msg = String(e instanceof Error ? e.message : e);
      setSeedKeyError(msg);
      canStore.pushActivity(`SeedKey DLL 로드 실패: ${msg}`);
    } finally {
      setSeedKeyUploading(false);
    }
  };

  return (
    <>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', padding: '0', background: 'none', borderRadius: '0', border: 'none' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '11px', whiteSpace: 'nowrap', color: 'inherit' }}>
          <input type="checkbox" checked={stminTxEnabled}
            onChange={e => canStore.setGlobalStminEnabled(e.target.checked)}
            style={{ margin: 0, width: '14px', height: '14px' }} />
          <span style={{ fontWeight: 500, fontSize: '13px', color: 'inherit' }}>STmin</span>
        </label>
        <input
          value={globalStminTx}
          onChange={e => canStore.setGlobalStminTx(e.target.value)}
          disabled={!stminTxEnabled}
          placeholder="0A"
          style={{
            width: '32px',
            fontSize: '11px',
            padding: '1px 3px',
            border: '1px solid var(--border)',
            borderRadius: '2px',
            backgroundColor: stminTxEnabled ? 'var(--input-bg)' : 'var(--panel)',
            color: 'inherit',
            flexShrink: 0,
          }}
          title="Flow Control STmin (hex), unit 0.1ms"
        />
        <span style={{ fontSize: '9px', color: '#6b7280', whiteSpace: 'nowrap' }}>×0.1ms</span>
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
        <label style={{
          padding: '2px 6px', fontSize: '10px', border: '1px solid var(--border)', borderRadius: '3px',
          cursor: 'pointer', whiteSpace: 'nowrap', background: 'var(--panel)', color: 'inherit',
        }}>
          ASK 선택
          <input type="file" accept=".dll" style={{ display: 'none' }}
            onChange={e => setSeedKeyFile(e.target.files?.[0] ?? null)} />
        </label>
        {seedKeyFile && (
          <span style={{ fontSize: '10px', whiteSpace: 'nowrap', color: 'inherit' }}>{seedKeyFile.name}</span>
        )}
        <button disabled={!seedKeyFile || seedKeyUploading} onClick={uploadSeedKey}
          style={{
            padding: '2px 6px',
            fontSize: '10px',
            backgroundColor: seedKeyFile ? '#3b82f6' : '#d1d5db',
            color: seedKeyFile ? '#fff' : '#9ca3af',
            border: 'none',
            borderRadius: '3px',
            cursor: seedKeyFile ? 'pointer' : 'not-allowed',
            whiteSpace: 'nowrap',
          }}>SeedKey DLL 업로드</button>
        <span style={{ fontSize: '9px', whiteSpace: 'nowrap', color: seedKeyStatus?.loaded ? '#10b981' : '#6b7280' }}
          title={seedKeyError ?? undefined}>
          {seedKeyStatus?.loaded
            ? `✓ ${seedKeyStatus.filename} (v${seedKeyStatus.version?.version ?? '?'})`
            : '미로드 (더미 키 사용)'}
        </span>
      </span>
    </>
  );
}

/** STmin override to pass into a UDS start call: undefined when the shared
 * checkbox is off (each widget falls back to its own XML/default timing),
 * the parsed hex value when on. */
export function getGlobalStminOverride(): number | undefined {
  return canStore.getGlobalStminEnabled() ? parseInt(canStore.getGlobalStminTx(), 16) : undefined;
}
