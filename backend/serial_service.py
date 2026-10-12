"""Dual-port serial (UART/RS-232) RX monitor for the Serial Monitor widget.

Two independent receive channels ("a" and "b"), each with its own port +
baudrate setting (8N1 fixed). A background reader thread per open port
appends arrival-timestamped byte chunks to an in-memory ring (widget live
view + scrollback) and, while CAN logging is active, to a per-port text log
file (``serial_A_<ts>.log`` / ``serial_B_<ts>.log``).

Lifecycle (called from main.py):
  * ``POST /api/serial/config`` stores the port settings (persisted only in
    the widget layout's options on the frontend; the backend keeps the
    last-set copy for the session).
  * CAN ``Start`` (``/api/run/start``) -> ``open_all()`` (ports configured
    in the widget start monitoring together with the simulator).
  * CAN ``Stop`` / ``disconnect`` -> ``close_all()``.
  * CAN ``로깅`` start/stop (``/api/log/start|stop``) -> ``start_logging()`` /
    ``stop_logging()`` (serial data logged alongside CAN, in 2 files).

Optional hardware: pyserial may not be installed, or no COM/tty device may
be attached. In either case every public method returns a graceful
``{ok: False, ...}``-style dict instead of raising (same convention as
power_supply_service / audio_service), so CAN-only usage never breaks.
Tests use pyserial's ``loop://`` URL -- no hardware required.
"""

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("cansim.serial")

try:
    import serial
    from serial.tools import list_ports

    _PYSERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via _PYSERIAL_AVAILABLE branch
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]
    _PYSERIAL_AVAILABLE = False

PORTS = ("a", "b")
# In-memory scrollback per port (widget live view + lines REST).
SCROLLBACK_CAP = 5000
# Pending entries waiting for the WS broadcast drain (bounded so a fast
# device can't grow memory without a connected browser).
PENDING_CAP = 20000
# Max entries returned per WS broadcast drain, per port.
DRAIN_MAX = 500
# Reader chunk timeout -- short so close_all()/threads exit promptly.
READ_TIMEOUT_S = 0.1

DEFAULT_BAUD = 115200


def _format_log_ts(ts_ms: float) -> str:
    """Epoch-ms -> ``YYYY-MM-DD HH:MM:SS.mmm`` (local time). Integer math:
    epoch-ms no longer fits in float64 without losing the ms digit."""
    total_ms = int(ts_ms)
    secs, ms = divmod(total_ms, 1000)
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(secs))}.{ms:03d}"


def _hex_ascii_line(data: bytes) -> str:
    """``'48 45 4C  | HELLO'`` -- hex always lossless, ascii best-effort."""
    hex_part = " ".join(f"{b:02X}" for b in data)
    ascii_part = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in data)
    return f"{hex_part}  | {ascii_part}"


