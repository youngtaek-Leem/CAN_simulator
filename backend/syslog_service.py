"""sysLog 분석 위젯 백엔드: 바이너리 sysLog 파일 + logDB(ID;NAME) 텍스트 파일을
파싱해 log ID별 시계열을 만든다.

레코드 포맷 (실제 reference/sysLog/syslog.bin을 빅엔디안 8바이트로 디코딩해
검증됨 -- Requirement.md "sysLog 분석" 위젯 절 참고): 8바이트 고정, 빅엔디안.
  - byte 0~3: 시간 32bit 비트팩킹 -- day(5bit) / hour(5bit) / min(6bit) / ms(16bit)
  - byte 4~5: log ID (uint16)
  - byte 6~7: ID value (uint16)

x축 좌표는 reference/sysLog/sysLogAnalysis/sysLogAnalyze.py의 세그먼트
이어붙이기 아이디어를 day 포함 절대ms 기준으로 재구성한 것이다: 절대ms(day
포함)가 직전 레코드보다 작아지면("역주행") 새 세그먼트를 시작해 직전 점
바로 다음으로 이어붙이고, 세그먼트 내부에서는 그 세그먼트 시작점 대비
상대 경과ms를 x좌표로 써서 실제 시간 간격을 보존한다(첫 세그먼트는 0부터
시작). 레퍼런스는 time_ms에 day를 포함하지 않아 값이 하루 범위(0~86.4M ms)
안에 묶이므로 raw 값을 그대로 오프셋에 더해도 괜찮지만, 여기서는 day까지
합산해 값이 매우 커질 수 있어(최대 31일) raw 값을 그대로 더하면 세그먼트마다
x축 규모가 기하급수적으로 벌어진다 -- 그래서 상대 경과ms 방식으로 일반화했다.
day 포함 판단 자체는 사양에 명시된 역주행 예시(day 변화)를 정확히 잡아내고,
자정 경과로 인한 정상적인 시간 진행을 역주행으로 오판하지 않기 위함이다.

역주행 외에도 두 조건이 더 있다(Requirement.md "후속 보완 7", 사용자 요청):
day가 0에서 2 이상으로 전이하거나 hour가 0에서 2 이상으로 전이하면 절대ms가
증가하는 방향이라도 새 세그먼트를 연다(_starts_new_segment 참고) -- 0->1
전이(정상 진행)는 분리하지 않는다.

세그먼트 이어붙이기는 (Requirement.md "후속 보완" 참고) **전체 레코드
스트림 순서 기준으로 한 번만** 계산한다 -- 최초 구현은 ID별로 독립적으로
계산했지만(레퍼런스 스크립트와 동일), 사용자가 "선택한 모든 그래프가 하나의
x축 타임라인에 동기화되어야 한다"고 요청해 전역 계산으로 바꿨다. 각 ID의
시리즈는 이 전역 매핑(레코드 seq -> plot_x)을 그대로 재사용하므로, 어떤
ID를 선택해도 같은 x좌표 공간을 공유해 여러 그래프의 확대/축소가 함께
움직인다. 세그먼트 경계 목록(plot_x_start, abs_ms_start)은 프론트가 임의의
x좌표를 실제 day/hour/min/ms로 역산해 축 눈금에 표시할 수 있도록
`/api/syslog/timeline`으로 노출한다.
"""

import bisect
import struct
from dataclasses import dataclass

RECORD_SIZE = 8
DAY_SHIFT = 27
DAY_MASK = 0b11111
HOUR_SHIFT = 22
HOUR_MASK = 0b11111
MIN_SHIFT = 16
MIN_MASK = 0b111111
MS_MASK = 0xFFFF

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000
MS_PER_MIN = 60_000


@dataclass
class SysLogRecord:
    seq: int
    day: int
    hour: int
    minute: int
    ms: int
    abs_ms: int
    log_id: int
    value: int


def parse_log(data: bytes) -> list[SysLogRecord]:
    """8바이트씩 빅엔디안으로 파싱한다. 끝에 8바이트 미만이 남으면(잘린
    파일) 그 나머지는 조용히 버린다 -- 파일 끝단의 자연스러운 경계 상황이지
    파싱 오류가 아니다."""
    records = []
    count = len(data) // RECORD_SIZE
    for i in range(count):
        chunk = data[i * RECORD_SIZE : i * RECORD_SIZE + RECORD_SIZE]
        time_word, log_id, value = struct.unpack(">IHH", chunk)
        day = (time_word >> DAY_SHIFT) & DAY_MASK
        hour = (time_word >> HOUR_SHIFT) & HOUR_MASK
        minute = (time_word >> MIN_SHIFT) & MIN_MASK
        ms = time_word & MS_MASK
        abs_ms = day * MS_PER_DAY + hour * MS_PER_HOUR + minute * MS_PER_MIN + ms
        records.append(SysLogRecord(i, day, hour, minute, ms, abs_ms, log_id, value))
    return records


