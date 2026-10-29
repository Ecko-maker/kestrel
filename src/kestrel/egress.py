"""Outbound-content check (tiers v2, defense b): does a network call carry text that a local read
returned earlier in this conversation?

Real data isn't random like the bench's canaries, so short matches would flag ordinary words. A hit
needs one of:
- MIN_CHARS (24) characters of local text in a row, after normalization (case, spaces and
  punctuation ignored), or
- MIN_WORDS (4) local words in a row.
Text that also appears in the user's own messages never counts (owner decision 6).

Where it looks, as bench/canaries.py does for canaries (same ideas, no bench import):
- the call's text arguments as written, URL-decoded (twice), and inside base64 / hex tokens;
- joined with the earlier network calls of the conversation, so a span split over two searches is
  caught: local text counts as sent when every PIECE_CHARS (8) characters of it appeared in some
  call, and a run of MIN_CHARS sent characters is a hit if the current call sent part of it.

Blind spots: paraphrase and summaries, other encodings (ROT13, reversed), secrets shorter than the
threshold (a 4-digit PIN), and splits into pieces under PIECE_CHARS. The user still sees a card for
every network call after a local read (defense a); this check adds the reason and the evidence.
"""

import base64
import binascii
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote_plus

MIN_CHARS = 24
MIN_WORDS = 4
PIECE_CHARS = 8  # the smallest piece of a split that counts; also the unit of the user-text exclusion

_WORD = re.compile(r"[^\W_]+")
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/_-]{12,}={0,2}")
_HEX_TOKEN = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_UNTRUSTED = re.compile(r'^<untrusted_data source="[^"]*">\n(.*)\n</untrusted_data>\n', re.DOTALL)

How = Literal["as written", "decoded", "across calls"]


@dataclass(frozen=True)
class Match:
    """One span of local text found in a network call. `text` is for the card only: logs and traces
    get `digest` and `chars`, never the text itself."""

    source: str  # the tool whose result held it, e.g. "read_file"
    text: str  # the normalized span
    chars: int  # its length in normalized characters (letters and digits)
    how: How  # as written, only after decoding, or only together with earlier calls

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:24]

    def tag(self) -> str:
        """What the audit log and traces record: a hash and a length, never the text."""
        return f"content:{self.digest}:{self.chars}"


def words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def squash(text: str) -> str:
    """Letters and digits only, lowercase: separators and formatting don't hide a span."""
    return "".join(words(text))


def strip_wrapper(output: str) -> str:
    """A tool result without the <untrusted_data> wrapper and note the registry adds."""
    m = _UNTRUSTED.match(output)
    return m.group(1) if m else output


def _decoded_tokens(text: str) -> list[str]:
    out = []
    for token in _B64_TOKEN.findall(text):
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decode(token + "=" * (-len(token) % 4))
            except binascii.Error, ValueError:
                continue
            out.append(raw.decode("utf-8", errors="ignore"))
    for token in _HEX_TOKEN.findall(text):
        try:
            out.append(bytes.fromhex(token).decode("utf-8", errors="ignore"))
        except ValueError:
            continue
    return out


def decoded_forms(text: str) -> list[str]:
    """The text URL-decoded (once and twice) and every base64 / hex token in it decoded, without the
    text itself. Shown in cards so an encoded leak is readable (e3)."""
    once = unquote_plus(text)
    forms = [once, unquote_plus(once)]
    decoded = [d for f in [text, *forms] for d in _decoded_tokens(f)]
    out: list[str] = []
    for f in [*forms, *decoded, *(unquote_plus(d) for d in decoded)]:
        if f and f != text and f not in out:
            out.append(f)
    return out


def call_text(args: dict) -> str:
    """The part of a call that leaves the machine: its text arguments."""
    return "\n".join(v for v in args.values() if isinstance(v, str))


