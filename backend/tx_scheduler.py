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
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import diag_log

EVENT_INVALID_DELAY_S = 0.030
MAX_TX_ENTRIES = 20
DEFAULT_AUTO_PERIOD_MS = 100.0
MAX_CLASSIC_DATA_LEN = 8
MAX_FD_DATA_LEN = 64
# CAN-FD에서 실제로 전송 가능한 DLC 길이 (ISO 11898-1)
FD_VALID_DATA_LENS = frozenset((0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64))
FD_VALID_LIST_TEXT = "0~8, 12, 16, 20, 24, 32, 48, 64"


def _validate_raw_payload(data: Optional[bytes], is_fd: bool) -> None:
    """raw 페이로드 길이 검증 -- 어긋나면 한글 ValueError.

    FD 미체크 시 초과분은 자동 절삭하지 않고 에러로 알려 수정을 유도한다."""
    if data is None:
        return
    n = len(data)
    if not is_fd and n > MAX_CLASSIC_DATA_LEN:
        raise ValueError(
            f"{n}바이트 페이로드는 FD를 체크해야 전송할 수 있습니다 "
            f"(classic CAN 최대 {MAX_CLASSIC_DATA_LEN}바이트)"
        )
    if is_fd:
        if n > MAX_FD_DATA_LEN:
            raise ValueError(f"{n}바이트는 CAN-FD 최대 {MAX_FD_DATA_LEN}바이트를 초과합니다")
        if n not in FD_VALID_DATA_LENS:
            raise ValueError(
                f"CAN-FD에서는 {n}바이트 길이를 사용할 수 없습니다 "
                f"(가능한 길이: {FD_VALID_LIST_TEXT})"
            )

