from pathlib import Path

from syslog_script_generator import (
    DEFAULT_GAP_MS,
    find_matching_signal,
    generate_can_test_script,
)
from syslog_service import SysLogRecord, SysLogService, build_global_timeline

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference" / "sysLog"


def _mk(seq: int, day: int, hour: int, minute: int, ms: int, log_id: int, value: int) -> SysLogRecord:
    abs_ms = day * 86_400_000 + hour * 3_600_000 + minute * 60_000 + ms
    return SysLogRecord(seq, day, hour, minute, ms, abs_ms, log_id, value)


def _fixed_send_type(mapping: dict[str, str]):
    def fn(message_name: str, signal_name: str) -> str:
        return mapping.get(f"{message_name}.{signal_name}", "event")

    return fn


# ---- find_matching_signal ---------------------------------------------------


def test_find_matching_signal_exact_match_preferred():
    signal_index = [("MsgA", "Warn_Sound_TikTok"), ("MsgB", "Warn_Sound_TikTokStatus")]
    matched, exact = find_matching_signal("Warn_Sound_TikTok", signal_index)
    assert matched == ("MsgA", "Warn_Sound_TikTok")
    assert exact is True


def test_find_matching_signal_prefix_fallback_for_differing_suffix():
    # 사용자가 준 예시: 로그는 ...Sta, DBC는 ...Set
    signal_index = [("BDC_FD_10_200ms", "PDW_FrCtWrngSndSet"), ("Other", "Unrelated")]
    matched, exact = find_matching_signal("PDW_FrCtWrngSndSta", signal_index)
    assert matched == ("BDC_FD_10_200ms", "PDW_FrCtWrngSndSet")
    assert exact is False


def test_find_matching_signal_ambiguous_prefix_fails():
    # "AB"의 모든 prefix("AB", "A")에서 ABC/ABD 둘 다 계속 걸리므로(prefix를
    # 줄일수록 후보가 늘어나기만 함) 끝까지 유일하게 못 좁혀진다.
    signal_index = [("MsgA", "ABC"), ("MsgB", "ABD")]
    matched, exact = find_matching_signal("AB", signal_index)
    assert matched is None
    assert exact is False


def test_find_matching_signal_no_match_at_all():
    signal_index = [("MsgA", "Completely_Different")]
    matched, exact = find_matching_signal("Warn_Sound_TikTok", signal_index)
    assert matched is None
    assert exact is False


def test_find_matching_signal_ambiguous_exact_across_messages_falls_back_to_prefix():
    # 이름이 완전히 같은 신호가 두 메시지에 있으면(드묾), exact 판정은 실패하고
    # prefix 루프로 넘어간다 -- 하지만 prefix를 줄여도 같은 두 후보만 계속
    # 걸리므로(둘 다 그 prefix로 시작) 결국 못 찾는다.
    signal_index = [("MsgA", "Dup_Signal"), ("MsgB", "Dup_Signal")]
    matched, exact = find_matching_signal("Dup_Signal", signal_index)
    assert matched is None


# ---- generate_can_test_script -----------------------------------------------


def test_generate_script_basic_flow_with_delay_and_step_types():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=49, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=500, log_id=49, value=2),
        _mk(2, day=1, hour=0, minute=1, ms=0, log_id=100, value=3),
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {49: "Warn_Sound_TikTok", 100: "AMP_AudioMode"}
    dbc_messages = [
        {"name": "CLU_AMP_01_200ms", "signals": [{"name": "Warn_Sound_TikTok"}]},
        {"name": "HU_AMP_02_00ms", "signals": [{"name": "AMP_AudioMode"}]},
    ]
    send_type = _fixed_send_type(
        {"CLU_AMP_01_200ms.Warn_Sound_TikTok": "event", "HU_AMP_02_00ms.AMP_AudioMode": "periodic"}
    )
    checked = set(range(len(segments)))

    result = generate_can_test_script(records, plot_x_by_seq, segments, checked, log_db, dbc_messages, send_type)

    assert result.errors == []
    assert result.warnings == []
    assert result.matched_count == 2  # 서로 다른 log_id 2개(49, 100)
    # 스텝: [CANEv(49=1), delay(500), CANEv(49=2), delay(30000), CANReq(100=3)]
    assert result.steps[0] == {"type": "CANEv", "Message": "CLU_AMP_01_200ms", "Signal": "Warn_Sound_TikTok", "Value": "0x1"}
    assert result.steps[1] == {"type": "delay", "ms": 500}
    assert result.steps[2]["Value"] == "0x2"
    assert result.steps[3] == {"type": "delay", "ms": 59_500}  # 86400500ms -> 86460000ms 사이 실제 경과
    assert result.steps[4] == {"type": "CANReq", "Message": "HU_AMP_02_00ms", "Signal": "AMP_AudioMode", "Value": "0x3"}