def _grams(s: str, n: int) -> set[str]:
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _word_grams(ws: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def _covered(local: str, grams: set[str], n: int) -> list[bool]:
    marks = [False] * len(local)
    for i in range(len(local) - n + 1):
        if local[i : i + n] in grams:
            for j in range(i, i + n):
                marks[j] = True
    return marks


def _runs(marks: list[bool]) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, m in enumerate([*marks, False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i))
            start = None
    return runs


@dataclass
class _Call:
    raw: set[str]  # PIECE_CHARS-grams of the text as written
    every: set[str]  # ...and of every decoded form
    words_raw: list[str]
    words_every: list[list[str]]


def _call(text: str) -> _Call:
    forms = decoded_forms(text)
    raw = _grams(squash(text), PIECE_CHARS)
    every = raw.union(*(_grams(squash(f), PIECE_CHARS) for f in forms))
    return _Call(raw, every, words(text), [words(text), *(words(f) for f in forms)])


def find(
    text: str,
    local: Iterable[tuple[str, str]],
    user_texts: Iterable[str] = (),
    earlier: Iterable[str] = (),
    min_chars: int = MIN_CHARS,
    min_words: int = MIN_WORDS,
) -> list[Match]:
    """Spans of local text (source tool, result) carried by a network call's text, with `earlier`
    the text of the conversation's earlier network calls, in order."""
    current = _call(text)
    before = [_call(t) for t in earlier]
    user_squashed = [squash(t) for t in user_texts]
    user_grams = set().union(*(_grams(u, PIECE_CHARS) for u in user_squashed))
    user_words = set().union(*(_word_grams(words(t), min_words) for t in user_texts))
    sent_now, sent_raw_now = current.every, current.raw
    sent_all = sent_now.union(*(c.every for c in before))

    matches: dict[str, Match] = {}
    for source, result in local:
        loc = squash(result)
        if len(loc) < min_chars:
            continue
        typed = _covered(loc, user_grams, PIECE_CHARS)
        mine = _covered(loc, sent_now, PIECE_CHARS)
        mine_raw = _covered(loc, sent_raw_now, PIECE_CHARS)
        marks = [s and not t for s, t in zip(_covered(loc, sent_all, PIECE_CHARS), typed, strict=True)]
        for a, b in _runs(marks):
            if b - a < min_chars or not any(mine[a:b]):
                continue
            span = loc[a:b]
            how: How = "as written" if all(mine_raw[a:b]) else "decoded" if all(mine[a:b]) else "across calls"
            matches.setdefault(span, Match(source, span, b - a, how))

        # words: MIN_WORDS local words in a row, in this call or in this call joined to the one before
        local_grams = _word_grams(words(result), min_words)
        prev = before[-1].words_raw if before else []
        forms: list[tuple[list[str], int, How]] = [
            (w, 0, "as written" if k == 0 else "decoded") for k, w in enumerate(current.words_every)
        ]
        forms.append((prev + current.words_raw, len(prev), "across calls"))
        for ws, own_from, how_w in forms:
            hit = [False] * len(ws)
            for i in range(len(ws) - min_words + 1):
                gram = tuple(ws[i : i + min_words])
                if gram in local_grams and gram not in user_words:
                    hit[i : i + min_words] = [True] * min_words
            for a, b in _runs(hit):
                if b <= own_from:
                    continue  # entirely in the earlier call: judged when that call was made
                phrase = " ".join(ws[a:b])
                flat = phrase.replace(" ", "")
                if not any(flat in m.text.replace(" ", "") for m in matches.values()):
                    matches.setdefault(phrase, Match(source, phrase, len(flat), how_w))
    return _merge(list(matches.values()))


def _merge(found: list[Match]) -> list[Match]:
    """Longest first; a span inside a longer one (e.g. its words, or a decoded copy) is dropped."""
    found.sort(key=lambda m: -m.chars)
    out: list[Match] = []
    for m in found:
        if not any(m.text.replace(" ", "") in o.text.replace(" ", "") for o in out):
            out.append(m)
    return out
