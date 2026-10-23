"""Check that the power state Kestrel reads matches what Windows shows: watch two sources side by side
and print a line whenever either changes, while you toggle Energy Saver (Quick Settings) or unplug.

- GetSystemPowerStatus (what the wake word uses): mains, battery percent, SystemStatusFlag (the saver).
- WinRT PowerManager.EnergySaverStatus (On / Off / Disabled), read through Windows PowerShell 5.1,
  an independent source: if the saver flag were stale, the two would disagree.

    uv run --no-sync python scripts/wake_power_watch.py --minutes 5
"""

import argparse
import ctypes
import subprocess
import sys
import time

from kestrel.wake.power import SYSTEM_POWER_STATUS, read_power_state

WINRT = (
    "$null = [Windows.System.Power.PowerManager, Windows.System.Power, ContentType = WindowsRuntime]; "
    "[Windows.System.Power.PowerManager]::EnergySaverStatus"
)


def winrt_saver() -> str:
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", WINRT], capture_output=True, text=True)
    return out.stdout.strip() or f"error: {out.stderr.strip()[:80]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=5.0)
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Windows only")
        return 1
    kernel32 = ctypes.WinDLL("kernel32")
    end = time.monotonic() + args.minutes * 60
    last = None
    while time.monotonic() < end:
        status = SYSTEM_POWER_STATUS()
        kernel32.GetSystemPowerStatus(ctypes.byref(status))
        state = read_power_state(status)
        row = (state.on_ac, state.percent, status.SystemStatusFlag, winrt_saver())
        if row != last:
            print(
                f"{time.strftime('%H:%M:%S')}  mains={row[0]} battery={row[1]}%  "
                f"SystemStatusFlag={row[2]}  WinRT EnergySaverStatus={row[3]}",
                flush=True,
            )
            last = row
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
