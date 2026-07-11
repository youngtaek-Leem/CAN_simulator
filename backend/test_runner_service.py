"""Test scenario runner: interprets a JSON step script (ported from
Automation/AppTest.py's test_script_Rev01.json format) and drives the CAN bus
through the existing dbc_service/can_manager/tx_scheduler/replay_service --
no separate bus connection or DBC parser of its own.

Step types (see Requirement.md "Automation 시나리오 러너 통합 계획"):
- ID: case boundary. {"type": "ID", "num": "1", "Cycle": 3}
- CANReq / CANEv: send one or more signal values on a message. Both are
  handled identically here -- tx_scheduler.send_signal() already applies the
  correct Event(30ms invalid)/Periodic rule per signal via the DBC's
  [TAG]-based classification, so there is no need (and no correct way,
  without duplicating that classification) to hardcode a separate manual
  "send invalid 30ms later" path for CANEv the way AppTest.py did.
- delay: {"type": "delay", "ms": 1000}
- CANResp: wait up to a timeout for a signal to reach an expected raw value.
- CANlogReplay: replay a .blf/.asc log (via replay_service), optionally
  excluding frames whose message is sent by given DBC node(s) -- this
  replaces AppTest.py's hardcoded hex ID exclude-list with a DBC-driven one.
- Power / Audio / AP: Phase 2 (not yet implemented) -- logged and skipped so
  a script mixing these with CAN steps still runs its CAN portion.
- Loop: two supported forms --
  - new: {"type": "loop", "cycle": 3, "steps": [...]} (nested)
  - legacy: {"type": "Loop", "id": "id1", "Cycle": 3} ... {"type": "Loop",
    "gotoid": "id1"} (the id/gotoid span is scanned and normalized into the
    same nested representation), for backward compatibility with existing
    Automation/*.json files.
- Any block with "_type" instead of "type" is disabled/commented-out and
  skipped (including a whole case if it's "_type": "ID"), matching
  AppTest.py's convention.

Values in the JSON are raw hex bit patterns (e.g. "0x03"), not physical
values -- they are converted via each signal's scale/offset before being
handed to dbc_service (which expects physical values), and CANResp compares
against a raw decode (dbc_service.decode_raw) rather than the scaled/
choice-label decode used for display, so the comparison is correct
regardless of a signal's scale or VAL_ table.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import can

from audio_service import DEFAULT_THRESHOLD as DEFAULT_COMPARE_THRESHOLD

MAX_EVENTS = 500  # cap kept in memory / returned via status()


@dataclass
class Step:
    type: str
    raw: dict = field(default_factory=dict)
    cycle: int = 1
    children: list["Step"] = field(default_factory=list)


@dataclass
class Case:
    num: str
    cycle: int
    steps: list[Step]


def _parse_step_list(blocks: list[dict]) -> list[Step]:
    steps: list[Step] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if "type" not in b and "_type" in b:
            i += 1  # disabled leaf step
            continue
        t = b.get("type")
        if t == "loop" and "steps" in b:
            cycle = int(b.get("cycle", b.get("Cycle", 1)))
            steps.append(Step(type="loop", cycle=cycle, children=_parse_step_list(b["steps"])))
            i += 1
        elif t == "Loop" and "id" in b:
            loop_id = b["id"]
            cycle = int(b.get("Cycle", 1))
            j = i + 1
            body: list[dict] = []
            while j < n and not (blocks[j].get("type") == "Loop" and blocks[j].get("gotoid") == loop_id):
                body.append(blocks[j])
                j += 1
            steps.append(Step(type="loop", cycle=cycle, children=_parse_step_list(body)))
            i = j + 1  # skip past the matching gotoid marker
        elif t == "Loop" and "gotoid" in b:
            i += 1  # orphan end marker, ignore
        else:
            steps.append(Step(type=t, raw=b))
            i += 1
    return steps


def parse_script(raw_steps: list[dict]) -> list[Case]:
    cases: list[Case] = []
    i = 0
    n = len(raw_steps)
    while i < n:
        b = raw_steps[i]
        is_id = b.get("type") == "ID"
        is_disabled_id = b.get("_type") == "ID"
        if is_id or is_disabled_id:
            j = i + 1
            body: list[dict] = []
            while j < n and raw_steps[j].get("type") != "ID" and raw_steps[j].get("_type") != "ID":
                body.append(raw_steps[j])
                j += 1
            if is_id:
                cases.append(Case(num=str(b.get("num", "")), cycle=int(b.get("Cycle", 1)), steps=_parse_step_list(body)))
            i = j
        else:
            i += 1  # stray block before the first ID -- ignore
    return cases


class TestRunnerService:
    __test__ = False  # not a pytest test class, despite the name

    def __init__(
        self,
        can_manager,
        dbc_service,
        tx_scheduler,
        replay_service,
        log_dir: Path,
        result_dir: Path,
        power_service=None,
        audio_service=None,
    ):
        self._can = can_manager
        self._dbc = dbc_service
        self._tx = tx_scheduler
        self._replay = replay_service
        self._log_dir = log_dir
        self._result_dir = result_dir
        self._power = power_service
        self._audio = audio_service
        self._last_recording: Optional[str] = None

        # reentrant: status() calls summary() while already holding the lock
        self._lock = threading.RLock()
        self._cases: list[Case] = []
        self._script_name: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._events: list[dict] = []
        self._results: list[dict] = []

    # ---- script loading ---------------------------------------------------

    def load(self, text: str, filename: str) -> dict:
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("scenario JSON must be a top-level array of steps")
        cases = parse_script(raw)
        with self._lock:
            self._cases = cases
            self._script_name = filename
            self._events = []
            self._results = []
        return self.summary()

    # ---- status -------------------------------------------------------------

    def summary(self) -> dict:
        """Lightweight status for the general /api/status broadcast."""
        with self._lock:
            return {
                "loaded": self._script_name is not None,
                "filename": self._script_name,
                "running": self._running,
                "case_count": len(self._cases),
                "result_count": len(self._results),
            }

    def status(self) -> dict:
        """Full status (events + results) for the dedicated endpoint."""
        with self._lock:
            return {
                **self.summary(),
                "events": list(self._events),
                "results": list(self._results),
            }

    # ---- run / stop ---------------------------------------------------------

    def start(self) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("test script already running")
            if not self._cases:
                raise RuntimeError("no test script loaded")
            self._events = []
            self._results = []
            self._running = True
        self._last_recording = None
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        return self.status()

    def _log(self, **fields: Any) -> None:
        entry = {"ts": time.time(), **fields}
        with self._lock:
            self._events.append(entry)
            if len(self._events) > MAX_EVENTS:
                del self._events[: len(self._events) - MAX_EVENTS]

    def _add_result(self, **fields: Any) -> None:
        with self._lock:
            self._results.append(fields)

    def _run(self) -> None:
        try:
            with self._lock:
                cases = list(self._cases)
            for case in cases:
                if self._stop_event.is_set():
                    break
                for c in range(case.cycle):
                    if self._stop_event.is_set():
                        break
                    self._log(case=case.num, msg=f"케이스 {case.num} 반복 {c + 1}/{case.cycle} 시작")
                    ok = self._run_steps(case.steps, case.num)
                    self._add_result(case=case.num, cycle=c + 1, status="OK" if ok else "Fail")
        finally:
            # a script run is a self-contained scope: whatever periodic
            # auto-senders it armed via CANReq/CANEv must not keep firing
            # after the run ends (mirrors the global Start/Stop behavior).
            self._tx.stop_auto()
            with self._lock:
                self._running = False
            self._save_result_file()

    def _run_steps(self, steps: list[Step], case_num: str) -> bool:
        ok = True
        for step in steps:
            if self._stop_event.is_set():
                return ok
            if step.type == "loop":
                for _ in range(step.cycle):
                    if self._stop_event.is_set():
                        return ok
                    if not self._run_steps(step.children, case_num):
                        ok = False
            elif not self._run_leaf(step, case_num):
                ok = False
        return ok

    def _run_leaf(self, step: Step, case_num: str) -> bool:
        b = step.raw
        t = step.type
        try:
            if t in ("CANReq", "CANEv"):
                self._send_can(b)
                self._log(case=case_num, type=t, message=b.get("Message"), status="Sent")
                return True
            if t == "delay":
                self._stop_event.wait(timeout=b["ms"] / 1000.0)
                return True
            if t == "CANResp":
                ok = self._check_response(b)
                self._log(
                    case=case_num, type=t, message=b.get("Message"), signal=b.get("Signal"),
                    status="OK" if ok else "Fail",
                )
                return ok
            if t == "CANlogReplay":
                return self._replay_log(b, case_num)
            if t == "Power":
                return self._run_power(b, case_num)
            if t == "Audio":
                return self._run_audio(b, case_num)
            if t == "AP":
                # ported as-is from AppTest.py's AP class, which itself only
                # logs -- there was never a real analyzer integration to port.
                self._log(case=case_num, type=t, status="측정기 연동 없음 (로그만 기록)")
                return True
            self._log(case=case_num, type=t, status="알 수 없는 스텝 타입")
            return True
        except Exception as exc:
            self._log(case=case_num, type=t, status=f"오류: {exc}")
            return False

    # ---- CAN send / response -------------------------------------------------

    @staticmethod
    def _hex_to_scaled(message, signal_name: str, hex_str: str) -> float:
        signal = message.get_signal_by_name(signal_name)
        raw = int(hex_str, 16)
        return raw * float(signal.scale) + float(signal.offset)

    def _send_can(self, block: dict) -> None:
        message_name = block["Message"]
        message = self._dbc.get_message(message_name)
        values: dict[str, float] = {}
        if "Signals" in block:
            for sig in block["Signals"]:
                values[sig["Signal"]] = self._hex_to_scaled(message, sig["Signal"], sig["Value"])
        else:
            values[block["Signal"]] = self._hex_to_scaled(message, block["Signal"], block["Value"])
        self._tx.send_signal(message_name, values)

    def _check_response(self, block: dict) -> bool:
        message_name = block["Message"]
        signal_name = block["Signal"]
        expected_raw = int(block["Value"], 16)
        timeout_s = float(block.get("timeout_s", 1.0))
        message = self._dbc.get_message(message_name)
        frame_id = message.frame_id

        matched = threading.Event()
        dbc = self._dbc

        class _RespListener(can.Listener):
            def on_message_received(_self, msg: can.Message) -> None:
                if msg.arbitration_id != frame_id:
                    return
                decoded = dbc.decode_raw(frame_id, bytes(msg.data))
                if decoded is not None and decoded.get(signal_name) == expected_raw:
                    matched.set()

        listener = _RespListener()
        self._can.add_listener(listener)
        try:
            matched.wait(timeout=timeout_s)
        finally:
            self._can.remove_listener(listener)
        return matched.is_set()

    # ---- power supply -----------------------------------------------------------

    def _run_power(self, block: dict, case_num: str) -> bool:
        if self._power is None:
            self._log(case=case_num, type="Power", status="파워서플라이 서비스 없음")
            return False
        result = self._power.set_power(block)
        ok = bool(result.get("ok"))
        self._log(
            case=case_num, type="Power", message=block.get("command"),
            status="OK" if ok else f"실패: {result.get('reason')}",
        )
        return ok

    # ---- audio --------------------------------------------------------------------

    def _run_audio(self, block: dict, case_num: str) -> bool:
        if self._audio is None:
            self._log(case=case_num, type="Audio", status="오디오 서비스 없음")
            return False
        cmd = block.get("command")

        if cmd in ("StartREC", "StartRECtime", "StartRECref"):
            rec_name = block.get("recName", "rec")
            filename = f"{rec_name}_{case_num}_{int(time.time() * 1000)}.wav"
            result = self._audio.start(filename)
            if result.get("ok"):
                self._last_recording = filename
            ok = bool(result.get("ok"))
            self._log(case=case_num, type="Audio", message=cmd, status="OK" if ok else f"실패: {result.get('reason')}")
            return ok

        if cmd == "StopREC":
            result = self._audio.stop()
            ok = bool(result.get("ok"))
            self._log(case=case_num, type="Audio", message=cmd, status="OK" if ok else f"실패: {result.get('reason')}")
            return ok

        if cmd == "compWAV":
            golden = block.get("golden")
            if not golden:
                self._log(case=case_num, type="Audio", message=cmd, status="'golden' 필드 없음 -- 비교 생략")
                return False
            if not self._last_recording:
                self._log(case=case_num, type="Audio", message=cmd, status="비교할 녹음 파일 없음")
                return False
            threshold = float(block.get("threshold", DEFAULT_COMPARE_THRESHOLD))
            result = self._audio.compare(self._last_recording, golden, threshold)
            ok = bool(result.get("ok"))
            self._log(case=case_num, type="Audio", message=cmd, status="OK" if ok else f"실패: {result.get('reason', result.get('channels'))}")
            return ok

        if cmd == "saveAsGolden":
            golden = block.get("golden")
            if not golden or not self._last_recording:
                self._log(case=case_num, type="Audio", message=cmd, status="저장할 녹음 파일 또는 golden 이름 없음")
                return False
            result = self._audio.save_as_golden(self._last_recording, golden)
            ok = bool(result.get("ok"))
            self._log(case=case_num, type="Audio", message=cmd, status="OK" if ok else f"실패: {result.get('reason')}")
            return ok

        self._log(case=case_num, type="Audio", status=f"알 수 없는 Audio 명령: {cmd}")
        return True

    # ---- log replay -----------------------------------------------------------

    def _replay_log(self, block: dict, case_num: str) -> bool:
        logfile = block.get("logfile")
        cycle = max(1, int(block.get("Cycle", 1)))
        path = self._log_dir / logfile if logfile else None
        if not path or not path.exists():
            self._log(case=case_num, type="CANlogReplay", status=f"실패: {logfile} 파일 없음")
            return False

        exclude_nodes = block.get("excludeSenders") or []
        frame_ids: list[int] = []
        if exclude_nodes and self._dbc.loaded:
            for m in self._dbc.db.messages:
                if any(node in m.senders for node in exclude_nodes):
                    frame_ids.append(m.frame_id)
        mode = "stop" if frame_ids else "pass"

        try:
            self._replay.load(str(path), logfile)
        except Exception as exc:
            self._log(case=case_num, type="CANlogReplay", status=f"실패: {exc}")
            return False

        for _ in range(cycle):
            if self._stop_event.is_set():
                return True
            self._replay.start(mode, frame_ids)
            while self._replay.info()["progress"]["running"]:
                if self._stop_event.is_set():
                    self._replay.stop()
                    break
                time.sleep(0.05)
        self._log(case=case_num, type="CANlogReplay", status="완료")
        return True

    # ---- result file ------------------------------------------------------------

    def _save_result_file(self) -> None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = self._result_dir / f"scenario_result_{ts}.json"
        try:
            with self._lock:
                results = list(self._results)
            path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
