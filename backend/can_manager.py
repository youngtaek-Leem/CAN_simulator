"""CAN bus connection and RX buffering.

Supported interfaces: pcan (PEAK PCAN), vector (Vector CANcase), virtual
(in-process bus for development/testing without hardware). Each supports
classic CAN 2.0 and CAN-FD (up to 64 data bytes, optional bitrate switch).

RX frames are buffered in a bounded deque by a python-can Notifier thread and
drained periodically by the WebSocket broadcaster, so a burst of short-cycle
messages never blocks the event loop.

CAN-FD bit timing: PCAN has no simple "nominal/data bitrate" constructor
argument like Vector does, so it is driven here via explicit
``can.BitTimingFd(...)`` register values (FD_CLOCK_HZ / FD_NOM_* /
FD_DATA_* below) rather than a requested bitrate -- these fully determine
the resulting nominal/data bitrate, so PCAN-FD connections ignore the
bitrate/data_bitrate the caller passed in and always use this fixed timing
profile (500 kbit/s nominal @ 80% sample point, 1 Mbit/s data @ 75% sample
point, 80 MHz clock -- user-provided default, 2026-08-26). Change these
constants (or expose them through ``connect()``) if a different adapter
needs different timing.
"""

import threading
from collections import deque
from typing import Any, Optional

import can

SUPPORTED_INTERFACES = ("virtual", "pcan", "vector")

# CAN-FD bit timing defaults for PCAN (Vector takes bitrate/data_bitrate
# directly and needs none of this) -- raw BitTimingFd register values,
# not sample-point-derived, per real-adapter configuration confirmed by
# the user (2026-08-26): 80 MHz clock, nominal 500 kbit/s @ 80% sample
# point, data 1 Mbit/s @ 75% sample point.
FD_CLOCK_HZ = 80_000_000
FD_NOM_BRP = 16
FD_NOM_TSEG1 = 7
FD_NOM_TSEG2 = 2
FD_NOM_SJW = 2
FD_DATA_BRP = 2
FD_DATA_TSEG1 = 29
FD_DATA_TSEG2 = 10
FD_DATA_SJW = 8
DEFAULT_FD_DATA_BITRATE = 2_000_000  # Vector takes a plain data_bitrate int, unlike PCAN's register timing above
MAX_CLASSIC_DATA_LEN = 8


class _BufferListener(can.Listener):
    def __init__(self, buffer: deque, counter: dict):
        self._buffer = buffer
        self._counter = counter

    def on_message_received(self, msg: can.Message) -> None:
        self._buffer.append(msg)
        self._counter["rx"] += 1

    def on_error(self, exc: Exception) -> None:  # pragma: no cover
        self._counter["errors"] += 1


