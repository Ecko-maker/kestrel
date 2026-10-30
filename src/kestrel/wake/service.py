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
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kestrel.wake.audio import AudioSource
from kestrel.wake.detector import Detector
from kestrel.wake.greeting import clean_name, greeting
from kestrel.wake.power import Decision, Power, decide
from kestrel.wake.speech import Speaker
from kestrel.wake.talk import (
    CHECK_ON_SCREEN,
    DEFAULT_MIN_CONFIDENCE,
    DIDNT_CATCH,
    MAX_REQUEST_S,
    SEND_COUNTDOWN_S,
    START_TIMEOUT_S,
    Endpointer,
    NoiseFloor,
    for_speech,
    guard,
)
from kestrel.wake.transcribe import DEFAULT_TALK_MODEL, Transcriber, read_vocab, talk_prompt, vocab_file

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


def _later(delay: float, action: Callable[[], object]) -> None:
    timer = threading.Timer(delay, action)
    timer.daemon = True
    timer.start()


def smooth(previous: float, current: float) -> float:
    """Fast rise, slower fall, so the ring follows speech without flicker."""
    weight = 0.6 if current > previous else 0.25
    return previous + weight * (current - previous)


# Provisional (owner, 2026-10-24): 19/30 of the owner's clips, 1 false wake per hour on 1 h of podcast,
# chosen on the same data; 10 of the 11 misses were recording problems (docs/voice-wake.md).
DEFAULT_THRESHOLD = 0.9
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
    talk: bool = True  # KESTREL_TALK=off: a wake only greets (no spoken requests)
    talk_model: str = DEFAULT_TALK_MODEL  # KESTREL_TALK_MODEL: base.en-q5_1 or tiny.en-q5_1
    min_confidence: float = DEFAULT_MIN_CONFIDENCE  # KESTREL_TALK_MIN_CONFIDENCE
    # The vocabulary hint: off by default (KESTREL_TALK_HINT=on to use it; known issue #63: pooled over
    # 31 recorded requests it was worse, 17.62% vs 14.18% normalised WER). Names: KESTREL_TALK_VOCAB_FILE.
    talk_prompt: str = ""
    send_countdown_s: float = SEND_COUNTDOWN_S  # fixed; a field so tests can shorten it

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
            talk=_flag("KESTREL_TALK", "on"),
            talk_model=os.getenv("KESTREL_TALK_MODEL", DEFAULT_TALK_MODEL),
            min_confidence=float(os.getenv("KESTREL_TALK_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)),
            talk_prompt=talk_prompt(read_vocab(vocab_file())) if _flag("KESTREL_TALK_HINT", "off") else "",
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
        make_transcriber: Callable[[], Transcriber] | None = None,
        make_cancel: Callable[[], Detector] | None = None,
    ):
        self.settings = settings
        self.make_transcriber = make_transcriber  # None: a wake only greets
        self._transcriber: Transcriber | None = None
        self.make_detector = make_detector
        self.make_source = make_source
        self.power = power
        self.speaker = speaker
        self.record_wake = record_wake
        self.clock = clock
        self.wall = wall
        self.emit: Callable[[dict], None] = lambda event: None  # set by the web app (broadcast)
        self.on_request: Callable[[str], None] = lambda text: None  # set by the web app: one console
        self.on_check: Callable[[str], None] = lambda text: None  # low confidence: into the message box, unsent
        # The send countdown (set by the web app): on_pending shows "Sending: ..." in one console and
        # returns False if none is open; on_cancelled puts the text back in that console's message box.
        self.on_pending: Callable[[str, str, float], bool] = lambda request_id, text, seconds: True
        self.on_cancelled: Callable[[str, str, str], None] = lambda request_id, text, by: None
        self.make_cancel = make_cancel  # None: no spoken "cancel" (Esc and the button still work)
        self.schedule: Callable[[float, Callable[[], object]], None] = _later
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
        self._noise = NoiseFloor()  # the room's quiet level, learned while waiting
        self._recording: bytearray | None = None  # the spoken request, in memory only
        self._endpoint: Endpointer | None = None
        self._last_wake: float | None = None
        self._pending: tuple[str, str] | None = None  # (id, text) during the send countdown
        self._cancel_detector: Detector | None = None  # listens for "cancel" during the countdown only
        self._pending_lock = threading.Lock()
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
        with self._pending_lock:  # a countdown still running when the server stops is dropped
            self._pending = self._cancel_detector = None
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
                "talk": self.make_transcriber is not None,
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
                    level = loudness(chunk)
                    self._hear_cancel(chunk)
                    woke = detector.feed(chunk) is not None and self.on_wake(getattr(detector, "greeting", None))
                    if self._recording is not None and not woke:
                        self._record(chunk, level)
                    elif self._recording is None:
                        self._noise.update(level)
                    self._send_level(level)
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
        """The console reported typing, or a message was sent: stop listening, drop any recording."""
        self._attend_until = None
        self._recording = self._endpoint = None

    def _send_level(self, level: float) -> None:
        """While Listening only: one number, 0-1, for the console's level ring. Never the audio."""
        if not self.attending():
            self._attend_until = None
            return
        self._level = smooth(self._level, level)
        self.emit({"type": "wake_level", "level": round(self._level, 3)})

    # --- a spoken request ------------------------------------------------------------------------

    def say(self, text: str) -> None:
        self.speaker.say(for_speech(text))

    def _record(self, chunk: bytes, level: float) -> None:
        recording, endpoint = self._recording, self._endpoint
        if recording is None or endpoint is None:
            return  # ended by the console meanwhile
        if self.speaker.busy():
            return  # Kestrel's own voice (the greeting): not part of your request
        recording += chunk
        state = endpoint.feed(level)
        if state not in ("waiting", "speaking"):
            self._finish(state, bytes(recording), endpoint.speech_s)

    def _finish(self, state: str, audio: bytes, speech_s: float) -> None:
        self._recording = self._endpoint = None
        self._attend_until = None
        self.emit({"type": "wake_end", "reason": state})
        if state == "no_speech":
            self._ignored("no speech")
            return
        threading.Thread(target=self._transcribe, args=(audio, speech_s), name="kestrel-stt", daemon=True).start()

    def _transcribe(self, audio: bytes, speech_s: float) -> None:
        try:
            if self._transcriber is None and self.make_transcriber is not None:
                self._transcriber = self.make_transcriber()
            if self._transcriber is None:
                return
            transcript = self._transcriber.transcribe(audio)
        except Exception as e:  # a missing model, a broken file: say so, never crash the listener
            self._ignored(f"transcription failed: {type(e).__name__}")
            return
        finally:
            del audio  # the recording's last reference: it goes now
        verdict = guard(
            transcript.text, transcript.confidence, speech_s, self.settings.min_confidence, self.settings.talk_prompt
        )
        if verdict.action == "drop" or verdict.text is None:
            self._ignored(verdict.reason)
        elif verdict.action == "check":
            self.on_check(verdict.text)  # shown unsent: "Did I hear that right?"; nothing reaches the agent
            self.say(CHECK_ON_SCREEN)
        else:
            self._hold_request(verdict.text)

    # --- the send countdown ------------------------------------------------------------------------

    def _hold_request(self, text: str) -> None:
        """'Sending: <text>' for SEND_COUNTDOWN_S; then it goes to the agent unless cancelled."""
        request_id = uuid.uuid4().hex[:12]
        with self._pending_lock:
            self._pending = (request_id, text)
            self._cancel_detector = self.make_cancel() if self.make_cancel is not None else None
        if not self.on_pending(request_id, text, self.settings.send_countdown_s):  # no console: said so
            with self._pending_lock:
                self._pending = self._cancel_detector = None
            return
        self.schedule(self.settings.send_countdown_s, lambda: self.commit(request_id))

    def _take(self, request_id: str) -> str | None:
        with self._pending_lock:
            if self._pending is None or self._pending[0] != request_id:
                return None  # already sent, cancelled, or another request
            text = self._pending[1]
            self._pending = self._cancel_detector = None
            return text

    def pending(self) -> str | None:
        """The id of the request in its countdown, if any."""
        with self._pending_lock:
            return self._pending[0] if self._pending else None

    def commit(self, request_id: str) -> bool:
        """The countdown ran out: the request goes to the agent."""
        text = self._take(request_id)
        if text is not None:
            self.on_request(text)
        return text is not None

    def cancel(self, request_id: str, by: str) -> bool:
        """Esc, the Cancel button or a spoken "cancel": nothing is sent; the text goes back to the
        message box. Only a pending spoken request can be cancelled this way, never an approval."""
        text = self._take(request_id)
        if text is not None:
            self.on_cancelled(request_id, text, by)
        return text is not None

    def _hear_cancel(self, chunk: bytes) -> None:
        with self._pending_lock:
            detector, pending = self._cancel_detector, self._pending
        if detector is not None and pending is not None and detector.feed(chunk) is not None:
            self.cancel(pending[0], "voice")

    def _ignored(self, reason: str) -> None:
        """Nothing usable was heard: say so; nothing reaches the agent."""
        self.emit({"type": "voice_ignored", "reason": reason})
        self.say(DIDNT_CATCH)

    # --- a wake -----------------------------------------------------------------------

    def on_wake(self, heard: str | None = None) -> bool:
        """The detector heard the wake word (heard: a greeting said with it, echoed back).
        Returns False if it was within the cooldown."""
        now = self.clock()
        if self._last_wake is not None and now - self._last_wake < self.settings.cooldown_s:
            return False
        self._last_wake = now
        self.speaker.stop()  # barge-in: "Kestrel" while it speaks stops the speech
        at = self.wall()
        self.record_wake(at)
        self.power.wake_display()
        self.power.bring_to_front(self.settings.window_title)
        self._level = 0.0
        if self.make_transcriber is not None:  # record one spoken request
            self._recording, self._endpoint = bytearray(), Endpointer(floor=self._noise.value)
            window = 3.0 + START_TIMEOUT_S + MAX_REQUEST_S  # the greeting, then the request at most
        else:
            window = self.settings.listen_seconds
        self._attend_until = self.clock() + window  # from here: the calls above can be slow
        reply = greeting(self.settings.user_name, heard) if heard else self.greeting
        self.emit({"type": "wake", "at": at, "greeting": reply, "listen_seconds": window})
        self.say(reply)
        return True
