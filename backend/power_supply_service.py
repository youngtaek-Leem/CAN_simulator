"""Programmable DC power supply control (PyVISA/SCPI), ported from
Automation/AppTest.py's PowerSupply class. Drives an ACC/IGN digital output
bitmask to simulate the vehicle ignition switch, matching how the original
(already field-validated) test bench does it -- the SCPI commands and
bitmask logic below are intentionally unchanged from AppTest.py.

Optional hardware: pyvisa may not be installed, or no VISA resource may be
attached. In either case `initialized` stays False and set_power() calls are
reported as skipped rather than raising, so a script mixing Power steps with
CAN-only steps still runs its CAN portion (same pattern as
test_runner_service's Phase-1 handling of unimplemented step types).
"""

from typing import Optional

try:
    import pyvisa

    _PYVISA_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via _PYVISA_AVAILABLE branch
    _PYVISA_AVAILABLE = False

# ACC/IGN bitmask transitions, ported as-is from AppTest.py's
# PowerSupply.setPower (bit0 = ACC, bit1 = IGN).
_STATUS_COMMANDS = {
    "ACC_IGN_On": lambda s: s | 0x03,
    "ACC_On": lambda s: s | 0x01,
    "IGN_On": lambda s: s | 0x02,
    "ACC_Off": lambda s: s & 0x02,
    "IGN_Off": lambda s: s & 0x01,
    "ACC_IGN_Off": lambda s: s & 0x00,
}


class PowerSupplyService:
    def __init__(self):
        self.status = 0x3  # ACC+IGN On, matches AppTest.py's initial state
        self.initialized = False
        self.error: Optional[str] = None
        self._inst = None

    def connect(self) -> dict:
        if not _PYVISA_AVAILABLE:
            self.error = "pyvisa가 설치되어 있지 않습니다"
            self.initialized = False
            return self.info()
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
            if not resources:
                self.error = "VISA 리소스(파워서플라이)를 찾을 수 없습니다"
                self.initialized = False
                return self.info()
            self._inst = rm.open_resource(resources[0])
            idn = self._inst.query("*IDN?")
            self._inst.write("APPLy 14.4, 10")
            self._inst.write(f":SOURce:DIGital:OUTPut:DATA {self.status}")
            self.initialized = True
            self.error = None
            return {**self.info(), "idn": idn.strip()}
        except Exception as exc:
            self.error = str(exc)
            self.initialized = False
            return self.info()

    def disconnect(self) -> dict:
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
        self._inst = None
        self.initialized = False
        return self.info()

    def info(self) -> dict:
        return {"initialized": self.initialized, "error": self.error, "status_bits": self.status}

    def set_power(self, block: dict) -> dict:
        if not self.initialized:
            return {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}
        cmd = block.get("command")
        try:
            if cmd == "BATT":
                self._inst.write(f"APPLy {block['voltage']}")
            else:
                new_status = _STATUS_COMMANDS.get(cmd, lambda _s: 0x3)(self.status)
                self._inst.write(f":SOURce:DIGital:OUTPut:DATA {new_status}")
                self.status = new_status
            return {"ok": True, "status_bits": self.status}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
