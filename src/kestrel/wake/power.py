"""Windows calls for the wake word, through ctypes (no extra dependency).

- state: GetSystemPowerStatus: mains or battery, battery percent, and the saver flag
  (SystemStatusFlag; Windows 11's Energy Saver, formerly Battery Saver). decide() turns it into
  "listen or not, hold keep-awake or not, and why" (the rules are in its docstring).
- keep_awake: a "system required" power request (PowerCreateRequest/PowerSetRequest) while the
  microphone is open on mains power, so the screen can turn off without the PC entering Modern
  Standby (which pauses all desktop apps, Kestrel included). Never held on battery. It shows in
  `powercfg /requests` with the reason below.
- wake_display: resets the display idle timer (SetThreadExecutionState ES_DISPLAY_REQUIRED) and
  sends one zero-distance mouse move (SendInput). Never a click, a key or a real movement.
- bring_to_front: finds the console's window by its title and activates it. Windows allows this
  because this process just produced the last input event; if it refuses, the taskbar button flashes.

None of this can wake the PC from sleep or get past the lock screen: a sleeping PC runs no Kestrel
code at all, and synthetic input never reaches the secure (lock screen) desktop.
"""

import ctypes
import sys
from dataclasses import dataclass
from typing import Any, Protocol

ES_DISPLAY_REQUIRED = 0x00000002
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
SW_RESTORE = 9
POWER_REQUEST_CONTEXT_VERSION = 0
POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
POWER_REQUEST_SYSTEM_REQUIRED = 1
KEEP_AWAKE_REASON = "Kestrel is listening for its wake word"
EDGE_WINDOW_CLASS = "Chrome_WidgetWin_1"
NO_SYSTEM_BATTERY = 128
UNKNOWN = 255
RESUME_MARGIN = 5  # after a low-battery pause, listening resumes at the minimum + 5 points


# Windows' LONG/DWORD/ULONG are 32-bit on every platform, but c_long/c_ulong are 64-bit on 64-bit
# Linux; fixed-width types keep these layouts right wherever the tests run.
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]  # the largest member; keyboard input is never built


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_uint32),
        ("BatteryFullLifeTime", ctypes.c_uint32),
    ]


class REASON_CONTEXT(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32), ("Flags", ctypes.c_uint32), ("SimpleReasonString", ctypes.c_wchar_p)]


@dataclass(frozen=True)
class PowerState:
    on_ac: bool  # mains power, or a PC without a battery
    percent: int | None  # battery charge, None if unknown or no battery
    saver: bool  # Energy Saver / Battery Saver is on


MAINS = PowerState(on_ac=True, percent=None, saver=False)


@dataclass(frozen=True)
class Decision:
    listen: bool
    keep_awake: bool
    reason: str | None  # why not listening, for the console
    low: bool  # the low-battery latch, carried to the next decision


def decide(state: PowerState, min_battery: int, allow_battery: bool, was_low: bool) -> Decision:
    """The battery rule.

    - Plugged in: listen and hold the keep-awake request (and forget any low-battery pause).
    - On battery: listen only while the PC is awake, never holding a keep-awake request, if the
      battery is at least min_battery and the saver is off.
    - Below min_battery: pause, and stay paused until min_battery + 5 (or mains): no flapping
      around the threshold.
    - KESTREL_WAKE_ON_BATTERY=off (allow_battery False): never listen on battery.
    """
    if state.on_ac:
        return Decision(listen=True, keep_awake=True, reason=None, low=False)
    if not allow_battery:
        return Decision(False, False, "on battery (KESTREL_WAKE_ON_BATTERY=off)", was_low)
    low = was_low
    if state.percent is not None:
        if state.percent < min_battery:
            low = True
        elif state.percent >= min_battery + RESUME_MARGIN:
            low = False
    if low:
        shown = f" ({state.percent}%)" if state.percent is not None else ""
        return Decision(False, False, f"battery low{shown}", low=True)
    if state.saver:
        return Decision(False, False, "power saver", low=False)
    return Decision(listen=True, keep_awake=False, reason=None, low=False)


