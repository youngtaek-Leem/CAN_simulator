"""Periodic/event CAN TX scheduler.

A single daemon thread ticks with ~1 ms resolution and handles:
- the user-configured TX list (max 20 messages, start/stop controlled)
- auto-periodic messages created when a periodic DBC signal is written
- one-shot jobs, used for the "invalid value 30 ms after an event signal" rule
- optional per-signal value generators (Random/Range "Random 버튼" widget):
  registered generators are called to produce a fresh raw value right before
  every periodic auto-resend tick, so a periodic signal keeps changing value
  on its own without further user interaction; event signals have no
  auto-resend at all, so their value only changes on an explicit
  send_generated() call (one per button click)
"""

import heapq
import itertools
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

EVENT_INVALID_DELAY_S = 0.030
MAX_TX_ENTRIES = 20
DEFAULT_AUTO_PERIOD_MS = 100.0


def _signal_raw_bounds(signal) -> tuple[int, int]:
    """Raw (unscaled) integer bounds representable in the signal's bit width."""
    if signal.is_signed:
        return -(2 ** (signal.length - 1)), 2 ** (signal.length - 1) - 1
    return 0, (2 ** signal.length) - 1


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
        # message_name -> signal_name -> generator producing a raw int value
        self._value_generators: dict[str, dict[str, Callable[[], int]]] = {}
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

    def _send_frame(self, message, data: bytes) -> None:
        self._can.send(
            message.frame_id,
            data,
            message.is_extended_frame,
            is_fd=message.is_fd,
            bitrate_switch=message.is_fd,
        )

    def _dispatch_send_type(self, message, signal_name: str) -> str:
        send_type = self._dbc.signal_send_type(message.name, signal_name)
        if send_type == "event":
            self._schedule_invalid(message, signal_name)
        else:
            self._upsert_auto(message)
        return send_type

    def send_signal(self, message_name: str, values: dict[str, Any]) -> dict:
        """Send DBC signal values following the Event/Periodic rule."""
        message = self._dbc.get_message(message_name)
        data = self._dbc.encode_with_values(message_name, values)
        self._send_frame(message, data)

        result: dict[str, Any] = {"sent": True, "signals": {}}
        for signal_name in values:
            result["signals"][signal_name] = self._dispatch_send_type(message, signal_name)
        return result

    # ---- Random/Range value generators ("Random 버튼" widget) -------------

    def set_value_generator(
        self,
        message_name: str,
        signal_name: str,
        mode: str,
        range_min: Optional[int] = None,
        range_max: Optional[int] = None,
        step: int = 1,
    ) -> None:
        """Register (or clear, with mode="fixed") a raw-value generator for a
        signal. "random": every call returns a fresh random raw value, within
        [range_min, range_max] if given, else across the signal's full bit
        range (the default). "range": a stateful generator that starts at
        range_min and advances by `step` on every call, wrapping back to
        range_min once it passes range_max -- both bounds are clamped into
        the signal's bit-representable range."""
        if mode == "fixed":
            with self._lock:
                self._value_generators.get(message_name, {}).pop(signal_name, None)
            return

        message = self._dbc.get_message(message_name)
        signal = next(s for s in message.signals if s.name == signal_name)
        raw_min, raw_max = _signal_raw_bounds(signal)

        if mode == "random":
            lo = raw_min if range_min is None else max(raw_min, min(raw_max, int(range_min)))
            hi = raw_max if range_max is None else max(raw_min, min(raw_max, int(range_max)))
            if lo > hi:
                lo, hi = hi, lo

            def generator() -> int:
                return random.randint(lo, hi)
        elif mode == "range":
            lo = raw_min if range_min is None else max(raw_min, min(raw_max, int(range_min)))
            hi = raw_max if range_max is None else max(raw_min, min(raw_max, int(range_max)))
            if lo > hi:
                lo, hi = hi, lo
            step_size = max(1, int(step))
            state = {"value": lo}

            def generator() -> int:
                current = state["value"]
                nxt = current + step_size
                state["value"] = lo if nxt > hi else nxt
                return current
        else:
            raise ValueError(f"unknown generator mode: {mode}")

        with self._lock:
            self._value_generators.setdefault(message_name, {})[signal_name] = generator

    def send_generated(self, message_name: str, signal_name: str) -> dict:
        """One-shot trigger for a registered generator -- the "Random 버튼"
        widget's click handler. Computes one fresh raw value, applies it, and
        sends immediately, following the same Event/Periodic rule as
        send_signal (event: schedules the 30ms-later invalid follow-up;
        periodic: arms the auto-resend entry, which then keeps calling this
        same generator on every subsequent tick via _make_send_job)."""
        message = self._dbc.get_message(message_name)
        generator = self._value_generators.get(message_name, {}).get(signal_name)
        if generator is None:
            raise ValueError(f"no value generator registered for {message_name}.{signal_name}")
        raw_value = generator()
        self._dbc.set_raw_signal_value(message_name, signal_name, raw_value)
        data = self._dbc.encode_current(message_name)
        self._send_frame(message, data)
        return {
            "sent": True,
            "raw_value": raw_value,
            "send_type": self._dispatch_send_type(message, signal_name),
        }

    def send_invalid(self, message_name: str, signal_name: str) -> dict:
        """Force a signal's raw state to its invalid value (bit-max) and send
        immediately. Unlike encode_invalid()/_schedule_invalid() (the
        one-shot 30ms-later follow-up used for Event signals, which
        deliberately does NOT persist), this PERSISTS the invalid value into
        signal_state -- so a Periodic signal's auto-resend keeps sending
        invalid on every subsequent tick until something else overwrites it.
        Used by the "버튼/Random 버튼 valid<->invalid 토글" widget behavior.
        Clears any registered value generator for this signal first, so a
        Random/Range generator can't immediately overwrite the invalid value
        on the very next tick."""
        message = self._dbc.get_message(message_name)
        signal = next(s for s in message.signals if s.name == signal_name)
        with self._lock:
            self._value_generators.get(message_name, {}).pop(signal_name, None)
        invalid_raw = (1 << signal.length) - 1
        self._dbc.set_raw_signal_value(message_name, signal_name, invalid_raw)
        data = self._dbc.encode_current(message_name)
        self._send_frame(message, data)
        return {
            "sent": True,
            "raw_value": invalid_raw,
            "send_type": self._dispatch_send_type(message, signal_name),
        }

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

    def enable_all_periodic(self, rx_node: str = "") -> dict:
        """"Enable Msg" button: arm auto-periodic resend for every
        Periodic-tagged message in the loaded DBC. Each is sent once
        immediately with its current signal state (all-zero/default unless a
        widget has already touched it), then keeps resending at its own
        cycle time via the normal auto-entry mechanism -- a later widget
        send for the same message just updates the persisted state that the
        auto-resend already reads from (see _upsert_auto / encode_current).

        rx_node, when given, excludes messages sent by that DBC node -- the
        real DUT on the bus, whose own periodic messages must not be
        duplicated by the simulator (mirrors the frontend's TX/RX message
        grouping in appContext.ts's groupedMessages).

        A message that fails its initial send (e.g. an FD message while
        connected to a classic-CAN bus) is reported in "failed" and left
        unarmed, rather than aborting the whole batch or auto-resending a
        frame that can never actually go out."""
        if not self._dbc.loaded:
            raise RuntimeError("no DBC loaded")
        armed = []
        failed = []
        for message in self._dbc.db.messages:
            if rx_node and rx_node in message.senders:
                continue
            if self._dbc.message_send_type(message.name) != "periodic":
                continue
            try:
                data = self._dbc.encode_current(message.name)
                self._send_frame(message, data)
            except Exception as exc:
                failed.append({"message_name": message.name, "reason": str(exc)})
                continue
            self._upsert_auto(message)
            armed.append(message.name)
        return {"armed": armed, "failed": failed, **self.status()}

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
                generators = self._value_generators.get(entry.message_name)
                if generators:
                    for signal_name, gen in generators.items():
                        self._dbc.set_raw_signal_value(entry.message_name, signal_name, gen())
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
