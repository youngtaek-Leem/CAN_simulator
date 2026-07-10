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


class DbcService:
    def __init__(self):
        self.db: Optional[cantools.database.can.Database] = None
        self.filename: Optional[str] = None
        # last valid signal values per message, used to fill the other
        # signals of a frame when one signal is written
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
            self._signal_state = {
                m.name: self._zero_state(m) for m in db.messages
            }
            self._send_type_override = {}
        return self.summary()

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
                            "invalid_raw": (1 << s.length) - 1,
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

    def encode_with_values(self, message_name: str, values: dict[str, Any]) -> bytes:
        """Encode a frame applying `values` (scaled) over the stored state."""
        message = self.get_message(message_name)
        with self._lock:
            state = self._signal_state[message_name]
            data = message.encode(
                {**self._raw_to_scaled(message, state), **values}, strict=False
            )
            state.update(message.decode(data, scaling=False, decode_choices=False))
        return data

    def encode_invalid(self, message_name: str, signal_name: str) -> bytes:
        """Encode a frame with `signal_name` forced to its invalid raw value.

        The invalid value is NOT stored in the state, so later frames of the
        same message fall back to the last valid values.
        """
        message = self.get_message(message_name)
        signal = next(s for s in message.signals if s.name == signal_name)
        with self._lock:
            raw = dict(self._signal_state[message_name])
        raw[signal_name] = (1 << signal.length) - 1
        return message.encode(raw, scaling=False, strict=False)

    def encode_current(self, message_name: str) -> bytes:
        message = self.get_message(message_name)
        with self._lock:
            raw = dict(self._signal_state[message_name])
        return message.encode(raw, scaling=False, strict=False)

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
        except Exception:
            return None
        return {
            "name": message.name,
            "signals": {
                k: (str(v) if isinstance(v, NamedSignalValue) else v)
                for k, v in decoded.items()
            },
        }
