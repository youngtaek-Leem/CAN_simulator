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

전원 컨트롤 위젯 (2026-08-04): 배터리 전압/전류 수동 설정, ACC/IGN 토글 외에
자동 On/Off 반복(전압을 On값<->0V로 주기적으로 전환)과 전압 삼각파 스윕(Low<->High
를 왕복)을 상시 백그라운드 스레드로 구동한다. 둘 다 같은 전압 채널을 다루므로
동시 실행은 막는다 (start_* 쪽에서 서로를 거부).
"""

import threading
import time
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

# How often the auto On/Off-repeat and voltage-sweep background thread
# checks whether it's time to move to the next phase/step. Fine enough for
# a visually smooth ramp without spamming the instrument with near-identical
# APPLy commands every tick.
_AUTO_TICK_S = 0.2


class PowerSupplyService:
    def __init__(self):
        self.status = 0x3  # ACC+IGN On, matches AppTest.py's initial state
        self.initialized = False
        self.error: Optional[str] = None
        self._inst = None

        # Last commanded battery voltage/current -- there is no SCPI
        # read-back (measure) command in use here, so this is purely
        # what we last told the instrument to apply, kept for the widget's
        # display.
        self._battery_voltage: float = 0.0
        self._battery_current: float = 0.0

        # 자동 On/Off 반복: battery output alternates between a configured
        # "on" voltage/current and a configured "off" voltage/current,
        # spending on_s/off_s in each phase.
        self._onoff_enabled = False
        self._onoff_on_voltage = 0.0
        self._onoff_on_current = 0.0
        self._onoff_on_s = 0.0
        self._onoff_off_voltage = 0.0
        self._onoff_off_current = 0.0
        self._onoff_off_s = 0.0
        self._onoff_phase = "off"  # "on" | "off" -- phase currently applied
        self._onoff_phase_started_at = 0.0

        # 자동 전압 Up/Down 반복: triangle wave between low and high, each
        # leg (up or down) taking leg_s seconds, current held constant.
        self._sweep_enabled = False
        self._sweep_low = 0.0
        self._sweep_high = 0.0
        self._sweep_current = 0.0
        self._sweep_leg_s = 0.0
        self._sweep_started_at = 0.0

        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()

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
            self._battery_voltage, self._battery_current = 14.4, 10.0
            return {**self.info(), "idn": idn.strip()}
        except Exception as exc:
            self.error = str(exc)
            self.initialized = False
            return self.info()

    def disconnect(self) -> dict:
        self._onoff_enabled = False
        self._sweep_enabled = False
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
        self._inst = None
        self.initialized = False
        return self.info()

    def info(self) -> dict:
        return {
            "initialized": self.initialized,
            "error": self.error,
            "status_bits": self.status,
            "acc": bool(self.status & 0x01),
            "ign": bool(self.status & 0x02),
            "battery_voltage": self._battery_voltage,
            "battery_current": self._battery_current,
            "onoff": {
                "enabled": self._onoff_enabled,
                "on_voltage": self._onoff_on_voltage,
                "on_current": self._onoff_on_current,
                "on_s": self._onoff_on_s,
                "off_voltage": self._onoff_off_voltage,
                "off_current": self._onoff_off_current,
                "off_s": self._onoff_off_s,
                "phase": self._onoff_phase,
            },
            "sweep": {
                "enabled": self._sweep_enabled,
                "low": self._sweep_low,
                "high": self._sweep_high,
                "current": self._sweep_current,
                "leg_s": self._sweep_leg_s,
            },
        }

    def set_power(self, block: dict) -> dict:
        """Legacy entry point for the test-runner's Power step (JSON script
        `{"type": "Power", "command": "BATT", "voltage": "14.4,5"}` or an
        ACC/IGN command name) -- kept exactly as before, string-formatted
        voltage field and all, since existing test scripts already depend on
        this shape."""
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

    # ---- 전원 컨트롤 위젯: battery voltage/current, ACC/IGN toggles --------

    def _apply_battery(self, voltage: float, current: float) -> None:
        """Shared low-level write for every voltage-setting path (manual OK
        button, on/off repeat, sweep) -- also tracks the last-commanded
        values for the widget's display, since there's no read-back
        command."""
        self._inst.write(f"APPLy {voltage},{current}")
        self._battery_voltage = voltage
        self._battery_current = current

    def set_battery(self, voltage: float, current: float) -> dict:
        """전원 컨트롤 위젯의 전압/전류 입력 + OK 버튼."""
        if not self.initialized:
            return {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}
        try:
            self._apply_battery(voltage, current)
            return {"ok": True, **self.info()}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def set_acc_ign(self, command: str) -> dict:
        """전원 컨트롤 위젯의 ACC/IGN 토글 스위치."""
        if command not in ("ACC_On", "ACC_Off", "IGN_On", "IGN_Off", "ACC_IGN_On", "ACC_IGN_Off"):
            return {"ok": False, "reason": f"알 수 없는 명령: {command}"}
        return self.set_power({"command": command})

    # ---- 자동 On/Off 반복 -------------------------------------------------

    def start_onoff_repeat(
        self,
        on_voltage: float,
        on_current: float,
        on_s: float,
        off_voltage: float,
        off_current: float,
        off_s: float,
    ) -> dict:
        if not self.initialized:
            return {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}
        if self._sweep_enabled:
            return {"ok": False, "reason": "전압 스윕이 실행 중이라 On/Off 반복을 시작할 수 없습니다"}
        if on_s <= 0 or off_s <= 0:
            return {"ok": False, "reason": "On/Off 시간은 0보다 커야 합니다"}
        try:
            self._onoff_on_voltage = on_voltage
            self._onoff_on_current = on_current
            self._onoff_on_s = on_s
            self._onoff_off_voltage = off_voltage
            self._onoff_off_current = off_current
            self._onoff_off_s = off_s
            self._onoff_phase = "on"
            self._onoff_phase_started_at = time.time()
            self._apply_battery(on_voltage, on_current)
            self._onoff_enabled = True
            return {"ok": True, **self.info()}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def stop_onoff_repeat(self) -> dict:
        self._onoff_enabled = False
        return {"ok": True, **self.info()}

    # ---- 자동 전압 Up/Down 반복 (삼각파) -----------------------------------

    def start_sweep(self, low: float, high: float, current: float, leg_s: float) -> dict:
        if not self.initialized:
            return {"ok": False, "reason": "파워서플라이가 연결되어 있지 않습니다"}
        if self._onoff_enabled:
            return {"ok": False, "reason": "On/Off 반복이 실행 중이라 전압 스윕을 시작할 수 없습니다"}
        if leg_s <= 0:
            return {"ok": False, "reason": "시간은 0보다 커야 합니다"}
        try:
            self._sweep_low = low
            self._sweep_high = high
            self._sweep_current = current
            self._sweep_leg_s = leg_s
            self._sweep_started_at = time.time()
            self._apply_battery(low, current)
            self._sweep_enabled = True
            return {"ok": True, **self.info()}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def stop_sweep(self) -> dict:
        self._sweep_enabled = False
        return {"ok": True, **self.info()}

    # ---- background timer --------------------------------------------------

    def _auto_loop(self) -> None:
        """Runs for the process lifetime; cheap no-op tick when neither auto
        mode is enabled (same pattern as audio_service's rotation timer)."""
        while True:
            time.sleep(_AUTO_TICK_S)
            try:
                self._auto_tick()
            except Exception:
                pass  # never let the timer thread die over one bad tick

    def _auto_tick(self, now: Optional[float] = None) -> None:
        """Exposed with an optional `now` override so tests can drive phase
        transitions deterministically instead of sleeping for real."""
        if not self.initialized or self._inst is None:
            return
        now = time.time() if now is None else now
        if self._onoff_enabled:
            self._tick_onoff(now)
        if self._sweep_enabled:
            self._tick_sweep(now)

    def _tick_onoff(self, now: float) -> None:
        duration = self._onoff_on_s if self._onoff_phase == "on" else self._onoff_off_s
        if now - self._onoff_phase_started_at < duration:
            return
        if self._onoff_phase == "on":
            self._onoff_phase = "off"
            self._apply_battery(self._onoff_off_voltage, self._onoff_off_current)
        else:
            self._onoff_phase = "on"
            self._apply_battery(self._onoff_on_voltage, self._onoff_on_current)
        self._onoff_phase_started_at = now

    def _tick_sweep(self, now: float) -> None:
        cycle = self._sweep_leg_s * 2
        if cycle <= 0:
            return
        pos = (now - self._sweep_started_at) % cycle
        if pos <= self._sweep_leg_s:
            frac = pos / self._sweep_leg_s
            voltage = self._sweep_low + (self._sweep_high - self._sweep_low) * frac
        else:
            frac = (pos - self._sweep_leg_s) / self._sweep_leg_s
            voltage = self._sweep_high - (self._sweep_high - self._sweep_low) * frac
        voltage = round(voltage, 2)
        # avoid spamming near-identical APPLy commands every tick
        if abs(voltage - self._battery_voltage) >= 0.05:
            self._apply_battery(voltage, self._sweep_current)
