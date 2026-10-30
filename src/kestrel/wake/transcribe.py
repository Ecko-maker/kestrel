"""Local speech-to-text for spoken requests: whisper.cpp (pywhispercpp) with an English-only model.

base.en, 5-bit quantised (57 MB) by default; tiny.en (31 MB) is the faster fallback
(KESTREL_TALK_MODEL=tiny.en-q5_1). Model files come from scripts/get_talk_model.py, SHA-256 pinned
here; the transcriber only ever opens a local file, so nothing is downloaded at run time.
Audio arrives as bytes in memory and is never written anywhere.

Vocabulary hint: whisper.cpp gets a short initial prompt (talk_prompt) with Kestrel's own words, so it
leans towards "notes" over "knots" and spells "Kestrel" and your contacts' names right. It is built
from general Kestrel words and a local, git-ignored name list (KESTREL_TALK_VOCAB_FILE), never from
the evaluation sentences. The server and scripts/talk_eval.py build it with the same function.
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
    # 2026-10-24, owner-approved download (190 MB); hash from the Hugging Face listing, checked on download
    "small.en-q5_1": ("bfdff4894dcb76bbf647d56263ea2a96645423f1669176f4844a1bf8e478ad30", 190_098_681),
}
DEFAULT_TALK_MODEL = "base.en-q5_1"
NO_SPEECH_THOLD = 0.6  # whisper.cpp's own no-speech threshold (its default)

# Kestrel's name and its tools in plain words (create_note, append_to_file, list_files, read_file,
# web_search, calculator, send_message, get_current_time / weather questions). Not the test sentences.
VOCAB_WORDS = ("note", "notes", "todo list", "workspace", "file", "folder", "email", "search", "calculator", "weather")
MAX_PROMPT_CHARS = 200
MAX_NAME_CHARS = 40
DEFAULT_VOCAB_FILE = Path("data") / "talk" / "vocab.txt"  # git-ignored: your contacts' names, one per line


def vocab_file() -> Path:
    env = os.getenv("KESTREL_TALK_VOCAB_FILE")
    return Path(env) if env else DEFAULT_VOCAB_FILE


def read_vocab(path: Path) -> list[str]:
    """Names, one per line; blank lines and # comments skipped; a missing file means no names."""
    if not path.is_file():
        return []
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = " ".join(line.split("#", 1)[0].split())
        if name and name.isprintable() and len(name) <= MAX_NAME_CHARS and name not in names:
            names.append(name)
    return names


def talk_prompt(names: list[str] | None = None) -> str:
    """'Kestrel: note, notes, ..., weather. Names: Priya, Sam.' At most MAX_PROMPT_CHARS: names that
    don't fit are left out (the first ones in the file win)."""
    prompt = f"Kestrel: {', '.join(VOCAB_WORDS)}."
    kept: list[str] = []
    for name in names or []:
        candidate = f"{prompt} Names: {', '.join([*kept, name])}."
        if len(candidate) > MAX_PROMPT_CHARS:
            break
        kept.append(name)
    return f"{prompt} Names: {', '.join(kept)}." if kept else prompt


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
    def __init__(self, model_file: Path, threads: int = 8, prompt: str | None = None):
        self.prompt = prompt  # the vocabulary hint; None: no hint (talk_eval compares both)
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
        # Always passed, "" for none: pywhispercpp keeps parameters between calls, so leaving it out
        # would silently reuse the previous call's prompt (found while comparing with and without).
        segments = self.model.transcribe(audio, extract_probability=True, initial_prompt=self.prompt or "")
        text = " ".join(s.text.strip() for s in segments).strip()
        probs = [float(s.probability) for s in segments if not math.isnan(float(s.probability))]
        return Transcript(text, sum(probs) / len(probs) if probs else None)
