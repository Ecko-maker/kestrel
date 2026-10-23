"""Wake-word detection behind a small interface, so the engine can be swapped (docs/voice-wake.md).

VoskDetector runs the Vosk small English model restricted to a two-entry grammar:
"kestrel" and "[unk]". The "[unk]" entry matters: with a grammar of only "kestrel", every
utterance would be forced onto the wake word. With it, other speech comes out as "[unk]".

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


RecognizerFactory = Callable[[str], Recognizer]  # grammar JSON -> recognizer


def grammar_json() -> str:
    return json.dumps(list(GRAMMAR))


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


def wake_confidence(result_json: str) -> float | None:
    """The best confidence for the wake word in one Vosk result, or None if it isn't there."""
    words = json.loads(result_json or "{}").get("result", [])
    confs = [float(w.get("conf", 0.0)) for w in words if w.get("word") == WAKE_WORD]
    return max(confs) if confs else None


class VoskDetector:
    def __init__(self, make_recognizer: RecognizerFactory, threshold: float):
        self.threshold = threshold
        self.recognizer = make_recognizer(grammar_json())

    def feed(self, pcm: bytes) -> float | None:
        if not self.recognizer.AcceptWaveform(pcm):  # True at the end of an utterance
            return None
        conf = wake_confidence(self.recognizer.Result())
        return conf if conf is not None and conf >= self.threshold else None
