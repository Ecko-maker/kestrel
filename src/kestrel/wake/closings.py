"""The follow-up window and local closings (docs/voice-wake.md, "Follow-up and closings").

After Kestrel finishes speaking an answer to a spoken request, it listens for a few seconds without the
wake word. What it hears takes one of three paths, decided by FollowupWindow, the same code in the live
service and in scripts/closings_eval.py:

- a closing ("thank you", "bye"...), heard by a small Vosk grammar, gets a fixed local reply: no model,
  no chat message, no tool. It must be the whole utterance: "thanks, now list my files" comes out as
  "thanks [unk] ..." and is not a closing, so a request that starts with thanks still reaches Whisper;
- other speech takes the normal path (Whisper, the guard, the send countdown, "cancel");
- silence ends the window: back to Standby, with nothing said.

"Thank you" stays on Whisper's filler list (talk.FILLERS): Whisper invents it on silence, so a bare
Whisper "thank you" is never trusted. Closings are handled only by this grammar, and only in the window.

Audio stays in memory, as everywhere in the wake flow.
"""

import json
from dataclasses import dataclass, field

from kestrel.wake.detector import Recognizer, RecognizerFactory, grammar_json
from kestrel.wake.talk import MAX_REQUEST_S, Endpointer, NoiseFloor

# All in vosk-model-small-en-us-0.15's vocabulary, "that's" as one word (vosk_model_find_word, 2026-11-05).
CLOSINGS = ("thank you", "thanks", "cheers", "that's all", "never mind", "bye")
THANKS = frozenset({"thank you", "thanks", "cheers"})
CLOSING_GRAMMAR = (*CLOSINGS, "[unk]")
DEFAULT_FOLLOWUP_S = 5.0  # KESTREL_TALK_FOLLOWUP_SECONDS
DEFAULT_CLOSING_THRESHOLD = 0.5  # KESTREL_TALK_CLOSING_THRESHOLD; provisional until scripts/closings_eval.py
SPEECH_ENDED = ("waiting", "speaking")  # the Endpointer's states while the window is still open


def parse_closing(words: list[str]) -> str | None:
    """The first closing, if the words are closings and nothing else ("thanks bye"), else None.
    Any other word, "[unk]" included, means this was not only a closing."""
    first = None
    i = 0
    while i < len(words):
        match = next((c for c in CLOSINGS if words[i : i + len(c.split())] == c.split()), None)
        if match is None:
            return None
        first = first or match
        i += len(match.split())
    return first


def closing_reply(phrase: str, name: str | None, turn: int = 0) -> str:
    """The local reply: fixed text, no model. turn alternates the two thanks replies."""
    if phrase not in THANKS:
        return "Okay, I'm here if you need me."
    reply = ("You're welcome", "Anytime")[turn % 2]
    return f"{reply}, {name}." if name else f"{reply}."


class ClosingListener:
    """Vosk restricted to the closings and "[unk]", one per window. Fed the live chunks; finish() says
    what the whole utterance was. It never acts on a partial result: "thanks" may be how a request starts."""

    def __init__(self, make_recognizer: RecognizerFactory):
        self.recognizer: Recognizer = make_recognizer(grammar_json(grammar=CLOSING_GRAMMAR))
        self.words: list[dict] = []

    def feed(self, pcm: bytes) -> None:
        if self.recognizer.AcceptWaveform(pcm):
            self.words += json.loads(self.recognizer.Result() or "{}").get("result", [])

    def finish(self) -> tuple[str | None, float]:
        """(the closing heard, or None; the lowest confidence among its words)."""
        self.words += json.loads(self.recognizer.FinalResult() or "{}").get("result", [])
        phrase = parse_closing([str(w.get("word")) for w in self.words])
        if phrase is None:
            return None, 0.0
        return phrase, min(float(w.get("conf", 0.0)) for w in self.words)


@dataclass
class WindowEnd:
    state: str  # the Endpointer's: done | no_speech | max
    phrase: str | None  # the closing the grammar heard, at any confidence
    confidence: float
    audio: bytes
    speech_s: float
    seconds: float  # how long the window listened

    def is_closing(self, threshold: float) -> bool:
        return self.phrase is not None and self.confidence >= threshold

    def path(self, threshold: float) -> str:
        """closing, silence, or speech (the normal path: Whisper, the guard, the countdown)."""
        if self.is_closing(threshold):
            return "closing"
        return "silence" if self.state == "no_speech" else "speech"


@dataclass
class FollowupWindow:
    """One window: speech must start within seconds; once it has, the usual end-of-speech rule applies."""

    seconds: float
    floor: float
    listener: ClosingListener | None = None
    audio: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        self.endpoint = Endpointer(floor=self.floor, start_timeout_s=self.seconds)

    @property
    def longest_s(self) -> float:
        """The most it can listen: speech starting at the very end, then a full request."""
        return self.seconds + MAX_REQUEST_S

    def feed(self, chunk: bytes, level: float) -> WindowEnd | None:
        self.audio += chunk
        if self.listener is not None:
            self.listener.feed(chunk)
        state = self.endpoint.feed(level)
        return None if state in SPEECH_ENDED else self.finish(state)

    def finish(self, state: str | None = None) -> WindowEnd:
        """The window is over. state None: the audio ran out (the eval's clips)."""
        phrase, confidence = self.listener.finish() if self.listener is not None else (None, 0.0)
        ended = state or ("done" if self.endpoint.started else "no_speech")
        seconds = self.endpoint.chunks * 0.1
        return WindowEnd(ended, phrase, confidence, bytes(self.audio), self.endpoint.speech_s, seconds)


def learned_floor(levels: list[float]) -> list[float]:
    """The noise floor the live service would have reached at each chunk (for the eval's random windows)."""
    noise = NoiseFloor()
    return [noise.update(level) for level in levels]
