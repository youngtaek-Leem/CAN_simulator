"""CAN log replay (BLF / ASC).

Loads a log file into memory, then replays it on the connected bus with
original timestamp pacing. A message filter selects which frames are
replayed: "pass" replays only the selected frame ids, "stop" replays
everything except them; with no selection every frame is replayed.
"""

import threading
import time
from pathlib import Path
from typing import Iterable, Optional

import can

READERS = {".blf": can.BLFReader, ".asc": can.ASCReader}


class ReplayService:
    def __init__(self, can_manager):
        self._can = can_manager
        self._messages: list[can.Message] = []
        self._filename: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._progress = {"sent": 0, "skipped": 0, "total": 0, "running": False}

    def load(self, path: str, original_name: Optional[str] = None) -> dict:
        suffix = Path(original_name or path).suffix.lower()
        reader_cls = READERS.get(suffix)
        if reader_cls is None:
            raise ValueError(f"unsupported log format: {suffix} (use .blf or .asc)")
        self.stop()
        with reader_cls(path) as reader:
            messages = [m for m in reader if not m.is_error_frame]
        with self._lock:
            self._messages = messages
            self._filename = original_name or Path(path).name
            self._progress = {
                "sent": 0,
                "skipped": 0,
                "total": len(messages),
                "running": False,
            }
        return self.info()

    def info(self) -> dict:
        with self._lock:
            msgs = self._messages
            duration = (msgs[-1].timestamp - msgs[0].timestamp) if len(msgs) > 1 else 0.0
            tx_count = sum(1 for m in msgs if not m.is_rx)
            return {
                "loaded": bool(msgs),
                "filename": self._filename,
                "message_count": len(msgs),
                "tx_count": tx_count,
                "rx_count": len(msgs) - tx_count,
                "duration_s": round(duration, 3),
                "progress": dict(self._progress),
            }

    def start(self, mode: str = "pass", frame_ids: Optional[Iterable[int]] = None) -> dict:
        """Start replay with a message filter.

        mode "pass": replay only the frames whose id is in `frame_ids`.
        mode "stop": replay everything except the frames in `frame_ids`.
        Empty/None `frame_ids`: no filtering, replay everything.
        """
        if mode not in ("pass", "stop"):
            raise ValueError("mode must be 'pass' or 'stop'")
        if not self._messages:
            raise RuntimeError("no log file loaded")
        if self._thread and self._thread.is_alive():
            raise RuntimeError("replay already running")
        ids = frozenset(frame_ids or ())
        self._stop_event.clear()
        with self._lock:
            self._progress.update({"sent": 0, "skipped": 0, "running": True})
        self._thread = threading.Thread(
            target=self._run, args=(mode, ids), daemon=True
        )
        self._thread.start()
        return self.info()

    def stop(self) -> dict:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._progress["running"] = False
        return self.info()

    def _run(self, mode: str, frame_ids: frozenset[int]) -> None:
        messages = self._messages
        t0 = messages[0].timestamp
        wall0 = time.perf_counter()
        for msg in messages:
            if self._stop_event.is_set():
                break
            target = wall0 + (msg.timestamp - t0)
            delay = target - time.perf_counter()
            if delay > 0:
                if self._stop_event.wait(timeout=delay):
                    break
            if frame_ids and (
                (mode == "pass" and msg.arbitration_id not in frame_ids)
                or (mode == "stop" and msg.arbitration_id in frame_ids)
            ):
                with self._lock:
                    self._progress["skipped"] += 1
                continue
            try:
                self._can.send(
                    msg.arbitration_id,
                    bytes(msg.data),
                    msg.is_extended_id,
                    is_fd=msg.is_fd,
                    bitrate_switch=msg.bitrate_switch,
                )
                with self._lock:
                    self._progress["sent"] += 1
            except Exception:
                break
        with self._lock:
            self._progress["running"] = False
