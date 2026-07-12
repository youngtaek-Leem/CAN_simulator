// Runs one named FUNC block from the loaded function master script (see
// App.tsx's "함수 마스터 스크립트" toolbar upload) through the same
// test_runner_service engine as the "테스트 시나리오 실행기" widget --
// this widget itself has no log/result display, it just triggers the run
// and stops there; watch the Test Scenario Runner widget for step-by-step
// progress (shared execution log by design, see Requirement.md "Function
// Test 기능").

import { useState } from 'react';
import { api } from '../api/client';
import { canStore, useCanVersion } from '../store/canStore';
import type { WidgetConfig } from '../types';

export function FunctionButtonWidget({ config }: { config: WidgetConfig }) {
  useCanVersion();
  const [error, setError] = useState<string | null>(null);
  const funcName = config.options.funcName as string | undefined;
  const running = canStore.status?.test_runner.running ?? false;
  const runningCase = canStore.status?.test_runner.running_case ?? null;
  // this button's own function is the one currently executing -- distinct
  // from merely being disabled because some other script/function is busy
  const isActive = running && funcName !== undefined && runningCase === funcName;

  const activate = async () => {
    if (!funcName) {
      setError('함수 미할당');
      return;
    }
    try {
      await api.functionStart(funcName);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="control-widget">
      <button
        className={`big-btn ${isActive ? 'func-running' : ''}`}
        onClick={activate}
        onKeyDown={(e) => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            activate();
          }
        }}
        disabled={!funcName || running}
      >
        {funcName ?? '함수 미할당'}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
