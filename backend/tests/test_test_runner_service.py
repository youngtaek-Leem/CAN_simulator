import json
import time

from pathlib import Path

import can
import pytest
from conftest import SAMPLES_DIR

from can_manager import CanManager
from dbc_service import DbcService
from replay_service import ReplayService
from test_runner_service import TestRunnerService, parse_functions, parse_script
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
    assert status["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "EngineData.EngineSpeed 응답 없음 (0.3s 초과)"}
    ]


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
        self.saved_golden = []
        self.active_golden = None
        self.missing_goldens = set()

    def start(self, filename):
        self.started.append(filename)
        return {"ok": True, "filename": filename}

    def stop(self):
        self.stopped += 1
        return {"ok": True, "filename": self.started[-1] if self.started else None}

    def rec_path(self, filename):
        return Path("rec") / filename

    def golden_path(self, golden_name):
        return Path("golden") / golden_name

    def get_active_golden(self):
        if self.active_golden and self.active_golden not in self.missing_goldens:
            return self.active_golden
        return None

    def save_as_golden(self, filename, golden):
        self.saved_golden.append((filename, golden))
        return {"ok": True, "saved": golden}


def _wavcmp_result(overall_pass=True, passed=10, total=10, threshold=0.8, per_channel=None):
    segments = [
        {
            "index": i, "t_start_ms": float(i * 100), "t_end_ms": float((i + 1) * 100),
            "pass": i < passed, "value": 0.95 if i < passed else 0.5,
            "sims": {"mfcc_dtw": 0.95, "fft": 0.9, "fft_band": 0.9, "xcorr": 0.9,
                     "rms": 0.9, "zcr": 0.9, "centroid": 0.9},
        }
        for i in range(total)
    ]
    result = {
        "algorithm": "comprehensive", "threshold": threshold, "segment_ms": 100,
        "align": True, "channel": "all", "samplerate": 22050, "offset_ms": 0.0,
        "overall_pass": overall_pass, "segment_count": total, "passed_count": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "overall_value": 0.95 if overall_pass else 0.5,
        "overall_sims": {"mfcc_dtw": 0.95, "fft": 0.9, "fft_band": 0.9, "xcorr": 0.9,
                        "rms": 0.9, "zcr": 0.9, "centroid": 0.9},
        "segments": segments,
    }
    if per_channel is not None:
        result["per_channel"] = per_channel
    return result


class FakeWavCompare:
    """파형비교 엔진 fake -- 호출 기록 + 설정 가능한 결과."""

    def __init__(self):
        self.calls = []
        self.params = {
            "algorithm": "comprehensive", "threshold": 0.8,
            "segment_ms": 100, "align": True,
        }
        self.next_result = None
        self.next_error = None

    def get_params(self):
        return dict(self.params)

    def compare_files(self, path_a, path_b, label_a, label_b, source="runner",
                      algorithm="comprehensive", threshold=0.8, segment_ms=100,
                      align=True, channel="all", publish=True):
        self.calls.append({
            "path_a": str(path_a), "path_b": str(path_b),
            "label_a": label_a, "label_b": label_b, "source": source,
            "algorithm": algorithm, "threshold": threshold,
            "segment_ms": segment_ms, "align": align, "channel": channel,
        })
        if self.next_error is not None:
            raise self.next_error
        if self.next_result is not None:
            return self.next_result
        return _wavcmp_result(overall_pass=True, threshold=threshold)


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
    wavcmp = FakeWavCompare()
    runner = TestRunnerService(
        cm, dbc, sched, replay, tmp_path / "logs", tmp_path / "results",
        power_service=power, audio_service=audio, wav_compare_service=wavcmp,
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "results").mkdir()
    yield runner, power, audio, wavcmp
    runner.stop()
    sched.shutdown()
    cm.disconnect()


def test_power_step_calls_fake_service(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
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
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "파워서플라이 서비스 없음"}
    ]


def test_audio_record_and_compare_sequence(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
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
    # 파형비교 엔진으로 golden 비교 -- 전채널 AND 고정
    assert len(wavcmp.calls) == 1
    call = wavcmp.calls[0]
    assert call["label_a"] == audio.started[0] and call["label_b"] == "case1_golden.wav"
    assert call["threshold"] == 0.9 and call["channel"] == "all"
    assert call["algorithm"] == "comprehensive" and call["source"] == "runner"
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_bare_compwav_with_one_recording_fails_as_no_compare_file(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert wavcmp.calls == []
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "비교파일없음"}
    ]


