"""The wake flow: listen -> "kestrel" heard -> display on, console to the front, greeting, `wake` event.

What a wake can do, and nothing else: wake the display, bring the console forward, speak a fixed
greeting and tell open consoles to play the fly-in. It holds no agent, model or tool, so it can't
start a model call, run a tool or answer an approval (approvals stay clicks).

Privacy: audio chunks live only in memory (audio.py -> detector -> dropped). The only record of
a wake is a span with its time (Tracer.record_wake). Listening is off unless KESTREL_WAKE=on, and
the console shows "Listening" whenever the microphone is open: the `wake_status` event saying so is
sent before the stream opens and again after it closes.

Power (power.decide): plugged in, listen and keep the PC awake; on battery, listen only while the PC
is awake, at or above KESTREL_WAKE_MIN_BATTERY and with the saver off. The state is polled every
5 seconds (one cheap Windows call), so plugging in resumes within seconds and the keep-awake request
is dropped within seconds of switching to battery. After standby (a jump in wall-clock time between
two loop turns) or when the stream stops delivering audio, the microphone is closed and reopened.
"""

import array
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kestrel.wake.audio import AudioSource
from kestrel.wake.detector import Detector
from kestrel.wake.greeting import clean_name, greeting
from kestrel.wake.power import Decision, Power, decide
from kestrel.wake.speech import Speaker

COOLDOWN_S = 10.0
POWER_CHECK_S = 5.0  # how often the power state is read (one GetSystemPowerStatus call)
STANDBY_GAP_S = 10.0  # a wall-clock jump this long between two loop turns means the PC was asleep
STALL_S = 5.0  # an open stream that delivers no audio this long is reopened
RETRY_S = 10.0  # after a microphone or model error
DEFAULT_MIN_BATTERY = 30
DEFAULT_LISTEN_S = 8.0  # KESTREL_WAKE_LISTEN_SECONDS: the Listening window after a wake
LEVEL_FLOOR_DB = -60.0  # loudness 0 at -60 dBFS, 1 at 0 dBFS


