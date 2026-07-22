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
        # last valid signal values per message, used to fill the other
        # signals of a frame when one signal is written (periodic messages
        # only -- see encode_with_values)
        self._signal_state: dict[str, dict[str, Any]] = {}
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
                m.name: self._zero_state(m) for m in db.messages
            }
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

    def _zero_state(self, message) -> dict[str, Any]:
        raw = message.decode(bytes(message.length), scaling=False, decode_choices=False)
        return dict(raw)

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

    def encode_with_values(self, message_name: str, values: dict[str, Any]) -> bytes:
        """Encode a frame applying `values` (scaled) over the stored state.

        If any of the signals being set is "event" type, every OTHER signal
        in the message is forced to its own invalid raw value in the
        outgoing frame -- an Event send carries exactly one real value (the
        signal just set); nothing else is "remembered" from earlier writes.
        This substitution is transmit-only: the persisted state keeps the
        real (pre-substitution) values, so a later real write still starts
        from the true baseline. Periodic-only sends are unaffected -- their
        other signals keep coming from the persisted state as before, since
        Periodic messages have no invalid concept and rely on that state
        accumulating real values across successive writes.
        """
        message = self.get_message(message_name)
        with self._lock:
            state = self._signal_state[message_name]
            data = message.encode(
                {**self._raw_to_scaled(message, state), **values}, strict=False
            )
            persisted = message.decode(data, scaling=False, decode_choices=False)
            state.update(persisted)

            is_event_send = any(
                self._signal_send_type(message, s) == "event"
                for s in message.signals
                if s.name in values
            )
            if is_event_send:
                tx_raw = dict(persisted)
                for s in message.signals:
                    if s.name not in values:
                        tx_raw[s.name] = _invalid_raw(s)
                data = message.encode(tx_raw, scaling=False, strict=False)
        return data

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

    def set_raw_signal_value(self, message_name: str, signal_name: str, raw_value: int) -> None:
        """Poke a single signal's raw state directly, bypassing encode/decode --
        used by tx_scheduler's Random/Range value generators, which work in
        raw bit units rather than physical (scaled) values."""
        with self._lock:
            self._signal_state[message_name][signal_name] = raw_value

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
