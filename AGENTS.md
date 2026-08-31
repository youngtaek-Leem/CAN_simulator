# AGENTS.md — CAN Simulator Repository Instructions

## Quick Start Commands

**Backend** (run from `backend/`):
```bash
cd backend
python3 -m venv .venv                  # first time only
.venv/bin/pip install -r requirements.txt  # first time only
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

**Windows**: Use `backend\run_windows.bat` (auto-creates venv, installs deps, starts uvicorn)

**Frontend** (run from `frontend/`):
```bash
cd frontend
npm install      # first time only
npm run dev      # http://127.0.0.1:5173 (proxies to backend :8000)
npm run build    # outputs to frontend/dist (served statically by backend)
npm run lint     # oxlint
```

**Test** (backend only, virtual CAN bus — no hardware needed):
```bash
cd backend
.venv/bin/python -m pytest tests/      # 200+ tests (virtual bus, no hardware)
```

## Architecture Overview

- **Backend**: FastAPI + python-can (virtual/PCAN/Vector) + cantools (DBC)
  - Entry: `backend/main.py` (REST + WebSocket + StaticFiles for frontend/dist)
  - Core modules: `can_manager.py`, `dbc_service.py`, `tx_scheduler.py`, `isotp_service.py`, `uds_core.py`, `uds_download_manager.py`, `ota_tester_download_manager.py`, `test_runner_service.py`, `power_supply_service.py`, `audio_service.py`, `replay_service.py`, `syslog_service.py`, `syslog_script_generator.py`
- **Frontend**: React 19 + TypeScript + Vite + react-grid-layout
  - Entry: `frontend/src/main.tsx` → `App.tsx`
  - State: `frontend/src/store/canStore.ts` (signals, frames, graphs), `appContext.ts` (DBC, layout, config)
  - Widgets: `frontend/src/widgets/*.tsx` (self-contained components with config modals)

## Key Non-Obvious Conventions

1. **Backend serves frontend in production**: `frontend/dist` is mounted by FastAPI at `/`. Production always uses relative API paths (`BASE = ''`). Dev mode uses `VITE_BACKEND_URL` (default `http://127.0.0.1:8000`).

2. **Windows timer resolution**: Backend calls `timeBeginPeriod(1)` on Windows via `timer_util.py` for 1ms scheduling precision.

3. **WebSocket batching**: Backend batches received frames and pushes every 30ms (configurable) to reduce browser load.

4. **UI throttling**: Frontend accumulates data in stores and updates DOM via `requestAnimationFrame` at 10–60 FPS (configurable in settings).

5. **Event vs Periodic signal classification**: Determined by DBC message comment (`CM_ BO_`) leading tag:
   - `[P]` or `[PE]` → Periodic
   - Everything else (`[EC]`, `[EW]`, `[TP]`, no tag) → Event
   - Widget override takes priority (stored per-signal in `sendTypeOverrides`)

6. **Event signal encoding**: When sending an Event signal, **all other signals in the same message are forced to their invalid value (max raw value for bit-length) every time** — no history/remembering. Periodic signals accumulate state normally.

7. **RX/TX classification**: Based on user-selected "RX Node" (DUT node name). Messages sent by RX Node = RX (simulator receives); all others = TX (simulator transmits). Stored in `localStorage` key `can-sim.rx-node`.

8. **CAN-FD**: Enabled via "FD" checkbox in connection settings (data bitrate 1/2/4/5/8 Mbit/s). Classic bus (FD off) rejects >8 byte payloads with 400 error. PCAN FD timing constants in `can_manager.py`: `FD_CLOCK_HZ=80_000_000`, `FD_SAMPLE_POINT=80%`, `FD_DATA_SAMPLE_POINT=80%`.

9. **Multi-page tabs**: Widgets/layout stored per-page (`pages: {id,name,widgets,layout}[]`). Hidden tabs' widgets still transmit (backend unaware of tabs). GraphWidget stops recording when unmounted.

10. **Layout save/load**: Saves widget config + CAN config + **DBC/Function Script filenames only** (not file contents). On load, if filenames don't match currently loaded files, shows error banner — user must re-upload manually.

## Testing Notes

- All backend tests use python-can `virtual` interface — no hardware required
- Power/Audio tests verify graceful degradation paths (mocked/no hardware)
- Run single test: `.venv/bin/python -m pytest tests/test_dbc_service.py -v`
- Frontend: `npm run build` (runs `tsc -b && vite build`; if `tsc -b` throws stale TS6133/TS2304 errors after edits, clear `node_modules/.tmp/*.tsbuildinfo` and retry)

## Important Files to Know

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app, all REST/WS endpoints, static file serving |
| `backend/can_manager.py` | CAN bus connect/send/receive (virtual/PCAN/Vector, classic+FD) |
| `backend/dbc_service.py` | DBC parsing, signal encode/decode, Event/Periodic classification |
| `backend/tx_scheduler.py` | Periodic/Event scheduling, value generators (Random/Range) |
| `backend/isotp_service.py` | ISO-TP send (FF/CF/FC handling) |
| `backend/test_runner_service.py` | Test scenario executor (CAN/Power/Audio/Loop steps) |
| `frontend/src/store/canStore.ts` | Signal values, frame history, graph data, WebSocket ingest |
| `frontend/src/store/appContext.ts` | DBC summary, layout/pages, RX node, widget bindings |
| `frontend/src/widgets/registry.tsx` | Widget type registry (metadata, default config, component map) |
| `frontend/src/widgets/GraphWidget.tsx` | Canvas 2D step-chart, per-signal mini charts, rolling window |
| `frontend/src/widgets/SysLogAnalysisWidget.tsx` | SysLog chart grid with cross-graph hover timeline cursor |
| `frontend/src/widgets/OtaTesterWidget.tsx` | OTA tester: folder-scan, manifest-driven test case runner |
| `frontend/src/widgets/AudioWaveformChart.tsx` | Shared chart types/helpers reused by SysLog & AudioMonitor widgets |

## Common Gotchas

- **Don't edit `frontend/dist` directly** — it's a build artifact. Edit `frontend/src/` and run `npm run build`.
- **Backend venv is platform-specific** — `.venv`, `node_modules`, `__pycache__` excluded when copying to Windows.
- **SeedKey DLL (SecurityAccess)**: Windows-only (`HKMC_AdvancedSeedKey_*.dll`). Non-Windows uses dummy key (0).
- **Vector CANcase**: Windows-only (XL driver). Use Virtual interface on macOS/Linux.
- **Power supply**: Requires PyVISA + VISA backend (NI-VISA or pyvisa-py + libusb). Degrades gracefully if absent.
- **Audio**: Requires `sounddevice` + input device. Degrades gracefully if absent.

## Development Workflow (from CLAUDE.md)

1. Read `Requirement.md` first — it's the single source of truth
2. Plan before coding; get approval for new features
3. Implement module → verify with defined test method → update Requirement.md status
4. Run full test suite after each module to catch regressions
5. Commit after each verified module

## Useful Scripts

- `backend/xlsx_to_script.py` — Convert CAN Test Script Editor `.xlsx` → test runner JSON
- `backend/dbc_to_script_editor.py` — DBC → test script input `.xlsx`
- `samples/make_sample_logs.py` — Regenerate sample `.blf`/`.asc` logs