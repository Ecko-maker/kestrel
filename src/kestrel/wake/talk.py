"""Talking to Kestrel: after a wake, one spoken request is recorded in memory, transcribed locally and
sent to the console as if typed. This module holds the pure parts (docs/voice-wake.md, "Talking to Kestrel"):

- NoiseFloor / Endpointer: when the request starts and ends, from the loudness of each 0.1 s chunk
  (wake.service.loudness): speech is loudness above the room's noise floor; the request ends after
  1 s of quiet, after 20 s, or with nothing heard if no speech starts within 3 s.
- guard(): Whisper can invent text on silence or noise, which would reach the agent as if you had
  said it. Three outcomes: "send" (as if typed); "check" (low confidence: the text goes unsent into
  the console's message box, "Did I hear that right?"); "drop" ("I didn't catch that": under 0.4 s
  of speech, empty, a known filler hallucination, or the vocabulary hint echoed back).
- spoken_answer() and the fixed sentences: what Kestrel says back. Never an approval card's details.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

CHUNK_S = 0.1
START_TIMEOUT_S = 3.0  # nothing said within this after the greeting: "I didn't catch that"
END_SILENCE_S = 1.0  # this much quiet after speech ends the request
MAX_REQUEST_S = 20.0
SPEECH_MARGIN = 0.15  # loudness above the noise floor that counts as speech (0-1 scale, ~9 dB)
MIN_SPEECH_S = 0.4  # less speech than this: dropped (guard)
DEFAULT_MIN_CONFIDENCE = 0.45  # provisional; scripts/talk_eval.py reports confidences

APPROVAL_SENTENCE = "I need your approval on screen."
DIDNT_CATCH = "I didn't catch that."
CHECK_ON_SCREEN = "Please check what I heard on screen."
REST_ON_SCREEN = "The rest is on screen."
NO_CONSOLE = "Open the console first."  # never "Kestrel": it would wake itself
SPOKEN_SENTENCES = 2
MAX_SPOKEN_CHARS = 300


class NoiseFloor:
    """The room's quiet level, learned while waiting for the wake word: follows quiet chunks quickly
    and loud ones (speech, a door) only very slowly."""

    def __init__(self, start: float = 0.2):
        self.value = start

    def update(self, level: float) -> float:
        weight = 0.3 if level < self.value else 0.01
        self.value += weight * (level - self.value)
        return self.value


@dataclass
class Endpointer:
    """Fed one loudness value per 0.1 s chunk; says when the spoken request is over."""

    floor: float
    chunks: int = 0
    speech_chunks: int = 0
    quiet_chunks: int = 0
    started: bool = False
    state: str = field(default="waiting")  # waiting -> speaking -> done | no_speech | max

    def feed(self, level: float) -> str:
        self.chunks += 1
        speaking = level >= self.floor + SPEECH_MARGIN
        if speaking:
            self.started = True
            self.speech_chunks += 1
            self.quiet_chunks = 0
        elif self.started:
            self.quiet_chunks += 1
        if not self.started:
            self.state = "no_speech" if self.chunks * CHUNK_S >= START_TIMEOUT_S else "waiting"
        elif self.quiet_chunks * CHUNK_S >= END_SILENCE_S:
            self.state = "done"
        elif self.chunks * CHUNK_S >= MAX_REQUEST_S:
            self.state = "max"
        else:
            self.state = "speaking"
        return self.state

    @property
    def speech_s(self) -> float:
        return self.speech_chunks * CHUNK_S


# --- the hallucination guard -------------------------------------------------------------------

# Whole-transcript matches only (after normalising): "thank you for watching the match" is kept.
FILLERS = frozenset(
    {
        "",
        "you",
        "thank you",
        "thanks",
        "thank you for watching",
        "thanks for watching",
        "thank you so much for watching",
        "please subscribe",
        "bye",
        "okay",
        "so",
    }
)


def strip_annotations(text: str) -> str:
    """Whisper's sound labels: "[BLANK_AUDIO]", "(dramatic music)", "*laughs*", "♪"."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|♪+", " ", text)
    return " ".join(text.split())


def filler_key(text: str) -> str:
    """Lower case, no punctuation, single spaces: the form fillers are compared in."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", strip_annotations(text).lower()).split())


@dataclass(frozen=True)
class Verdict:
    action: Literal["send", "check", "drop"]
    text: str | None  # what to send or show; None when dropped
    reason: str


def echoes_prompt(text: str, prompt: str) -> bool:
    """Whisper sometimes returns its initial prompt on near-silence. Two or more words that appear,
    in order, inside the vocabulary hint are taken for that echo, not a request."""
    words = filler_key(text)
    return bool(prompt) and len(words.split()) >= 2 and f" {words} " in f" {filler_key(prompt)} "


def guard(text: str, confidence: float | None, speech_s: float, min_confidence: float, prompt: str = "") -> Verdict:
    """send, check (low confidence: you confirm on screen) or drop ("I didn't catch that")."""
    if speech_s < MIN_SPEECH_S:
        return Verdict("drop", None, "too little speech")
    cleaned = strip_annotations(text)
    key = filler_key(cleaned)
    if key in FILLERS:
        return Verdict("drop", None, "filler" if key else "empty")
    if echoes_prompt(cleaned, prompt):
        return Verdict("drop", None, "prompt echo")
    if confidence is not None and confidence < min_confidence:
        # 1 - confidence stands in for whisper.cpp's no-speech probability (not exposed by pywhispercpp)
        return Verdict("check", cleaned, "low confidence")
    return Verdict("send", cleaned, "ok")


# --- what Kestrel says ------------------------------------------------------------------------------


def for_speech(text: str) -> str:
    """Kestrel never says its own name: it would wake itself (known issue: self-interruption)."""
    return re.sub(r"\bkestrel\b", "I", text, flags=re.IGNORECASE)


def plain_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)  # code blocks
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> their text
    text = re.sub(r"https?://\S+", " ", text)  # bare URLs
    text = "\n".join(line for line in text.splitlines() if "|" not in line)  # tables
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", "", text)  # headings, quotes, lists
    text = re.sub(r"[*_~]+", "", text)
    return " ".join(text.split())


def spoken_answer(markdown: str) -> str:
    """The first two sentences, then "The rest is on screen." if there is more."""
    text = plain_text(markdown)
    if not text:
        return "The answer is on screen."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = " ".join(sentences[:SPOKEN_SENTENCES])
    more = len(sentences) > SPOKEN_SENTENCES
    if len(spoken) > MAX_SPOKEN_CHARS:
        spoken = spoken[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + "…"
        more = True
    return f"{spoken} {REST_ON_SCREEN}" if more else spoken
