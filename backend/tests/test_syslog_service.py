from pathlib import Path

from syslog_service import SysLogRecord, SysLogService, build_global_timeline, build_series, parse_db, parse_log

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference" / "sysLog"


def test_parse_log_matches_known_records():
    data = (REFERENCE_DIR / "syslog.bin").read_bytes()
    records = parse_log(data)

    assert len(data) % 8 == 0
    assert len(records) == len(data) // 8

    # Hand-verified against a raw hex dump of the first few 8-byte chunks
    # (see Requirement.md "sysLog 분석" 위젯 절).
    expected = [
        (13, 5, 13, 11171, 49, 1),
        (13, 5, 13, 11671, 49, 2),
        (13, 5, 13, 12171, 49, 1),
        (13, 5, 13, 12691, 49, 2),
        (13, 5, 13, 13024, 92, 1),
        (13, 5, 13, 13144, 92, 0),
        (13, 5, 13, 13369, 1102, 1),
        (13, 5, 13, 13370, 1103, 1),
        (13, 5, 13, 13379, 1509, 1),
        (13, 5, 13, 13453, 87, 2),
    ]
    for rec, (day, hour, minute, ms, log_id, value) in zip(records, expected):
        assert (rec.day, rec.hour, rec.minute, rec.ms, rec.log_id, rec.value) == (
            day,
            hour,
            minute,
            ms,
            log_id,
            value,
        )


def test_parse_log_truncated_trailing_bytes_ignored():
    data = (REFERENCE_DIR / "syslog.bin").read_bytes()
    records_full = parse_log(data)
    records_truncated = parse_log(data + b"\x01\x02\x03")
    assert records_truncated == records_full


def test_parse_db_from_reference_file():
    text = (REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8")
    db = parse_db(text)

    assert db[1] == "AMP_Analog_OutputCh_Select"
    assert db[1600] == "tampered_area_status"
    # id 5 has an empty NAME column in the reference file
    assert db[5] == ""
    assert len(db) == 344


def test_build_series_names_and_unknown_id():
    records = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1, value=7),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=999, value=3),
    ]
    db = {1: "AMP_Analog_OutputCh_Select"}

    series, _segments, _plot_x_max = build_series(records, db)

    assert series[1]["name"] == "AMP_Analog_OutputCh_Select"
    assert series[999]["name"] == "Unknown(999)"


def test_build_series_segment_offset_on_time_reversal():
    # Synthetic single-ID stream: 3day 2h 5min 12000ms, then a reversal to
    # 1day 0h 0min 200ms -- the exact example from the approved spec.
    abs_ms_1 = 3 * 86_400_000 + 2 * 3_600_000 + 5 * 60_000 + 12_000
    abs_ms_2 = 1 * 86_400_000 + 0 + 0 + 200
    abs_ms_3 = abs_ms_2 + 500  # continues forward within the new segment

    records = [
        SysLogRecord(seq=0, day=3, hour=2, minute=5, ms=12000, abs_ms=abs_ms_1, log_id=1, value=10),
        SysLogRecord(seq=1, day=1, hour=0, minute=0, ms=200, abs_ms=abs_ms_2, log_id=1, value=20),
        SysLogRecord(seq=2, day=1, hour=0, minute=0, ms=700, abs_ms=abs_ms_3, log_id=1, value=30),
    ]

    points = build_series(records, {})[0][1]["points"]

    # First point: start of the first segment -- x == 0.
    assert points[0]["x_ms"] == 0
    # Reversal point: placed right after the previous segment's last x.
    assert points[1]["x_ms"] == 1
    # Third point stays within the new segment: spacing reflects its own
    # real elapsed-time delta from the reversal point (500ms).
    assert points[2]["x_ms"] == points[1]["x_ms"] + 500
    # x is strictly increasing across the whole plotted series.
    assert points[0]["x_ms"] < points[1]["x_ms"] < points[2]["x_ms"]


def test_build_series_no_reversal_uses_relative_elapsed_ms():
    records = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1, value=1),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=250, abs_ms=250, log_id=1, value=2),
        SysLogRecord(seq=2, day=0, hour=0, minute=1, ms=0, abs_ms=60_000, log_id=1, value=3),
    ]

    points = build_series(records, {})[0][1]["points"]

    # x is elapsed ms since this ID's first occurrence within the segment.
    assert [p["x_ms"] for p in points] == [0, 150, 59_900]