class CanManager:
    def __init__(self, rx_buffer_size: int = 20000):
        self.bus: Optional[can.BusABC] = None
        self.notifier: Optional[can.Notifier] = None
        self._rx_buffer: deque = deque(maxlen=rx_buffer_size)
        self._lock = threading.Lock()
        self.counters = {"rx": 0, "tx": 0, "errors": 0}
        self.config: dict[str, Any] = {}
        self.fd_enabled = False

    @property
    def connected(self) -> bool:
        return self.bus is not None

    def connect(
        self,
        interface: str,
        channel: str,
        bitrate: int = 500000,
        receive_own_messages: bool = True,
        fd: bool = False,
        data_bitrate: Optional[int] = None,
    ) -> dict:
        if interface not in SUPPORTED_INTERFACES:
            raise ValueError(f"unsupported interface: {interface}")
        self.disconnect()
        kwargs: dict[str, Any] = {
            "interface": interface,
            "channel": channel,
            "receive_own_messages": receive_own_messages,
        }
        if interface == "virtual":
            kwargs["protocol"] = can.CanProtocol.CAN_FD if fd else can.CanProtocol.CAN_20
        elif interface == "vector":
            kwargs["bitrate"] = bitrate
            if fd:
                kwargs["fd"] = True
                data_bitrate = data_bitrate or DEFAULT_FD_DATA_BITRATE
                kwargs["data_bitrate"] = data_bitrate
        elif interface == "pcan":
            if fd:
                fd_timing = can.BitTimingFd(
                    f_clock=FD_CLOCK_HZ,
                    nom_brp=FD_NOM_BRP,
                    nom_tseg1=FD_NOM_TSEG1,
                    nom_tseg2=FD_NOM_TSEG2,
                    nom_sjw=FD_NOM_SJW,
                    data_brp=FD_DATA_BRP,
                    data_tseg1=FD_DATA_TSEG1,
                    data_tseg2=FD_DATA_TSEG2,
                    data_sjw=FD_DATA_SJW,
                )
                kwargs["timing"] = fd_timing
                # the fixed register values above fully determine the actual
                # bitrate, which may not match whatever the caller requested
                bitrate = fd_timing.nom_bitrate
                data_bitrate = fd_timing.data_bitrate
            else:
                kwargs["bitrate"] = bitrate
        self.bus = can.Bus(**kwargs)
        self.notifier = can.Notifier(
            self.bus, [_BufferListener(self._rx_buffer, self.counters)], timeout=0.1
        )
        self.fd_enabled = fd
        self.config = {
            "interface": interface,
            "channel": channel,
            "bitrate": bitrate,
            "receive_own_messages": receive_own_messages,
            "fd": fd,
            "data_bitrate": data_bitrate if fd else None,
            "epoch_aligned": self._check_epoch_aligned(interface),
        }
        return self.status()

    @staticmethod
    def _check_epoch_aligned(interface: str) -> bool:
        """Whether Message.timestamp on this connection is wall-clock epoch
        seconds (required for comparing it against another epoch-based
        timeline, e.g. the CAN-audio latency widget's audio waveform, which
        is stamped with time.time()). virtual/Vector always are (see module
        docstring). PCAN only is if the optional `uptime` package resolved a
        real boot-time epoch at import time -- otherwise its timestamps are
        relative to device boot and this returns False."""
        if interface != "pcan":
            return True
        import can.interfaces.pcan.pcan as _pcan_mod

        return _pcan_mod.boottimeEpoch != 0

    def disconnect(self) -> None:
        if self.notifier is not None:
            self.notifier.stop()
            self.notifier = None
        if self.bus is not None:
            self.bus.shutdown()
            self.bus = None
        self.config = {}
        self.fd_enabled = False
        self._rx_buffer.clear()
        self.counters.update({"rx": 0, "tx": 0, "errors": 0})

    def send(
        self,
        arbitration_id: int,
        data: bytes,
        is_extended_id: bool = False,
        is_fd: bool = False,
        bitrate_switch: bool = False,
    ) -> None:
        if self.bus is None:
            raise RuntimeError("CAN bus is not connected")
        if len(data) > MAX_CLASSIC_DATA_LEN and not self.fd_enabled:
            raise ValueError(
                f"{len(data)}바이트 페이로드는 CAN-FD 연결에서만 전송할 수 있습니다 "
                f"(현재 버스는 classic CAN, 최대 {MAX_CLASSIC_DATA_LEN}바이트)"
            )
        if not self.fd_enabled:
            # HS-CAN(classic) 연결에서는 행/DBC의 FD 설정과 무관하게 항상
            # classic 프레임으로 나가도록 강제한다. 그렇지 않으면 classic
            # 버스로 연결된 상태에서도 is_fd=True 프레임 전송이 시도되어
            # (예: PCAN classic Write API에 FD 플래그가 실려) 드라이버 오류나
            # 프레임 손상으로 이어질 수 있다.
            is_fd = False
            bitrate_switch = False
        msg = can.Message(
            arbitration_id=arbitration_id,
            data=data,
            is_extended_id=is_extended_id,
            is_fd=is_fd or len(data) > MAX_CLASSIC_DATA_LEN,
            bitrate_switch=bitrate_switch,
        )
        with self._lock:
            self.bus.send(msg)
            self.counters["tx"] += 1

    def add_listener(self, listener: can.Listener) -> None:
        """Attach an extra listener (e.g. a test-runner CANResp watcher) that
        gets every RX message alongside the main buffer, without draining or
        otherwise disturbing it."""
        if self.notifier is None:
            raise RuntimeError("CAN bus is not connected")
        self.notifier.add_listener(listener)

    def remove_listener(self, listener: can.Listener) -> None:
        if self.notifier is not None:
            self.notifier.remove_listener(listener)

    def drain_rx(self, max_messages: int = 2000) -> list[can.Message]:
        out = []
        for _ in range(max_messages):
            try:
                out.append(self._rx_buffer.popleft())
            except IndexError:
                break
        return out

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "config": self.config,
            "counters": dict(self.counters),
        }
