from conftest import SAMPLES_DIR

from dbc_service import DbcService


def make_service() -> DbcService:
    svc = DbcService()
    svc.load_string((SAMPLES_DIR / "sample.dbc").read_text(encoding="utf-8"), "sample.dbc")
    return svc


def test_summary_structure():
    svc = make_service()
    summary = svc.summary()
    assert summary["loaded"] is True
    names = {m["name"] for m in summary["messages"]}
    assert names == {
        "EngineData",
        "VehicleSpeed",
        "DriverCommand",
        "BodyStatus",
        "FdSensorData",
    }
    assert set(summary["nodes"]) == {"ECU_A", "ECU_B", "TESTER"}
    engine = next(m for m in summary["messages"] if m["name"] == "EngineData")
    assert engine["cycle_time_ms"] == 10
    assert engine["send_type"] == "Cyclic"
    assert engine["is_fd"] is False
    assert engine["senders"] == ["ECU_A"]
    turn = next(
        s
        for m in summary["messages"]
        if m["name"] == "DriverCommand"
        for s in m["signals"]
        if s["name"] == "TurnSignal"
    )
    assert turn["invalid_raw"] == 0xF
    assert turn["choices"][1] == "Left"
    assert turn["is_signed"] is False
    fd = next(m for m in summary["messages"] if m["name"] == "FdSensorData")
    assert fd["is_fd"] is True
    assert fd["length"] == 32
    assert fd["cycle_time_ms"] == 20


def test_send_type_classification():
    svc = make_service()
    # message comment tagged "[P]" -> every signal on it is periodic
    assert svc.signal_send_type("EngineData", "EngineSpeed") == "periodic"
    assert svc.signal_send_type("VehicleSpeed", "Speed") == "periodic"
    # message comment tagged "[EC]" (anything other than P/PE) -> event
    assert svc.signal_send_type("DriverCommand", "TurnSignal") == "event"
    assert svc.signal_send_type("DriverCommand", "HornRequest") == "event"
    assert svc.signal_send_type("DriverCommand", "WiperMode") == "event"
    # manual override wins over the comment tag
    svc.set_send_type_override("EngineData", "EngineSpeed", "event")
    assert svc.signal_send_type("EngineData", "EngineSpeed") == "event"


def test_send_type_no_comment_tag_defaults_to_event():
    svc = DbcService()
    svc.load_string(
        """
        BU_: ECU_A
        BO_ 100 Untagged: 8 ECU_A
         SG_ Foo : 0|8@1+ (1,0) [0|255] "" ECU_A
        """,
        "untagged.dbc",
    )
    # no CM_ BO_ comment at all (e.g. NM_* network-management frames) -> event
    assert svc.signal_send_type("Untagged", "Foo") == "event"


def test_encode_decode_roundtrip():
    svc = make_service()
    data = svc.encode_with_values("EngineData", {"EngineSpeed": 3000, "EngineTemp": 90})
    decoded = svc.decode(0x100, data)
    assert decoded["name"] == "EngineData"
    assert decoded["signals"]["EngineSpeed"] == 3000
    assert decoded["signals"]["EngineTemp"] == 90


def test_invalid_value_encoding():
    svc = make_service()
    svc.encode_with_values("DriverCommand", {"TurnSignal": 2, "WiperMode": 3})
    inv = svc.encode_invalid("DriverCommand", "TurnSignal")
    # the whole frame reads as invalid -- not just the named signal
    assert inv[0] & 0x0F == 0x0F  # TurnSignal (4-bit) invalid
    assert (inv[0] >> 4) & 0x1 == 1  # HornRequest (1-bit) invalid
    assert inv[1] == 0xFF  # WiperMode (8-bit) invalid, not its last value of 3
    # invalid value is not persisted in the state
    current = svc.encode_current("DriverCommand")
    assert current[0] & 0x0F == 0x02


def test_event_send_forces_other_signals_invalid_every_time():
    svc = make_service()
    # send TurnSignal -- the other two signals (never set) must be invalid
    data = svc.encode_with_values("DriverCommand", {"TurnSignal": 2})
    assert (data[0] >> 4) & 0x1 == 1  # HornRequest invalid
    assert data[1] == 0xFF  # WiperMode invalid

    # now send WiperMode -- TurnSignal/HornRequest must ALSO be invalid in
    # this frame, even though TurnSignal was just set moments ago: an Event
    # send has no "memory" of previously-sent real values for other signals
    data2 = svc.encode_with_values("DriverCommand", {"WiperMode": 5})
    assert data2[0] & 0x0F == 0x0F  # TurnSignal invalid, not 2
    assert (data2[0] >> 4) & 0x1 == 1  # HornRequest invalid
    assert data2[1] == 5  # WiperMode itself carries the real value

    # the 30ms-later invalid follow-up forces the WHOLE frame invalid,
    # including the signal that was just set
    inv = svc.encode_invalid("DriverCommand", "WiperMode")
    assert inv[0] & 0x0F == 0x0F
    assert (inv[0] >> 4) & 0x1 == 1
    assert inv[1] == 0xFF

    # substitution is transmit-only -- persisted state keeps each signal's
    # real value even though no single frame ever showed them together
    current = svc.encode_current("DriverCommand")
    assert current[0] & 0x0F == 0x02  # TurnSignal
    assert current[1] == 5  # WiperMode