def test_build_series_shares_one_global_timeline_across_ids():
    # Requirement.md "후속 보완": 선택한 모든 그래프가 하나의 x축에 동기화
    # 되어야 하므로, x좌표는 ID별이 아니라 전체 레코드 스트림의 원본 순서
    # 기준으로 전역적으로 계산된다. seq1(id2, abs_ms=50)이 seq0(id1,
    # abs_ms=100)보다 작아 전역 스트림 관점에서는 역주행이지만, id1만 놓고
    # 보면(seq0->seq2) 역주행이 아니다 -- 전역 계산이 아니라면 이 케이스를
    # 구분할 수 없다.
    records = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1, value=1),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=50, abs_ms=50, log_id=2, value=9),
        SysLogRecord(seq=2, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=1, value=2),
    ]

    series, segments, plot_x_max = build_series(records, {})

    # id1: 첫 점(seq0)은 세그먼트0 시작(x=0). seq2는 seq1이 만든 새 세그먼트
    # (offset=1) 기준 상대경과(200-50=150) => x=1+150=151.
    assert [p["x_ms"] for p in series[1]["points"]] == [0, 151]
    # id2: seq1이 전역 역주행 지점이라 x=1(직전 세그먼트 마지막 x=0 다음).
    assert [p["x_ms"] for p in series[2]["points"]] == [1]

    assert segments == [
        {"plot_x_start": 0, "abs_ms_start": 100},
        {"plot_x_start": 1, "abs_ms_start": 50},
    ]
    assert plot_x_max == 151


def test_build_series_plot_x_max_and_segments_empty_when_no_records():
    series, segments, plot_x_max = build_series([], {})
    assert series == {}
    assert segments == []
    assert plot_x_max == 0


