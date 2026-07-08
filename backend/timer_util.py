"""Windows high-resolution timer control.

On Windows the default system timer resolution (~15.6 ms) is too coarse for
1 ms-class CAN TX scheduling, so we request 1 ms via winmm timeBeginPeriod.
On other platforms this is a no-op.
"""

import ctypes
import sys

_active = False


def enable_1ms_timer() -> bool:
    global _active
    if sys.platform != "win32" or _active:
        return _active
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        _active = True
    except (OSError, AttributeError):
        _active = False
    return _active


def disable_1ms_timer() -> None:
    global _active
    if sys.platform == "win32" and _active:
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except (OSError, AttributeError):
            pass
        _active = False