def loudness(pcm: bytes) -> float:
    """One chunk's loudness as 0-1: RMS of the 16-bit samples, in dBFS, mapped from -60..0 dB.
    Computed in memory; only this number ever leaves the wake service."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    if rms < 1:
        return 0.0
    db = 20 * math.log10(rms / 32768)
    return max(0.0, min(1.0, (db - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB))


def smooth(previous: float, current: float) -> float:
    """Fast rise, slower fall, so the ring follows speech without flicker."""
    weight = 0.6 if current > previous else 0.25
    return previous + weight * (current - previous)


DEFAULT_THRESHOLD = 0.5  # provisional until measured with scripts/wake_eval.py (docs/voice-wake.md)
MODEL_NAME = "vosk-model-small-en-us-0.15"


def _flag(name: str, default: str = "off") -> bool:
    return os.getenv(name, default).strip().lower() in ("on", "1", "true", "yes")


def default_model_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") if sys.platform == "win32" else None
    root = Path(base) if base else Path.home() / ".cache"
    return root / "kestrel" / "models" / MODEL_NAME


@dataclass(frozen=True)
class WakeSettings:
    enabled: bool = False  # KESTREL_WAKE: the master switch; off by default
    on_battery: bool = True  # KESTREL_WAKE_ON_BATTERY=off: never listen on battery (an override)
    min_battery: int = DEFAULT_MIN_BATTERY  # KESTREL_WAKE_MIN_BATTERY: pause below this on battery
    listen_seconds: float = DEFAULT_LISTEN_S  # KESTREL_WAKE_LISTEN_SECONDS
    user_name: str | None = None  # KESTREL_USER_NAME
    threshold: float = DEFAULT_THRESHOLD  # KESTREL_WAKE_THRESHOLD
    model_dir: Path = Path(MODEL_NAME)  # KESTREL_WAKE_MODEL
    window_title: str = "Kestrel"
    cooldown_s: float = COOLDOWN_S

    @classmethod
    def from_env(cls) -> WakeSettings:
        model = os.getenv("KESTREL_WAKE_MODEL")
        return cls(
            enabled=_flag("KESTREL_WAKE"),
            on_battery=_flag("KESTREL_WAKE_ON_BATTERY", "on"),
            min_battery=max(0, min(100, int(os.getenv("KESTREL_WAKE_MIN_BATTERY", DEFAULT_MIN_BATTERY)))),
            listen_seconds=max(1.0, min(60.0, float(os.getenv("KESTREL_WAKE_LISTEN_SECONDS", DEFAULT_LISTEN_S)))),
            user_name=clean_name(os.getenv("KESTREL_USER_NAME")),
            threshold=float(os.getenv("KESTREL_WAKE_THRESHOLD", DEFAULT_THRESHOLD)),
            model_dir=Path(model) if model else default_model_dir(),
        )


class WakeService:
    """One listening thread. start()/stop() follow the server's lifetime; set_enabled() is the
    console's toggle. The keep-awake request is held only while the microphone is open on mains."""

    reopened = 0  # times the stream was reopened after standby or a stall

    def __init__(
        self,
        settings: WakeSettings,
        make_detector: Callable[[], Detector],
        make_source: Callable[[], AudioSource],
        power: Power,
        speaker: Speaker,
        record_wake: Callable[[float], None],
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.make_detector = make_detector
        self.make_source = make_source
        self.power = power
        self.speaker = speaker
        self.record_wake = record_wake
        self.clock = clock
        self.wall = wall
        self.emit: Callable[[dict], None] = lambda event: None  # set by the web app (broadcast)
        self.greeting = greeting(settings.user_name)
        self._enabled = settings.enabled
        self._listening = False
        self._keeping_awake = False
        self._reason: str | None = None
        self._battery: int | None = None
        self._on_ac = True
        self._low = False  # the low-battery latch (power.decide)
        self._attend_until: float | None = None  # end of the Listening window after a wake (clock)
        self._level = 0.0
        self._last_wake: float | None = None
        self._changed = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # --- control ------------------------------------------------------------------

    def start(self) -> None:
        if not self.settings.enabled or self._thread is not None:
            return  # KESTREL_WAKE is off: no thread, the microphone is never opened
        self._thread = threading.Thread(target=self._run, name="kestrel-wake", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._release()  # even if the thread didn't finish in time
        self.speaker.close()

    def set_enabled(self, enabled: bool) -> dict:
        if not self.settings.enabled:
            raise RuntimeError("the wake word is off; set KESTREL_WAKE=on and restart")
        with self._lock:
            self._enabled = enabled
        self._changed.set()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return {
                "type": "wake_status",
                "available": self.settings.enabled,
                "enabled": self._enabled,
                "listening": self._listening,
                "keeping_awake": self._keeping_awake,
                "paused": self._reason,
                "on_ac": self._on_ac,
                "battery": self._battery,
            }

    # --- the listening thread -----------------------------------------------------------

    def _set(self, **changes: Any) -> None:
        """Update the status; tell the consoles only if something changed (the loop polls often)."""
        with self._lock:
            changed = any(getattr(self, f"_{key}") != value for key, value in changes.items())
            for key, value in changes.items():
                setattr(self, f"_{key}", value)
        if changed:
            self.emit(self.status())

    def _wait(self, seconds: float) -> None:
        self._changed.wait(seconds)
        self._changed.clear()

    def _power(self) -> Decision:
        """Read the power state, apply the battery rule, and show the battery in the status."""
        state = self.power.state()
        decision = decide(state, self.settings.min_battery, self.settings.on_battery, self._low)
        self._low = decision.low
        self._set(on_ac=state.on_ac, battery=state.percent)
        return decision

    def _hold(self, keep_awake: bool) -> None:
        self._set(keeping_awake=self.power.keep_awake(keep_awake))  # shown only if Windows granted it

    def _run(self) -> None:
        while not self._stopping.is_set():
            if not self._enabled:
                self._release()
                self._set(reason="off")
                self._wait(POWER_CHECK_S)
                continue
            decision = self._power()
            if not decision.listen:
                self._release()
                self._set(reason=decision.reason)
                self._wait(POWER_CHECK_S)
                continue
            try:
                self._listen(decision)
            except Exception as e:  # no microphone, no model, permission denied...
                self._release()
                self._set(reason=f"error: {type(e).__name__}: {e}"[:200])
                self._wait(RETRY_S)

    def _listen(self, decision: Decision) -> None:
        detector = self.make_detector()
        source = self.make_source()
        self._set(listening=True, reason=None)  # the indicator turns on before the microphone does
        try:
            source.open()
            self._hold(decision.keep_awake)
            checked = heard = self.clock()
            last_turn = self.wall()
            while not self._stopping.is_set() and not self._changed.is_set():
                chunk = source.read(timeout=0.5)
                now = self.wall()
                if now - last_turn > STANDBY_GAP_S:  # the PC slept: reopen the stream, re-read power
                    self.reopened += 1
                    break
                last_turn = now
                if chunk is not None:
                    heard = self.clock()
                    if detector.feed(chunk) is not None:
                        self.on_wake()
                    self._send_level(chunk)
                elif self.clock() - heard > STALL_S:  # the device went away or stopped delivering
                    self.reopened += 1
                    break
                if self.clock() - checked >= POWER_CHECK_S:
                    checked = self.clock()
                    decision = self._power()
                    if not decision.listen:
                        break
                    self._hold(decision.keep_awake)  # dropped at once on battery, taken again on mains
        finally:
            source.close()
            self._release()
        self._changed.clear()

    def _release(self) -> None:
        """Close down: clear the keep-awake request and say the microphone is off."""
        self.power.keep_awake(False)
        if self._listening or self._keeping_awake:
            self._set(listening=False, keeping_awake=False)

    # --- the Listening window ---------------------------------------------------------------

    def attending(self) -> bool:
        until = self._attend_until
        return until is not None and self.clock() < until

    def end_attention(self) -> None:
        """The console reported typing, or a message was sent: stop sending levels."""
        self._attend_until = None

    def _send_level(self, chunk: bytes) -> None:
        """While Listening only: one number, 0-1, for the console's level ring. Never the audio."""
        if not self.attending():
            self._attend_until = None
            return
        self._level = smooth(self._level, loudness(chunk))
        self.emit({"type": "wake_level", "level": round(self._level, 3)})

    # --- a wake -----------------------------------------------------------------------

    def on_wake(self) -> bool:
        """The detector heard the wake word. Returns False if it was within the cooldown."""
        now = self.clock()
        if self._last_wake is not None and now - self._last_wake < self.settings.cooldown_s:
            return False
        self._last_wake = now
        at = self.wall()
        self.record_wake(at)
        self.power.wake_display()
        self.power.bring_to_front(self.settings.window_title)
        self._level = 0.0
        self._attend_until = now + self.settings.listen_seconds
        self.emit({"type": "wake", "at": at, "greeting": self.greeting, "listen_seconds": self.settings.listen_seconds})
        self.speaker.say(self.greeting)
        return True
