"""Local speech-to-text for spoken requests: whisper.cpp (pywhispercpp) with an English-only model.

base.en, 5-bit quantised (57 MB) by default; tiny.en (31 MB) is the faster fallback
(KESTREL_TALK_MODEL=tiny.en-q5_1). Model files come from scripts/get_talk_model.py, SHA-256 pinned
here; the transcriber only ever opens a local file, so nothing is downloaded at run time.
Audio arrives as bytes in memory and is never written anywhere.
"""

import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kestrel.wake.detector import SAMPLE_RATE

TALK_MODELS = {  # name -> (SHA-256, bytes), fetched 2026-10-23 from huggingface.co/ggerganov/whisper.cpp
    "base.en-q5_1": ("4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f", 59_721_011),
    "tiny.en-q5_1": ("c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b", 32_166_155),
}
DEFAULT_TALK_MODEL = "base.en-q5_1"
NO_SPEECH_THOLD = 0.6  # whisper.cpp's own no-speech threshold (its default)


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None  # mean segment confidence (token probabilities), None if unknown


class Transcriber(Protocol):
    def transcribe(self, pcm: bytes) -> Transcript: ...


def default_talk_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") if sys.platform == "win32" else None
    return (Path(base) if base else Path.home() / ".cache") / "kestrel" / "models" / "whisper"


def talk_model_file(name: str, folder: Path | None = None) -> Path:
    if name not in TALK_MODELS:
        raise ValueError(f"unknown talk model {name!r}; choose one of {', '.join(TALK_MODELS)}")
    return (folder or default_talk_dir()) / f"ggml-{name}.bin"


class WhisperCppTranscriber:
    def __init__(self, model_file: Path, threads: int = 8):
        if not model_file.is_file():
            raise FileNotFoundError(f"no talk model at {model_file} (run scripts/get_talk_model.py)")
        from pywhispercpp.model import Model  # optional dependency: uv sync --extra wake

        self.model: Any = Model(
            str(model_file),  # a path: pywhispercpp only downloads when given a bare model name
            n_threads=threads,
            print_progress=False,
            print_realtime=False,
            redirect_whispercpp_logs_to=None,
            language="en",
            no_speech_thold=NO_SPEECH_THOLD,
        )

    def transcribe(self, pcm: bytes) -> Transcript:
        import numpy as np

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if len(audio) < SAMPLE_RATE // 10:
            return Transcript("", None)
        segments = self.model.transcribe(audio, extract_probability=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        probs = [float(s.probability) for s in segments if not math.isnan(float(s.probability))]
        return Transcript(text, sum(probs) / len(probs) if probs else None)
