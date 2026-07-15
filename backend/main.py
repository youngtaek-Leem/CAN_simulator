"""CAN evaluation environment backend.

FastAPI server running on the local PC. The browser GUI talks to this server
via REST (configuration) and WebSocket (real-time CAN RX stream / status),
and the server drives the USB-CAN adapter (PCAN / Vector CANcase) or an
in-process virtual bus.

Run:  uvicorn main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import timer_util
import isotp_service
from audio_service import AudioService
from can_manager import CanManager
from dbc_service import DbcService
from power_supply_service import PowerSupplyService
from replay_service import ReplayService
from test_runner_service import TestRunnerService
from tx_scheduler import TxScheduler

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
LAYOUT_DIR = BASE_DIR / "layouts"
TESTRUNNER_LOG_DIR = BASE_DIR / "uploads" / "testrunner_logs"
TESTRUNNER_RESULT_DIR = BASE_DIR / "testrunner_results"
TESTRUNNER_AUDIO_DIR = BASE_DIR / "uploads" / "testrunner_audio"
TESTRUNNER_GOLDEN_DIR = BASE_DIR / "uploads" / "testrunner_golden"
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

can_manager = CanManager()
dbc_service = DbcService()
tx_scheduler = TxScheduler(can_manager, dbc_service)
replay_service = ReplayService(can_manager)
power_supply_service = PowerSupplyService()
audio_service = AudioService(TESTRUNNER_AUDIO_DIR, TESTRUNNER_GOLDEN_DIR)
test_runner_service = TestRunnerService(
    can_manager,
    dbc_service,
    tx_scheduler,
    replay_service,
    TESTRUNNER_LOG_DIR,
    TESTRUNNER_RESULT_DIR,
    power_service=power_supply_service,
    audio_service=audio_service,
)

settings = {"ws_flush_ms": 30}
# global run gate: when stopped, no TX at all and the RX stream is discarded
run_state = {"running": True}
ws_clients: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(exist_ok=True)
    LAYOUT_DIR.mkdir(exist_ok=True)
    TESTRUNNER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    TESTRUNNER_RESULT_DIR.mkdir(exist_ok=True)
    TESTRUNNER_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TESTRUNNER_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    timer_util.enable_1ms_timer()
    broadcaster = asyncio.create_task(_broadcast_loop())
    yield
    broadcaster.cancel()
    test_runner_service.stop()
    replay_service.stop()
    tx_scheduler.shutdown()
    can_manager.disconnect()
    power_supply_service.disconnect()
    timer_util.disable_1ms_timer()


app = FastAPI(title="CAN Evaluation Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- WebSocket: RX stream + status ------------------------------------


def _frame_to_dict(msg) -> dict:
    d = {
        "ts": msg.timestamp,
        "id": msg.arbitration_id,
        "ext": msg.is_extended_id,
        "dlc": msg.dlc,
        "data": msg.data.hex(),
        "fd": msg.is_fd,
        "brs": msg.bitrate_switch,
    }
    decoded = dbc_service.decode(msg.arbitration_id, bytes(msg.data))
    if decoded:
        d["decoded"] = decoded
    return d


async def _broadcast(payload: dict) -> None:
    if not ws_clients:
        return
    text = json.dumps(payload)
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


async def _broadcast_loop() -> None:
    last_status = 0.0
    while True:
        await asyncio.sleep(settings["ws_flush_ms"] / 1000.0)
        frames = can_manager.drain_rx()
        if not run_state["running"]:
            frames = []  # discard RX while globally stopped
        if frames and ws_clients:
            await _broadcast(
                {"type": "rx", "frames": [_frame_to_dict(m) for m in frames]}
            )
        now = time.monotonic()
        if now - last_status >= 0.5:
            last_status = now
            await _broadcast({"type": "status", **_status()})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "status", **_status()}))
        while True:
            await ws.receive_text()  # keepalive; commands go through REST
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# ---- status / connection ----------------------------------------------


def _status() -> dict:
    return {
        "can": can_manager.status(),
        "tx": tx_scheduler.status(),
        "replay": replay_service.info(),
        "dbc": {"loaded": dbc_service.loaded, "filename": dbc_service.filename},
        "settings": dict(settings),
        "run": dict(run_state),
        # lightweight summary only -- the full step-by-step event log is
        # fetched on demand via GET /api/testrunner/status, not broadcast
        # to every client every 0.5s.
        "test_runner": test_runner_service.summary(),
        "power": power_supply_service.info(),
        "audio": audio_service.info(),
    }


def _require_running() -> None:
    if not run_state["running"]:
        raise HTTPException(
            status_code=400, detail="전체 송수신이 정지 상태입니다 (Start를 누르세요)"
        )


@app.get("/api/status")
def get_status():
    return _status()


@app.post("/api/run/start")
def run_start():
    # "Start" means a clean restart, not "resume whatever was armed before" --
    # auto-periodic senders left over from signals touched before the last
    # Stop must not silently resume without the user touching that widget again.
    tx_scheduler.stop_auto()
    run_state["running"] = True
    tx_scheduler.set_paused(False)
    return _status()


@app.post("/api/run/stop")
def run_stop():
    run_state["running"] = False
    tx_scheduler.set_paused(True)
    tx_scheduler.stop_auto()
    replay_service.stop()
    test_runner_service.stop()
    return _status()


class ConnectRequest(BaseModel):
    interface: str
    channel: str
    bitrate: int = 500000
    receive_own_messages: bool = True
    fd: bool = False
    data_bitrate: int = 2_000_000  # CAN-FD data-phase bitrate; ignored unless fd=True


@app.post("/api/connect")
def connect(req: ConnectRequest):
    try:
        return can_manager.connect(
            req.interface,
            req.channel,
            req.bitrate,
            req.receive_own_messages,
            fd=req.fd,
            data_bitrate=req.data_bitrate,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/disconnect")
def disconnect():
    test_runner_service.stop()
    replay_service.stop()
    tx_scheduler.stop()
    tx_scheduler.stop_auto()
    can_manager.disconnect()
    return _status()


class SettingsRequest(BaseModel):
    ws_flush_ms: int


@app.post("/api/settings")
def update_settings(req: SettingsRequest):
    settings["ws_flush_ms"] = max(10, min(500, req.ws_flush_ms))
    return dict(settings)


# ---- DBC ----------------------------------------------------------------


@app.post("/api/dbc/upload")
async def upload_dbc(file: UploadFile):
    raw = await file.read()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        return dbc_service.load_string(text, file.filename or "uploaded.dbc")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"DBC parse error: {exc}")


@app.get("/api/dbc")
def get_dbc():
    return dbc_service.summary()


@app.get("/api/dbc/raw")
def get_dbc_raw():
    return dbc_service.raw() or {"loaded": False}


class SendTypeOverride(BaseModel):
    message_name: str
    signal_name: str
    send_type: str  # "event" | "periodic"


@app.post("/api/dbc/send-type")
def override_send_type(req: SendTypeOverride):
    try:
        dbc_service.set_send_type_override(
            req.message_name, req.signal_name, req.send_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return dbc_service.summary()


# ---- TX -----------------------------------------------------------------


class TxConfigRequest(BaseModel):
    entries: list[dict]


@app.post("/api/tx/configure")
def tx_configure(req: TxConfigRequest):
    try:
        return tx_scheduler.configure(req.entries)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tx/start")
def tx_start():
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    return tx_scheduler.start()


@app.post("/api/tx/stop")
def tx_stop():
    return tx_scheduler.stop()


class SignalSendRequest(BaseModel):
    message_name: str
    values: dict[str, float | int | str]


@app.post("/api/tx/signal")
def tx_signal(req: SignalSendRequest):
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        return tx_scheduler.send_signal(req.message_name, req.values)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class AutoStopRequest(BaseModel):
    message_name: str | None = None


@app.post("/api/tx/auto/stop")
def tx_auto_stop(req: AutoStopRequest):
    return tx_scheduler.stop_auto(req.message_name)


class ValueGeneratorRequest(BaseModel):
    message_name: str
    signal_name: str
    mode: str  # "fixed" | "random" | "range"
    range_min: int | None = None
    range_max: int | None = None
    step: int = 1


@app.post("/api/tx/signal/generator")
def tx_signal_generator(req: ValueGeneratorRequest):
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        tx_scheduler.set_value_generator(
            req.message_name, req.signal_name, req.mode, req.range_min, req.range_max, req.step
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


class GenerateSendRequest(BaseModel):
    message_name: str
    signal_name: str


@app.post("/api/tx/signal/generate")
def tx_signal_generate(req: GenerateSendRequest):
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        return tx_scheduler.send_generated(req.message_name, req.signal_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class InvalidSendRequest(BaseModel):
    message_name: str
    signal_name: str


@app.post("/api/tx/signal/invalid")
def tx_signal_invalid(req: InvalidSendRequest):
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        return tx_scheduler.send_invalid(req.message_name, req.signal_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class IsoTpSendRequest(BaseModel):
    tx_id: int
    fc_id: int
    data: str  # hex string, spaces allowed
    is_extended_id: bool = False
    fc_timeout_ms: int = 1000
    max_wait_frames: int = 10


@app.post("/api/isotp/send")
def isotp_send(req: IsoTpSendRequest):
    # blocking (waits for Flow Control) -- runs in FastAPI's threadpool since
    # this handler is a plain `def`, so it does not block the event loop.
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    hex_str = req.data.replace(" ", "").replace("\n", "").replace("\t", "")
    if len(hex_str) % 2 != 0:
        raise HTTPException(status_code=400, detail="데이터 hex 문자열의 길이가 홀수입니다")
    try:
        data = bytes.fromhex(hex_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"잘못된 hex 데이터: {exc}")
    try:
        return isotp_service.send(
            can_manager,
            req.tx_id,
            req.fc_id,
            data,
            is_extended_id=req.is_extended_id,
            fc_timeout_s=req.fc_timeout_ms / 1000.0,
            max_wait_frames=req.max_wait_frames,
        )
    except isotp_service.IsoTpError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- Replay -------------------------------------------------------------


@app.post("/api/replay/upload")
async def upload_replay(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".blf", ".asc"):
        raise HTTPException(status_code=400, detail="only .blf / .asc are supported")
    dest = UPLOAD_DIR / f"replay{suffix}"
    dest.write_bytes(await file.read())
    try:
        return replay_service.load(str(dest), file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"log parse error: {exc}")


class ReplayStartRequest(BaseModel):
    mode: str = "pass"  # "pass" | "stop"
    frame_ids: list[int] = []


@app.post("/api/replay/start")
def replay_start(req: ReplayStartRequest):
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    try:
        return replay_service.start(req.mode, req.frame_ids)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/replay/stop")
def replay_stop():
    return replay_service.stop()


# ---- Test scenario runner (Automation JSON scripts) -----------------------


@app.post("/api/testrunner/upload")
async def testrunner_upload_script(file: UploadFile):
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        return test_runner_service.load(text, file.filename or "script.json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"시나리오 JSON 파싱 오류: {exc}")


@app.post("/api/testrunner/logfile/upload")
async def testrunner_upload_logfile(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".blf", ".asc"):
        raise HTTPException(status_code=400, detail="only .blf / .asc are supported")
    dest = TESTRUNNER_LOG_DIR / (file.filename or f"log{suffix}")
    dest.write_bytes(await file.read())
    return {"saved": dest.name}


@app.post("/api/testrunner/start")
def testrunner_start():
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        return test_runner_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/testrunner/stop")
def testrunner_stop():
    return test_runner_service.stop()


@app.get("/api/testrunner/status")
def testrunner_status():
    return test_runner_service.status()


@app.post("/api/testrunner/functions/upload")
async def testrunner_upload_functions(file: UploadFile):
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        return test_runner_service.load_functions(text, file.filename or "functions.json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"함수 마스터 JSON 파싱 오류: {exc}")


@app.get("/api/testrunner/functions/raw")
def testrunner_functions_raw():
    return test_runner_service.functions_raw() or {"loaded": False}


class FunctionStartRequest(BaseModel):
    name: str


@app.post("/api/testrunner/functions/start")
def testrunner_start_function(req: FunctionStartRequest):
    _require_running()
    if not can_manager.connected:
        raise HTTPException(status_code=400, detail="CAN bus is not connected")
    if not dbc_service.loaded:
        raise HTTPException(status_code=400, detail="no DBC loaded")
    try:
        return test_runner_service.start_function(req.name)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- Power supply (Phase 2) ------------------------------------------------


@app.post("/api/power/connect")
def power_connect():
    return power_supply_service.connect()


@app.post("/api/power/disconnect")
def power_disconnect():
    return power_supply_service.disconnect()


@app.get("/api/power/status")
def power_status():
    return power_supply_service.info()


# ---- Audio (Phase 2) --------------------------------------------------------


@app.get("/api/audio/devices")
def audio_devices():
    return audio_service.refresh_devices()


class AudioDeviceRequest(BaseModel):
    index: int


@app.post("/api/audio/device")
def audio_select_device(req: AudioDeviceRequest):
    return audio_service.select_device(req.index)


@app.get("/api/audio/status")
def audio_status():
    return audio_service.info()


@app.post("/api/testrunner/golden/upload")
async def testrunner_upload_golden(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".wav":
        raise HTTPException(status_code=400, detail="only .wav is supported")
    dest = TESTRUNNER_GOLDEN_DIR / (file.filename or "golden.wav")
    dest.write_bytes(await file.read())
    return {"saved": dest.name}


# ---- Layout persistence --------------------------------------------------


def _layout_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    if not safe:
        raise HTTPException(status_code=400, detail="invalid layout name")
    return LAYOUT_DIR / f"{safe}.json"


@app.get("/api/layouts")
def list_layouts():
    return {"layouts": sorted(p.stem for p in LAYOUT_DIR.glob("*.json"))}


@app.get("/api/layouts/{name}")
def get_layout(name: str):
    path = _layout_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="layout not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/layouts/{name}")
async def save_layout(name: str, body: dict):
    _layout_path(name).write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"saved": name}


@app.delete("/api/layouts/{name}")
def delete_layout(name: str):
    path = _layout_path(name)
    if path.exists():
        path.unlink()
    return {"deleted": name}


# ---- Frontend static files (production build) ----------------------------

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
