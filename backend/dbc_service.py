"""DBC parsing, signal encode/decode and send-type classification.

Send-type rule (Requirement.md):
- Event signal: send the valid value, then 30 ms later send the invalid value
  (the largest value representable in the signal's bit width).
- Periodic signal: keep sending the valid value at the configured cycle time.

Effective send type of a signal = the message's leading "[TAG]" comment tag
(CM_ BO_ "[P] Periodic", "[PE] Periodic and On Event", "[EC] On Event and On
Change", ...): "P" or "PE" -> periodic, every other tag (or no tag at all,
e.g. NM_* messages) -> event. This is how the source DBCs document intended
send behavior; the DBC's own GenMsgSendType/GenSigSendType attributes are not
reliable enough on their own (e.g. "OnChangeWithRepetition" doesn't map
cleanly to either bucket, and untagged/unset messages need a clear default).
"""

import re
import threading
from typing import Any, Optional

import cantools
from cantools.database.namedsignalvalue import NamedSignalValue

PERIODIC_TAGS = {"P", "PE"}
_TAG_RE = re.compile(r"^\[([A-Za-z]+)\]")


def _message_send_type(message) -> str:
    match = _TAG_RE.match((message.comment or "").strip())
    tag = match.group(1).upper() if match else None
    return "periodic" if tag in PERIODIC_TAGS else "event"


def _plain(value: Any) -> Any:
    if isinstance(value, NamedSignalValue):
        return value.value
    return value


def _invalid_raw(signal) -> int:
    return (1 << signal.length) - 1