def parse_db(text: str) -> dict[int, str]:
    """`ID;NAME` 형식 한 줄씩. NAME이 비어있는 줄도 그대로(빈 문자열) 반영한다."""
    db: dict[int, str] = {}
    for line in text.splitlines():
        line = line.strip("\r\n")
        if not line:
            continue
        parts = line.split(";", 1)
        id_str = parts[0].strip()
        if not id_str.isdigit():
            continue
        name = parts[1].strip() if len(parts) > 1 else ""
        db[int(id_str)] = name
    return db


def _starts_new_segment(prev: SysLogRecord, rec: SysLogRecord) -> bool:
    """직전 레코드(prev) 대비 현재 레코드(rec)가 새 세그먼트를 열어야 하는지
    판단한다(Requirement.md "후속 보완 7" 참고). 세 조건 중 하나라도 해당하면
    분리한다:
      1. 절대ms 역주행 (day 포함 절대ms가 직전보다 작아짐)
      2. day가 0에서 2 이상으로 전이 (0->1은 정상 진행이라 제외)
      3. hour가 0에서 2 이상으로 전이 (0->1은 정상 진행이라 제외)
    2/3번은 절대ms가 증가하는 방향이라 1번(역주행)으로는 못 잡는 케이스다.
    반대 방향(2 이상 -> 0)은 그 자체로 절대ms가 감소하므로 이미 1번이 잡아서
    별도 규칙이 필요 없다."""
    if rec.abs_ms < prev.abs_ms:
        return True
    if prev.day == 0 and rec.day >= 2:
        return True
    if prev.hour == 0 and rec.hour >= 2:
        return True
    return False


def build_global_timeline(records: list[SysLogRecord]) -> tuple[dict[int, int], list[dict]]:
    """전체 레코드 스트림을 원본(seq) 순서로 훑으며 세그먼트 이어붙이기
    x좌표(plot_x)를 한 번만 계산한다. 반환값은 (seq -> plot_x 매핑, 세그먼트
    경계 목록)이며, 세그먼트 경계는 각 세그먼트의 시작 plot_x와 그 지점의
    절대ms를 담아 프론트가 임의의 plot_x를 실제 day/hour/min/ms로 역산할 수
    있게 한다. 세그먼트 분리 조건은 _starts_new_segment 참고."""
    plot_x_by_seq: dict[int, int] = {}
    segments: list[dict] = []
    current_offset = 0
    seg_start_abs: int | None = None
    prev_rec: SysLogRecord | None = None
    prev_x = 0
    for rec in records:
        if seg_start_abs is None:
            seg_start_abs = rec.abs_ms
            segments.append({"plot_x_start": 0, "abs_ms_start": rec.abs_ms})
        elif _starts_new_segment(prev_rec, rec):  # type: ignore[arg-type]
            # New segment: start right after the previous segment's last
            # plotted point, then resume using this segment's own real
            # elapsed-time deltas.
            current_offset = prev_x + 1
            seg_start_abs = rec.abs_ms
            segments.append({"plot_x_start": current_offset, "abs_ms_start": rec.abs_ms})
        plot_x = current_offset + (rec.abs_ms - seg_start_abs)
        plot_x_by_seq[rec.seq] = plot_x
        prev_rec = rec
        prev_x = plot_x
    return plot_x_by_seq, segments


def build_series(
    records: list[SysLogRecord], db: dict[int, str]
) -> tuple[dict[int, dict], list[dict], int]:
    """log ID별로 원본 순서를 유지한 채 그룹핑한다. x좌표는 전체 스트림
    기준으로 한 번만 계산한 전역 타임라인(build_global_timeline)을 그대로
    재사용하므로, 어떤 ID를 선택해도 같은 x좌표 공간을 공유한다. 세 번째
    반환값은 전체 plot_x 범위의 최댓값(레코드가 없으면 0)이다."""
    plot_x_by_seq, segments = build_global_timeline(records)
    plot_x_max = max(plot_x_by_seq.values(), default=0)

    by_id: dict[int, list[SysLogRecord]] = {}
    for rec in records:
        by_id.setdefault(rec.log_id, []).append(rec)

    series: dict[int, dict] = {}
    for log_id, recs in by_id.items():
        name = db.get(log_id) or f"Unknown({log_id})"
        points = [
            {
                "seq": rec.seq,
                "x_ms": plot_x_by_seq[rec.seq],
                "value": rec.value,
                "day": rec.day,
                "hour": rec.hour,
                "minute": rec.minute,
                "ms": rec.ms,
            }
            for rec in recs
        ]
        series[log_id] = {"name": name, "count": len(points), "points": points}
    return series, segments, plot_x_max