def test_untouched_periodic_sibling_stays_zero_not_invalid():
    svc = make_service()
    # EngineData is periodic ([P] tag) -- periodic has no invalid concept, so
    # an untouched sibling should stay at raw 0, not be substituted
    data = svc.encode_with_values("EngineData", {"EngineSpeed": 1000})
    decoded = svc.decode_raw(0x100, data)
    assert decoded["EngineTemp"] == 0


def test_state_persists_between_writes():
    svc = make_service()
    svc.encode_with_values("EngineData", {"EngineSpeed": 1000})
    data = svc.encode_with_values("EngineData", {"EngineTemp": 50})
    decoded = svc.decode(0x100, data)
    assert decoded["signals"]["EngineSpeed"] == 1000
    assert decoded["signals"]["EngineTemp"] == 50


def test_fd_message_encode_decode_32_bytes():
    svc = make_service()
    data = svc.encode_with_values(
        "FdSensorData",
        {"Pressure": 1013.2, "Temperature": 25.0, "Humidity": 45.0, "VibrationX": 0.5},
    )
    assert len(data) == 32
    decoded = svc.decode(0x500, data)
    assert decoded["name"] == "FdSensorData"
    assert decoded["signals"]["Pressure"] == 1013.2
    assert decoded["signals"]["Humidity"] == 45.0


def test_encode_initial_uses_start_value_or_invalid():
    svc = DbcService()
    svc.load_string(
        """
        BU_: ECU_A
        BO_ 100 InitMsg: 8 ECU_A
         SG_ WithStart : 0|8@1+ (1,0) [0|255] "" ECU_A
         SG_ NoStart : 8|4@1+ (1,0) [0|15] "" ECU_A
        BA_DEF_ SG_ "GenSigStartValue" INT 0 100000;
        BA_ "GenSigStartValue" SG_ 100 WithStart 16;
        """,
        "init.dbc",
    )
    data = svc.encode_initial("InitMsg")
    assert len(data) == 8
    assert data[0] == 16  # DBC 정의 초기값
    assert data[1] & 0x0F == 0x0F  # 미정의 신호는 Invalid(비트幅 최대값)


def test_encode_initial_all_undefined_is_all_invalid():
    # sample.dbc에는 GenSigStartValue가 없어 전 신호가 Invalid가 된다
    svc = make_service()
    data = svc.encode_initial("DriverCommand")
    assert data[0] & 0x0F == 0x0F  # TurnSignal (4-bit)
    assert (data[0] >> 4) & 0x1 == 1  # HornRequest (1-bit)
    assert data[1] == 0xFF  # WiperMode (8-bit)
    fd = svc.encode_initial("FdSensorData")
    assert len(fd) == 32


def test_summary_signal_default_value():
    svc = make_service()
    summary = svc.summary()
    driver = next(m for m in summary["messages"] if m["name"] == "DriverCommand")
    by_name = {s["name"]: s for s in driver["signals"]}
    # GenSigStartValue 미정의 -> Invalid raw의 물리값
    assert by_name["TurnSignal"]["default_value"] == 0xF
    assert by_name["HornRequest"]["default_value"] == 1
    assert by_name["WiperMode"]["default_value"] == 0xFF


def test_summary_signal_default_value_uses_start_value():
    svc = DbcService()
    svc.load_string(
        """
        BU_: ECU_A
        BO_ 100 InitMsg: 8 ECU_A
         SG_ WithStart : 0|8@1+ (10,5) [0|255] "" ECU_A
         SG_ NoStart : 8|8@1+ (1,0) [0|255] "" ECU_A
        BA_DEF_ SG_ "GenSigStartValue" INT 0 100000;
        BA_ "GenSigStartValue" SG_ 100 WithStart 16;
        """,
        "init.dbc",
    )
    by_name = {s["name"]: s for s in svc.summary()["messages"][0]["signals"]}
    assert by_name["WithStart"]["default_value"] == 16 * 10 + 5
    assert by_name["NoStart"]["default_value"] == 0xFF


# ---- Transmit priority (widget value -> last valid -> initial -> 0x0) ------
# ---- + Event multi-valid sends (all widgets share this via dbc_service) ---