def test_generate_script_reports_warning_for_prefix_match():
    records = [_mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=3)]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "PDW_FrCtWrngSndSta"}
    dbc_messages = [{"name": "BDC_FD_10_200ms", "signals": [{"name": "PDW_FrCtWrngSndSet"}]}]
    send_type = _fixed_send_type({})
    checked = set(range(len(segments)))

    result = generate_can_test_script(records, plot_x_by_seq, segments, checked, log_db, dbc_messages, send_type)

    assert result.errors == []
    assert len(result.warnings) == 1
    assert result.warnings[0] == {
        "log_id": 1,
        "log_name": "PDW_FrCtWrngSndSta",
        "matched_message": "BDC_FD_10_200ms",
        "matched_signal": "PDW_FrCtWrngSndSet",
    }
    assert len(result.steps) == 1


def test_generate_script_reports_error_for_unmatched_id_and_excludes_it():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=100, log_id=2, value=1),
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "Known_Signal", 2: "Totally_Unknown"}
    dbc_messages = [{"name": "MsgA", "signals": [{"name": "Known_Signal"}]}]
    send_type = _fixed_send_type({})
    checked = set(range(len(segments)))

    result = generate_can_test_script(records, plot_x_by_seq, segments, checked, log_db, dbc_messages, send_type)

    assert len(result.errors) == 1
    assert result.errors[0]["log_id"] == 2
    assert result.errors[0]["log_name"] == "Totally_Unknown"
    assert len(result.steps) == 1  # id=1만 성공


def test_generate_script_reports_error_for_id_missing_from_db():
    records = [_mk(0, day=1, hour=0, minute=0, ms=0, log_id=5, value=1)]  # 0~399 범위 안
    plot_x_by_seq, segments = build_global_timeline(records)
    result = generate_can_test_script(records, plot_x_by_seq, segments, {0}, {}, [], _fixed_send_type({}))
    assert len(result.errors) == 1
    assert result.errors[0]["log_name"] is None
    assert result.steps == []


def test_generate_script_id_range_filter_excludes_out_of_range():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=400, value=1),  # 범위 밖(0~399 아님)
        _mk(1, day=1, hour=0, minute=0, ms=100, log_id=399, value=1),
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {400: "Out_Of_Range_Sig", 399: "In_Range_Sig"}
    dbc_messages = [
        {"name": "M1", "signals": [{"name": "Out_Of_Range_Sig"}]},
        {"name": "M2", "signals": [{"name": "In_Range_Sig"}]},
    ]
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, _fixed_send_type({})
    )
    assert result.matched_count == 1
    assert result.steps[0]["Signal"] == "In_Range_Sig"


def test_generate_script_uses_default_gap_across_unchecked_segment():
    # 역주행으로 세그먼트가 둘로 나뉘고, 두 세그먼트 다 체크돼 있어도 서로
    # 다른 세그먼트 사이 delay는 실제 경과가 아니라 DEFAULT_GAP_MS.
    records = [
        _mk(0, day=3, hour=2, minute=5, ms=12_000, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=200, log_id=1, value=2),  # 역주행 -> 새 세그먼트
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    assert len(segments) == 2
    log_db = {1: "Sig"}
    dbc_messages = [{"name": "M", "signals": [{"name": "Sig"}]}]
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0, 1}, log_db, dbc_messages, _fixed_send_type({})
    )
    assert result.steps[1] == {"type": "delay", "ms": DEFAULT_GAP_MS}


def test_generate_script_skips_unchecked_segment_records_entirely():
    records = [
        _mk(0, day=3, hour=2, minute=5, ms=12_000, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=200, log_id=1, value=2),  # 역주행 -> 새 세그먼트(인덱스 1)
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "Sig"}
    dbc_messages = [{"name": "M", "signals": [{"name": "Sig"}]}]
    # 세그먼트 0만 체크 -> 두 번째 레코드(세그먼트 1)는 아예 빠져야 함
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, _fixed_send_type({})
    )
    assert len(result.steps) == 1


# ---- 같은 메시지 + 딜레이 0 -> Signals 배열로 병합 -------------------------


