"""Measure the live wake word on this PC: CPU while listening, and the keep-awake request.

Runs the real service (microphone, Vosk, Windows power calls) for --minutes, without the web server
and without writing traces: a wake heard during the run is printed, not recorded. Audio stays in memory.

- CPU: this process's CPU time (all threads: PortAudio, Vosk) over wall time, per minute and overall.
- Keep-awake: `powercfg /requests` while listening and again after stopping, looking for the request's
  reason text. powercfg needs an elevated prompt; without one, that part is skipped and says so.

    uv run --no-sync python scripts/wake_measure.py --minutes 10
"""

import argparse
import ctypes
import dataclasses
import subprocess
import sys
import time

from kestrel.wake import WakeService, WakeSettings
from kestrel.wake.audio import MicSource
from kestrel.wake.detector import VoskDetector, vosk_recognizer_factory
from kestrel.wake.power import KEEP_AWAKE_REASON, default_power
from kestrel.wake.speech import default_speaker


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except AttributeError:
        return False


def keep_awake_listed() -> str:
    if not is_admin():
        return "not checked (powercfg /requests needs an elevated prompt)"
    out = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, check=False).stdout
    return "LISTED" if KEEP_AWAKE_REASON in out else "not listed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=10.0)
    args = parser.parse_args()

    settings = dataclasses.replace(WakeSettings.from_env(), enabled=True)
    factory = vosk_recognizer_factory(settings.model_dir)
    power = default_power()
    service = WakeService(
        settings,
        lambda: VoskDetector(factory, settings.threshold),
        MicSource,
        power,
        default_speaker(),
        record_wake=lambda at: print(f"  wake heard at {time.strftime('%H:%M:%S', time.localtime(at))}"),
    )
    service.emit = lambda e: print(f"  event: {e}") if e["type"] == "wake" else None
    print(f"on battery: {power.on_battery()}; KESTREL_WAKE_ON_BATTERY: {settings.on_battery}")
    service.start()
    deadline = time.monotonic() + 30
    while not service.status()["listening"] and time.monotonic() < deadline:
        time.sleep(0.2)
    status = service.status()
    print(f"listening: {status['listening']}, keeping PC awake: {status['keeping_awake']}, paused: {status['paused']}")
    if not status["listening"]:
        service.stop()
        return 1

    time.sleep(5)
    print(f"keep-awake request while listening: {keep_awake_listed()}")
    cpu0, wall0 = time.process_time(), time.monotonic()
    shares = []
    for minute in range(int(args.minutes)):
        c, w = time.process_time(), time.monotonic()
        time.sleep(60)
        shares.append(100 * (time.process_time() - c) / (time.monotonic() - w))
        print(f"  minute {minute + 1}: {shares[-1]:.1f}% of one core")
    total = 100 * (time.process_time() - cpu0) / (time.monotonic() - wall0)
    service.stop()
    time.sleep(1)
    spread = f"min {min(shares):.1f}, max {max(shares):.1f}"
    print(f"CPU while listening: {total:.1f}% of one core over {args.minutes:g} min ({spread})")
    print(f"keep-awake request after stopping: {keep_awake_listed()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
