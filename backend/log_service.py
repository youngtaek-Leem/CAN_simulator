"""Record live CAN traffic to a Vector .blf log file.

Attaches an extra can.Listener to CanManager's notifier -- the same
mechanism test_runner_service's CANResp watcher uses (see
CanManager.add_listener) -- so every message already flowing through the RX
path (genuine bus RX, and our own TX looped back when the connection has
receive_own_messages=True) is also written to the log, without touching the
existing RX buffer/broadcast pipeline. Read back with replay_service.py (or
any BLF-aware tool, e.g. Vector CANoe).
"""

import threading
import time
from pathlib import Path
from typing import Optional

import can


class _CountingWriter(can.Listener):
    """Wraps a BLFWriter so status() can report how many frames were logged
    without reaching into python-can's writer internals."""

    def __init__(self, writer: "can.BLFWriter"):
        self._writer = writer
        self.count = 0

    def on_message_received(self, msg: can.Message) -> None:
        self._writer.on_message_received(msg)
        self.count += 1

    def stop(self) -> None:
        self._writer.stop()


class LogService:
    def __init__(self, can_manager, log_dir: Path):
        self._can = can_manager
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._listener: Optional[_CountingWriter] = None
        self._filename: Optional[str] = None
        self._started_at: Optional[float] = None
        # snapshot of the listener's count taken at stop() -- status() must
        # keep reporting it afterward (e.g. for a "recording stopped, N
        # frames" notification) rather than resetting to 0 once _listener
        # is cleared, until the next start() begins a new file.
        self._last_count = 0
        self._lock = threading.Lock()

    def start(self) -> dict:
        with self._lock:
            if self._listener is not None:
                raise RuntimeError("이미 로깅 중입니다")
            if not self._can.connected:
                raise RuntimeError("CAN bus is not connected")
            filename = f"canlog_{time.strftime('%Y%m%d_%H%M%S')}.blf"
            writer = can.BLFWriter(str(self._log_dir / filename))
            self._listener = _CountingWriter(writer)
            self._can.add_listener(self._listener)
            self._filename = filename
            self._started_at = time.monotonic()
            self._last_count = 0
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._listener is not None:
                self._can.remove_listener(self._listener)
                self._last_count = self._listener.count
                self._listener.stop()
                self._listener = None
        return self.status()

    def status(self) -> dict:
        with self._lock:
            count = self._listener.count if self._listener else self._last_count
            return {
                "recording": self._listener is not None,
                "filename": self._filename,
                "count": count,
                "duration_s": (
                    round(time.monotonic() - self._started_at, 1)
                    if (self._listener and self._started_at)
                    else 0.0
                ),
            }
