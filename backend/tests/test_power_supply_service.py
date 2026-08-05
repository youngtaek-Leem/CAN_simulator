from power_supply_service import PowerSupplyService


class _FakeInst:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, cmd):
        self.writes.append(cmd)


def _connected() -> PowerSupplyService:
    svc = PowerSupplyService()
    svc.initialized = True
    svc._inst = _FakeInst()
    return svc


def test_starts_uninitialized():
    svc = PowerSupplyService()
    assert svc.initialized is False
    assert svc.status == 0x3


def test_connect_without_hardware_degrades_gracefully():
    # No real VISA instrument attached in CI/dev -- must not raise.
    svc = PowerSupplyService()
    result = svc.connect()
    assert result["initialized"] is False
    assert result["error"]  # some explanatory message, either way


def test_set_power_rejected_when_not_connected():
    svc = PowerSupplyService()
    r = svc.set_power({"command": "ACC_IGN_On"})
    assert r == {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}


def test_status_bitmask_logic_matches_apptest_py():
    """The bitmask transitions themselves (bit0=ACC, bit1=IGN) are pure
    logic, independent of whether real hardware is attached -- exercise them
    directly against a fake VISA instrument so this doesn't depend on lab
    equipment being present."""
    svc = PowerSupplyService()
    svc.initialized = True

    class FakeInst:
        def __init__(self):
            self.writes: list[str] = []

        def write(self, cmd):
            self.writes.append(cmd)

    svc._inst = FakeInst()
    svc.status = 0x3  # ACC+IGN on

    svc.set_power({"command": "ACC_IGN_Off"})
    assert svc.status == 0x0
    svc.set_power({"command": "ACC_On"})
    assert svc.status == 0x1
    svc.set_power({"command": "IGN_On"})
    assert svc.status == 0x3
    svc.set_power({"command": "ACC_Off"})
    assert svc.status == 0x2
    svc.set_power({"command": "IGN_Off"})
    assert svc.status == 0x0
    svc.set_power({"command": "ACC_IGN_On"})
    assert svc.status == 0x3
    assert svc._inst.writes[-1] == ":SOURce:DIGital:OUTPut:DATA 3"


def test_batt_command_writes_apply():
    svc = PowerSupplyService()
    svc.initialized = True

    class FakeInst:
        def __init__(self):
            self.writes: list[str] = []

        def write(self, cmd):
            self.writes.append(cmd)

    svc._inst = FakeInst()
    r = svc.set_power({"command": "BATT", "voltage": "12.6, 5"})
    assert r["ok"] is True
    assert svc._inst.writes == ["APPLy 12.6, 5"]


# ---- 전원 컨트롤 위젯: set_battery/set_acc_ign -----------------------------


def test_set_battery_writes_apply_and_tracks_last_values():
    svc = _connected()
    r = svc.set_battery(12.6, 5)
    assert r["ok"] is True
    assert svc._inst.writes == ["APPLy 12.6,5"]
    assert r["battery_voltage"] == 12.6
    assert r["battery_current"] == 5


def test_set_battery_rejected_when_not_connected():
    svc = PowerSupplyService()
    r = svc.set_battery(12.6, 5)
    assert r == {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}


def test_set_acc_ign_rejects_unknown_command():
    svc = _connected()
    r = svc.set_acc_ign("NOT_A_COMMAND")
    assert r["ok"] is False


def test_set_acc_ign_toggles_bits():
    svc = _connected()
    svc.status = 0x3
    r = svc.set_acc_ign("ACC_Off")
    assert r["ok"] is True
    info = svc.info()
    assert info["acc"] is False
    assert info["ign"] is True


def test_info_includes_acc_ign_booleans():
    svc = PowerSupplyService()
    svc.status = 0x1  # ACC only
    info = svc.info()
    assert info["acc"] is True
    assert info["ign"] is False


# ---- 자동 On/Off 반복 -------------------------------------------------------


def test_onoff_repeat_rejected_when_not_connected():
    svc = PowerSupplyService()
    r = svc.start_onoff_repeat(12.0, 5, 1, 1.0, 0, 1)
    assert r["ok"] is False


def test_onoff_repeat_rejects_non_positive_durations():
    svc = _connected()
    assert svc.start_onoff_repeat(12.0, 5, 0, 1.0, 0, 1)["ok"] is False
    assert svc.start_onoff_repeat(12.0, 5, 1, 1.0, 0, -1)["ok"] is False


