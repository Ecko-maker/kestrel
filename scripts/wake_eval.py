"""Measure the wake word on recorded audio, with the same detector code the live wake word uses.

- Detection rate: on your own "Kestrel" clips (scripts/wake_record.py), with a 95% Clopper-Pearson interval.
- Per phrase (--phrases, from wake_record.py --phrases: "Kestrel", "Hi Kestrel", ...): clips detected,
  and how often the greeting was heard right (Kestrel greets back with it).
- False wakes per hour: on background audio with no "Kestrel" in it (a podcast, TV), counting the
  10-second cooldown the live service applies.
- Old and new grammar side by side: "kestrel" + "[unk]" (before 2026-10-26) and with the greetings
  added (now). The wake rule is the same for both: "kestrel" anywhere in the utterance.
- CPU: process time per second of audio (new grammar).

Every threshold is reported, so the threshold (KESTREL_WAKE_THRESHOLD) can be chosen from the
numbers; docs/voice-wake.md says how. Reads WAV/FLAC/OGG/MP3 (soundfile); any rate, mixed to 16 kHz mono.

    uv run --no-sync python scripts/wake_eval.py --positives data/wake/positives --background data/wake/background
    uv run --no-sync python scripts/wake_eval.py --phrases data/wake/phrases --background data/wake/background
"""

import argparse
import json
import sys
import time
from pathlib import Path

from kestrel.bench.stats import clopper_pearson
from kestrel.wake.audio import CHUNK_FRAMES
from kestrel.wake.detector import (
    GRAMMAR,
    KESTREL_ONLY,
    SAMPLE_RATE,
    WAKE_WORD,
    after_cooldown,
    vosk_recognizer_factory,
    wake_events,
)
from kestrel.wake.service import COOLDOWN_S, default_model_dir

AUDIO = {".wav", ".flac", ".ogg", ".mp3"}
GRAMMARS = {"old": KESTREL_ONLY, "new": GRAMMAR}
Events = list[tuple[float, float, str | None]]


def phrase_of(name: str) -> str:
    """'hi-kestrel-003.wav' -> 'hi kestrel'; 'kestrel-012.wav' -> 'kestrel'."""
    stem = Path(name).stem
    head, _, tail = stem.rpartition("-")
    return (head if tail.isdigit() and head else stem).replace("-", " ").lower()


def expected_greeting(phrase: str) -> str | None:
    """The greeting said before "kestrel" in a phrase, or None."""
    before = phrase.split(WAKE_WORD)[0].strip()
    return before or None


def detected(clips: dict[str, Events], threshold: float) -> int:
    return sum(any(c >= threshold for _, c, _ in ev) for ev in clips.values())


def greeted(clips: dict[str, Events], threshold: float) -> int:
    """Clips detected whose greeting was heard as said (the reply echoes it)."""
    count = 0
    for name, ev in clips.items():
        hits = [g for _, c, g in ev if c >= threshold]
        count += bool(hits) and hits[0] == expected_greeting(phrase_of(name))
    return count


def false_wakes(background: dict[str, Events], threshold: float) -> int:
    return sum(len(after_cooldown([s for s, c, _ in ev if c >= threshold], COOLDOWN_S)) for ev in background.values())


def files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO) if p.is_dir() else [p]
    return out


