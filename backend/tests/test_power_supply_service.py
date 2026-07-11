from power_supply_service import PowerSupplyService


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
