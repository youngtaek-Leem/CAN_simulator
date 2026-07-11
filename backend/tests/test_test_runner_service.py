import json
import time

import can
import pytest
from conftest import SAMPLES_DIR

from can_manager import CanManager
from dbc_service import DbcService
from replay_service import ReplayService
from test_runner_service import TestRunnerService, parse_script
from tx_scheduler import TxScheduler


@pytest.fixture
def stack(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_runner", receive_own_messages=False)
    dbc = DbcService()
    dbc.load_string((SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8"), "sample.dbc")
    sched = TxScheduler(cm, dbc)
    replay = ReplayService(cm)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    runner = TestRunnerService(cm, dbc, sched, replay, log_dir, result_dir)
    peer = can.Bus(interface="virtual", channel="t_runner")
    yield cm, dbc, sched, replay, runner, peer, log_dir, result_dir
    runner.stop()
    sched.shutdown()
    peer.shutdown()
    cm.disconnect()


def drain(peer, duration_s: float = 0.3):
    frames = []
    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        msg = peer.recv(timeout=0.05)
        if msg is not None:
            frames.append(msg)
    return frames


# ---- parsing ---------------------------------------------------------------


def test_parse_legacy_id_gotoid_loop():
    raw = [
        {"type": "ID", "num": "1", "Cycle": 2},
        {"type": "Loop", "id": "l1", "Cycle": 3},
        {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
        {"type": "delay", "ms": 10},
        {"type": "Loop", "gotoid": "l1"},
        {"type": "delay", "ms": 5},
    ]
    cases = parse_script(raw)
    assert len(cases) == 1
    case = cases[0]
    assert case.num == "1" and case.cycle == 2
    assert len(case.steps) == 2
    loop_step, delay_step = case.steps
    assert loop_step.type == "loop" and loop_step.cycle == 3
    assert [s.type for s in loop_step.children] == ["CANReq", "delay"]
    assert delay_step.type == "delay"


def test_parse_new_nested_loop():
    raw = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {
            "type": "loop",
            "cycle": 4,
            "steps": [
                {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
            ],
        },
    ]
    cases = parse_script(raw)
    assert len(cases) == 1
    (loop_step,) = cases[0].steps
    assert loop_step.type == "loop" and loop_step.cycle == 4
    assert len(loop_step.children) == 1


def test_disabled_blocks_are_skipped():
    raw = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"_type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
        {"type": "delay", "ms": 5},
        {"_type": "ID", "num": "2", "Cycle": 1},
        {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
    ]
    cases = parse_script(raw)
    # case 1 keeps only the delay step (disabled CANReq skipped);
    # case 2 is entirely disabled (_type: ID) and does not appear at all
    assert len(cases) == 1
    assert [s.type for s in cases[0].steps] == ["delay"]


# ---- CANReq / value conversion ---------------------------------------------


def test_canreq_converts_raw_hex_to_scaled_value(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        # EngineTemp: scale=1, offset=-40 -> raw 0x5A(90) => physical 50
        {"type": "CANReq", "Message": "EngineData", "Signal": "EngineTemp", "Value": "0x5A"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    frames = drain(peer, 0.3)
    runner.stop()
    engine_frames = [f for f in frames if f.arbitration_id == 0x100]
    assert engine_frames, "no EngineData frame sent"
    decoded = dbc.decode(0x100, bytes(engine_frames[0].data))
    assert decoded["signals"]["EngineTemp"] == 50


def test_multi_signal_canreq(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {
            "type": "CANReq",
            "Message": "DriverCommand",
            "Signals": [
                {"Signal": "TurnSignal", "Value": "0x01"},
                {"Signal": "WiperMode", "Value": "0x02"},
            ],
        },
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    frames = drain(peer, 0.3)
    runner.stop()
    cmd_frames = [f for f in frames if f.arbitration_id == 0x300]
    assert cmd_frames
    decoded = dbc.decode(0x300, bytes(cmd_frames[0].data))
    assert decoded["signals"]["TurnSignal"] == "Left"
    assert decoded["signals"]["WiperMode"] == "Low"  # WiperMode has a VAL_ table


# ---- CANResp -----------------------------------------------------------------


def test_canresp_pass_when_peer_replies_in_time(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        # EngineSpeed scale=0.25 -> physical 3000 rpm is raw 12000 (0x2EE0)
        {"type": "CANResp", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x2EE0", "timeout_s": 1.0},
    ]
    runner.load(json.dumps(script), "t.json")

    def reply_soon():
        time.sleep(0.2)
        data = dbc.encode_with_values("EngineData", {"EngineSpeed": 3000})
        peer.send(can.Message(arbitration_id=0x100, data=data, is_extended_id=False))

    import threading

    threading.Thread(target=reply_soon, daemon=True).start()
    runner.start()
    time.sleep(1.2)
    status = runner.status()
    assert status["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_canresp_fails_on_timeout(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "CANResp", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x0BB8", "timeout_s": 0.3},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.6)
    status = runner.status()
    assert status["results"] == [{"case": "1", "cycle": 1, "status": "Fail"}]


# ---- Loop repeat count -------------------------------------------------------


def test_loop_repeats_exact_count(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    # HornRequest ([EC] event, not periodic) so nothing auto-resends between
    # iterations -- each CANReq send produces exactly valid+invalid (2 frames).
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {
            "type": "loop",
            "cycle": 5,
            "steps": [
                {"type": "CANReq", "Message": "DriverCommand", "Signal": "HornRequest", "Value": "0x01"},
                {"type": "delay", "ms": 50},
            ],
        },
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    frames = drain(peer, 1.5)
    runner.stop()
    cmd_frames = [f for f in frames if f.arbitration_id == 0x300]
    assert len(cmd_frames) == 10, f"expected 5x(valid+invalid)=10 sends, got {len(cmd_frames)}"


# ---- cleanup on completion ----------------------------------------------------


def test_periodic_signal_auto_entry_cleared_after_run(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x03E8"},
        {"type": "delay", "ms": 50},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert not runner.status()["running"]
    assert sched.status()["auto_entries"] == []


def test_result_file_saved(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "delay", "ms": 10},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    saved = list(result_dir.glob("scenario_result_*.json"))
    assert len(saved) == 1
    data = json.loads(saved[0].read_text(encoding="utf-8"))
    assert data == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_stop_interrupts_running_script(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "delay", "ms": 5000},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.1)
    assert runner.status()["running"] is True
    runner.stop()
    assert runner.status()["running"] is False


# ---- CANlogReplay -------------------------------------------------------------


def write_blf(path, frame_id: int, count: int = 5):
    with can.BLFWriter(str(path)) as writer:
        for i in range(count):
            writer.on_message_received(
                can.Message(
                    arbitration_id=frame_id,
                    data=bytes([i] * 4),
                    is_extended_id=False,
                    timestamp=i * 0.02,
                    is_rx=True,
                )
            )


def test_canlogreplay_sends_logged_frames(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    write_blf(log_dir / "log.blf", 0x222, count=5)
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "CANlogReplay", "logfile": "log.blf", "Cycle": 1},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    frames = drain(peer, 1.0)
    runner.stop()
    matching = [f for f in frames if f.arbitration_id == 0x222]
    assert len(matching) == 5


def test_canlogreplay_excludes_sender_node(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    # EngineData (0x100) is sent by ECU_A in sample.dbc; VehicleSpeed (0x200-ish) too.
    write_blf(log_dir / "mix.blf", 0x100, count=3)
    with can.BLFWriter(str(log_dir / "mix2.blf")) as writer:
        pass
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {
            "type": "CANlogReplay",
            "logfile": "mix.blf",
            "Cycle": 1,
            "excludeSenders": ["ECU_A"],
        },
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    frames = drain(peer, 0.6)
    runner.stop()
    # 0x100 (EngineData) is sent by ECU_A -> excluded entirely
    assert not any(f.arbitration_id == 0x100 for f in frames)


# ---- Power / Audio step wiring (fake service doubles -- no real hardware) ----


class FakePower:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def set_power(self, block):
        self.calls.append(block)
        return {"ok": self.ok, "reason": None if self.ok else "fake failure"}


class FakeAudio:
    def __init__(self):
        self.started = []
        self.stopped = 0
        self.compared = []
        self.saved_golden = []

    def start(self, filename):
        self.started.append(filename)
        return {"ok": True, "filename": filename}

    def stop(self):
        self.stopped += 1
        return {"ok": True, "filename": self.started[-1] if self.started else None}

    def compare(self, filename, golden, threshold):
        self.compared.append((filename, golden, threshold))
        return {"ok": True, "channels": {}}

    def save_as_golden(self, filename, golden):
        self.saved_golden.append((filename, golden))
        return {"ok": True, "saved": golden}


@pytest.fixture
def runner_with_fakes(tmp_path):
    cm = CanManager()
    cm.connect("virtual", "t_runner_pa", receive_own_messages=False)
    dbc = DbcService()
    dbc.load_string((SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8"), "sample.dbc")
    sched = TxScheduler(cm, dbc)
    replay = ReplayService(cm)
    power = FakePower()
    audio = FakeAudio()
    runner = TestRunnerService(
        cm, dbc, sched, replay, tmp_path / "logs", tmp_path / "results",
        power_service=power, audio_service=audio,
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "results").mkdir()
    yield runner, power, audio
    runner.stop()
    sched.shutdown()
    cm.disconnect()


def test_power_step_calls_fake_service(runner_with_fakes):
    runner, power, audio = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Power", "command": "ACC_IGN_On"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert power.calls == [{"type": "Power", "command": "ACC_IGN_On"}]
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_power_step_without_service_fails_gracefully(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Power", "command": "ACC_IGN_On"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "Fail"}]


def test_audio_record_and_compare_sequence(runner_with_fakes):
    runner, power, audio = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "delay", "ms": 10},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV", "golden": "case1_golden.wav", "threshold": 0.9},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert len(audio.started) == 1 and audio.started[0].startswith("log1_1_")
    assert audio.stopped == 1
    assert audio.compared == [(audio.started[0], "case1_golden.wav", 0.9)]
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_compwav_without_golden_field_fails(runner_with_fakes):
    runner, power, audio = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert audio.compared == []
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "Fail"}]


def test_audio_save_as_golden(runner_with_fakes):
    runner, power, audio = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "ref"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "saveAsGolden", "golden": "case1_golden.wav"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert audio.saved_golden == [(audio.started[0], "case1_golden.wav")]
