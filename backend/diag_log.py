"""Rate-limited WARNING logging shared by the Windows audio/CAN-lag
diagnostic logs added to audio_service.py, tx_scheduler.py, and main.py (see
Requirement.md). Without this, a condition that stays continuously true --
e.g. every single get_waveform() call being slow while an audio widget is
open, polled every 60-100ms -- logs on every single occurrence and floods
the terminal, which is exactly what a user reported. should_log() throttles
each warning *site* (identified by `key`) to at most once per
min_interval_s, across all threads, while still reporting how many
occurrences were suppressed in between so the signal isn't lost.
"""

import threading
import time

DEFAULT_MIN_INTERVAL_S = 2.0

_lock = threading.Lock()
_last_logged: dict[str, float] = {}
_suppressed_count: dict[str, int] = {}


def should_log(key: str, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> int:
    """Returns -1 if this occurrence should be suppressed (too soon after the
    last log for this key -- caller must not log). Otherwise returns the
    number of prior occurrences suppressed since the last time this key
    actually logged (>= 0) -- caller should log and may include this count
    (e.g. "(+12 more suppressed in the last 2s)")."""
    now = time.monotonic()
    with _lock:
        last = _last_logged.get(key, 0.0)
        if now - last < min_interval_s:
            _suppressed_count[key] = _suppressed_count.get(key, 0) + 1
            return -1
        n = _suppressed_count.pop(key, 0)
        _last_logged[key] = now
        return n


def suffix(n: int, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> str:
    """Formats should_log()'s suppressed-count into a log-message suffix
    (empty string if nothing was suppressed)."""
    return f" (+{n} more suppressed in the last {min_interval_s:.0f}s)" if n > 0 else ""