def test_service_list_ids_sorted_alphabetically():
    svc = SysLogService()
    svc.load_log((REFERENCE_DIR / "syslog.bin").read_bytes(), "syslog.bin")
    svc.load_db((REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8"), "db.txt")

    names = [info["name"] for info in svc.list_ids()]

    assert names == sorted(names, key=str.lower)
    assert names[0] < "B"  # "AMP_..." 계열이 맨 앞


def test_service_timeline_reflects_real_syslog_bin():
    svc = SysLogService()
    svc.load_log((REFERENCE_DIR / "syslog.bin").read_bytes(), "syslog.bin")
    svc.load_db((REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8"), "db.txt")

    timeline = svc.timeline()

    assert timeline["plot_x_min"] == 0
    assert timeline["plot_x_max"] > 0
    assert len(timeline["segments"]) >= 1
    assert timeline["segments"][0]["plot_x_start"] == 0
    # every series point's x_ms must fall inside [plot_x_min, plot_x_max]
    for info in svc.list_ids():
        series = svc.get_series([info["id"]])[info["id"]]
        for p in series["points"]:
            assert 0 <= p["x_ms"] <= timeline["plot_x_max"]


def test_service_timeline_segments_have_consistent_start_end():
    svc = SysLogService()
    svc.load_log((REFERENCE_DIR / "syslog.bin").read_bytes(), "syslog.bin")
    svc.load_db((REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8"), "db.txt")

    segments = svc.timeline()["segments"]
    plot_x_max = svc.timeline()["plot_x_max"]

    assert len(segments) > 1  # 이 샘플 파일은 역주행이 여러 번 있음(알려진 특성)
    for i, seg in enumerate(segments):
        assert seg["plot_x_end"] >= seg["plot_x_start"]
        assert seg["abs_ms_end"] - seg["abs_ms_start"] == seg["plot_x_end"] - seg["plot_x_start"]
        if i + 1 < len(segments):
            assert seg["plot_x_end"] == segments[i + 1]["plot_x_start"] - 1
        else:
            assert seg["plot_x_end"] == plot_x_max


def _mk(seq: int, day: int, hour: int, minute: int, ms: int, log_id: int = 1, value: int = 0) -> SysLogRecord:
    abs_ms = day * 86_400_000 + hour * 3_600_000 + minute * 60_000 + ms
    return SysLogRecord(seq, day, hour, minute, ms, abs_ms, log_id, value)


def test_build_global_timeline_splits_on_day_zero_to_two_or_more():
    # 절대ms는 증가하는 방향(day 0 -> 5)이라 기존 역주행 조건으로는 못 잡는 케이스.
    records = [
        _mk(0, day=0, hour=1, minute=0, ms=0),
        _mk(1, day=5, hour=0, minute=0, ms=0),
    ]
    _plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 2
    assert segments[1]["abs_ms_start"] == records[1].abs_ms


def test_build_global_timeline_no_split_on_day_zero_to_one():
    # 0 -> 1은 정상적인 하루 진행이므로 분리하지 않는다.
    records = [
        _mk(0, day=0, hour=1, minute=0, ms=0),
        _mk(1, day=1, hour=0, minute=0, ms=0),
    ]
    _plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 1


def test_build_global_timeline_splits_on_hour_zero_to_two_or_more():
    records = [
        _mk(0, day=3, hour=0, minute=5, ms=0),
        _mk(1, day=3, hour=4, minute=0, ms=0),
    ]
    _plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 2
    assert segments[1]["abs_ms_start"] == records[1].abs_ms


def test_build_global_timeline_no_split_on_hour_zero_to_one():
    records = [
        _mk(0, day=3, hour=0, minute=5, ms=0),
        _mk(1, day=3, hour=1, minute=0, ms=0),
    ]
    _plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 1


def test_build_global_timeline_still_splits_on_reversal():
    # 회귀 확인: 기존 역주행 조건은 day/hour 조건 추가 후에도 그대로 동작해야 함.
    records = [
        _mk(0, day=3, hour=2, minute=5, ms=12_000),
        _mk(1, day=1, hour=0, minute=0, ms=200),
    ]
    _plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 2


def _load_real_service() -> SysLogService:
    svc = SysLogService()
    svc.load_log((REFERENCE_DIR / "syslog.bin").read_bytes(), "syslog.bin")
    svc.load_db((REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8"), "db.txt")
    return svc


def test_list_ids_no_filter_matches_full_counts():
    svc = _load_real_service()
    unfiltered = {info["id"]: info["count"] for info in svc.list_ids()}
    all_indices = list(range(len(svc.timeline()["segments"])))
    filtered_all = {info["id"]: info["count"] for info in svc.list_ids(all_indices)}
    assert unfiltered == filtered_all


def test_list_ids_empty_segment_filter_gives_zero_counts():
    svc = _load_real_service()
    filtered_none = svc.list_ids([])
    assert len(filtered_none) > 0
    assert all(info["count"] == 0 for info in filtered_none)


def test_list_ids_single_segment_count_matches_manual_tally():
    svc = _load_real_service()
    target_id = 49  # Warn_Sound_TikTok -- 사용자가 실제로 검증에 쓴 ID
    segments = svc.timeline()["segments"]
    seg_idx = 0
    seg = segments[seg_idx]

    full_points = svc.get_series([target_id])[target_id]["points"]
    expected = sum(1 for p in full_points if seg["plot_x_start"] <= p["x_ms"] <= seg["plot_x_end"])

    filtered = {info["id"]: info["count"] for info in svc.list_ids([seg_idx])}
    assert filtered[target_id] == expected
    assert expected > 0  # 이 세그먼트에 실제로 해당 ID 레코드가 있어야 의미 있는 검증


def test_pair_to_float_basic():
    from syslog_service import _pair_to_float

    # C211 420F -> -36.31451 (실제 reference.sysLog.syslog.bin의 A2B_CH1 값)
    assert abs(_pair_to_float(0xC211, 0x420F) - (-36.31451)) < 0.0001
    # C2EF F830 -> -119.98474 (가장 흔한 A2B_CH1 값)
    assert abs(_pair_to_float(0xC2EF, 0xF830) - (-119.98474)) < 0.001
    # 0000 0000 -> 0.0
    assert _pair_to_float(0x0000, 0x0000) == 0.0
    # NaN(7FC0 0000), 양수(4211 C211), +Inf(7F80 0000) -> -120
    assert _pair_to_float(0x7FC0, 0x0000) == -120.0  # NaN
    assert _pair_to_float(0x4211, 0xC211) == -120.0  # 양수
    assert _pair_to_float(0x7F80, 0x0000) == -120.0  # +Inf


def test_build_series_paired_float_1300_real():
    svc = _load_real_service()
    s = svc.get_series([1300])[1300]
    assert s["name"] == "A2B_CH1"
    assert len(s["points"]) > 0
    raw_count = sum(1 for r in svc._records if r.log_id == 1300)
    # 1ms 검증으로 단일 무시 시 count가 raw//2보다 약간 적어질 수 있음
    assert raw_count // 2 - 100 <= s["count"] <= raw_count // 2 + 50
    # 모든 값은 [-120, 0] (0 포함, -120은 범위 밖 클램프)
    for p in s["points"]:
        assert -120 <= p["value"] <= 0, f"값 {p['value']}가 범위 밖 (seq={p['seq']})"
    # 재동기화 전 57%보다 높아야 함, 1ms 필터로 약간 낮아질 수 있어 85%로 완화
    valid = sum(1 for p in s["points"] if p["value"] != -120.0)
    assert valid / s["count"] > 0.85, f"valid {valid}/{s['count']} too low"


def test_build_series_paired_float_out_of_range_clamped():
    """범위를 벗어나는 조합(NaN, Inf, 양수, -120 미만)은 -120으로, 0000 0000은 0으로."""
    from syslog_service import build_series

    recs = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0x7F80),  # +Inf (high)
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=101, abs_ms=101, log_id=1300, value=0x0000),  # low -> +Inf -> -120
        SysLogRecord(seq=2, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=1300, value=0xC211),  # -36.31 high
        SysLogRecord(seq=3, day=0, hour=0, minute=0, ms=201, abs_ms=201, log_id=1300, value=0x420F),  # -36.31 low
        SysLogRecord(seq=4, day=0, hour=0, minute=0, ms=300, abs_ms=300, log_id=1300, value=0x7FC0),  # NaN high
        SysLogRecord(seq=5, day=0, hour=0, minute=0, ms=301, abs_ms=301, log_id=1300, value=0x0000),  # NaN low -> -120
        SysLogRecord(seq=6, day=0, hour=0, minute=0, ms=400, abs_ms=400, log_id=1300, value=0x0000),  # 0000 high
        SysLogRecord(seq=7, day=0, hour=0, minute=0, ms=401, abs_ms=401, log_id=1300, value=0x0000),  # 0000 low -> 0
    ]
    series, _, _ = build_series(recs, {1300: "A2B_CH1"})
    pts = series[1300]["points"]
    assert len(pts) == 4  # 8개 레코드 -> 4개 점
    assert pts[0]["value"] == -120.0   # +Inf -> -120
    assert abs(pts[1]["value"] - (-36.31451)) < 0.0001
    assert pts[2]["value"] == -120.0   # NaN -> -120
    assert pts[3]["value"] == 0.0      # 0000 0000 -> 0


def test_build_series_paired_float_odd_ignored():
    """레코드 수가 홀수이면 마지막 하나는 무시된다."""
    from syslog_service import build_series

    recs = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0xC211),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=101, abs_ms=101, log_id=1300, value=0x420F),
        SysLogRecord(seq=2, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=1300, value=0xC211),  # 홀수 잔류 -> 무시
    ]
    series, _, _ = build_series(recs, {1300: "A2B_CH1"})
    assert series[1300]["count"] == 1


def test_build_series_paired_float_1ms_and_single_ignore():
    """1ms 이내 2개가 아니면 단일로 무시, 정상 페어는 변환."""
    from syslog_service import build_series

    # dt=1 정상: 2점이 -120으로 변환됨
    recs_ok = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0xC2F0),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=101, abs_ms=101, log_id=1300, value=0x0000),
    ]
    s_ok = build_series(recs_ok, {1300: "A2B_CH1"})[0][1300]
    assert len(s_ok["points"]) == 1
    assert s_ok["points"][0]["value"] == -120.0

    # dt=2 → 1ms 밖 → 단일 무시 (0점)
    recs_single = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0xC2F0),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=102, abs_ms=102, log_id=1300, value=0x0000),
    ]
    s_single = build_series(recs_single, {1300: "A2B_CH1"})[0][1300]
    assert len(s_single["points"]) == 0

    # dt 음수(역주행)도 무시
    recs_neg = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=101, abs_ms=101, log_id=1300, value=0xC2F0),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0x0000),
    ]
    s_neg = build_series(recs_neg, {1300: "A2B_CH1"})[0][1300]
    assert len(s_neg["points"]) == 0

    # 단일 1개만 있는 경우도 무시
    recs_one = [SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0xC2F0)]
    s_one = build_series(recs_one, {1300: "A2B_CH1"})[0][1300]
    assert len(s_one["points"]) == 0