def test_audio_bare_compwav_uses_uploaded_gold_with_single_recording(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    audio.active_golden = "gold.wav"
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert len(wavcmp.calls) == 1
    call = wavcmp.calls[0]
    assert call["label_a"] == audio.started[0] and call["label_b"] == "gold.wav"
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]
    log_line = next(
        e.get("msg", "") for e in runner.status()["events"] if e.get("message") == "compWAV"
    )
    assert "gold.wav" in log_line and "Pass" in log_line


def test_audio_bare_compwav_falls_back_to_recordings_when_gold_missing(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    audio.active_golden = "deleted.wav"
    audio.missing_goldens.add("deleted.wav")
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "StartREC", "recName": "b"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert [(c["label_a"], c["label_b"]) for c in wavcmp.calls] == [
        (audio.started[0], audio.started[1])
    ]
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_step_golden_field_beats_remembered_gold(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    audio.active_golden = "remembered.wav"
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "Audio", "command": "compWAV", "golden": "explicit.wav"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert wavcmp.calls[0]["label_b"] == "explicit.wav"
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_bare_compwav_compares_previous_and_current_recordings(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "prev"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "StartREC", "recName": "curr"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV", "threshold": 0.9},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert len(wavcmp.calls) == 1
    call = wavcmp.calls[0]
    assert call["label_a"] == audio.started[0] and call["label_b"] == audio.started[1]
    assert call["threshold"] == 0.9 and call["channel"] == "all"
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]
    log_line = next(
        e.get("msg", "") for e in runner.status()["events"] if e.get("message") == "compWAV"
    )
    assert audio.started[0] in log_line and audio.started[1] in log_line
    assert "Pass" in log_line and "0.9" in log_line


def test_audio_bare_compwav_sliding_window_uses_last_two(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "StartREC", "recName": "b"},
        {"type": "Audio", "command": "StartREC", "recName": "c"},
        {"type": "Audio", "command": "compWAV"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert [(c["label_a"], c["label_b"]) for c in wavcmp.calls] == [
        (audio.started[1], audio.started[2]),
        (audio.started[1], audio.started[2]),
    ]
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_bare_compwav_failure_reason_reaches_result(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    wavcmp.next_result = _wavcmp_result(overall_pass=False, passed=3, threshold=0.8)
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "StartREC", "recName": "b"},
        {"type": "Audio", "command": "compWAV"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "통과율 3/10 (임계 0.8)"}
    ]
    log_line = next(
        e.get("msg", "") for e in runner.status()["events"] if e.get("message") == "compWAV"
    )
    assert "Fail" in log_line and "실패구간 7개" in log_line


def test_audio_compwav_block_overrides_shared_params(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "StartREC", "recName": "b"},
        {"type": "Audio", "command": "compWAV", "algorithm": "mfcc_dtw",
         "segment_ms": 50, "align": False},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    call = wavcmp.calls[0]
    assert call["algorithm"] == "mfcc_dtw"
    assert call["segment_ms"] == 50 and call["align"] is False
    # 스텝 재지정은 공유 설정에 영향을 주지 않는다
    assert wavcmp.get_params() == {
        "algorithm": "comprehensive", "threshold": 0.8,
        "segment_ms": 100, "align": True,
    }
    assert runner.status()["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]


def test_audio_compwav_without_compare_service_fails(runner_with_fakes, monkeypatch):
    runner, power, audio, wavcmp = runner_with_fakes
    monkeypatch.setattr(runner, "_wavcmp", None)
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "compWAV", "golden": "g.wav"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "파형비교 서비스 없음"}
    ]


def test_audio_compwav_missing_file_reports_reason(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
    wavcmp.next_error = FileNotFoundError("비교 파일 없음: no.wav")
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "a"},
        {"type": "Audio", "command": "compWAV", "golden": "no.wav"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "비교 파일 없음: no.wav"}
    ]


def test_audio_save_as_golden(runner_with_fakes):
    runner, power, audio, wavcmp = runner_with_fakes
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


def test_compwav_compare_failure_reason_reaches_result(runner_with_fakes):
    """CompWAV 불일치 시 통과율이 케이스 결과의 reason에 그대로 담겨야 한다
    (테스트 결과 화면 표시용)."""
    runner, power, audio, wavcmp = runner_with_fakes
    wavcmp.next_result = _wavcmp_result(overall_pass=False, passed=0, threshold=0.9)
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Audio", "command": "StartREC", "recName": "log1"},
        {"type": "Audio", "command": "StopREC"},
        {"type": "Audio", "command": "compWAV", "golden": "case1_golden.wav", "threshold": 0.9},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    assert runner.status()["results"] == [
        {"case": "1", "cycle": 1, "status": "Fail", "reason": "통과율 0/10 (임계 0.9)"}
    ]


