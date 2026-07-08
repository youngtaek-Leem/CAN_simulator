"""Periodic/event CAN TX scheduler.

A single daemon thread ticks with ~1 ms resolution and handles:
- the user-configured TX list (max 20 messages, start/stop controlled)
- auto-periodic messages created when a periodic DBC signal is written
- one-shot jobs, used for the "invalid value 30 ms after an event signal" rule
"""

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

EVENT_INVALID_DELAY_S = 0.030
MAX_TX_ENTRIES = 20
DEFAULT_AUTO_PERIOD_MS = 100.0


@dataclass
class TxEntry:
    key: str
    arbitration_id: int
    period_ms: float
    is_extended: bool = False
    enabled: bool = True
    data: Optional[bytes] = None          # fixed payload, or ...
    message_name: Optional[str] = None    # ... encode from DBC signal state
    is_fd: bool = False                   # raw-ID rows only; DBC rows use message.is_fd
    bitrate_switch: bool = False
    next_due: float = 0.0
    tx_count: int = 0


class TxScheduler:
    def __init__(self, can_manager, dbc_service):
        self._can = can_manager
        self._dbc = dbc_service
        self._entries: dict[str, TxEntry] = {}       # user TX list
        self._auto_entries: dict[str, TxEntry] = {}  # periodic signal senders
        self._oneshots: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._running = False        # user TX list start/stop
        self._paused = False         # global run/stop gate (pauses everything)
        self._shutdown = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---- user TX list ----------------------------------------------------

    def configure(self, entries: list[dict]) -> dict:
        if len(entries) > MAX_TX_ENTRIES:
            raise ValueError(f"at most {MAX_TX_ENTRIES} TX messages are allowed")
        new: dict[str, TxEntry] = {}
        for e in entries:
            key = str(e["key"])
            data = bytes.fromhex(e["data"]) if e.get("data") else None
            new[key] = TxEntry(
                key=key,
                arbitration_id=int(e["arbitration_id"]),
                period_ms=float(e["period_ms"]),
                is_extended=bool(e.get("is_extended", False)),
                enabled=bool(e.get("enabled", True)),
                data=data,
                message_name=e.get("message_name"),
                is_fd=bool(e.get("is_fd", False)),
                bitrate_switch=bool(e.get("bitrate_switch", False)),
            )
        with self._lock:
            self._entries = new
        return self.status()

    def start(self) -> dict:
        now = time.perf_counter()
        with self._lock:
            for entry in self._entries.values():
                entry.next_due = now
                entry.tx_count = 0
            self._running = True
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._running = False
        return self.status()

    def set_paused(self, paused: bool) -> None:
        """Global gate: pauses user TX list, auto entries and pending
        one-shots without discarding their configuration."""
        with self._lock:
            self._paused = paused
            if paused:
                self._oneshots.clear()

    # ---- signal-level sending (GUI components) ---------------------------

    def send_signal(self, message_name: str, values: dict[str, Any]) -> dict:
        """Send DBC signal values following the Event/Periodic rule."""
        message = self._dbc.get_message(message_name)
        data = self._dbc.encode_with_values(message_name, values)
        self._can.send(
            message.frame_id,
            data,
            message.is_extended_frame,
            is_fd=message.is_fd,
            bitrate_switch=message.is_fd,
        )

        result: dict[str, Any] = {"sent": True, "signals": {}}
        for signal_name in values:
            send_type = self._dbc.signal_send_type(message_name, signal_name)
            result["signals"][signal_name] = send_type
            if send_type == "event":
                self._schedule_invalid(message, signal_name)
            else:
                self._upsert_auto(message)
        return result

    def _schedule_invalid(self, message, signal_name: str) -> None:
        def send_invalid() -> None:
            data = self._dbc.encode_invalid(message.name, signal_name)
            self._can.send(
                message.frame_id,
                data,
                message.is_extended_frame,
                is_fd=message.is_fd,
                bitrate_switch=message.is_fd,
            )

        due = time.perf_counter() + EVENT_INVALID_DELAY_S
        with self._lock:
            heapq.heappush(self._oneshots, (due, next(self._seq), send_invalid))

    def _upsert_auto(self, message) -> None:
        period = float(message.cycle_time or DEFAULT_AUTO_PERIOD_MS)
        with self._lock:
            entry = self._auto_entries.get(message.name)
            if entry is None:
                entry = TxEntry(
                    key=f"auto:{message.name}",
                    arbitration_id=message.frame_id,
                    period_ms=period,
                    is_extended=message.is_extended_frame,
                    message_name=message.name,
                    is_fd=message.is_fd,
                    bitrate_switch=message.is_fd,
                    next_due=time.perf_counter() + period / 1000.0,
                )
                self._auto_entries[message.name] = entry
            else:
                entry.period_ms = period

    def stop_auto(self, message_name: Optional[str] = None) -> dict:
        with self._lock:
            if message_name is None:
                self._auto_entries.clear()
            else:
                self._auto_entries.pop(message_name, None)
        return self.status()

    # ---- scheduler loop ---------------------------------------------------

    def _due_entries(self, now: float) -> list[TxEntry]:
        due = []
        if self._running:
            due.extend(
                e for e in self._entries.values() if e.enabled and e.next_due <= now
            )
        due.extend(e for e in self._auto_entries.values() if e.next_due <= now)
        return due

    def _loop(self) -> None:
        while not self._shutdown:
            now = time.perf_counter()
            jobs: list[Callable[[], None]] = []
            with self._lock:
                paused = self._paused
                if not paused:
                    while self._oneshots and self._oneshots[0][0] <= now:
                        jobs.append(heapq.heappop(self._oneshots)[2])
                    for entry in self._due_entries(now):
                        jobs.append(self._make_send_job(entry))
                        # keep phase stable; skip cycles if we fell behind
                        period_s = entry.period_ms / 1000.0
                        entry.next_due += period_s
                        if entry.next_due <= now:
                            entry.next_due = now + period_s
            for job in jobs:
                try:
                    job()
                except Exception:
                    pass  # bus errors are counted by CanManager
            time.sleep(0.001)

    def _make_send_job(self, entry: TxEntry) -> Callable[[], None]:
        def send() -> None:
            if entry.message_name:
                data = self._dbc.encode_current(entry.message_name)
                message = self._dbc.get_message(entry.message_name)
                is_fd, brs = message.is_fd, message.is_fd
            else:
                data = entry.data or b""
                is_fd, brs = entry.is_fd, entry.bitrate_switch
            self._can.send(
                entry.arbitration_id, data, entry.is_extended, is_fd=is_fd, bitrate_switch=brs
            )
            entry.tx_count += 1

        return send

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "entries": [
                    {
                        "key": e.key,
                        "arbitration_id": e.arbitration_id,
                        "period_ms": e.period_ms,
                        "enabled": e.enabled,
                        "message_name": e.message_name,
                        "is_fd": e.is_fd,
                        "bitrate_switch": e.bitrate_switch,
                        "tx_count": e.tx_count,
                    }
                    for e in self._entries.values()
                ],
                "auto_entries": [
                    {
                        "message_name": e.message_name,
                        "period_ms": e.period_ms,
                        "tx_count": e.tx_count,
                    }
                    for e in self._auto_entries.values()
                ],
            }

    def shutdown(self) -> None:
        self._shutdown = True
