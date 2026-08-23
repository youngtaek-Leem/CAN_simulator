"""sysLog 기록을 CAN 테스트 시나리오(test_script_Rev01.json 호환 .json)로
변환한다. Requirement.md "sysLog -> CAN 테스트 스크립트 생성" 절 참고.

- 로그 ID 0~399 범위만 CAN 신호에 해당한다(사용자 사양).
- 신호명 매칭: logDB의 ID 이름과 DBC 신호명이 정확히 같은 게 유일하면
  그걸 쓴다. 아니면 로그 이름을 뒤에서부터 한 글자씩 줄여가며(가장 긴
  것부터) 그 prefix로 시작하는 DBC 신호가 유일하게 하나로 좁혀지는
  지점을 찾는다(신호명 뒤쪽 접미사만 다른 경우, 예: ...Sta vs ...Set,
  를 잡기 위함). 유일하게 못 찾으면 그 log ID는 결과에서 빼고 에러로
  보고한다.
- CANReq/CANEv 선택: DBC의 send_type이 periodic이면 CANReq, event면
  CANEv (dbc_service.signal_send_type과 동일한 분류를 그대로 재사용).
- delay 계산: 같은 시간 구간(세그먼트) 안에서는 실제 경과ms를 delay로
  쓰고, 체크 해제된 구간을 건너뛰어 다른 구간으로 넘어가면(실제
  경과시간이 무의미해짐) 고정 DEFAULT_GAP_MS를 대신 쓴다.
- 신호 묶기(사용자 요청): 같은 메시지에 딜레이 없이(같은 시각) 연속으로
  값이 설정되는 경우, 그 메시지를 한 번 보낼 때 모든 신호값이 동시에
  실려야 실제 동작과 같아진다 -- delay=0으로 이어지고 Message/type이
  같은 연속 스텝들은 낱개 CANReq/CANEv가 아니라
  `{"type":..,"Message":..,"Signals":[{"Signal":..,"Value":..}, ...]}`
  하나로 합친다(test_script_Rev01.json에 이미 있는 다중 신호 블록
  형식과 동일).
"""

import bisect
from dataclasses import dataclass, field
from typing import Callable

ID_RANGE_MIN = 0
ID_RANGE_MAX = 399
DEFAULT_GAP_MS = 200


def build_signal_index(dbc_messages: list[dict]) -> list[tuple[str, str]]:
    """DBC summary(dbc_service.summary()["messages"])에서 (message_name,
    signal_name) 쌍 목록을 뽑는다."""
    return [(m["name"], s["name"]) for m in dbc_messages for s in m["signals"]]


def find_matching_signal(
    log_name: str, signal_index: list[tuple[str, str]]
) -> tuple[tuple[str, str] | None, bool]:
    """반환: ((message_name, signal_name) | None, 정확히 일치했는가).
    못 찾으면 (None, False)."""
    exact = [c for c in signal_index if c[1] == log_name]
    if len(exact) == 1:
        return exact[0], True
    for length in range(len(log_name), 0, -1):
        prefix = log_name[:length]
        candidates = [c for c in signal_index if c[1].startswith(prefix)]
        if len(candidates) == 1:
            sig = candidates[0]
            return sig, sig[1] == log_name
    return None, False


@dataclass
class ScriptGenerationResult:
    steps: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    matched_count: int = 0


def generate_can_test_script(
    records: list,  # SysLogRecord: seq/log_id/value 필드 사용, records는 원본(seq) 순서
    plot_x_by_seq: dict[int, int],
    segments: list[dict],  # plot_x_start 기준 정렬됨(build_global_timeline이 이렇게 만듦)
    checked_segment_indices: set[int],
    log_db: dict[int, str],
    dbc_messages: list[dict],
    signal_send_type: Callable[[str, str], str],
) -> ScriptGenerationResult:
    signal_index = build_signal_index(dbc_messages)
    starts = [seg["plot_x_start"] for seg in segments]

    def segment_index_for(x: int) -> int:
        return max(0, bisect.bisect_right(starts, x) - 1)

    warnings: list[dict] = []
    errors: list[dict] = []
    steps: list[dict] = []
    match_cache: dict[int, tuple[tuple[str, str] | None, bool]] = {}
    matched_ids: set[int] = set()
    prev_x: int | None = None
    prev_seg_idx: int | None = None
    # 방금 낸 CANReq/CANEv 스텝을 가리켜서, delay 없이 같은 메시지가 또
    # 나오면 그 스텝을(필요하면 Signals 배열로 바꿔서) 제자리에서 확장한다.
    last_step: dict | None = None
    last_message: str | None = None
    last_type: str | None = None

    for rec in records:
        if not (ID_RANGE_MIN <= rec.log_id <= ID_RANGE_MAX):
            continue
        x = plot_x_by_seq.get(rec.seq)
        if x is None:
            continue
        seg_idx = segment_index_for(x)
        if seg_idx not in checked_segment_indices:
            continue

        log_id = rec.log_id
        if log_id not in match_cache:
            log_name = log_db.get(log_id)
            if not log_name:
                match_cache[log_id] = (None, False)
                errors.append({"log_id": log_id, "log_name": None, "reason": "logDB에 이름이 없음"})
            else:
                matched, exact = find_matching_signal(log_name, signal_index)
                match_cache[log_id] = (matched, exact)
                if matched is None:
                    errors.append(
                        {"log_id": log_id, "log_name": log_name, "reason": "일치하는 CAN 신호를 찾지 못함"}
                    )
                elif not exact:
                    warnings.append(
                        {
                            "log_id": log_id,
                            "log_name": log_name,
                            "matched_message": matched[0],
                            "matched_signal": matched[1],
                        }
                    )

        matched, _exact = match_cache[log_id]
        if matched is None:
            continue
        matched_ids.add(log_id)
        message_name, signal_name = matched

        send_type = signal_send_type(message_name, signal_name)
        step_type = "CANReq" if send_type == "periodic" else "CANEv"
        value_hex = f"0x{rec.value:X}"

        delay_ms = (x - prev_x) if (prev_x is not None and prev_seg_idx == seg_idx) else None
        if prev_x is not None and delay_ms is None:
            delay_ms = DEFAULT_GAP_MS

        same_moment_same_message = (
            delay_ms == 0 and last_step is not None and last_message == message_name and last_type == step_type
        )

        if same_moment_same_message:
            assert last_step is not None
            if "Signals" not in last_step:
                last_step["Signals"] = [{"Signal": last_step.pop("Signal"), "Value": last_step.pop("Value")}]
            last_step["Signals"].append({"Signal": signal_name, "Value": value_hex})
        else:
            if delay_ms is not None and delay_ms > 0:
                steps.append({"type": "delay", "ms": delay_ms})
            new_step = {"type": step_type, "Message": message_name, "Signal": signal_name, "Value": value_hex}
            steps.append(new_step)
            last_step = new_step
            last_message = message_name
            last_type = step_type

        prev_x = x
        prev_seg_idx = seg_idx

    return ScriptGenerationResult(steps=steps, warnings=warnings, errors=errors, matched_count=len(matched_ids))