def test_multiple_step_failures_joined_in_result_reason(stack):
    """한 케이스에서 여러 스텝이 실패하면 모든 이유가 ' / '로 연결된다."""
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "Power", "command": "ACC_IGN_On"},
        {"type": "Audio", "command": "compWAV", "golden": "case1_golden.wav"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.3)
    results = runner.status()["results"]
    assert len(results) == 1 and results[0]["status"] == "Fail"
    assert results[0]["reason"] == "파워서플라이 서비스 없음 / 오디오 서비스 없음"


# ---- FUNC / Function Button (parsing + single-name execution) ----------------


def test_parse_functions_boundary():
    raw = [
        {"type": "FUNC", "name": "PowerTest", "Cycle": 1},
        {"type": "Power", "command": "ACC_On"},
        {"type": "FUNC", "name": "TickTok", "Cycle": 2},
        {"type": "delay", "ms": 5},
    ]
    functions = parse_functions(raw)
    assert [f.num for f in functions] == ["PowerTest", "TickTok"]
    assert functions[0].cycle == 1 and [s.type for s in functions[0].steps] == ["Power"]
    assert functions[1].cycle == 2 and [s.type for s in functions[1].steps] == ["delay"]


def test_disabled_func_blocks_are_skipped():
    raw = [
        {"type": "FUNC", "name": "A", "Cycle": 1},
        {"type": "delay", "ms": 5},
        {"_type": "FUNC", "name": "B", "Cycle": 1},
        {"type": "delay", "ms": 5},
    ]
    functions = parse_functions(raw)
    assert [f.num for f in functions] == ["A"]


def test_start_function_runs_only_that_case(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "FUNC", "name": "SendSpeed", "Cycle": 1},
        {"type": "CANReq", "Message": "EngineData", "Signal": "EngineSpeed", "Value": "0x01"},
        {"type": "FUNC", "name": "SendTurn", "Cycle": 1},
        {"type": "CANReq", "Message": "DriverCommand", "Signal": "TurnSignal", "Value": "0x01"},
    ]
    runner.load_functions(json.dumps(script), "funcs.json")
    runner.start_function("SendTurn")
    frames = drain(peer, 0.3)
    runner.stop()
    assert any(f.arbitration_id == 0x300 for f in frames)
    assert not any(f.arbitration_id == 0x100 for f in frames)
    assert runner.status()["results"] == [{"case": "SendTurn", "cycle": 1, "status": "OK"}]


