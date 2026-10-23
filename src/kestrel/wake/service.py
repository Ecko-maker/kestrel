"""The wake flow: listen -> "kestrel" heard -> display on, console to the front, greeting, `wake` event.

What a wake can do, and nothing else: wake the display, bring the console forward, speak a fixed
greeting and tell open consoles to play the fly-in. It holds no agent, model or tool, so it can't
start a model call, run a tool or answer an approval (approvals stay clicks).

Privacy: audio chunks live only in memory (audio.py -> detector -> dropped). The only record of
a wake is a span with its time (Tracer.record_wake). Listening is off unless KESTREL_WAKE=on, and
the console shows "Listening" whenever the microphone is open: the `wake_status` event saying so is
sent before the stream opens and again after it closes.
"""

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
from kestrel.wake.power import Power
from kestrel.wake.speech import Speaker

COOLDOWN_S = 10.0
BATTERY_CHECK_S = 5.0
RETRY_S = 10.0  # after a microphone or model error
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
    on_battery: bool = False  # KESTREL_WAKE_ON_BATTERY: listen on battery too
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
            on_battery=_flag("KESTREL_WAKE_ON_BATTERY"),
            user_name=clean_name(os.getenv("KESTREL_USER_NAME")),
            threshold=float(os.getenv("KESTREL_WAKE_THRESHOLD", DEFAULT_THRESHOLD)),
            model_dir=Path(model) if model else default_model_dir(),
        )


class WakeService:
    """One listening thread. start()/stop() follow the server's lifetime; set_enabled() is the
    console's toggle. The keep-awake request is held only while the microphone is open on mains."""

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
            }

    # --- the listening thread -----------------------------------------------------------

    def _set(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, f"_{key}", value)
        self.emit(self.status())

    def _wait(self, seconds: float) -> None:
        self._changed.wait(seconds)
        self._changed.clear()

    def _should_listen(self) -> str | None:
        """None if the microphone should be open now, else why not."""
        if not self._enabled:
            return "off"
        if not self.settings.on_battery and self.power.on_battery():
            return "on battery"
        return None

    def _run(self) -> None:
        while not self._stopping.is_set():
            reason = self._should_listen()
            if reason is not None:
                self._set(reason=reason)
                self._wait(BATTERY_CHECK_S)
                continue
            try:
                self._listen()
            except Exception as e:  # no microphone, no model, permission denied...
                self._release()
                self._set(reason=f"error: {type(e).__name__}: {e}"[:200])
                self._wait(RETRY_S)

    def _listen(self) -> None:
        detector = self.make_detector()
        source = self.make_source()
        self._set(listening=True, reason=None)  # the indicator turns on before the microphone does
        try:
            source.open()
            on_mains = not self.power.on_battery()
            if on_mains:
                self._set(keeping_awake=self.power.keep_awake(True))  # shown only if Windows granted it
            checked = self.clock()
            while not self._stopping.is_set() and not self._changed.is_set():
                chunk = source.read(timeout=0.5)
                if chunk is not None and detector.feed(chunk) is not None:
                    self.on_wake()
                if self.clock() - checked >= BATTERY_CHECK_S:
                    checked = self.clock()
                    if self._should_listen() is not None:
                        break
        finally:
            source.close()
            self._release()
        self._changed.clear()

    def _release(self) -> None:
        """Close down: clear the keep-awake request and say the microphone is off."""
        self.power.keep_awake(False)
        if self._listening or self._keeping_awake:
            self._set(listening=False, keeping_awake=False)

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
        self.emit({"type": "wake", "at": at, "greeting": self.greeting})
        self.speaker.say(self.greeting)
        return True
