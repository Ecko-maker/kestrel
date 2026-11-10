"""Wake-word detection behind a small interface, so the engine can be swapped (docs/voice-wake.md).

VoskDetector runs the Vosk small English model restricted to a small grammar: "kestrel", a few
greetings ("hi", "hello", "hey", "shalom", "good morning"; all in the model's vocabulary) and
"[unk]". The "[unk]" entry matters: with a grammar of only "kestrel", every utterance would be forced
onto the wake word. With it, other speech comes out as "[unk]".

The rule (rule B, 2026-11-07): a wake is "kestrel" with nothing but greetings before it in the
recognised utterance, at or above the threshold (wake_confidence). So "Kestrel, what time is it",
"hi kestrel" and "shalom kestrel" wake; "[unk] kestrel" and a sentence with "kestrel" in the middle
don't (they were most of the false wakes with a podcast playing; docs/voice-wake.md). The greetings
are there only so Kestrel can greet back with the same word (heard_greeting); they never wake it.
The same class, with the word "cancel", listens for a spoken cancel during the 2-second send
countdown (talk.CANCEL_WORD): only then, and acting on Vosk's partial result so it is fast enough.

Audio arrives as 16 kHz mono 16-bit PCM chunks and is only ever held in memory.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

WAKE_WORD = "kestrel"
GREETINGS = ("hi", "hello", "hey", "shalom", "good morning")  # checked in the model: vosk_model_find_word
GRAMMAR = (WAKE_WORD, *GREETINGS, "[unk]")
KESTREL_ONLY = (WAKE_WORD, "[unk]")  # the grammar before greetings (2026-10-26), kept for wake_eval
SAMPLE_RATE = 16_000


class Detector(Protocol):
    def feed(self, pcm: bytes) -> float | None:
        """Process one chunk; return the confidence when the wake word was heard, else None."""
        ...


class Recognizer(Protocol):
    """The part of vosk.KaldiRecognizer the detector uses."""

    def AcceptWaveform(self, data: bytes) -> bool: ...  # Vosk's method names
    def Result(self) -> str: ...
    def FinalResult(self) -> str: ...
    def PartialResult(self) -> str: ...


RecognizerFactory = Callable[[str], Recognizer]  # grammar JSON -> recognizer


def grammar_json(word: str = WAKE_WORD, grammar: tuple[str, ...] | None = None) -> str:
    """The wake word's grammar (GRAMMAR), or any other single word with "[unk]" (the spoken cancel)."""
    if grammar is None:
        grammar = GRAMMAR if word == WAKE_WORD else (word, "[unk]")
    return json.dumps(list(grammar))


def vosk_recognizer_factory(model_dir: Path) -> RecognizerFactory:
    """Load the model from a local folder only. Vosk can also download models by name or language;
    that path is never used, so nothing is fetched at run time."""
    if not (model_dir / "am" / "final.mdl").is_file():
        raise FileNotFoundError(f"no Vosk model in {model_dir} (run scripts/get_wake_model.py)")
    import vosk  # optional dependency: uv sync --extra wake

    vosk.SetLogLevel(-1)
    model = vosk.Model(str(model_dir))

    def make(grammar: str) -> Recognizer:
        rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, grammar)
        rec.SetWords(True)  # per-word confidence
        return rec

    return make


GREETING_WORDS = frozenset(part for greeting in GREETINGS for part in greeting.split())


def wake_confidence(result_json: str, word: str = WAKE_WORD) -> float | None:
    """The best confidence for the word in one Vosk result, or None if it isn't there.

    For the wake word, only where nothing unknown comes before it in the utterance (rule B, 2026-11-07):
    "Kestrel ...", "hi Kestrel", "good morning Kestrel" wake it; "[unk] kestrel" (a podcast, "I saw a
    kestrel") doesn't. Other words (the spoken "cancel") are unchanged."""
    words = json.loads(result_json or "{}").get("result", [])
    names = [w.get("word") for w in words]
    confs = [
        float(w.get("conf", 0.0))
        for i, w in enumerate(words)
        if w.get("word") == word and (word != WAKE_WORD or all(n in GREETING_WORDS for n in names[:i]))
    ]
    return max(confs) if confs else None