class SysLogService:
    """log 파일 1개 + DB 파일 1개만 세션에 유지하는 교체 방식 상태 저장소."""

    def __init__(self):
        self._records: list[SysLogRecord] = []
        self._db: dict[int, str] = {}
        self._series_cache: dict[int, dict] | None = None
        self._segments_cache: list[dict] = []
        self._plot_x_max_cache: int = 0
        self._log_filename: str | None = None
        self._db_filename: str | None = None

    def load_log(self, data: bytes, filename: str) -> dict:
        self._records = parse_log(data)
        self._log_filename = filename
        self._series_cache = None
        return {"filename": filename, "record_count": len(self._records)}

    def load_db(self, text: str, filename: str) -> dict:
        self._db = parse_db(text)
        self._db_filename = filename
        self._series_cache = None
        return {"filename": filename, "entry_count": len(self._db)}

    def _ensure_series(self) -> dict[int, dict]:
        if self._series_cache is None:
            self._series_cache, self._segments_cache, self._plot_x_max_cache = build_series(
                self._records, self._db
            )
        return self._series_cache

    def status(self) -> dict:
        return {
            "log_filename": self._log_filename,
            "db_filename": self._db_filename,
            "record_count": len(self._records),
        }

    def _enriched_segments(self) -> list[dict]:
        """세그먼트마다 끝(plot_x_end/abs_ms_end -- 다음 세그먼트 시작 바로 전,
        마지막 세그먼트는 plot_x_max)을 계산해 붙인다. timeline()과
        list_ids()의 구간 필터링이 공유하는 헬퍼."""
        segments = self._segments_cache
        enriched = []
        for i, seg in enumerate(segments):
            plot_x_end = (
                segments[i + 1]["plot_x_start"] - 1 if i + 1 < len(segments) else self._plot_x_max_cache
            )
            abs_ms_end = seg["abs_ms_start"] + (plot_x_end - seg["plot_x_start"])
            enriched.append(
                {
                    "plot_x_start": seg["plot_x_start"],
                    "abs_ms_start": seg["abs_ms_start"],
                    "plot_x_end": plot_x_end,
                    "abs_ms_end": abs_ms_end,
                }
            )
        return enriched

    def list_ids(self, checked_segments: list[int] | None = None) -> list[dict]:
        """ID name 알파벳순(대소문자 무시) 정렬. 이름이 같으면(드묾) ID로
        타이브레이크(프론트가 이름순/ID순을 다시 고를 수 있어 이 순서 자체는
        중요하지 않음). `checked_segments`를 주면(체크된 시간 구간 인덱스
        목록) 각 ID의 count를 그 구간들에 속한 레코드 수로만 계산한다 --
        생략하면(None) 전체 개수."""
        self._ensure_series()
        series = self._series_cache
        if checked_segments is None:
            counts = {log_id: s["count"] for log_id, s in series.items()}
        else:
            checked_set = set(checked_segments)
            # 세그먼트는 plot_x_start 기준 겹치지 않고 이어 붙어 있으므로(모든
            # plot_x가 정확히 하나의 세그먼트에 속함), 이분탐색으로 각 점이
            # 속한 세그먼트 인덱스를 O(log n)에 찾는다.
            starts = [seg["plot_x_start"] for seg in self._segments_cache]
            counts = {}
            for log_id, s in series.items():
                n = 0
                for p in s["points"]:
                    idx = bisect.bisect_right(starts, p["x_ms"]) - 1
                    if idx in checked_set:
                        n += 1
                counts[log_id] = n
        return sorted(
            ({"id": log_id, "name": s["name"], "count": counts.get(log_id, 0)} for log_id, s in series.items()),
            key=lambda x: (x["name"].lower(), x["id"]),
        )

    def get_series(self, ids: list[int]) -> dict[int, dict]:
        series = self._ensure_series()
        return {i: series[i] for i in ids if i in series}

    def timeline(self) -> dict:
        """전역 plot_x 좌표 공간의 세그먼트 경계 + 전체 범위. 프론트가 모든
        그래프의 기본(전체 범위) 뷰와 x축 눈금의 실제 day/hour/min/ms 라벨을
        계산하는 데 쓴다."""
        self._ensure_series()
        return {
            "segments": self._enriched_segments(),
            "plot_x_min": 0,
            "plot_x_max": self._plot_x_max_cache,
        }