class DbcService:
    def __init__(self):
        self.db: Optional[cantools.database.can.Database] = None
        self.filename: Optional[str] = None
        self.raw_text: Optional[str] = None
        # last signal values per message, used to fill the other signals of
        # a frame when one signal is written (see _resolve_tx_raw for the
        # widget-value -> last-valid -> initial -> 0x0 priority).
        self._signal_state: dict[str, dict[str, Any]] = {}
        # Intentional-invalid marks per (message, signal): set only by
        # set_raw_invalid() (the Button valid<->invalid toggle). The priority
        # resolver keeps transmitting these as invalid; every valid write
        # clears the mark. A bit-max pattern that merely *happens* to be in
        # state (e.g. a full-range random draw) is NOT remembered as valid --
        # it falls back to initial/0x0 on the next send.
        self._forced_invalid: set[tuple[str, str]] = {}
        # user override of send type per "message.signal" key
        self._send_type_override: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.db is not None

    def load_string(self, text: str, filename: str = "uploaded.dbc") -> dict:
        db = cantools.database.load_string(text, database_format="dbc")
        with self._lock:
            self.db = db
            self.filename = filename
            self.raw_text = text
            self._signal_state = {
                m.name: self._initial_state(m) for m in db.messages
            }
            self._forced_invalid = set()
            self._send_type_override = {}
        return self.summary()

    def raw(self) -> Optional[dict]:
        """Currently-loaded DBC's original source text and filename, so a
        saved layout can bundle it and a later load can restore it (see
        POST /api/layouts/{name}). None if no DBC is loaded."""
        with self._lock:
            if self.raw_text is None:
                return None
            return {"filename": self.filename, "content": self.raw_text}

    def _initial_state(self, message) -> dict[str, Any]:
        """Per-signal initial raw value: the DBC GenSigStartValue when
        defined, else 0x0. A never-written signal therefore already sits at
        the "initial -> 0x0" tail of the transmit priority."""
        return {
            s.name: (s.raw_initial if s.raw_initial is not None else 0)
            for s in message.signals
        }

    # ---- introspection -------------------------------------------------

    def _signal_send_type(self, message, signal) -> str:
        override = self._send_type_override.get(f"{message.name}.{signal.name}")
        if override:
            return override
        return _message_send_type(message)

    def set_send_type_override(self, message_name: str, signal_name: str, send_type: str) -> None:
        if send_type not in ("event", "periodic"):
            raise ValueError("send_type must be 'event' or 'periodic'")
        self._send_type_override[f"{message_name}.{signal_name}"] = send_type

    def summary(self) -> dict:
        if self.db is None:
            return {"loaded": False}
        messages = []
        for m in self.db.messages:
            messages.append(
                {
                    "name": m.name,
                    "frame_id": m.frame_id,
                    "senders": list(m.senders),
                    "is_extended": m.is_extended_frame,
                    "is_fd": m.is_fd,
                    "length": m.length,
                    "cycle_time_ms": m.cycle_time,
                    "send_type": (m.send_type or "NoMsgSendType"),
                    "comment": m.comment,
                    "signals": [
                        {
                            "name": s.name,
                            "start": s.start,
                            "length": s.length,
                            "is_signed": s.is_signed,
                            "scale": float(s.scale),
                            "offset": float(s.offset),
                            "minimum": s.minimum,
                            "maximum": s.maximum,
                            "unit": s.unit,
                            "choices": (
                                {int(k): str(v) for k, v in s.choices.items()}
                                if s.choices
                                else None
                            ),
                            "send_type": self._signal_send_type(m, s),
                            "invalid_raw": _invalid_raw(s),
                            # DBC 정의 초기값의 물리값 (GenSigStartValue, 미정의 시
                            # Invalid raw의 물리값) -- TX 박스 신호 에디터 기본값
                            "default_value": (
                                (s.raw_initial if s.raw_initial is not None else _invalid_raw(s))
                                * float(s.scale)
                                + float(s.offset)
                            ),
                        }
                        for s in m.signals
                    ],
                }
            )
        return {
            "loaded": True,
            "filename": self.filename,
            "nodes": [n.name for n in self.db.nodes],
            "messages": messages,
        }

    # ---- encode / decode -----------------------------------------------

    def get_message(self, message_name: str):
        if self.db is None:
            raise RuntimeError("no DBC loaded")
        return self.db.get_message_by_name(message_name)

    def signal_send_type(self, message_name: str, signal_name: str) -> str:
        message = self.get_message(message_name)
        signal = next(s for s in message.signals if s.name == signal_name)
        return self._signal_send_type(message, signal)

    def message_send_type(self, message_name: str) -> str:
        """Message-level Periodic/Event classification (the [TAG] comment
        rule), ignoring any per-signal overrides -- used to pick which
        messages the "Enable Msg" bulk action arms for periodic auto-resend."""
        return _message_send_type(self.get_message(message_name))

    def _resolve_tx_raw_locked(self, message, explicit: set[str]) -> dict[str, Any]:
        """Transmit-priority resolution for every signal of a message
        (caller must hold the lock; explicit values must already be
        persisted into ``_signal_state``):

        1. widget-provided valid value (the ``explicit`` signals),
        2. last valid value (persisted state, unless it is a bit-max
           "invalid" pattern without an intentional-invalid mark),
        3. DBC initial value (GenSigStartValue),
        4. 0x0.
        """
        state = self._signal_state[message.name]
        tx_raw: dict[str, Any] = {}
        for s in message.signals:
            if s.name in explicit:
                tx_raw[s.name] = state[s.name]
                continue
            v = state.get(s.name)
            if v is None or (
                v == _invalid_raw(s)
                and (message.name, s.name) not in self._forced_invalid
            ):
                v = s.raw_initial if s.raw_initial is not None else 0
            tx_raw[s.name] = v
        return tx_raw

    def encode_with_values(self, message_name: str, values: dict[str, Any]) -> bytes:
        """Encode a frame applying `values` (scaled) over the stored state.

        Other (non-explicit) signals follow the transmit priority
        (widget value -> last valid -> initial -> 0x0, see
        `_resolve_tx_raw_locked`), except in an Event send: there every
        OTHER signal not carried in `values` is forced to its own invalid
        raw value -- an Event send carries only real values for the signals
        the widget is actually sending this time (one or several); nothing
        else is "remembered" from earlier writes.
        Substitution is transmit-only: the persisted state keeps the real
        (pre-substitution) values, so a later real write still starts from
        the true baseline. Explicitly written signals clear their
        intentional-invalid mark.
        """
        message = self.get_message(message_name)
        with self._lock:
            state = self._signal_state[message_name]
            data = message.encode(
                {**self._raw_to_scaled(message, state), **values}, strict=False
            )
            persisted = message.decode(data, scaling=False, decode_choices=False)
            state.update(persisted)
            for name in values:
                self._forced_invalid.discard((message_name, name))

            is_event_send = any(
                self._signal_send_type(message, s) == "event"
                for s in message.signals
                if s.name in values
            )
            tx_raw = self._resolve_tx_raw_locked(message, set(values))
            if is_event_send:
                for s in message.signals:
                    if s.name not in values:
                        tx_raw[s.name] = _invalid_raw(s)
            data = message.encode(tx_raw, scaling=False, strict=False)
        return data

    def encode_with_raw_values(self, message_name: str, raw_values: dict[str, Any]) -> bytes:
        """Raw-unit variant of `encode_with_values` for callers working in
        raw bit units (Random/Range generators, periodic auto-resend ticks).

        The explicit raw values are persisted (clearing intentional-invalid
        marks) and the frame follows the same transmit priority / Event rule
        as `encode_with_values`, so every widget shares one behavior.
        """
        message = self.get_message(message_name)
        with self._lock:
            state = self._signal_state[message_name]
            for name, raw in raw_values.items():
                state[name] = int(raw)
                self._forced_invalid.discard((message_name, name))

            is_event_send = any(
                self._signal_send_type(message, s) == "event"
                for s in message.signals
                if s.name in raw_values
            )
            tx_raw = self._resolve_tx_raw_locked(message, set(raw_values))
            if is_event_send:
                for s in message.signals:
                    if s.name not in raw_values:
                        tx_raw[s.name] = _invalid_raw(s)
            return message.encode(tx_raw, scaling=False, strict=False)

    def encode_invalid(self, message_name: str, signal_name: str) -> bytes:
        """Encode a frame with every signal in the message -- `signal_name`
        included -- forced to its own invalid raw value. This is the 30ms-
        later Event follow-up: the whole frame reads as invalid, nothing is
        read from or written to persisted state."""
        message = self.get_message(message_name)
        raw = {s.name: _invalid_raw(s) for s in message.signals}
        return message.encode(raw, scaling=False, strict=False)

    def encode_current(self, message_name: str) -> bytes:
        message = self.get_message(message_name)
        with self._lock:
            raw = dict(self._signal_state[message_name])
        return message.encode(raw, scaling=False, strict=False)

    def encode_initial(self, message_name: str) -> bytes:
        """DBC에 정의된 초기값으로 인코딩한 페이로드.

        각 신호는 GenSigStartValue(raw_initial)가 있으면 그 값을,
        정의되어 있지 않으면 Invalid 값(비트幅 최대값)으로 채운다.
        TX 박스의 DBC 직접 입력 모드에서 선택 직후의 프리필 값으로 쓴다."""
        message = self.get_message(message_name)
        raw = {
            s.name: (s.raw_initial if s.raw_initial is not None else _invalid_raw(s))
            for s in message.signals
        }
        return message.encode(raw, scaling=False, strict=False)

    def set_raw_signal_value(self, message_name: str, signal_name: str, raw_value: int) -> None:
        """Poke a single signal's raw state directly, bypassing encode/decode --
        used by tx_scheduler's Random/Range value generators, which work in
        raw bit units rather than physical (scaled) values. Any write clears
        the signal's intentional-invalid mark (a fresh value supersedes it)."""
        with self._lock:
            self._signal_state[message_name][signal_name] = raw_value
            self._forced_invalid.discard((message_name, signal_name))

    def set_raw_invalid(self, message_name: str, signal_name: str) -> None:
        """Persist the bit-max "invalid" pattern AND mark it intentional --
        the transmit priority keeps sending it (the Button valid<->invalid
        toggle). This is the only path that marks; every valid write clears.
        """
        message = self.get_message(message_name)
        signal = next(s for s in message.signals if s.name == signal_name)
        with self._lock:
            self._signal_state[message_name][signal_name] = _invalid_raw(signal)
            self._forced_invalid.add((message_name, signal_name))

    def _raw_to_scaled(self, message, raw: dict[str, Any]) -> dict[str, Any]:
        data = message.encode(raw, scaling=False, strict=False)
        return {
            k: _plain(v)
            for k, v in message.decode(data, decode_choices=False).items()
        }

    def decode(self, frame_id: int, data: bytes) -> Optional[dict]:
        if self.db is None:
            return None
        try:
            message = self.db.get_message_by_frame_id(frame_id)
        except KeyError:
            return None
        try:
            decoded = message.decode(data, decode_choices=True)
            raw = message.decode(data, decode_choices=False, scaling=False)
        except Exception:
            return None
        return {
            "name": message.name,
            "signals": {
                k: (str(v) if isinstance(v, NamedSignalValue) else v)
                for k, v in decoded.items()
            },
            # Signals whose raw value is NOT the bit-max "invalid" pattern --
            # used by the RX-only "수신 CAN 신호 표시창" widget, which shows a
            # signal's value only while it's valid (see PERIODIC_TAGS / the
            # Event 30ms-later-invalid rule this mirrors on the RX side).
            "valid_signals": [
                s.name for s in message.signals if raw.get(s.name) != _invalid_raw(s)
            ],
        }

    def decode_raw(self, frame_id: int, data: bytes) -> Optional[dict[str, int]]:
        """Decode without scaling or VAL_ label lookup -- the raw bit pattern
        per signal. Used where a test step's expected value is itself a raw
        hex constant (e.g. test_runner_service's CANResp), so the comparison
        never depends on a signal's scale/offset/choices."""
        if self.db is None:
            return None
        try:
            message = self.db.get_message_by_frame_id(frame_id)
        except KeyError:
            return None
        try:
            return message.decode(data, decode_choices=False, scaling=False)
        except Exception:
            return None