def test_event_send_keeps_all_widget_values_valid():
    svc = make_service()
    # 위젯이 한 번에 2개 신호값을 보내면 둘 다 valid, 나머지만 invalid
    data = svc.encode_with_values("DriverCommand", {"TurnSignal": 2, "WiperMode": 3})
    assert data[0] & 0x0F == 0x02
    assert data[1] == 3
    assert (data[0] >> 4) & 0x1 == 1  # HornRequest invalid


def test_event_raw_send_forces_others_invalid():
    svc = make_service()
    # Random/주기 경로(raw 단위)도 Event 규칙 동일: 타신호 invalid
    data = svc.encode_with_raw_values("DriverCommand", {"TurnSignal": 2})
    assert data[0] & 0x0F == 0x02
    assert (data[0] >> 4) & 0x1 == 1
    assert data[1] == 0xFF
    # 영속 상태는 실제값 유지
    current = svc.encode_current("DriverCommand")
    assert current[0] & 0x0F == 0x02


def test_periodic_untouched_sibling_uses_initial_value():
    svc = DbcService()
    svc.load_string(
        """
        BU_: ECU_A
        CM_ BO_ 100 "[P] periodic";
        BO_ 100 PMsg: 8 ECU_A
         SG_ WithStart : 0|8@1+ (1,0) [0|255] "" ECU_A
         SG_ NoStart : 8|8@1+ (1,0) [0|255] "" ECU_A
        BA_DEF_ SG_ "GenSigStartValue" INT 0 100000;
        BA_ "GenSigStartValue" SG_ 100 WithStart 16;
        """,
        "init.dbc",
    )
    data = svc.encode_with_values("PMsg", {"WithStart": 7})
    raw = svc.decode_raw(0x64, data)
    assert raw["WithStart"] == 7
    assert raw["NoStart"] == 0  # 미정의 초기값 -> 0x0
    # last valid 유지
    data = svc.encode_with_values("PMsg", {"NoStart": 1})
    raw = svc.decode_raw(0x64, data)
    assert raw["WithStart"] == 7
    assert raw["NoStart"] == 1


def test_periodic_nonforced_invalid_falls_back():
    svc = make_service()
    # 강제 표시 없는 bit-max 영속값은 last valid가 아니므로 초기값/0으로 대체
    # (sample.dbc 무 GenSigStartValue -> 0x0)
    svc.set_raw_signal_value("EngineData", "EngineSpeed", 0xFFFF)
    data = svc.encode_with_values("EngineData", {"EngineTemp": 50})
    raw = svc.decode_raw(0x100, data)
    assert raw["EngineSpeed"] == 0
    assert raw["EngineTemp"] == 90  # 물리 50 / scale 1, offset -40 -> raw 90


def test_periodic_forced_invalid_kept_until_valid_write():
    svc = make_service()
    # 버튼 INVALID 토글(의도적 invalid)은 계속 송신
    svc.set_raw_invalid("EngineData", "EngineSpeed")
    data = svc.encode_with_values("EngineData", {"EngineTemp": 50})
    raw = svc.decode_raw(0x100, data)
    assert raw["EngineSpeed"] == 0xFFFF
    # valid 쓰기가 표시 해제 (물리 1000 / scale 0.25 -> raw 4000)
    svc.encode_with_values("EngineData", {"EngineSpeed": 1000})
    data = svc.encode_with_values("EngineData", {"EngineTemp": 50})
    raw = svc.decode_raw(0x100, data)
    assert raw["EngineSpeed"] == 4000


def test_explicit_bitmax_transmits_but_not_remembered():
    svc = make_service()
    # 명시 전송은 그대로 나가지만 last valid로는 기억되지 않음
    # (물리 16383.75 / scale 0.25 -> raw 65535 = 16bit bit-max)
    data = svc.encode_with_values("EngineData", {"EngineSpeed": 16383.75})
    raw = svc.decode_raw(0x100, data)
    assert raw["EngineSpeed"] == 0xFFFF
    data = svc.encode_with_values("EngineData", {"EngineTemp": 50})
    raw = svc.decode_raw(0x100, data)
    assert raw["EngineSpeed"] == 0


def test_periodic_never_written_sibling_uses_initial_value():
    svc = DbcService()
    svc.load_string(
        """
        BU_: ECU_A
        CM_ BO_ 100 "[P] periodic";
        BO_ 100 PMsg: 8 ECU_A
         SG_ WithStart : 0|8@1+ (1,0) [0|255] "" ECU_A
         SG_ NoStart : 8|8@1+ (1,0) [0|255] "" ECU_A
        BA_DEF_ SG_ "GenSigStartValue" INT 0 100000;
        BA_ "GenSigStartValue" SG_ 100 WithStart 16;
        """,
        "init.dbc",
    )
    data = svc.encode_with_values("PMsg", {"NoStart": 1})
    raw = svc.decode_raw(0x64, data)
    assert raw["WithStart"] == 16  # 기록 없는 신호는 초기값