def test_build_series_paired_float_resync():
    """stray 1개가 끼어도 다음 정상 페어는 1ms 검증 후 복구된다."""
    from syslog_service import build_series

    recs = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=1300, value=0xC2F0),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=101, abs_ms=101, log_id=1300, value=0x0000),  # -120 (dt1)
        SysLogRecord(seq=2, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=1300, value=0x0000),  # stray (dt to next 1)
        SysLogRecord(seq=3, day=0, hour=0, minute=0, ms=201, abs_ms=201, log_id=1300, value=0x0000),  # 0000 0000 -> 0 (dt1)
    ]
    series, _, _ = build_series(recs, {1300: "A2B_CH1"})
    pts = series[1300]["points"]
    assert len(pts) == 2
    assert pts[0]["value"] == -120.0
    assert pts[1]["value"] == 0.0


def test_build_series_paired_float_non_paired_unchanged():
    """ID 1300~1399가 아닌 레코드는 기존 정수 동작이 유지된다."""
    from syslog_service import build_series

    recs = [
        SysLogRecord(seq=0, day=0, hour=0, minute=0, ms=100, abs_ms=100, log_id=49, value=1),
        SysLogRecord(seq=1, day=0, hour=0, minute=0, ms=200, abs_ms=200, log_id=49, value=2),
        SysLogRecord(seq=2, day=0, hour=0, minute=0, ms=300, abs_ms=300, log_id=49, value=3),
    ]
    series, _, _ = build_series(recs, {49: "Warn_Sound_TikTok"})
    assert series[49]["count"] == 3
    assert series[49]["points"][0]["value"] == 1
    assert series[49]["points"][1]["value"] == 2
    assert series[49]["points"][2]["value"] == 3
