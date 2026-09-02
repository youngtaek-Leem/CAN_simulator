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

    def generate_test_script(
        self,
        range_ms: dict | None,
        dbc_messages: list[dict],
        signal_send_type,
        rx_node: str | None = None,
        dbc_nodes: list[str] | None = None,
    ) -> dict:
        """CAN log를 시뮬레이터 TX 신호 기준 테스트 스크립트로 생성한다.

        range_ms: {"a_ms": int, "b_ms": int} | None — None이면 전체, 아니면 [a,b] 구간만.
        dbc_messages/signal_send_type은 syslog와 동일하게 DBC에서 Periodic/Event를 판정한다.
        rx_node: 필수 설정에서 선택한 RX 노드 (예: AMP_FD / AMP_HS). None/빈문자열이면
            호출부에서 차단해야 하나, 방어적으로 빈 결과+에러를 반환한다.
        규칙:
        - 시뮬레이터 TX 신호만 대상 (senders에 rx_node가 포함되지 않은 메시지)
        - raw == invalid_raw → Invalid → 스킵
        - Periodic: 최초 수신 또는 값이 변경되어 Valid인 경우만 생성
        - 동일 x_ms + 동일 Message + 동일 type이면 Signals 배열로 합치기 (1개면 단일 Signal 형식)
        - 그룹 간 delay가 1ms 이하이면 생략
        """
        self._ensure_series()
        assert self._series_cache is not None

        warnings: list[dict] = []
        errors: list[dict] = []

        # RX 노드 미선택 — 프론트에서 1차 차단하지만 백엔드도 방어적으로 에러 반환
        if not rx_node:
            errors.append({"log_id": -1, "log_name": "", "reason": "RX 노드가 선택되지 않았습니다. 필수 설정에서 RX 노드(예: AMP_FD / AMP_HS)를 선택하세요."})
            return {"steps": [], "warnings": warnings, "errors": errors, "matched_count": 0}

        # rx_node 유효성 검사 — DBC에 정의된 노드/메시지 senders에 존재하지 않으면 경고
        available_senders: set[str] = set()
        for m in dbc_messages:
            for s in m.get("senders", []):
                available_senders.add(s)
        # dbc_nodes가 주어지면 거기도 합친 전체 노드 풀
        all_nodes: set[str] = set(available_senders)
        if dbc_nodes:
            all_nodes.update(dbc_nodes)
        if rx_node not in all_nodes:
            # 경고: 필터 결과는 전체가 TX가 되어(아무 메시지도 rx_node를 포함하지 않음)
            # 의도치 않은 전체 추출이 될 수 있으므로 사용자에게 알린다
            warnings.append({
                "log_id": -1,
                "log_name": rx_node,
                "matched_message": "",
                "matched_signal": "",
                "reason": f"선택한 RX 노드 '{rx_node}'가 현재 로드된 DBC에 존재하지 않습니다.",
            })

        # TX 메시지 필터: rx_node를 sender로 포함하지 않고 NM_로 시작하지 않는 메시지만 시뮬레이터가 송신
        tx_msg_names = {
            m["name"] for m in dbc_messages
            if rx_node not in m.get("senders", []) and not m["name"].startswith("NM_")
        }

        # invalid_raw 맵
        invalid_map: dict[str, int] = {}
        for m in dbc_messages:
            for s in m["signals"]:
                invalid_map[f"{m['name']}.{s['name']}"] = s["invalid_raw"]

        # range 필터
        a_ms = b_ms = None
        if range_ms is not None:
            a_ms = min(range_ms["a_ms"], range_ms["b_ms"])
            b_ms = max(range_ms["a_ms"], range_ms["b_ms"])

        # 후보 점 수집: Valid만 (Periodic 변경 필터는 정렬 후 적용) — NM_ 2중 방어
        candidates: list[dict] = []  # {x_ms, message, signal, key, value, type, seq}
        for key, series in self._series_cache.items():
            if series["message"].startswith("NM_"):
                continue
            if series["message"] not in tx_msg_names:
                continue
            for p in series["points"]:
                x = p["x_ms"]
                if a_ms is not None and not (a_ms <= x <= b_ms):
                    continue
                raw = p["value"]
                invalid = invalid_map.get(key)
                if invalid is not None and raw == invalid:
                    continue
                try:
                    st = signal_send_type(series["message"], series["signal"])
                except Exception:
                    st = "event"
                candidates.append({
                    "x_ms": x,
                    "seq": p["seq"],
                    "message": series["message"],
                    "signal": series["signal"],
                    "key": key,
                    "value": raw,
                    "type": st,
                })
        candidates.sort(key=lambda c: (c["x_ms"], c["seq"]))
        # Periodic: 최초 수신 또는 값이 변경된 경우만
        filtered: list[dict] = []
        last2: dict[str, int] = {}
        for c in candidates:
            if c["type"] == "periodic":
                prev = last2.get(c["key"])
                if prev is not None and prev == c["value"]:
                    continue
                last2[c["key"]] = c["value"]
            else:
                last2[c["key"]] = c["value"]
            filtered.append(c)

        # type별 grouping: 동일 x_ms + 동일 Message + 동일 type → Signals 배열
        steps: list[dict] = []
        matched_keys = set()
        last_step = None
        last_message = None
        last_type = None
        prev_x = None

        for c in filtered:
            x = c["x_ms"]
            msg = c["message"]
            sig = c["signal"]
            typ = "CANReq" if c["type"] == "periodic" else "CANEv"
            value_hex = f"0x{c['value']:X}"
            matched_keys.add(c["key"])

            orig_delay = None
            if prev_x is not None:
                orig_delay = x - prev_x

            same_moment_same_message = (
                orig_delay == 0 and last_step is not None and last_message == msg and last_type == typ
            )
            if same_moment_same_message:
                assert last_step is not None
                if "Signals" not in last_step:
                    last_step["Signals"] = [{"Signal": last_step.pop("Signal"), "Value": last_step.pop("Value")}]
                last_step["Signals"].append({"Signal": sig, "Value": value_hex})
            else:
                # 그룹 간 delay가 1ms 이하이면 생략
                if orig_delay is not None and orig_delay > 1:
                    steps.append({"type": "delay", "ms": orig_delay})
                new_step = {"type": typ, "Message": msg, "Signal": sig, "Value": value_hex}
                steps.append(new_step)
                last_step = new_step
                last_message = msg
                last_type = typ
            prev_x = x

        # delay가 0으로 정규화된 경우 그룹 간 delay를 생략했으므로, 실제 delay가 1ms 이하였던 그룹 간격은 기록되지 않음
        # 위 로직에서 delay==0인 그룹 간은 same_moment가 아니면 delay append를 건너뛰므로 생략됨

        if steps:
            steps = [{"type": "ID", "num": "1", "Cycle": 1}, *steps]

        return {
            "steps": steps,
            "warnings": warnings,
            "errors": errors,
            "matched_count": len(matched_keys),
        }
