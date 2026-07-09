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
    fd = next(m for m in summary["messages"] if m["name"] == "FdSensorData")
    assert fd["is_fd"] is True
    assert fd["length"] == 32
    assert fd["cycle_time_ms"] == 20


def test_send_type_classification():
    svc = make_service()
    # GenSigSendType OnWrite/OnChange -> event
    assert svc.signal_send_type("DriverCommand", "TurnSignal") == "event"
    assert svc.signal_send_type("DriverCommand", "HornRequest") == "event"
    # no signal attribute -> falls back to message GenMsgSendType (Event)
    assert svc.signal_send_type("DriverCommand", "WiperMode") == "event"
    # cyclic message -> periodic
    assert svc.signal_send_type("EngineData", "EngineSpeed") == "periodic"
    # manual override wins
    svc.set_send_type_override("EngineData", "EngineSpeed", "event")
    assert svc.signal_send_type("EngineData", "EngineSpeed") == "event"


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
    # low nibble of byte 0 must be all ones (4-bit invalid = 0xF)
    assert inv[0] & 0x0F == 0x0F
    # other signals keep their last valid values
    assert inv[1] == 3  # WiperMode
    # invalid value is not persisted in the state
    current = svc.encode_current("DriverCommand")
    assert current[0] & 0x0F == 0x02


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
