// 전원 컨트롤 위젯: 파워서플라이(PyVISA/SCPI) 연결, 배터리 전압/전류 수동 설정,
// ACC/IGN 토글, 자동 On/Off 반복(전압을 On값<->Off값으로 주기 전환), 자동 전압
// Up/Down 반복(Low<->High 삼각파, 전류는 고정). 실제 명령 시퀀싱/타이머는 전부
// 백엔드(power_supply_service.py)에서 돌고, 이 위젯은 canStore.status.power를
// 읽고 /api/power/* 를 호출하기만 한다 (TestRunnerBox의 연결 버튼과 동일한 패턴).

import { useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import type { WidgetConfig } from '../types';

function parseNumbers(values: string[]): number[] | null {
  const nums = values.map(Number);
  return nums.every(Number.isFinite) ? nums : null;
}

// 숫자 입력 + 단위 표시("14.4" + "V"), 숫자는 우측 정렬.
function UnitField({
  value,
  onChange,
  unit,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  unit: string;
  disabled?: boolean;
}) {
  return (
    <span className="power-unit-field">
      <input className="mono power-num" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      <span className="power-unit">{unit}</span>
    </span>
  );
}

export function PowerControlWidget(_: { config: WidgetConfig }) {
  useCanVersion();
  const power = canStore.status?.power;
  const [error, setError] = useState<string | null>(null);

  const [voltage, setVoltage] = useState('14.4');
  const [current, setCurrent] = useState('10');

  const [onVoltage, setOnVoltage] = useState('14.4');
  const [onCurrent, setOnCurrent] = useState('10');
  const [onS, setOnS] = useState('5');
  const [offVoltage, setOffVoltage] = useState('1');
  const [offCurrent, setOffCurrent] = useState('0');
  const [offS, setOffS] = useState('10');

  const [low, setLow] = useState('5');
  const [high, setHigh] = useState('15');
  const [sweepCurrent, setSweepCurrent] = useState('10');
  const [legS, setLegS] = useState('20');

  const run = async (fn: () => Promise<{ ok: boolean; reason?: string }>) => {
    try {
      const r = await fn();
      setError(r.ok ? null : (r.reason ?? '실패'));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const togglePower = async () => {
    try {
      if (connected) await api.powerDisconnect();
      else await api.powerConnect();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const submitBattery = () => {
    const parsed = parseNumbers([voltage, current]);
    if (!parsed) {
      setError('전압/전류 값이 올바르지 않습니다');
      return;
    }
    void run(() => api.powerSetBattery(parsed[0], parsed[1]));
  };

  const toggleAcc = (next: boolean) => void run(() => api.powerSetAccIgn(next ? 'ACC_On' : 'ACC_Off'));
  const toggleIgn = (next: boolean) => void run(() => api.powerSetAccIgn(next ? 'IGN_On' : 'IGN_Off'));

  const startOnOff = () => {
    const parsed = parseNumbers([onVoltage, onCurrent, onS, offVoltage, offCurrent, offS]);
    if (!parsed || parsed[2] <= 0 || parsed[5] <= 0) {
      setError('On/Off 반복 값이 올바르지 않습니다 (시간은 0보다 커야 함)');
      return;
    }
    void run(() => api.powerOnOffStart(parsed[0], parsed[1], parsed[2], parsed[3], parsed[4], parsed[5]));
  };
  const stopOnOff = () => void run(() => api.powerOnOffStop());

  const startSweep = () => {
    const parsed = parseNumbers([low, high, sweepCurrent, legS]);
    if (!parsed || parsed[3] <= 0) {
      setError('전압 스윕 값이 올바르지 않습니다 (시간은 0보다 커야 함)');
      return;
    }
    void run(() => api.powerSweepStart(parsed[0], parsed[1], parsed[2], parsed[3]));
  };
  const stopSweep = () => void run(() => api.powerSweepStop());

  const connected = power?.initialized ?? false;
  const onoffEnabled = power?.onoff.enabled ?? false;
  const sweepEnabled = power?.sweep.enabled ?? false;
  const autoActive = onoffEnabled || sweepEnabled; // either mode owns the voltage channel

  return (
    <div className="power-control">
      <div className="power-control-toolbar">
        <button className={`small-btn ${connected ? 'danger' : 'primary'}`} onClick={togglePower}>
          {connected ? '전원 연결 해제' : '전원 연결'}
        </button>
        <span className="hint" title={power?.error ?? undefined}>
          {connected ? '✅ 연결됨' : `⚠️ 미연결${power?.error ? ` (${power.error})` : ''}`}
        </span>
      </div>
      {error && <div className="error">{error}</div>}

      <div className="power-section">
        <div className="power-section-title">배터리 전압 / 전류</div>
        <div className="power-row">
          <UnitField value={voltage} unit="V" disabled={!connected || autoActive} onChange={setVoltage} />
          <UnitField value={current} unit="A" disabled={!connected || autoActive} onChange={setCurrent} />
          <button className="small-btn primary" disabled={!connected || autoActive} onClick={submitBattery}>
            OK
          </button>
        </div>
        {connected && (
          <div className="hint mono">
            현재: {power!.battery_voltage}V / {power!.battery_current}A
          </div>
        )}
      </div>

      <div className="power-section">
        <div className="power-section-title">ACC / IGN</div>
        <div className="power-row">
          <label className="check-label toggle">
            <input type="checkbox" checked={power?.acc ?? false} disabled={!connected} onChange={(e) => toggleAcc(e.target.checked)} />
            ACC
          </label>
          <label className="check-label toggle">
            <input type="checkbox" checked={power?.ign ?? false} disabled={!connected} onChange={(e) => toggleIgn(e.target.checked)} />
            IGN
          </label>
        </div>
      </div>

      <div className="power-section">
        <div className="power-section-title">자동 On/Off 반복 (전압)</div>
        <div className="power-row">
          <span className="power-row-label">On</span>
          <UnitField value={onVoltage} unit="V" disabled={onoffEnabled} onChange={setOnVoltage} />
          <UnitField value={onCurrent} unit="A" disabled={onoffEnabled} onChange={setOnCurrent} />
          <UnitField value={onS} unit="S" disabled={onoffEnabled} onChange={setOnS} />
        </div>
        <div className="power-row">
          <span className="power-row-label">Off</span>
          <UnitField value={offVoltage} unit="V" disabled={onoffEnabled} onChange={setOffVoltage} />
          <UnitField value={offCurrent} unit="A" disabled={onoffEnabled} onChange={setOffCurrent} />
          <UnitField value={offS} unit="S" disabled={onoffEnabled} onChange={setOffS} />
          <button
            className={`small-btn ${onoffEnabled ? 'danger' : 'primary'}`}
            disabled={!connected || sweepEnabled}
            onClick={onoffEnabled ? stopOnOff : startOnOff}
          >
            {onoffEnabled ? '정지' : '시작'}
          </button>
        </div>
        {onoffEnabled && <div className="hint">현재 단계: {power!.onoff.phase === 'on' ? 'On' : 'Off'}</div>}
      </div>

      <div className="power-section">
        <div className="power-section-title">자동 전압 Up/Down 반복 (삼각파)</div>
        <div className="power-row">
          <span className="power-row-label">Low</span>
          <UnitField value={low} unit="V" disabled={sweepEnabled} onChange={setLow} />
          <span className="power-row-label">High</span>
          <UnitField value={high} unit="V" disabled={sweepEnabled} onChange={setHigh} />
        </div>
        <div className="power-row">
          <span className="power-row-label">전류</span>
          <UnitField value={sweepCurrent} unit="A" disabled={sweepEnabled} onChange={setSweepCurrent} />
          <span className="power-row-label">Time</span>
          <UnitField value={legS} unit="S" disabled={sweepEnabled} onChange={setLegS} />
          <button
            className={`small-btn ${sweepEnabled ? 'danger' : 'primary'}`}
            disabled={!connected || onoffEnabled}
            onClick={sweepEnabled ? stopSweep : startSweep}
          >
            {sweepEnabled ? '정지' : '시작'}
          </button>
        </div>
        {sweepEnabled && <div className="hint mono">현재 전압: {power!.battery_voltage}V</div>}
      </div>
    </div>
  );
}
