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
import time
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

# Minimum inter-CF gap (seconds) forced on multi-frame sends when the caller
# asks for "as fast as the ECU allows" (STmin checkbox off). PCAN needs a
# ~200us floor -- a zero-gap back-to-back CF burst intermittently loses tail
# frames on some DUT/PCAN setups. Vector/virtual can go true back-to-back
# (gap 0): with STmin=0 the only spacing left is the frame's own wire time
# (~220-270us for a classic 8-byte frame on 500Kbps HS-CAN), which is exactly
# the "200us-class" pacing under test. Callers (uds/ota download managers)
# take max(user STmin override, this default) -- see default_tx_gap_s().
PCAN_MIN_TX_GAP_S = 0.0002


# PCAN 드라이버 타임스탬프가 벽시계(epoch)와 어긋났는지 판단하는 가드.
# Windows P-CAN FD 실측: 드라이버 ts가 항상 epoch보다 약 +20s 앞서
# 들어오며, 이 경우 GraphWidget의 nowMs()=Date.now()-timeBase*1000이
# -20000ms에서 시작해 신호 변경이 20초 후에 그래프에 나타난다.
# `epoch_aligned` 플래그만 믿지 않고, 호스트 수신시각과 2초 이상
# 벌어지면 호스트 시각을 사용한다 (WS 30ms 플러시 + 버퍼 지연을
# 감안해도 정상 지터는 수백ms 이하이므로 2s는 안전 마진).
PCAN_TS_SKEW_GUARD_S = 2.0


class _BufferListener(can.Listener):
    def __init__(self, buffer: deque, host_ts_buffer: deque, counter: dict):
        self._buffer = buffer
        self._host_ts_buffer = host_ts_buffer
        self._counter = counter

    def on_message_received(self, msg: can.Message) -> None:
        # can.Message는 __slots__라 임의 속성을 붙일 수 없어 호스트
        # 수신시각(time.time, epoch seconds)을 병렬 덱에 함께 보관한다.
        self._buffer.append(msg)
        self._host_ts_buffer.append(time.time())
        self._counter["rx"] += 1

    def on_error(self, exc: Exception) -> None:  # pragma: no cover
        self._counter["errors"] += 1


class CanManager:
    def __init__(self, rx_buffer_size: int = 20000):
        self.bus: Optional[can.BusABC] = None
        self.notifier: Optional[can.Notifier] = None
        self._rx_buffer: deque = deque(maxlen=rx_buffer_size)
        self._rx_host_ts: deque = deque(maxlen=rx_buffer_size)
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
            self.bus, [_BufferListener(self._rx_buffer, self._rx_host_ts, self.counters)], timeout=0.1
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
        self._rx_host_ts.clear()
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

    def default_tx_gap_s(self) -> float:
        """Per-interface minimum inter-CF gap (seconds) for multi-frame sends
        when no explicit user STmin override is set. PCAN keeps the 200us
        tail-frame-loss guard; Vector/virtual return 0.0 for true
        back-to-back (wire time ~220-270us/frame on 500Kbps HS-CAN is the
        only spacing left). Unknown/disconnected -> 0.0 (fast)."""
        if self.config.get("interface") == "pcan":
            return PCAN_MIN_TX_GAP_S
        return 0.0

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

    def clear_rx(self) -> None:
        self._rx_buffer.clear()
        self._rx_host_ts.clear()

    def effective_timestamp(self, hw_ts: float, host_ts: float) -> float:
        """그래프/WS에 내보낼 최종 타임스탬프(epoch seconds).

        정상이면 드라이버 ts(hw_ts)를 그대로 쓰고, PCAN처럼 드라이버
        시계가 벽시계와 어긋난 경우(플래그 False 또는 2s 이상 skew)에는
        호스트 수신시각을 쓴다. virtual/Vector는 항상 hw_ts 경로."""
        if self.config.get("interface") == "pcan":
            if not self.config.get("epoch_aligned", True):
                return host_ts
            if abs(hw_ts - host_ts) > PCAN_TS_SKEW_GUARD_S:
                return host_ts
        return hw_ts

    def drain_rx(self, max_messages: int = 2000) -> list:
        """버퍼를 비워 (msg, host_ts) 튜플 리스트로 반환한다.

        host_ts는 Notifier 스레드가 on_message_received 시점에 찍은
        time.time() (epoch seconds)이며, PCAN 드라이버 ts skew 보정용.
        기존 호출부(main._broadcast_loop, tests)는 튜플 언패킹한다."""
        out = []
        for _ in range(max_messages):
            try:
                msg = self._rx_buffer.popleft()
            except IndexError:
                break
            try:
                host_ts = self._rx_host_ts.popleft()
            except IndexError:  # pragma: no cover - 병렬 덱 불일치 방어
                host_ts = time.time()
            out.append((msg, host_ts))
        return out

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "config": self.config,
            "counters": dict(self.counters),
        }
