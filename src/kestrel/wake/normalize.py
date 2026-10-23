"""Text normalisation for word error rate (scripts/talk_eval.py): two transcripts that mean the same
words should compare equal, the way Whisper's English normaliser does it, written small here.

    "What's 17.5% of 2,340?"  and  "what is seventeen and a half percent of two thousand three hundred forty"
    both become  "what is 17.5 percent of 2340"

Steps: lower case; drop sound labels ("[BLANK_AUDIO]", "(music)"); expand common contractions;
symbols and units to words (%, &, $); thousands separators out; punctuation out (decimal points kept);
number words to digits ("seventeen and a half" -> 17.5, "a hundred" -> 100, "point five" -> .5).
"""

import re

from kestrel.wake.talk import strip_annotations

CONTRACTIONS = {
    "what's": "what is",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "here's": "here is",
    "let's": "let us",
    "i'm": "i am",
    "i'll": "i will",
    "i'd": "i would",
    "i've": "i have",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "can't": "can not",
    "cannot": "can not",
    "won't": "will not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "couldn't": "could not",
    "wouldn't": "would not",
    "shouldn't": "should not",
}
SPELLINGS = {"ok": "okay", "e-mail": "email", "todo": "to do"}

UNITS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
TENS = {
    w: 10 * i
    for i, w in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], start=2)
}
SCALES = {"thousand": 1_000, "million": 1_000_000}
NUMBER_WORDS = set(UNITS) | set(TENS) | set(SCALES) | {"hundred"}


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _numbers(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t not in UNITS and t not in TENS and not (t == "a" and nxt in ("hundred", *SCALES)):
            out.append(t)
            i += 1
            continue
        total = current = 0
        j = i
        while j < len(tokens):
            w = tokens[j]
            after = tokens[j + 1] if j + 1 < len(tokens) else ""
            if w in UNITS:
                current += UNITS[w]
            elif w in TENS:
                current += TENS[w]
            elif w == "a" and after in ("hundred", *SCALES) and current == 0:
                current = 1
            elif w == "hundred":
                current = (current or 1) * 100
            elif w in SCALES:
                total += (current or 1) * SCALES[w]
                current = 0
            elif w == "and" and after in NUMBER_WORDS and (current or total):
                pass  # "three hundred and forty"
            else:
                break
            j += 1
        value: float = total + current
        if tokens[j : j + 3] == ["and", "a", "half"]:
            value += 0.5
            j += 3
        elif tokens[j : j + 1] == ["point"] and j + 1 < len(tokens) and tokens[j + 1] in UNITS:
            digits = ""
            j += 1
            while j < len(tokens) and tokens[j] in UNITS and UNITS[tokens[j]] < 10:
                digits += str(UNITS[tokens[j]])
                j += 1
            value = float(f"{int(value)}.{digits}")
        out.append(_fmt(value))
        i = j
    return out


def normalize(text: str) -> str:
    text = strip_annotations(text).lower().replace("’", "'")
    for short, long in CONTRACTIONS.items():
        text = re.sub(rf"\b{re.escape(short)}\b", long, text)
    text = re.sub(r"\$\s*(\d[\d,.]*)", r"\1 dollars", text)
    text = text.replace("%", " percent").replace("&", " and ")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 2,340 -> 2340
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # full stops out, decimal points kept
    text = re.sub(r"[^\w\s'.-]|(?<!\w)'|'(?!\w)", " ", text)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)  # twenty-one -> twenty one
    tokens = [SPELLINGS.get(t, t) for t in text.replace("-", " ").split()]
    return " ".join(_numbers(tokens))


def raw_words(text: str) -> list[str]:
    """For the raw WER: lower case and punctuation only, nothing else."""
    return re.sub(r"[^\w\s']+", " ", text.lower()).split()


def word_errors(reference: list[str], hypothesis: list[str]) -> int:
    """Substitutions + deletions + insertions (Levenshtein over words)."""
    row = list(range(len(hypothesis) + 1))
    for i in range(1, len(reference) + 1):
        previous, row[0] = row[0], i
        for j in range(1, len(hypothesis) + 1):
            substitution = previous + (reference[i - 1] != hypothesis[j - 1])
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, substitution)
    return row[len(hypothesis)]
