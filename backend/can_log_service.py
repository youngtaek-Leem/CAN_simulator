"""CAN log 분석 위젯 백엔드: BLF/ASC CAN 로그 + DBC로 신호 시계열을 만든다.

sysLog와 유사하지만 차이점:
- 파서는 8바이트 고정 sysLog가 아니라 python-can BLF/ASC 리더.
- x축은 단순 선형: (timestamp - t0)*1000 ms (세그먼트 이어붙이기 없음, 요청: 단순선형)
- 신호 목록은 DBC 정의 전체가 아니라 CAN log에 실제 포함된 신호만(B).
- Y값은 raw 정수 그대로 (DEC/HEX/Value Description은 프론트에서 선택).
"""

import bisect
import tempfile
from pathlib import Path
from collections import defaultdict

import can

from dbc_service import DbcService


class CanLogService:
    """CAN log 1개만 세션에 유지하는 교체 방식. DBC는 전역 DbcService를 참조."""

    def __init__(self, dbc_service: DbcService):
        self._dbc = dbc_service
        self._messages: list[can.Message] = []
        self._log_filename: str | None = None
        # signal_key -> {message, signal, points, count}
        self._series_cache: dict[str, dict] | None = None
        self._timeline_cache: dict | None = None
        self._t0: float = 0.0

    def load_log(self, data: bytes, filename: str) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in (".blf", ".asc"):
            raise ValueError(f"unsupported log format: {suffix} (use .blf or .asc)")
        # BLFReader needs a file path
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            reader_cls = can.BLFReader if suffix == ".blf" else can.ASCReader
            with reader_cls(tmp_path) as reader:
                msgs = [m for m in reader if not m.is_error_frame]
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
        self._messages = msgs
        self._log_filename = filename
        self._series_cache = None
        self._timeline_cache = None
        if msgs:
            self._t0 = msgs[0].timestamp
        else:
            self._t0 = 0.0
        return self.status()

    def status(self) -> dict:
        msgs = self._messages
        duration = (msgs[-1].timestamp - msgs[0].timestamp) if len(msgs) > 1 else 0.0
        return {
            "log_filename": self._log_filename,
            "record_count": len(msgs),
            "duration_s": round(duration, 3),
            "t0": self._t0,
        }

    def _ensure_series(self):
        if self._series_cache is not None and self._timeline_cache is not None:
            return
        msgs = self._messages
        if not msgs:
            self._series_cache = {}
            self._timeline_cache = {"plot_x_min": 0, "plot_x_max": 1, "segments": []}
            return
        # timeline: 단순 선형, 0 ~ (last - t0)*1000
        t0 = self._t0
        plot_x_max = int((msgs[-1].timestamp - t0) * 1000)
        # single segment for compatibility with syslog frontend (optional)
        segments = []
        if plot_x_max > 0:
            segments = [{"plot_x_start": 0, "abs_ms_start": 0, "plot_x_end": plot_x_max, "abs_ms_end": plot_x_max}]
        self._timeline_cache = {"plot_x_min": 0, "plot_x_max": max(1, plot_x_max), "segments": segments}

        # build per-signal series (log 포함만)
        # signal_key -> list[point]
        tmp: dict[str, list[dict]] = defaultdict(list)
        # also track message/signal meta
        meta: dict[str, dict] = {}
        if not self._dbc.loaded:
            # DBC 없으면 신호 디코딩 불가 → 빈 시리즈
            self._series_cache = {}
            return
        for idx, msg in enumerate(msgs):
            raw = self._dbc.decode_raw(msg.arbitration_id, bytes(msg.data))
            if raw is None:
                continue
            x_ms = int((msg.timestamp - t0) * 1000)
            for sig_name, raw_val in raw.items():
                # raw_val is int
                try:
                    # find message name for this id
                    db_msg = self._dbc.db.get_message_by_frame_id(msg.arbitration_id)
                    msg_name = db_msg.name
                except Exception:
                    continue
                key = f"{msg_name}.{sig_name}"
                # cache meta once
                if key not in meta:
                    try:
                        sig = next(s for s in db_msg.signals if s.name == sig_name)
                        meta[key] = {
                            "message": msg_name,
                            "signal": sig_name,
                            "frame_id": msg.arbitration_id,
                            "choices": {int(k): str(v) for k, v in sig.choices.items()} if sig.choices else None,
                        }
                    except Exception:
                        meta[key] = {"message": msg_name, "signal": sig_name, "frame_id": msg.arbitration_id, "choices": None}
                tmp[key].append({
                    "seq": idx,
                    "x_ms": x_ms,
                    "value": int(raw_val),
                    "ts": msg.timestamp,
                })
        # build final series dict
        series: dict[str, dict] = {}
        for key, points in tmp.items():
            m = meta[key]
            # sort by x_ms (already in order, but ensure)
            points.sort(key=lambda p: p["x_ms"])
            series[key] = {
                "key": key,
                "message": m["message"],
                "signal": m["signal"],
                "frame_id": m["frame_id"],
                "choices": m["choices"],
                "count": len(points),
                "points": points,
            }
        self._series_cache = series

    def list_signals(self) -> list[dict]:
        self._ensure_series()
        assert self._series_cache is not None
        # signal별: 평면 리스트
        result = []
        for key, s in self._series_cache.items():
            result.append({
                "key": key,
                "message": s["message"],
                "signal": s["signal"],
                "frame_id": s["frame_id"],
                "count": s["count"],
                "choices": s["choices"],
            })
        # 정렬: 메시지 이름 + 신호 이름 알파벳순
        result.sort(key=lambda x: (x["message"].lower(), x["signal"].lower()))
        return result

    def list_messages(self) -> list[dict]:
        """메시지별: 메시지 리스트 + 하위 신호 목록"""
        self._ensure_series()
        assert self._series_cache is not None
        by_msg: dict[str, list[dict]] = defaultdict(list)
        for s in self._series_cache.values():
            by_msg[s["message"]].append(s)
        result = []
        for msg_name, sigs in by_msg.items():
            sigs_sorted = sorted(sigs, key=lambda x: x["signal"].lower())
            result.append({
                "message": msg_name,
                "frame_id": sigs_sorted[0]["frame_id"] if sigs_sorted else 0,
                "signals": [
                    {"key": s["key"], "signal": s["signal"], "count": s["count"], "choices": s["choices"]}
                    for s in sigs_sorted
                ],
                "count": sum(s["count"] for s in sigs_sorted),
            })
        result.sort(key=lambda x: x["message"].lower())
        return result

    def get_series(self, keys: list[str]) -> dict[str, dict]:
        self._ensure_series()
        assert self._series_cache is not None
        return {k: self._series_cache[k] for k in keys if k in self._series_cache}

    def timeline(self) -> dict:
        self._ensure_series()
        assert self._timeline_cache is not None
        return self._timeline_cache