def test_generate_script_merges_same_message_zero_delay_into_signals_array():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=0xA),
        _mk(1, day=1, hour=0, minute=0, ms=0, log_id=2, value=0xA),  # id=1과 정확히 같은 시각
        _mk(2, day=1, hour=0, minute=0, ms=0, log_id=3, value=0x19),  # 역시 같은 시각
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "AMP_BalanceSet", 2: "AMP_FadeSet", 3: "AMP_MainVolumeSet"}
    dbc_messages = [
        {
            "name": "HU_AMP_04_00ms",
            "signals": [{"name": "AMP_BalanceSet"}, {"name": "AMP_FadeSet"}, {"name": "AMP_MainVolumeSet"}],
        }
    ]
    send_type = _fixed_send_type({})  # 전부 event -> CANEv

    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, send_type
    )

    assert len(result.steps) == 1  # 세 스텝이 하나로 합쳐짐
    step = result.steps[0]
    assert step["type"] == "CANEv"
    assert step["Message"] == "HU_AMP_04_00ms"
    assert "Signal" not in step and "Value" not in step
    assert step["Signals"] == [
        {"Signal": "AMP_BalanceSet", "Value": "0xA"},
        {"Signal": "AMP_FadeSet", "Value": "0xA"},
        {"Signal": "AMP_MainVolumeSet", "Value": "0x19"},
    ]


def test_generate_script_does_not_merge_different_messages_even_at_same_time():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=0, log_id=2, value=1),  # 같은 시각이지만 다른 메시지
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "SigA", 2: "SigB"}
    dbc_messages = [{"name": "MsgA", "signals": [{"name": "SigA"}]}, {"name": "MsgB", "signals": [{"name": "SigB"}]}]
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, _fixed_send_type({})
    )
    assert len(result.steps) == 2
    assert "Signals" not in result.steps[0]
    assert "Signals" not in result.steps[1]


def test_generate_script_does_not_merge_same_message_when_delay_nonzero():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=100, log_id=2, value=1),  # 100ms 뒤 -> 병합 안 됨
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "SigA", 2: "SigB"}
    dbc_messages = [{"name": "MsgA", "signals": [{"name": "SigA"}, {"name": "SigB"}]}]
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, _fixed_send_type({})
    )
    assert len(result.steps) == 3  # step, delay(100), step
    assert result.steps[1] == {"type": "delay", "ms": 100}
    assert "Signals" not in result.steps[0] and "Signals" not in result.steps[2]


def test_generate_script_does_not_merge_when_step_type_differs():
    records = [
        _mk(0, day=1, hour=0, minute=0, ms=0, log_id=1, value=1),
        _mk(1, day=1, hour=0, minute=0, ms=0, log_id=2, value=1),
    ]
    plot_x_by_seq, segments = build_global_timeline(records)
    log_db = {1: "SigA", 2: "SigB"}
    dbc_messages = [{"name": "MsgA", "signals": [{"name": "SigA"}, {"name": "SigB"}]}]
    send_type = _fixed_send_type({"MsgA.SigA": "periodic", "MsgA.SigB": "event"})
    result = generate_can_test_script(
        records, plot_x_by_seq, segments, {0}, log_db, dbc_messages, send_type
    )
    assert len(result.steps) == 2
    assert result.steps[0]["type"] == "CANReq"
    assert result.steps[1]["type"] == "CANEv"
    assert result.steps[0]["Value"] == "0x1"


# ---- SysLogService.generate_test_script (end-to-end with real files) -------


def test_service_generate_test_script_end_to_end_with_real_syslog():
    svc = SysLogService()
    svc.load_log((REFERENCE_DIR / "syslog.bin").read_bytes(), "syslog.bin")
    svc.load_db((REFERENCE_DIR / "sysLogDB_RS4PE_260727.txt").read_text(encoding="utf-8"), "db.txt")

    all_segment_indices = list(range(len(svc.timeline()["segments"])))
    # 신호가 하나뿐인 DBC를 쓰면 무관한 log ID의 극단적으로 짧은 prefix가
    # 우연히 그 유일한 신호와 "유일하게" 매칭돼버릴 수 있어(경쟁하는 다른
    # 후보가 아예 없으므로) 전체 matched_count를 의미 있게 특정할 수 없다 --
    # 그래서 여기서는 id=49(Warn_Sound_TikTok, 정확히 일치)가 정확히
    # CANEv로, 정확 일치(경고 없이)로 나오는지만 검증한다.
    dbc_messages = [{"name": "CLU_AMP_01_200ms", "signals": [{"name": "Warn_Sound_TikTok"}]}]
    result = svc.generate_test_script(all_segment_indices, dbc_messages, _fixed_send_type({}))

    assert result["steps"][0] == {"type": "ID", "num": "1", "Cycle": 1}
    tiktok_steps = [s for s in result["steps"] if s.get("Signal") == "Warn_Sound_TikTok"]
    assert len(tiktok_steps) > 0
    assert all(s["type"] == "CANEv" for s in tiktok_steps)  # _fixed_send_type({}) 기본값 event
    assert all(s["Message"] == "CLU_AMP_01_200ms" for s in tiktok_steps)
    assert not any(w["log_id"] == 49 for w in result["warnings"])  # 정확 일치라 경고 대상 아님
    assert result["matched_count"] >= 1
