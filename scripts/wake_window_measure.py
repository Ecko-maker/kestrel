"""Measure the Listening window the live service really gives after a wake (offline, no audio).

WakeService.on_wake writes the wake to the trace store and makes two Windows calls (wake the display,
bring the console to the front) before Listening starts. This drives the real on_wake on a test clock,
with each of those three calls taking a set time, and reports how long Listening lasts from the
moment on_wake returns, against the window the wake event announces (`listen_seconds`).

    uv run python scripts/wake_window_measure.py [--json out.json]

Known issue #65. No model, microphone or network is used.
"""

import argparse
import json
import sys
from pathlib import Path

from kestrel.wake import WakeSettings
from kestrel.wake.service import WakeService

DELAYS_S = (0.0, 0.05, 0.25, 1.0)  # each of the three calls; 0.05 s is a slow SQLite write on CI


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class SlowPower:
    def __init__(self, clock: Clock, delay: float) -> None:
        self.clock, self.delay = clock, delay

    def state(self):
        raise AssertionError("not used by on_wake")

    def keep_awake(self, on: bool) -> bool:
        return on

    def wake_display(self) -> None:
        self.clock.t += self.delay

    def bring_to_front(self, title: str) -> bool:
        self.clock.t += self.delay
        return True


class Speaker:
    def say(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def busy(self) -> bool:
        return False

    def close(self) -> None:
        pass


def measure(delay: float, talk: bool) -> dict:
    clock = Clock()

    def record_wake(at: float) -> None:
        clock.t += delay

    service = WakeService(
        WakeSettings(enabled=True, listen_seconds=8.0),
        lambda: None,  # type: ignore[arg-type,return-value]
        lambda: None,  # type: ignore[arg-type,return-value]
        SlowPower(clock, delay),  # type: ignore[arg-type]
        Speaker(),
        record_wake,
        clock=clock,
        wall=lambda: 0.0,
        make_transcriber=(lambda: None) if talk else None,  # type: ignore[arg-type,return-value]
    )
    events: list[dict] = []
    service.emit = events.append
    assert service.on_wake()
    ready = clock.t
    announced = next(e for e in events if e["type"] == "wake")["listen_seconds"]
    until = service._attend_until
    assert until is not None
    got = until - ready
    return {
        "mode": "talk" if talk else "greet",
        "delay_per_call_s": delay,
        "calls_s": round(ready - 1000.0, 3),
        "announced_s": announced,
        "listening_s": round(got, 3),
        "lost_s": round(announced - got, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()
    rows = [measure(d, talk) for talk in (False, True) for d in DELAYS_S]
    print(f"{'mode':<6} {'per call':>9} {'3 calls':>8} {'announced':>10} {'listening':>10} {'lost':>6}")
    for r in rows:
        print(
            f"{r['mode']:<6} {r['delay_per_call_s']:>8.2f}s {r['calls_s']:>7.2f}s {r['announced_s']:>9.2f}s "
            f"{r['listening_s']:>9.2f}s {r['lost_s']:>5.2f}s"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"rows": rows}, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