def read_power_state(status: SYSTEM_POWER_STATUS) -> PowerState:
    no_battery = status.BatteryFlag == NO_SYSTEM_BATTERY
    on_ac = no_battery or status.ACLineStatus != 0  # 1 = mains; 255 = unknown, treated as mains
    percent = None if no_battery or status.BatteryLifePercent == UNKNOWN else int(status.BatteryLifePercent)
    return PowerState(on_ac=on_ac, percent=percent, saver=status.SystemStatusFlag == 1)


def nudge_input() -> INPUT:
    """The only input Kestrel ever synthesizes: a mouse move of zero pixels."""
    move = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_MOUSE, mi=move)


class Power(Protocol):
    def state(self) -> PowerState: ...
    def keep_awake(self, on: bool) -> bool:
        """Hold (on) or clear the keep-awake request; returns whether one is held now."""
        ...

    def wake_display(self) -> None: ...
    def bring_to_front(self, title: str) -> bool: ...


class NullPower:
    """Not Windows (or tests): nothing to do."""

    def state(self) -> PowerState:
        return MAINS

    def keep_awake(self, on: bool) -> bool:
        return False

    def wake_display(self) -> None:
        pass

    def bring_to_front(self, title: str) -> bool:
        return False


class WindowsPower:
    def __init__(self, user32: Any, kernel32: Any):
        self.user32 = user32
        self.kernel32 = kernel32
        self._request: Any = None
        self._reason: REASON_CONTEXT | None = None  # kept alive while the request exists

    def state(self) -> PowerState:
        status = SYSTEM_POWER_STATUS()
        if not self.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return MAINS  # can't tell: behave as before the battery rule existed
        return read_power_state(status)

    def keep_awake(self, on: bool) -> bool:
        if on and self._request is None:
            self._reason = REASON_CONTEXT(
                POWER_REQUEST_CONTEXT_VERSION, POWER_REQUEST_CONTEXT_SIMPLE_STRING, KEEP_AWAKE_REASON
            )
            handle = self.kernel32.PowerCreateRequest(ctypes.byref(self._reason))
            if handle and handle != ctypes.c_void_p(-1).value:
                if self.kernel32.PowerSetRequest(handle, POWER_REQUEST_SYSTEM_REQUIRED):
                    self._request = handle
                else:
                    self.kernel32.CloseHandle(handle)
        elif not on and self._request is not None:
            handle, self._request = self._request, None
            self.kernel32.PowerClearRequest(handle, POWER_REQUEST_SYSTEM_REQUIRED)
            self.kernel32.CloseHandle(handle)
        return self._request is not None

    def wake_display(self) -> None:
        self.kernel32.SetThreadExecutionState(ES_DISPLAY_REQUIRED)
        nudge = nudge_input()
        self.user32.SendInput(1, ctypes.byref(nudge), ctypes.sizeof(INPUT))

    def bring_to_front(self, title: str) -> bool:
        hwnd = self.user32.FindWindowW(EDGE_WINDOW_CLASS, title)
        if not hwnd:
            return False
        if self.user32.IsIconic(hwnd):
            self.user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(self.user32.SetForegroundWindow(hwnd))


def default_power() -> Power:
    if sys.platform != "win32":
        return NullPower()
    # Private handles, so the signatures set here don't change ctypes.windll for anyone else.
    k32 = ctypes.WinDLL("kernel32")
    k32.PowerCreateRequest.restype = ctypes.c_void_p
    k32.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
    k32.PowerClearRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.SetThreadExecutionState.argtypes = [ctypes.c_ulong]
    k32.SetThreadExecutionState.restype = ctypes.c_ulong
    u32 = ctypes.WinDLL("user32")
    u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u32.FindWindowW.restype = ctypes.c_void_p
    u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u32.IsIconic.argtypes = [ctypes.c_void_p]
    u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
    u32.SendInput.restype = ctypes.c_uint
    return WindowsPower(u32, k32)