def wake_position(result_json: str, word: str = WAKE_WORD) -> str | None:
    """Where the wake word sat in one Vosk result, as a category with no words in it: "alone",
    "greeting" (only greetings before it, nothing after), "first" (more after it), or "after_speech"
    (something unknown before it). For the wake span only; it doesn't decide anything."""
    words = [w.get("word") for w in json.loads(result_json or "{}").get("result", [])]
    if word not in words:
        return None
    at = words.index(word)
    before, after = words[:at], words[at + 1 :]
    known = {part for greeting in GREETINGS for part in greeting.split()}
    if not all(w in known for w in before):
        return "after_speech"
    if after:
        return "first"
    return "greeting" if before else "alone"


def heard_greeting(result_json: str) -> str | None:
    """The first greeting in one Vosk result ("hi", "good morning"...), or None."""
    words = [w.get("word") for w in json.loads(result_json or "{}").get("result", [])]
    for i in range(len(words)):
        for greeting in GREETINGS:
            parts = greeting.split()
            if words[i : i + len(parts)] == parts:
                return greeting
    return None


def wake_events(
    make_recognizer: RecognizerFactory, pcm: bytes, chunk_bytes: int, grammar: tuple[str, ...] = GRAMMAR
) -> list[tuple[float, float, str | None]]:
    """Offline (scripts/wake_eval.py, talk_eval.py): (seconds into the audio, confidence, greeting heard)
    for every "kestrel" the recognizer reports, fed in the live service's chunk size. The time is the
    end of the chunk where the live detector would fire."""
    rec = make_recognizer(grammar_json(grammar=grammar))
    events: list[tuple[float, float, str | None]] = []
    for i in range(0, len(pcm), chunk_bytes):
        if rec.AcceptWaveform(pcm[i : i + chunk_bytes]):
            result = rec.Result()
            if (conf := wake_confidence(result)) is not None:
                events.append((min(i + chunk_bytes, len(pcm)) / 2 / SAMPLE_RATE, conf, heard_greeting(result)))
    final = rec.FinalResult()
    if (conf := wake_confidence(final)) is not None:  # an utterance still open at the end
        events.append((len(pcm) / 2 / SAMPLE_RATE, conf, heard_greeting(final)))
    return events


def after_cooldown(times: list[float], cooldown_s: float) -> list[float]:
    """The wakes the live service acts on: one, then nothing for cooldown_s (WakeService.on_wake)."""
    kept: list[float] = []
    for t in sorted(times):
        if not kept or t - kept[-1] >= cooldown_s:
            kept.append(t)
    return kept


class VoskDetector:
    """partial=True also acts on Vosk's partial result (no confidence there: counted as 1.0), which
    comes before the end of the utterance. Used only for "cancel", where a false hit is harmless."""

    def __init__(
        self, make_recognizer: RecognizerFactory, threshold: float, word: str = WAKE_WORD, partial: bool = False
    ):
        self.threshold = threshold
        self.word = word
        self.partial = partial
        self.greeting: str | None = None  # the greeting heard with the last wake, for the reply
        self.position: str | None = None  # where the word sat in the last wake (wake_position), for the span
        self.recognizer = make_recognizer(grammar_json(word))

    def feed(self, pcm: bytes) -> float | None:
        if not self.recognizer.AcceptWaveform(pcm):  # True at the end of an utterance
            if (
                self.partial
                and self.word in json.loads(self.recognizer.PartialResult() or "{}").get("partial", "").split()
            ):
                return 1.0
            return None
        result = self.recognizer.Result()
        conf = wake_confidence(result, self.word)
        if conf is None or conf < self.threshold:
            return None
        self.greeting = heard_greeting(result)
        self.position = wake_position(result, self.word)
        return conf
