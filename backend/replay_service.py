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
        self._pause_event = threading.Event()
        self._lock = threading.Lock()
        self._progress = {"sent": 0, "skipped": 0, "total": 0, "running": False, "paused": False}

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
                "paused": False,
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
        self._pause_event.clear()
        with self._lock:
            self._progress.update({"sent": 0, "skipped": 0, "running": True, "paused": False})
        self._thread = threading.Thread(
            target=self._run, args=(mode, ids), daemon=True
        )
        self._thread.start()
        return self.info()

    def pause(self) -> dict:
        with self._lock:
            if not self._progress.get("running"):
                raise RuntimeError("replay is not running")
            if self._progress.get("paused"):
                raise RuntimeError("replay is already paused")
            self._pause_event.set()
            self._progress["paused"] = True
        return self.info()

    def resume(self) -> dict:
        with self._lock:
            if not self._progress.get("running"):
                raise RuntimeError("replay is not running")
            if not self._progress.get("paused"):
                raise RuntimeError("replay is not paused")
            self._pause_event.clear()
            self._progress["paused"] = False
        return self.info()

    def stop(self) -> dict:
        self._stop_event.set()
        self._pause_event.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._progress["running"] = False
            self._progress["paused"] = False
            # Stop은 초기에 로그를 loading 한 상태로 복귀 (Progress 초기화)
            self._progress["sent"] = 0
            self._progress["skipped"] = 0
        return self.info()

    def _run(self, mode: str, frame_ids: frozenset[int]) -> None:
        messages = self._messages
        t0 = messages[0].timestamp
        wall0 = time.perf_counter()
        for msg in messages:
            if self._stop_event.is_set():
                break
            # Pause 처리 — 일시정지 중에는 타임라인을 멈추고 stop/ resume 대기
            if self._pause_event.is_set():
                pause_start = time.perf_counter()
                while self._pause_event.is_set():
                    if self._stop_event.wait(timeout=0.05):
                        break
                if self._stop_event.is_set():
                    break
                # 일시정지 동안 경과한 시간만큼 wall0 보정 (재개 후 원래 간격 유지)
                wall0 += time.perf_counter() - pause_start
                if self._stop_event.is_set():
                    break
            target = wall0 + (msg.timestamp - t0)
            delay = target - time.perf_counter()
            if delay > 0:
                # delay 대기 중에도 pause/stop에 반응하도록 짧게 쪼개어 대기
                remaining = delay
                while remaining > 0:
                    if self._stop_event.is_set():
                        break
                    if self._pause_event.is_set():
                        pause_start = time.perf_counter()
                        while self._pause_event.is_set():
                            if self._stop_event.wait(timeout=0.05):
                                break
                        if self._stop_event.is_set():
                            break
                        wall0 += time.perf_counter() - pause_start
                        # pause로 wall0가 보정되었으므로 target 재계산
                        target = wall0 + (msg.timestamp - t0)
                        remaining = target - time.perf_counter()
                        if remaining <= 0:
                            break
                        continue
                    step = min(remaining, 0.05)
                    if self._stop_event.wait(timeout=step):
                        break
                    remaining = target - time.perf_counter()
                if self._stop_event.is_set():
                    break
                if self._pause_event.is_set():
                    # pause로 인해 위 루프에서 이미 wall0 보정됨 — 다음 메시지부터 정상 진행
                    # 현재 메시지는 pause 해제 후 즉시 처리되도록 delay 없이 진행
                    pass
            if self._stop_event.is_set():
                break
            # pause 중 stop 없이 루프를 빠져나온 경우 다시 pause 체크
            if self._pause_event.is_set():
                continue
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
            self._progress["paused"] = False