def test_start_function_unknown_name_raises(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    runner.load_functions(json.dumps([{"type": "FUNC", "name": "A", "Cycle": 1}, {"type": "delay", "ms": 5}]), "f.json")
    with pytest.raises(RuntimeError):
        runner.start_function("DoesNotExist")


def test_start_function_blocked_while_scenario_running(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    runner.load(json.dumps([{"type": "ID", "num": "1", "Cycle": 1}, {"type": "delay", "ms": 2000}]), "s.json")
    runner.load_functions(json.dumps([{"type": "FUNC", "name": "A", "Cycle": 1}, {"type": "delay", "ms": 5}]), "f.json")
    runner.start()
    time.sleep(0.1)
    with pytest.raises(RuntimeError):
        runner.start_function("A")
    runner.stop()


def test_scenario_start_blocked_while_function_running(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    runner.load(json.dumps([{"type": "ID", "num": "1", "Cycle": 1}, {"type": "delay", "ms": 10}]), "s.json")
    runner.load_functions(json.dumps([{"type": "FUNC", "name": "A", "Cycle": 1}, {"type": "delay", "ms": 2000}]), "f.json")
    runner.start_function("A")
    time.sleep(0.1)
    with pytest.raises(RuntimeError):
        runner.start()
    runner.stop()


def test_functions_summary_lists_names(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    assert runner.summary()["functions"] == {"loaded": False, "filename": None, "names": []}
    script = [
        {"type": "FUNC", "name": "PowerTest", "Cycle": 1},
        {"type": "delay", "ms": 5},
        {"type": "FUNC", "name": "TickTok", "Cycle": 1},
        {"type": "delay", "ms": 5},
    ]
    runner.load_functions(json.dumps(script), "funcs.json")
    assert runner.summary()["functions"] == {
        "loaded": True,
        "filename": "funcs.json",
        "names": ["PowerTest", "TickTok"],
    }


def test_load_functions_without_func_blocks_raises(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    # an ordinary ID-based scenario script has no FUNC boundaries at all
    script = [{"type": "ID", "num": "1", "Cycle": 1}, {"type": "delay", "ms": 5}]
    with pytest.raises(ValueError):
        runner.load_functions(json.dumps(script), "not_a_func_script.json")
    # a rejected upload must not clobber a previously loaded function library
    runner.load_functions(
        json.dumps([{"type": "FUNC", "name": "A", "Cycle": 1}, {"type": "delay", "ms": 5}]), "good.json"
    )
    with pytest.raises(ValueError):
        runner.load_functions(json.dumps(script), "bad.json")
    assert runner.summary()["functions"]["filename"] == "good.json"


def test_running_case_tracks_active_function(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    runner.load_functions(
        json.dumps([{"type": "FUNC", "name": "SlowFunc", "Cycle": 1}, {"type": "delay", "ms": 300}]),
        "f.json",
    )
    assert runner.summary()["running_case"] is None
    runner.start_function("SlowFunc")
    time.sleep(0.1)
    assert runner.summary()["running_case"] == "SlowFunc"
    runner.stop()
    assert runner.summary()["running_case"] is None


# ---- CANStart / CANStop ------------------------------------------------------


def test_canstart_arms_and_canstop_stops_only_those(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    # classic virtual bus: FdSensorData fails its initial send, the other
    # three arm -- partial success still passes the step
    assert runner._run_canstart({}, "1") is True
    assert runner._canstart_armed == {"EngineData", "VehicleSpeed", "BodyStatus"}
    # a TxBox row for the same message must survive CANStop
    sched.row_periodic_start("row1", "EngineData", {"EngineSpeed": 1000}, None, 50)
    assert runner._run_canstop("1") is True
    assert runner._canstart_armed == set()
    keys = {e["key"] for e in sched.status()["auto_entries"]}
    assert "row1" in keys
    assert not any("EngineData" in k for k in keys if k != "row1")
    sched.row_periodic_stop("row1")


def test_canstart_rxnode_exclusion(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    assert runner._run_canstart({"RxNode": "ECU_A"}, "1") is True
    # ECU_A sends EngineData/VehicleSpeed/FdSensorData -- only ECU_B's
    # BodyStatus remains (DriverCommand is event-tagged, never armed)
    assert runner._canstart_armed == {"BodyStatus"}
    assert runner._run_canstop("1") is True


def test_canstart_all_failed_returns_false(monkeypatch, stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    monkeypatch.setattr(
        sched, "enable_all_periodic",
        lambda rx_node="": {"armed": [], "failed": [{"message_name": "X", "reason": "boom"}]},
    )
    assert runner._run_canstart({}, "1") is False
    assert runner._canstart_armed == set()


def test_canstart_canstop_script_run(stack):
    cm, dbc, sched, replay, runner, peer, log_dir, result_dir = stack
    script = [
        {"type": "ID", "num": "1", "Cycle": 1},
        {"type": "CANStart"},
        {"type": "delay", "ms": 150},
        {"type": "CANStop"},
    ]
    runner.load(json.dumps(script), "t.json")
    runner.start()
    time.sleep(0.6)
    status = runner.status()
    assert status["results"] == [{"case": "1", "cycle": 1, "status": "OK"}]
    kinds = [(e.get("type"), e.get("status")) for e in status["events"]]
    assert any(k == "CANStart" and s.startswith("OK") for k, s in kinds)
    assert any(k == "CANStop" and s.startswith("OK") for k, s in kinds)
    assert runner._canstart_armed == set()


# ---- CAN raw Value 숫자 해석 ("0x.."→16진수, 그 외→10진수) --------------------


def test_parse_raw_value_hex_decimal_binary():
    p = TestRunnerService._parse_raw_value
    assert p("0x10") == 16
    assert p("0X10") == 16
    assert p(" 0x01 ") == 1
    assert p("10") == 10  # 10진수 -- 과거에는 16으로 오인됨
    assert p(10) == 10  # JSON 숫자 / 엑셀 숫자셀
    assert p(10.0) == 10
    assert p("0b101") == 5
    with pytest.raises(ValueError):
        p("zz")
    with pytest.raises(ValueError):
        p("")