class SerialService:
    def __init__(self, log_dir: Path):
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._config: dict[str, dict[str, Any]] = {
            p: {"port": "", "baud": DEFAULT_BAUD} for p in PORTS
        }
        self._handles: dict[str, Any] = {p: None for p in PORTS}
        self._threads: dict[str, Optional[threading.Thread]] = {p: None for p in PORTS}
        self._stop_flags: dict[str, threading.Event] = {p: threading.Event() for p in PORTS}
        self._errors: dict[str, Optional[str]] = {p: None for p in PORTS}
        # Each entry: {"ts": epoch-ms float, "data": hex str (no spaces)}.
        self._scrollback: dict[str, deque] = {p: deque(maxlen=SCROLLBACK_CAP) for p in PORTS}
        self._pending: dict[str, deque] = {p: deque(maxlen=PENDING_CAP) for p in PORTS}
        # CAN-logging sidecar files (2 files, one per port).
        self._log_lock = threading.Lock()
        self._log_files: dict[str, Any] = {p: None for p in PORTS}
        self._log_filenames: dict[str, Optional[str]] = {p: None for p in PORTS}
        self._log_counts: dict[str, int] = {p: 0 for p in PORTS}

    # ---- port enumeration -------------------------------------------------

    def list_ports(self) -> dict:
        if not _PYSERIAL_AVAILABLE:
            return {
                "available": False,
                "ports": [],
                "error": "pyserial이 설치되어 있지 않습니다 (pip install pyserial)",
            }
        try:
            ports = [
                {"device": p.device, "description": p.description or ""}
                for p in list_ports.comports()
            ]
        except Exception as exc:
            return {"available": True, "ports": [], "error": f"포트 열거 실패: {exc}"}
        return {"available": True, "ports": ports, "error": None}

    # ---- configuration ----------------------------------------------------

    def set_config(
        self,
        a_port: str = "",
        a_baud: int = DEFAULT_BAUD,
        b_port: str = "",
        b_baud: int = DEFAULT_BAUD,
    ) -> dict:
        try:
            a_baud = int(a_baud)
            b_baud = int(b_baud)
        except (TypeError, ValueError):
            raise RuntimeError("baud는 정수여야 합니다")
        if a_baud <= 0 or b_baud <= 0:
            raise RuntimeError("baud는 양의 정수여야 합니다")
        with self._lock:
            self._config = {
                "a": {"port": (a_port or "").strip(), "baud": a_baud},
                "b": {"port": (b_port or "").strip(), "baud": b_baud},
            }
        return self.status()

    # ---- open / close (CAN Start/Stop hooks) -------------------------------

    def _open_one(self, key: str, port: str, baud: int) -> dict:
        if not _PYSERIAL_AVAILABLE:
            return {"ok": False, "reason": "pyserial이 설치되어 있지 않습니다"}
        if not port:
            return {"ok": False, "reason": "포트가 설정되지 않았습니다"}
        try:
            handle = serial.serial_for_url(
                port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=READ_TIMEOUT_S,
            )
        except Exception as exc:
            return {"ok": False, "reason": f"포트 열기 실패 ({port}): {exc}"}
        stop = self._stop_flags[key]
        stop.clear()
        thread = threading.Thread(
            target=self._reader_loop, args=(key, handle, stop), daemon=True, name=f"serial-{key}"
        )
        self._handles[key] = handle
        self._threads[key] = thread
        self._errors[key] = None
        thread.start()
        return {"ok": True, "port": port, "baud": baud}

    def open_all(self) -> dict:
        """Open every configured port. Best-effort per port -- never raises,
        so CAN Start always succeeds even with a missing/unplugged device."""
        self._teardown_locked()
        with self._lock:
            results = {}
            for key in PORTS:
                cfg = self._config[key]
                if not cfg["port"]:
                    results[key] = {"ok": False, "reason": "포트가 설정되지 않았습니다"}
                    continue
                results[key] = self._open_one(key, cfg["port"], cfg["baud"])
        return {"results": results, **self.status()}

    def _teardown_locked(self) -> None:
        """Stop reader threads and close handles. Joining happens outside
        the lock -- readers only touch the deques and the log lock, so no
        deadlock is possible."""
        with self._lock:
            for key in PORTS:
                self._stop_flags[key].set()
            threads = dict(self._threads)
            handles = dict(self._handles)
            for key in PORTS:
                self._threads[key] = None
                self._handles[key] = None
        for thread in threads.values():
            if thread is not None:
                thread.join(timeout=2.0)
        for handle in handles.values():
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    def close_all(self) -> dict:
        self._teardown_locked()
        with self._lock:
            for key in PORTS:
                self._errors[key] = None
        return self.status()

    def shutdown(self) -> None:
        """Process teardown -- close ports and sidecar log files."""
        try:
            self.close_all()
        except Exception:
            pass
        try:
            self.stop_logging()
        except Exception:
            pass

    # ---- reader ------------------------------------------------------------

    def _reader_loop(self, key: str, handle: Any, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                n = handle.in_waiting
            except Exception as exc:
                with self._lock:
                    self._errors[key] = f"읽기 실패: {exc}"
                    self._handles[key] = None
                try:
                    handle.close()
                except Exception:
                    pass
                return
            try:
                # in_waiting may be 0 -> read(1) blocks up to READ_TIMEOUT_S.
                data = handle.read(max(1, n))
            except Exception as exc:
                with self._lock:
                    self._errors[key] = f"읽기 실패: {exc}"
                    self._handles[key] = None
                try:
                    handle.close()
                except Exception:
                    pass
                return
            if data:
                self._ingest(key, bytes(data))

    def _ingest(self, key: str, data: bytes) -> None:
        entry = {"ts": time.time() * 1000.0, "data": data.hex().upper()}
        self._scrollback[key].append(entry)
        self._pending[key].append(entry)
        with self._log_lock:
            fh = self._log_files[key]
            if fh is not None:
                try:
                    fh.write(f"[{_format_log_ts(entry['ts'])}] {_hex_ascii_line(data)}\n")
                    self._log_counts[key] += 1
                except Exception:
                    pass

    # ---- live drain (WS broadcast) + scrollback (REST) ----------------------

    def drain(self) -> dict[str, list]:
        """Pop and return entries arrived since the last drain, per port."""
        out: dict[str, list] = {}
        for key in PORTS:
            pending = self._pending[key]
            batch = []
            while pending and len(batch) < DRAIN_MAX:
                batch.append(pending.popleft())
            out[key] = batch
        return out

    def lines(self, port: str, since_ms: float = 0.0, limit: int = 500) -> dict:
        if port not in PORTS:
            raise RuntimeError("port는 a|b 중 하나여야 합니다")
        limit = max(1, min(2000, int(limit)))
        entries = [e for e in self._scrollback[port] if e["ts"] >= since_ms]
        return {"port": port, "lines": entries[-limit:]}

    # ---- CAN-logging sidecar (2 files) --------------------------------------

    def start_logging(self) -> dict:
        """Open ``serial_A_<ts>.log`` / ``serial_B_<ts>.log``. Best-effort and
        idempotent -- never raises, so a serial failure can't break CAN logging."""
        with self._log_lock:
            if self._log_files["a"] is not None or self._log_files["b"] is not None:
                return self._logging_status()
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for key, tag in (("a", "A"), ("b", "B")):
                filename = f"serial_{tag}_{stamp}.log"
                try:
                    fh = open(self._log_dir / filename, "w", encoding="utf-8")
                except OSError as exc:
                    logger.warning("serial log open failed (%s): %s", filename, exc)
                    continue
                self._log_files[key] = fh
                self._log_filenames[key] = filename
                self._log_counts[key] = 0
        return self._logging_status()

    def stop_logging(self) -> dict:
        with self._log_lock:
            for key in PORTS:
                fh, self._log_files[key] = self._log_files[key], None
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
        return self._logging_status()

    def _logging_status(self) -> dict:
        active = self._log_files["a"] is not None or self._log_files["b"] is not None
        return {
            "active": active,
            "files": dict(self._log_filenames),
            "counts": dict(self._log_counts),
        }

    # ---- status --------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            cfg = {k: dict(v) for k, v in self._config.items()}
            opened = {k: self._handles[k] is not None for k in PORTS}
            errors = dict(self._errors)
        return {
            "available": _PYSERIAL_AVAILABLE,
            "error": None if _PYSERIAL_AVAILABLE else "pyserial이 설치되어 있지 않습니다",
            "config": cfg,
            "open": opened,
            "errors": errors,
            "logging": self._logging_status(),
        }
