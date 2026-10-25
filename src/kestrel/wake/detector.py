"""Wake-word detection behind a small interface, so the engine can be swapped (docs/voice-wake.md).

VoskDetector runs the Vosk small English model restricted to a two-entry grammar:
"kestrel" and "[unk]". The "[unk]" entry matters: with a grammar of only "kestrel", every
utterance would be forced onto the wake word. With it, other speech comes out as "[unk]".
The same class, with the word "cancel", listens for a spoken cancel during the 2-second send
countdown (talk.CANCEL_WORD): only then, and acting on Vosk's partial result so it is fast enough.

Audio arrives as 16 kHz mono 16-bit PCM chunks and is only ever held in memory.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

WAKE_WORD = "kestrel"
GRAMMAR = (WAKE_WORD, "[unk]")
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


def grammar_json(word: str = WAKE_WORD) -> str:
    return json.dumps([word, "[unk]"])


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


def wake_confidence(result_json: str, word: str = WAKE_WORD) -> float | None:
    """The best confidence for the word in one Vosk result, or None if it isn't there."""
    words = json.loads(result_json or "{}").get("result", [])
    confs = [float(w.get("conf", 0.0)) for w in words if w.get("word") == word]
    return max(confs) if confs else None


def wake_events(make_recognizer: RecognizerFactory, pcm: bytes, chunk_bytes: int) -> list[tuple[float, float]]:
    """Offline (scripts/wake_eval.py, talk_eval.py): (seconds into the audio, confidence) for every
    "kestrel" the recognizer reports, fed in the live service's chunk size. The time is the end of the
    chunk where the live detector would fire."""
    rec = make_recognizer(grammar_json())
    events: list[tuple[float, float]] = []
    for i in range(0, len(pcm), chunk_bytes):
        if rec.AcceptWaveform(pcm[i : i + chunk_bytes]) and (conf := wake_confidence(rec.Result())) is not None:
            events.append((min(i + chunk_bytes, len(pcm)) / 2 / SAMPLE_RATE, conf))
    if (conf := wake_confidence(rec.FinalResult())) is not None:  # an utterance still open at the end
        events.append((len(pcm) / 2 / SAMPLE_RATE, conf))
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
        self.recognizer = make_recognizer(grammar_json(word))

    def feed(self, pcm: bytes) -> float | None:
        if not self.recognizer.AcceptWaveform(pcm):  # True at the end of an utterance
            if (
                self.partial
                and self.word in json.loads(self.recognizer.PartialResult() or "{}").get("partial", "").split()
            ):
                return 1.0
            return None
        conf = wake_confidence(self.recognizer.Result(), self.word)
        return conf if conf is not None and conf >= self.threshold else None