# Diagnostic logging for the Windows "오디오 위젯 사용 중 CAN periodic 전송이 매우
# 느려진다" 조사 (Requirement.md의 "CAN periodic 신호 영향 점검" 항목에서 이미 GIL
# 경합 메커니즘 자체는 실측 확인됨 -- 여기서는 실사용 중 실제로 얼마나 밀리는지
# 드러내기 위한 로그). "cansim." 네임스페이스 + main.py의 전용 핸들러로, 임계값을
# 넘을 때만 경고해 평상시엔 조용하다. diag_log로 반복 발생 시 폭주도 막는다(같은
# 조건이 계속 참이면 -- 예: 오디오 위젯이 켜진 동안 매 틱마다 지연 -- 로그가 끝없이
# 쏟아지는 것을 실사용에서 실제로 겪음).
logger = logging.getLogger("cansim.tx_scheduler")
_SLOW_TICK_MS = 10.0  # loop targets ~1ms; this much drift is a real stall
_SLOW_LOCK_WAIT_MS = 5.0
_SLOW_JOB_MS = 5.0  # a single message send() taking this long is unusual for virtual/most hardware


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
    periodic: bool = True                 # False -> never auto-resent by the
                                           # scheduler loop; only sent via
                                           # send_once() (the row's Send
                                           # button, or a live data-field edit)
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
        # message names armed by the last enable_all_periodic() call -- lets
        # the "Enable Msg" bulk toggle track its own on/off state separately
        # from _auto_entries as a whole, since individual widgets arm their
        # own entries in _auto_entries too just by sending a periodic signal
        # (existing, intentional behavior) and that must not make "Enable
        # Msg" look pressed, nor make turning "Enable Msg" off stop auto
        # sends a widget itself started.
        self._enable_msg_armed: set[str] = set()
        self._oneshots: list[tuple[float, int, Callable[[], None]]] = []
        # message_name -> signal_name -> generator producing a raw int value
        self._value_generators: dict[str, dict[str, Callable[[], int]]] = {}
        # TxBox signal-value toggle ("토글"): message_name -> {"a": {...},
        # "b": {...}} scaled value sets plus a flip phase. Each transmission
        # of the message (one-shot or periodic tick) sends the current phase
        # set for the toggled signals, then flips -- so the two values
        # alternate. Only TxBox rows with per-signal toggle enabled populate
        # this; every other caller behaves exactly as before.
        self._toggle_values: dict[str, dict[str, dict[str, Any]]] = {}
        self._toggle_phase: dict[str, bool] = {}
        # Row keys with a TxBox periodic entry in _auto_entries (row-keyed,
        # unlike message-keyed widget auto entries). Ticks for these rows
        # follow one-shot event semantics (30ms-invalid follow-up per valid
        # Event signal); other auto entries are untouched.
        self._row_entry_keys: set[str] = set()
        # Signals with a RUNNING Random/Range transmission. Registration
        # alone (widget mount / config save) must NOT randomize a signal:
        # the periodic auto-resend tick only invokes generators in this
        # set, so a merely-registered sibling signal in the same message
        # keeps its last-valid/initial/0x0 value instead of also turning
        # random. Marked by send_generated()/start_event_periodic(), cleared
        # by the matching stops, mode="fixed" and stop_auto().
        self._random_active: set[tuple[str, str]] = set()
        # (message_name, signal_name) -> {"period_ms", "next_due"} for
        # Event-signal periodic Random sends (Random button widgets): each
        # tick generates a fresh value, sends it, and follows the Event rule
        # (invalid value 30ms later via _schedule_invalid)
        self._event_periodic: dict[tuple[str, str], dict[str, float]] = {}
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
            try:
                data = bytes.fromhex(e["data"]) if e.get("data") else None
            except ValueError:
                raise ValueError(f"잘못된 hex 데이터입니다 (key={key})")
            if not e.get("message_name"):
                _validate_raw_payload(data, bool(e.get("is_fd", False)))
            new[key] = TxEntry(
                key=key,
                arbitration_id=int(e["arbitration_id"]),
                period_ms=float(e["period_ms"]),
                is_extended=bool(e.get("is_extended", False)),
                enabled=bool(e.get("enabled", True)),
                periodic=bool(e.get("periodic", True)),
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

    def send_once(
        self,
        arbitration_id: int,
        data: bytes,
        is_extended: bool = False,
        is_fd: bool = False,
        bitrate_switch: bool = False,
        key: Optional[str] = None,
    ) -> dict:
        """Immediate one-shot send of a raw ID/data row -- the TX box's Send
        button, and live edits to a row's data field while the list is
        running (so the bus reflects a typed value right away instead of
        waiting for the next periodic tick or a re-apply).

        If `key` matches a row already in the configured TX list, that row's
        stored data is updated too, so any later periodic auto-resend for it
        keeps using the fresh value instead of reverting to what was last
        applied via configure()."""
        _validate_raw_payload(data, is_fd)
        self._can.send(arbitration_id, data, is_extended, is_fd=is_fd, bitrate_switch=bitrate_switch)
        with self._lock:
            entry = self._entries.get(key) if key else None
            if entry is not None:
                entry.data = data
                entry.tx_count += 1
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

    def _store_toggle(self, message_name: str, values: dict[str, Any],
                        values_alt: Optional[dict[str, Any]],
                        reset_phase: bool = False) -> None:
        """(Re)configure the toggle sets for a message. values_alt=None
        clears a previously stored toggle (TxBox apply() always reflects the
        current UI toggle state). reset_phase=True restarts the alternation
        at the A set (used by preset = fresh configuration)."""
        with self._lock:
            if values_alt is None:
                self._toggle_values.pop(message_name, None)
                self._toggle_phase.pop(message_name, None)
            elif values_alt:
                self._toggle_values[message_name] = {
                    "a": dict(values),
                    "b": dict(values_alt),
                }
                if reset_phase:
                    self._toggle_phase[message_name] = False

    def _take_toggle_explicit(self, message_name: str) -> dict[str, Any]:
        """Current-phase toggle values for a message transmission, flipping
        the phase. Empty when no toggle is configured."""
        with self._lock:
            stored = self._toggle_values.get(message_name)
            if not stored:
                return {}
            phase = self._toggle_phase.get(message_name, False)
            self._toggle_phase[message_name] = not phase
            current = stored["b"] if phase else stored["a"]
            return dict(current)

    @staticmethod
    def _scaled_to_raw(message, signal_name: str, phys: Any) -> int:
        sig = next(s for s in message.signals if s.name == signal_name)
        scale = float(sig.scale)
        if scale == 0:
            return int(phys)
        return int(round((float(phys) - float(sig.offset)) / scale))

    def send_signal(self, message_name: str, values: dict[str, Any],
                    values_alt: Optional[dict[str, Any]] = None,
                    once: bool = False) -> dict:
        """Send DBC signal values following the Event/Periodic rule.

        values_alt (optional): TxBox toggle second set. Stored (with values
        as the first set) and this transmission uses the current phase set,
        then flips -- so repeated sends alternate A/B/A/... Omitted entirely
        (None) leaves any previously stored toggle untouched, so one-shot
        sends from other widgets never disturb a TxBox toggle.

        once=True: exactly one frame (plus the Event 30ms-invalid follow-up
        for Event signals) -- no periodic auto-entry is armed, for Periodic
        and Event alike. Used by the TxBox Send button."""
        message = self._dbc.get_message(message_name)
        if values_alt is not None:
            self._store_toggle(message_name, values, values_alt)
            toggled = self._take_toggle_explicit(message_name)
            send_values = {**values, **toggled}
        else:
            send_values = values
        data = self._dbc.encode_with_values(message_name, send_values)
        self._send_frame(message, data)

        result: dict[str, Any] = {"sent": True, "signals": {}}
        # At most ONE invalid follow-up per transmission: encode_invalid()
        # produces the identical whole-frame-invalid bytes regardless of
        # which signal triggers it, so per-signal scheduling only emitted
        # duplicates (1 valid + N invalid for an N-signal Event message).
        followup_scheduled = False
        for signal_name in send_values:
            send_type = self._dbc.signal_send_type(message.name, signal_name)
            if send_type == "event":
                if not followup_scheduled:
                    self._schedule_invalid(message, signal_name)
                    followup_scheduled = True
            elif not once:
                self._upsert_auto(message)
            result["signals"][signal_name] = send_type
        if values_alt:
            result["toggled"] = True
        return result

    def send_signal_invalid_first(self, message_name: str, values: dict[str, Any]) -> dict:
        """Inverted one-shot pulse for Periodic signals (button/input widgets):
        send an all-invalid frame immediately, then the configured (valid)
        values EVENT_INVALID_DELAY_S later -- the mirror image of the Event
        rule (valid now + invalid 30ms later). Event signals are rejected:
        they keep the existing send_signal path untouched.

        The valid values are persisted into signal state right away (via
        encode_with_values) so the periodic auto-resend armed below keeps
        transmitting them on every subsequent tick. The delayed valid frame
        is pre-encoded now (not re-encoded at fire time) so rapid repeated
        clicks each replay their own values in order."""
        message = self._dbc.get_message(message_name)
        if not values:
            raise ValueError("전송할 신호 값이 없습니다")
        for signal_name in values:
            if self._dbc.signal_send_type(message_name, signal_name) != "periodic":
                raise ValueError(
                    f"{message_name}.{signal_name}는 Event 신호입니다 "
                    "(invalid-first 펄스는 Periodic 신호 전용입니다)"
                )
        invalid_data = self._dbc.encode_invalid(message_name, next(iter(values)))
        self._send_frame(message, invalid_data)
        valid_data = self._dbc.encode_with_values(message_name, values)
        self._upsert_auto(message)

        def send_valid() -> None:
            self._can.send(
                message.frame_id,
                valid_data,
                message.is_extended_frame,
                is_fd=message.is_fd,
                bitrate_switch=message.is_fd,
            )

        due = time.perf_counter() + EVENT_INVALID_DELAY_S
        with self._lock:
            heapq.heappush(self._oneshots, (due, next(self._seq), send_valid))

        result: dict[str, Any] = {"sent": True, "mode": "invalid_first", "signals": {}}
        for signal_name in values:
            result["signals"][signal_name] = "periodic"
        return result

    def send_signal_zero_after(self, message_name: str, values: dict[str, Any]) -> dict:
        """One-shot pulse for Periodic signals (button widgets): send the
        configured (valid) values immediately, then raw 0x0 for those
        signals EVENT_INVALID_DELAY_S later -- the mirror image of
        send_signal_invalid_first. Event signals are rejected: they keep
        the existing send_signal path untouched.

        The valid values are persisted into signal state right away (via
        encode_with_values) so the periodic auto-resend armed below keeps
        transmitting them until the delayed zero frame goes out; the zero
        values are then persisted too (same call pre-encodes the zero frame
        with the transmit priority applied to the other signals), so the
        auto-resend keeps transmitting static 0x0 on every subsequent tick.
        The delayed zero frame is pre-encoded now (not re-encoded at fire
        time) so rapid repeated clicks each replay their own values in
        order."""
        message = self._dbc.get_message(message_name)
        if not values:
            raise ValueError("전송할 신호 값이 없습니다")
        for signal_name in values:
            if self._dbc.signal_send_type(message_name, signal_name) != "periodic":
                raise ValueError(
                    f"{message_name}.{signal_name}는 Event 신호입니다 "
                    "(zero-after 펄스는 Periodic 신호 전용입니다)"
                )
        valid_data = self._dbc.encode_with_values(message_name, values)
        self._send_frame(message, valid_data)
        self._upsert_auto(message)
        # Raw 0 (not physical 0 -- a signal with an offset would otherwise
        # land on a nonzero raw pattern), mirroring stop_generated().
        zero_data = self._dbc.encode_with_raw_values(
            message_name, {name: 0 for name in values}
        )

        def send_zero() -> None:
            self._can.send(
                message.frame_id,
                zero_data,
                message.is_extended_frame,
                is_fd=message.is_fd,
                bitrate_switch=message.is_fd,
            )

        due = time.perf_counter() + EVENT_INVALID_DELAY_S
        with self._lock:
            heapq.heappush(self._oneshots, (due, next(self._seq), send_zero))

        result: dict[str, Any] = {"sent": True, "mode": "zero_after", "signals": {}}
        for signal_name in values:
            result["signals"][signal_name] = "periodic"
        return result

    def preset_signal(self, message_name: str, values: dict[str, Any],
                      values_alt: Optional[dict[str, Any]] = None) -> dict:
        """Seed DBC signal state WITHOUT transmitting -- the TX box's signal
        editor stores per-row values here on apply, so the scheduler's later
        periodic resend (Start) and one-shot send_signal (Send) pick them up.
        Unlike send_signal this arms nothing: no immediate frame, no
        auto-entry, no 30ms-invalid follow-up. values_alt stores the TxBox
        toggle B set (alternation restarts at A); None clears a previously
        stored toggle."""
        self._dbc.encode_with_values(message_name, values)
        # Always reconciles the toggle store (None clears): apply() reflects
        # the current UI toggle state, so a removed toggle must not linger.
        self._store_toggle(message_name, values, values_alt, reset_phase=True)
        return {"preset": True, "message_name": message_name, "signals": sorted(values)}

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
                self._random_active.discard((message_name, signal_name))
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
        data = self._dbc.encode_with_raw_values(message_name, {signal_name: raw_value})
        self._send_frame(message, data)
        with self._lock:
            self._random_active.add((message_name, signal_name))
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
            self._random_active.discard((message_name, signal_name))
        self._dbc.set_raw_invalid(message_name, signal_name)
        data = self._dbc.encode_current(message_name)
        self._send_frame(message, data)
        invalid_raw = (1 << signal.length) - 1
        return {
            "sent": True,
            "raw_value": invalid_raw,
            "send_type": self._dispatch_send_type(message, signal_name),
        }

    # ---- Event-signal periodic Random sends ("Random 버튼" widget) ----

    def start_event_periodic(self, message_name: str, signal_name: str, period_ms: float) -> dict:
        """Start (or re-arm) periodic Random sends for an Event signal: every
        `period_ms` a fresh value comes from the registered generator and is
        sent immediately, followed by the Event-rule invalid value 30ms
        later. First tick fires on the next scheduler loop pass (~1ms).
        Periodic signals are rejected (they have their own generate<->invalid
        toggle path); the value generator must already be registered via
        set_value_generator (the widget registers it on mount)."""
        message = self._dbc.get_message(message_name)
        if self._dbc.signal_send_type(message_name, signal_name) != "event":
            raise ValueError(
                f"{message_name}.{signal_name}는 Periodic 신호입니다 "
                "(주기 Random 송신은 Event 신호 전용입니다)"
            )
        period = float(period_ms)
        if not 10.0 <= period <= 60000.0:
            raise ValueError(f"주기 {period_ms}ms는 10~60000 ms 범위여야 합니다")
        with self._lock:
            if self._value_generators.get(message_name, {}).get(signal_name) is None:
                raise ValueError(
                    f"{message_name}.{signal_name}에 값 생성기가 등록되지 않았습니다"
                )
            self._event_periodic[(message_name, signal_name)] = {
                "period_ms": period,
                "next_due": time.perf_counter(),
            }
            self._random_active.add((message_name, signal_name))
        return {
            "started": True,
            "message_name": message.name,
            "signal_name": signal_name,
            "period_ms": period,
        }

    def stop_event_periodic(
        self, message_name: str, signal_name: str, send_final_invalid: bool = False
    ) -> dict:
        """Stop periodic Random sends (idempotent). A 30ms-invalid follow-up
        already scheduled by the last tick still goes out -- same as the
        Event rule everywhere else.

        With send_final_invalid=True (Random button's 2nd click), one
        all-invalid frame is sent immediately, but only when the signal was
        actually running -- a stray stop for a non-running signal stays
        silent. The value generator is deliberately NOT cleared (unlike
        send_invalid), so the next start_event_periodic() works without
        re-registering."""
        with self._lock:
            was_running = self._event_periodic.pop((message_name, signal_name), None) is not None
            self._random_active.discard((message_name, signal_name))
        sent_final = False
        if was_running and send_final_invalid:
            message = self._dbc.get_message(message_name)
            data = self._dbc.encode_invalid(message_name, signal_name)
            self._send_frame(message, data)
            sent_final = True
        return {
            "stopped": True,
            "message_name": message_name,
            "signal_name": signal_name,
            "final_invalid_sent": sent_final,
        }

    def stop_generated(self, message_name: str, signal_name: str) -> dict:
        """Stop a Periodic signal's Random/Range transmission (Random button's
        2nd click): clear the value generator and send a final raw-0 frame
        once. The 0 is persisted into signal state (mirroring send_invalid's
        persist behavior) so the message's periodic auto-resend -- which is
        deliberately kept -- keeps transmitting static 0x0 afterwards. Other
        widgets' auto-resends for the same message are unaffected."""
        message = self._dbc.get_message(message_name)
        with self._lock:
            self._value_generators.get(message_name, {}).pop(signal_name, None)
            self._random_active.discard((message_name, signal_name))
        data = self._dbc.encode_with_raw_values(message_name, {signal_name: 0})
        self._send_frame(message, data)
        self._upsert_auto(message)
        return {"stopped": True, "raw_value": 0}

    def _make_event_periodic_job(self, message_name: str, signal_name: str) -> Callable[[], None]:
        def send() -> None:
            with self._lock:
                gen = self._value_generators.get(message_name, {}).get(signal_name)
            if gen is None:
                return  # generator vanished mid-run -- skip this tick
            raw_value = gen()
            message = self._dbc.get_message(message_name)
            data = self._dbc.encode_with_raw_values(message_name, {signal_name: raw_value})
            self._send_frame(message, data)
            self._schedule_invalid(message, signal_name)

        return send

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

    def row_periodic_start(self, key: str, message_name: Optional[str] = None,
                             values: Optional[dict[str, Any]] = None,
                             values_alt: Optional[dict[str, Any]] = None,
                             period_ms: float = 100.0,
                             data_hex: Optional[str] = None,
                             arbitration_id: Optional[int] = None,
                             is_extended: bool = False,
                             is_fd: bool = False,
                             bitrate_switch: bool = False) -> dict:
        """Start per-row periodic transmission (TxBox Send toggle on).
        DBC rows persist the values (toggle alternation restarts at A) and
        arm a row-keyed auto entry at the row's own period; raw rows arm the
        same with a fixed payload. One frame goes out immediately. Other
        rows and other widgets' auto entries are unaffected -- several rows
        may even transmit the same message at different periods."""
        period = max(1.0, float(period_ms))
        if message_name:
            message = self._dbc.get_message(message_name)
            if not values:
                raise ValueError("전송할 신호 값이 없습니다")
            self._dbc.encode_with_values(message_name, values)
            # Always reconciles the toggle store (None clears): a row (re)start
            # reflects the current UI toggle state, so a toggle removed while
            # stopped must not resurrect stale alternation (preset_signal and
            # row_periodic_update share this semantic).
            self._store_toggle(message_name, values, values_alt, reset_phase=True)
            # Immediate frame uses the current toggle phase (A first), like a
            # one-shot send, so the sequence starts A,B,A,... with no repeat
            # -- including its single Event invalid follow-up when applicable.
            toggled = self._take_toggle_explicit(message_name)
            send_values = {**values, **toggled}
            data = self._dbc.encode_with_values(message_name, send_values)
            self._send_frame(message, data)
            try:
                sent_raw = self._dbc.decode_raw(message.frame_id, bytes(data))
            except Exception:
                sent_raw = None
            if sent_raw:
                for s in message.signals:
                    if (
                        s.name in send_values
                        and self._dbc.signal_send_type(message.name, s.name) == "event"
                        and sent_raw.get(s.name) != (1 << s.length) - 1
                    ):
                        self._schedule_invalid(message, s.name)
                        break
            entry = TxEntry(
                key=key,
                arbitration_id=message.frame_id,
                period_ms=period,
                is_extended=message.is_extended_frame,
                message_name=message.name,
                is_fd=message.is_fd,
                bitrate_switch=message.is_fd,
            )
        else:
            if data_hex is None or arbitration_id is None:
                raise ValueError("raw 행은 data_hex와 arbitration_id가 필요합니다")
            try:
                raw = bytes.fromhex(str(data_hex).replace(" ", ""))
            except ValueError:
                raise ValueError("잘못된 hex 데이터입니다")
            _validate_raw_payload(raw, is_fd)
            self._can.send(int(arbitration_id), raw, is_extended,
                           is_fd=is_fd, bitrate_switch=bitrate_switch)
            entry = TxEntry(
                key=key,
                arbitration_id=int(arbitration_id),
                period_ms=period,
                is_extended=is_extended,
                data=raw,
                is_fd=is_fd,
                bitrate_switch=bitrate_switch,
            )
        with self._lock:
            entry.next_due = time.perf_counter() + period / 1000.0
            self._auto_entries[key] = entry
            self._row_entry_keys.add(key)
        return {"started": True, "key": key, "period_ms": period}

    def row_periodic_stop(self, key: str) -> dict:
        """Stop one row-keyed periodic entry (TxBox Send toggle off). Only
        that row stops -- nothing else is touched."""
        with self._lock:
            removed = self._auto_entries.pop(key, None)
            self._row_entry_keys.discard(key)
        return {"stopped": True, "key": key, "found": removed is not None}

    def row_periodic_update(self, key: str, message_name: Optional[str] = None,
                             values: Optional[dict[str, Any]] = None,
                             values_alt: Optional[dict[str, Any]] = None,
                             period_ms: Optional[float] = None) -> dict:
        """Update a transmitting row-keyed entry in place (TxBox signal /
        toggle / period edit while Send toggle is on). No immediate frame,
        tx_count and schedule are preserved. DBC rows reconcile the toggle
        store (values_alt=None clears a removed toggle, like preset_signal)
        and restart alternation at the A set. Unknown/stopped key returns
        found=False so the caller can ignore it."""
        with self._lock:
            if key not in self._row_entry_keys or key not in self._auto_entries:
                return {"updated": False, "key": key, "found": False}
        # NOTE: _store_toggle() takes self._lock itself (plain Lock, not
        # reentrant), so all DBC/toggle work happens outside the lock --
        # same split as row_periodic_start().
        message = None
        if message_name:
            message = self._dbc.get_message(message_name)
            if not values:
                raise ValueError("전송할 신호 값이 없습니다")
            self._dbc.encode_with_values(message_name, values)
            # Always reconciles (None clears): turning the TxBox toggle
            # off mid-transmission must stop the alternation at once.
            self._store_toggle(message_name, values, values_alt, reset_phase=True)
        with self._lock:
            entry = self._auto_entries.get(key)
            if entry is None or key not in self._row_entry_keys:
                stale = message_name is not None
            else:
                stale = False
                if message is not None:
                    # The row may have been switched to another DBC message
                    # mid-transmission -- retarget the entry so ticks follow it.
                    if message_name != entry.message_name:
                        entry.message_name = message.name
                        entry.arbitration_id = message.frame_id
                        entry.is_extended = message.is_extended_frame
                        entry.is_fd = message.is_fd
                        entry.bitrate_switch = message.is_fd
                        entry.data = None
                if period_ms is not None:
                    entry.period_ms = max(1.0, float(period_ms))
                period = entry.period_ms
        if stale:
            # Stopped concurrently after the toggle store above -- roll it
            # back (outside the lock) so no stale alternation lingers.
            self._store_toggle(message_name, values or {}, None)
            return {"updated": False, "key": key, "found": False}
        return {"updated": True, "key": key, "found": True, "period_ms": period}

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
        with self._lock:
            self._enable_msg_armed.update(armed)
        return {"armed": armed, "failed": failed, **self.status()}

    def disable_all_periodic(self) -> dict:
        """"Enable Msg" button pressed again to turn it off -- stops only the
        messages it armed (self._enable_msg_armed), leaving any auto-entry a
        widget armed independently (by sending a periodic signal) alone."""
        with self._lock:
            for name in self._enable_msg_armed:
                self._auto_entries.pop(name, None)
            self._enable_msg_armed.clear()
        return self.status()

    def stop_auto(self, message_name: Optional[str] = None) -> dict:
        with self._lock:
            if message_name is None:
                self._auto_entries.clear()
                self._enable_msg_armed.clear()
                self._event_periodic.clear()
                self._random_active.clear()
                self._toggle_values.clear()
                self._toggle_phase.clear()
                self._row_entry_keys.clear()
            else:
                # Row-keyed TxBox entries live in _auto_entries under row
                # keys, so sweep by message as well (a plain pop by message
                # name would miss them).
                for key in [k for k, e in self._auto_entries.items()
                            if e.message_name == message_name]:
                    del self._auto_entries[key]
                    self._row_entry_keys.discard(key)
                self._enable_msg_armed.discard(message_name)
                for key in [k for k in self._event_periodic if k[0] == message_name]:
                    del self._event_periodic[key]
                for key in [k for k in self._random_active if k[0] == message_name]:
                    self._random_active.discard(key)
                self._toggle_values.pop(message_name, None)
                self._toggle_phase.pop(message_name, None)
        return self.status()

    # ---- scheduler loop ---------------------------------------------------

    def _due_entries(self, now: float) -> list[TxEntry]:
        due = []
        if self._running:
            due.extend(
                e for e in self._entries.values()
                if e.enabled and e.periodic and e.next_due <= now
            )
        due.extend(e for e in self._auto_entries.values() if e.next_due <= now)
        return due

    def _loop(self) -> None:
        last_tick = time.perf_counter()
        while not self._shutdown:
            now = time.perf_counter()
            tick_gap_ms = (now - last_tick) * 1000.0
            last_tick = now
            jobs: list[Callable[[], None]] = []
            lock_wait_start = time.perf_counter()
            with self._lock:
                lock_wait_ms = (time.perf_counter() - lock_wait_start) * 1000.0
                if lock_wait_ms > _SLOW_LOCK_WAIT_MS:
                    n = diag_log.should_log("tx.lock_wait")
                    if n >= 0:
                        logger.warning("waited %.1fms to acquire _lock%s", lock_wait_ms, diag_log.suffix(n))
                paused = self._paused
                # Only warn about tick drift while actually sending -- while
                # paused (global Stop) no periodic sends are happening at
                # all, so "periodic CAN sends are late" would be a false
                # alarm (this was reported: the warning kept firing after
                # Stop even though nothing was actually being sent).
                if tick_gap_ms > _SLOW_TICK_MS and not paused:
                    n = diag_log.should_log("tx.tick_delay")
                    if n >= 0:
                        logger.warning(
                            "tick delayed %.1fms (target ~1ms) -- periodic CAN sends are "
                            "late/jittery this cycle (GIL contention from another thread?)%s",
                            tick_gap_ms,
                            diag_log.suffix(n),
                        )
                if not paused:
                    while self._oneshots and self._oneshots[0][0] <= now:
                        jobs.append(heapq.heappop(self._oneshots)[2])
                    for key, ep in self._event_periodic.items():
                        if ep["next_due"] <= now:
                            jobs.append(self._make_event_periodic_job(key[0], key[1]))
                            # keep phase stable; skip cycles if we fell behind
                            period_s = ep["period_ms"] / 1000.0
                            ep["next_due"] += period_s
                            if ep["next_due"] <= now:
                                ep["next_due"] = now + period_s
                    for entry in self._due_entries(now):
                        jobs.append(self._make_send_job(entry))
                        # keep phase stable; skip cycles if we fell behind
                        period_s = entry.period_ms / 1000.0
                        entry.next_due += period_s
                        if entry.next_due <= now:
                            entry.next_due = now + period_s
            for job in jobs:
                job_start = time.perf_counter()
                try:
                    job()
                except Exception:
                    pass  # bus errors are counted by CanManager
                job_ms = (time.perf_counter() - job_start) * 1000.0
                if job_ms > _SLOW_JOB_MS:
                    n = diag_log.should_log("tx.job_slow")
                    if n >= 0:
                        logger.warning(
                            "a single send job took %.1fms -- CanManager.send() blocking, "
                            "not GIL contention, if this repeats%s",
                            job_ms,
                            diag_log.suffix(n),
                        )
            time.sleep(0.001)

    def _make_send_job(self, entry: TxEntry) -> Callable[[], None]:
        def send() -> None:
            # At most one invalid follow-up per tick (whole-frame-invalid
            # bytes are identical regardless of trigger signal).
            row_followup: Optional[str] = None
            if entry.message_name:
                # Snapshot under the lock -- set_value_generator()/send_invalid()
                # mutate this same per-message dict (e.g. adding a new signal's
                # generator) from request-handling threads, and iterating a
                # dict while another thread inserts into it raises
                # "dictionary changed size during iteration".
                # Only actively-transmitting signals regenerate: a merely
                # registered sibling (e.g. an unclicked multi-Random cell or
                # a deleted widget's leftover) keeps last-valid/initial/0x0
                # via the resolver instead of also turning random.
                with self._lock:
                    generators = self._value_generators.get(entry.message_name)
                    active = set(self._random_active)
                    generators = (
                        [(n, g) for n, g in generators.items()
                         if (entry.message_name, n) in active]
                        if generators else []
                    )
                raw_values = {signal_name: gen() for signal_name, gen in generators}
                # TxBox toggle: overlay the current phase set (scaled ->
                # raw), then flip. Generator values win on conflict (a live
                # Random transmission overrides the toggle for that tick).
                message = self._dbc.get_message(entry.message_name)
                toggled = self._take_toggle_explicit(entry.message_name)
                for signal_name, phys in toggled.items():
                    try:
                        raw_values[signal_name] = self._scaled_to_raw(
                            message, signal_name, phys)
                    except (ValueError, StopIteration, TypeError):
                        continue
                data = self._dbc.encode_with_raw_values(entry.message_name, raw_values)
                is_fd, brs = message.is_fd, message.is_fd
                # TxBox row entries follow one-shot event semantics on every
                # tick: each valid Event signal gets its 30ms-invalid
                # follow-up, exactly as if Send had been pressed each period.
                if entry.key in self._row_entry_keys:
                    try:
                        sent_raw = self._dbc.decode_raw(entry.arbitration_id, bytes(data))
                    except Exception:
                        sent_raw = None
                    if sent_raw:
                        for s in message.signals:
                            if (
                                self._dbc.signal_send_type(message.name, s.name) == "event"
                                and sent_raw.get(s.name) != (1 << s.length) - 1
                            ):
                                row_followup = s.name
                                break
            else:
                data = entry.data or b""
                is_fd, brs = entry.is_fd, entry.bitrate_switch
            self._can.send(
                entry.arbitration_id, data, entry.is_extended, is_fd=is_fd, bitrate_switch=brs
            )
            entry.tx_count += 1
            if row_followup is not None:
                self._schedule_invalid(message, row_followup)

        return send

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                # "Enable Msg" button's own on/off state -- true only while
                # messages it armed are still auto-resending, independent of
                # auto_entries a widget armed on its own (see
                # _enable_msg_armed's docstring above).
                "periodic_enabled": bool(self._enable_msg_armed),
                "entries": [
                    {
                        "key": e.key,
                        "arbitration_id": e.arbitration_id,
                        "period_ms": e.period_ms,
                        "enabled": e.enabled,
                        "periodic": e.periodic,
                        "message_name": e.message_name,
                        "is_fd": e.is_fd,
                        "bitrate_switch": e.bitrate_switch,
                        "tx_count": e.tx_count,
                    }
                    for e in self._entries.values()
                ],
                "auto_entries": [
                    {
                        "key": e.key,
                        "message_name": e.message_name,
                        "period_ms": e.period_ms,
                        "tx_count": e.tx_count,
                    }
                    for e in self._auto_entries.values()
                ],
            }

    def shutdown(self) -> None:
        self._shutdown = True
