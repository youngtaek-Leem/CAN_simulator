"""Generate sample BLF/ASC logs from sample.dbc for replay testing.

Usage: python make_sample_logs.py  (run with the backend venv python)
Creates sample.asc and sample.blf: ~5 s of EngineData(10ms)/BodyStatus(50ms)/
VehicleSpeed(100ms)/FdSensorData(20ms, CAN-FD 32 bytes) traffic plus a few
event-driven DriverCommand frames. DriverCommand frames are marked
Tx-direction so the replay Tx filter can be demonstrated.
"""

import math
from pathlib import Path

import can
import cantools

HERE = Path(__file__).resolve().parent
db = cantools.database.load_file(HERE / "sample.dbc")

engine = db.get_message_by_name("EngineData")
body = db.get_message_by_name("BodyStatus")
speed = db.get_message_by_name("VehicleSpeed")
command = db.get_message_by_name("DriverCommand")
fd_sensor = db.get_message_by_name("FdSensorData")


def build_messages() -> list[can.Message]:
    msgs = []

    def add(t, message, signals, is_rx=True):
        msgs.append(
            can.Message(
                arbitration_id=message.frame_id,
                data=message.encode(signals, strict=False),
                is_extended_id=False,
                is_fd=message.is_fd,
                bitrate_switch=message.is_fd,
                timestamp=t,
                is_rx=is_rx,
                channel=0,
            )
        )

    t = 0.0
    while t < 5.0:
        ms = round(t * 1000)
        if ms % 10 == 0:
            rpm = 800 + 2000 * (1 + math.sin(t * 2)) / 2
            add(t, engine, {"EngineSpeed": rpm, "EngineTemp": 85, "ThrottlePos": 20})
        if ms % 50 == 0:
            add(t, body, {"DoorOpen": 0, "LightLevel": 120.5, "BatteryVoltage": 12.6})
        if ms % 100 == 0:
            add(t, speed, {"Speed": 60 + 20 * math.sin(t), "Direction": 0})
        if ms % 20 == 0:
            add(
                t,
                fd_sensor,
                {
                    "Pressure": 1000 + 20 * math.sin(t * 3),
                    "Temperature": 25 + 5 * math.sin(t),
                    "Humidity": 45,
                    "VibrationX": 0.1 * math.sin(t * 10),
                },
            )
        t = round(t + 0.01, 3)

    # event frames in Tx direction (valid + invalid 30 ms later)
    for t_evt, turn in ((1.0, 1), (2.5, 2), (4.0, 4)):
        add(t_evt, command, {"TurnSignal": turn, "HornRequest": 0, "WiperMode": 0},
            is_rx=False)
        msgs.append(
            can.Message(
                arbitration_id=command.frame_id,
                data=bytes([0x0F, 0x00, 0, 0, 0, 0, 0, 0]),  # TurnSignal invalid
                is_extended_id=False,
                timestamp=t_evt + 0.03,
                is_rx=False,
                channel=0,
            )
        )

    msgs.sort(key=lambda m: m.timestamp)
    return msgs


def main() -> None:
    messages = build_messages()
    for name, writer_cls in (("sample.asc", can.ASCWriter), ("sample.blf", can.BLFWriter)):
        path = HERE / name
        with writer_cls(str(path)) as writer:
            for msg in messages:
                writer.on_message_received(msg)
        print(f"wrote {path.name}: {len(messages)} frames")


if __name__ == "__main__":
    main()