def test_onoff_repeat_starts_in_on_phase_immediately():
    svc = _connected()
    r = svc.start_onoff_repeat(12.0, 5, 1, 1.0, 0, 2)
    assert r["ok"] is True
    assert r["onoff"]["enabled"] is True
    assert r["onoff"]["phase"] == "on"
    assert svc._inst.writes == ["APPLy 12.0,5"]


def test_onoff_repeat_flips_phase_after_duration_elapses():
    """Off phase now applies a user-configured off_voltage/off_current
    (not hardcoded 0V/0A) -- exercise a non-zero off value to prove it's
    actually used."""
    svc = _connected()
    svc.start_onoff_repeat(12.0, 5, 1, 1.0, 0.5, 2)
    started_at = svc._onoff_phase_started_at
    svc._inst.writes.clear()

    svc._auto_tick(now=started_at + 0.5)  # still within the 1s "on" phase
    assert svc._inst.writes == []
    assert svc._onoff_phase == "on"

    svc._auto_tick(now=started_at + 1.1)  # past the 1s "on" phase -> flips to off
    assert svc._inst.writes == ["APPLy 1.0,0.5"]
    assert svc._onoff_phase == "off"

    off_started_at = svc._onoff_phase_started_at
    svc._inst.writes.clear()
    svc._auto_tick(now=off_started_at + 2.1)  # past the 2s "off" phase -> back to on
    assert svc._inst.writes == ["APPLy 12.0,5"]
    assert svc._onoff_phase == "on"


def test_onoff_repeat_stops():
    svc = _connected()
    svc.start_onoff_repeat(12.0, 5, 1, 0.0, 0, 1)
    r = svc.stop_onoff_repeat()
    assert r["ok"] is True
    assert r["onoff"]["enabled"] is False
    started_at = svc._onoff_phase_started_at
    svc._inst.writes.clear()
    svc._auto_tick(now=started_at + 100)  # long past due -- must not fire while stopped
    assert svc._inst.writes == []


def test_sweep_rejected_while_onoff_repeat_active():
    svc = _connected()
    svc.start_onoff_repeat(12.0, 5, 1, 0.0, 0, 1)
    r = svc.start_sweep(9.0, 15.0, current=10, leg_s=5)
    assert r["ok"] is False


def test_onoff_repeat_rejected_while_sweep_active():
    svc = _connected()
    svc.start_sweep(9.0, 15.0, current=10, leg_s=5)
    r = svc.start_onoff_repeat(12.0, 5, 1, 0.0, 0, 1)
    assert r["ok"] is False


# ---- 자동 전압 Up/Down 반복 (삼각파) ----------------------------------------


def test_sweep_rejects_non_positive_leg_duration():
    svc = _connected()
    r = svc.start_sweep(9.0, 15.0, current=10, leg_s=0)
    assert r["ok"] is False


def test_sweep_starts_at_low_immediately():
    svc = _connected()
    r = svc.start_sweep(9.0, 15.0, current=10, leg_s=10)
    assert r["ok"] is True
    assert r["sweep"]["enabled"] is True
    assert svc._inst.writes == ["APPLy 9.0,10"]


def test_sweep_ramps_up_then_down_as_a_triangle_wave():
    svc = _connected()
    svc.start_sweep(low=10.0, high=20.0, current=10, leg_s=10)
    t0 = svc._sweep_started_at
    svc._inst.writes.clear()

    svc._auto_tick(now=t0 + 5)  # halfway up the first (up) leg
    assert svc._battery_voltage == 15.0

    svc._auto_tick(now=t0 + 10)  # top of the wave
    assert svc._battery_voltage == 20.0

    svc._auto_tick(now=t0 + 15)  # halfway down the second (down) leg
    assert svc._battery_voltage == 15.0

    svc._auto_tick(now=t0 + 20)  # back to the bottom -- one full cycle done
    assert svc._battery_voltage == 10.0

    svc._auto_tick(now=t0 + 25)  # cycle repeats: halfway up again
    assert svc._battery_voltage == 15.0


def test_sweep_stops():
    svc = _connected()
    svc.start_sweep(low=10.0, high=20.0, current=10, leg_s=10)
    r = svc.stop_sweep()
    assert r["ok"] is True
    assert r["sweep"]["enabled"] is False
    t0 = svc._sweep_started_at
    svc._inst.writes.clear()
    svc._auto_tick(now=t0 + 5)  # would ramp if still enabled
    assert svc._inst.writes == []


def test_disconnect_stops_both_auto_modes():
    svc = _connected()
    svc.start_onoff_repeat(12.0, 5, 1, 0.0, 0, 1)
    svc.disconnect()
    assert svc.info()["onoff"]["enabled"] is False