def load_pcm(path: Path) -> bytes:
    """16 kHz mono 16-bit PCM, in memory."""
    import numpy as np  # optional dependencies (with soundfile): uv sync --extra wake
    import soundfile as sf

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:  # linear resampling is enough for speech at these rates
        n = int(len(mono) * SAMPLE_RATE / rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--positives", type=Path, nargs="*", default=[], help="clips that each say 'Kestrel'")
    parser.add_argument("--phrases", type=Path, nargs="*", default=[], help="wake_record.py --phrases clips")
    parser.add_argument("--background", type=Path, nargs="*", default=[], help="audio with no 'Kestrel' in it")
    parser.add_argument("--model", type=Path, default=default_model_dir())
    parser.add_argument("--thresholds", default="0,0.3,0.5,0.6,0.7,0.8,0.9,0.95")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()

    if not args.positives and not args.phrases:
        parser.error("give --positives and/or --phrases")
    make_recognizer = vosk_recognizer_factory(args.model)
    thresholds = [float(t) for t in args.thresholds.split(",")]
    cpu = audio_s = 0.0

    def run(paths: list[Path]) -> dict[str, dict[str, Events]]:
        """{grammar: {file: events}} for both grammars."""
        nonlocal cpu, audio_s
        out: dict[str, dict[str, Events]] = {g: {} for g in GRAMMARS}
        for path in files(paths):
            pcm = load_pcm(path)
            audio_s += len(pcm) / 2 / SAMPLE_RATE
            for name, grammar in GRAMMARS.items():
                start = time.process_time()
                out[name][path.name] = wake_events(make_recognizer, pcm, CHUNK_FRAMES * 2, grammar)
                if name == "new":
                    cpu += time.process_time() - start
        return out

    positives = run(args.positives)
    phrases = run(args.phrases)
    background = run(args.background)
    hours = sum(len(load_pcm(p)) / 2 / SAMPLE_RATE for p in files(args.background)) / 3600
    clips = {g: positives[g] | phrases[g] for g in GRAMMARS}  # every clip says "kestrel"
    n = len(clips["new"])

    rows = []
    for t in thresholds:
        hit = detected(clips["new"], t)
        rate = clopper_pearson(hit, n)
        old_false, new_false = false_wakes(background["old"], t), false_wakes(background["new"], t)
        rows.append(
            {
                "threshold": t,
                "detected": hit,
                "detected_old_grammar": detected(clips["old"], t),
                "clips": n,
                "ci95": [rate.low, rate.high],
                "false_wakes": new_false,
                "false_wakes_old_grammar": old_false,
                "background_hours": round(hours, 3),
                "false_per_hour": round(new_false / hours, 2) if hours else None,
                "false_per_hour_old_grammar": round(old_false / hours, 2) if hours else None,
            }
        )

    def per_hour(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}"

    share = 100 * cpu / max(audio_s, 1e-9)
    print(f"{n} clips with 'Kestrel', {hours:.2f} h of background; CPU {share:.1f}% of one core")
    print("new = grammar with greetings (now); old = 'kestrel' + [unk] only. Wake rule: 'kestrel' anywhere.")
    print(
        f"{'threshold':>9}  {'detected (new)':>14}  {'95% CI':>9}  {'old':>6}  {'false/h new':>11}  {'false/h old':>11}"
    )
    for r in rows:
        ci = f"{100 * r['ci95'][0]:.0f}-{100 * r['ci95'][1]:.0f}%"
        new = f"{r['detected']}/{n} ({100 * r['detected'] / max(n, 1):.0f}%)"
        old = f"{r['detected_old_grammar']}/{n}"
        print(
            f"{r['threshold']:>9.2f}  {new:>14}  {ci:>9}  {old:>6}  "
            f"{per_hour(r['false_per_hour']):>11}  {per_hour(r['false_per_hour_old_grammar']):>11}"
        )

    by_phrase: dict[str, dict[str, Events]] = {}
    for name, ev in phrases["new"].items():
        by_phrase.setdefault(phrase_of(name), {})[name] = ev
    table = []
    if by_phrase:
        print("\nper phrase (new grammar): detected at each threshold; greeting heard right at each threshold")
        print(f"{'phrase':16}" + "".join(f"{t:>8.2f}" for t in thresholds))
        for phrase, group in sorted(by_phrase.items()):
            hits = [detected(group, t) for t in thresholds]
            echoes = [greeted(group, t) for t in thresholds]
            old_hits = [detected({k: phrases["old"][k] for k in group}, t) for t in thresholds]
            table.append(
                {
                    "phrase": phrase,
                    "clips": len(group),
                    "detected": hits,
                    "detected_old_grammar": old_hits,
                    "greeted": echoes,
                }
            )
            print(f"{phrase:16}" + "".join(f"{f'{h}/{len(group)}':>8}" for h in hits))
            if expected_greeting(phrase):
                print(f"{'  greeting right':16}" + "".join(f"{f'{e}/{len(group)}':>8}" for e in echoes))
            print(f"{'  old grammar':16}" + "".join(f"{f'{h}/{len(group)}':>8}" for h in old_hits))
    misses = [name for name, ev in clips["new"].items() if not ev]
    if misses:
        print(f"never heard as 'kestrel' at any threshold (new grammar): {', '.join(misses)}")
    if args.json:
        args.json.write_text(
            json.dumps({"rows": rows, "phrases": table, "cpu_share": cpu / max(audio_s, 1e-9)}, indent=2)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
