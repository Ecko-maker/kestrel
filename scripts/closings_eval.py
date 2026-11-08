"""Measure the follow-up window's closings on recorded audio, with the live code (wake/closings.py).

- Closing detection per phrase: your clips from `wake_record.py --phrases --set closings`, at every
  threshold: heard as that closing, heard as another closing, missed. Each miss then takes the live path
  (Whisper and the guard) and is counted as sent, checked or dropped: a missed "never mind" isn't a filler,
  so it can reach the agent as a request.
- False closings and false requests: on background audio with no one talking to Kestrel, the window opens
  at random points (seeded), with the noise floor the live service would have learned by then. Reported per
  100 windows and per hour of audio heard inside windows. A window only opens after a spoken answer, so
  these are rates while a window is open, not per hour of the day.

Numbers only: no transcript is printed or saved. Nothing is sent anywhere, and no audio is written.

    uv run --no-sync python scripts/closings_eval.py --closings data/wake/closings --background data/wake/background
"""

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from wake_eval import files, load_pcm
from wake_record import CLOSING_PHRASES, slug

from kestrel.bench.stats import clopper_pearson
from kestrel.wake.audio import CHUNK_FRAMES
from kestrel.wake.closings import (
    CLOSINGS,
    DEFAULT_CLOSING_THRESHOLD,
    ClosingListener,
    FollowupWindow,
    WindowEnd,
    learned_floor,
)
from kestrel.wake.detector import SAMPLE_RATE, RecognizerFactory, vosk_recognizer_factory
from kestrel.wake.service import default_model_dir, loudness
from kestrel.wake.talk import DEFAULT_MIN_CONFIDENCE, guard
from kestrel.wake.transcribe import DEFAULT_TALK_MODEL, WhisperCppTranscriber, talk_model_file

CHUNK_BYTES = CHUNK_FRAMES * 2
LEAD_IN_CHUNKS = 3  # the recorder's countdown leaves about 0.5 s of quiet before you speak
SLUG_TO_PHRASE = {slug(p): p.lower() for p in CLOSING_PHRASES}


def phrase_of_clip(name: str) -> str | None:
    """'thats-all-003.wav' -> "that's all"; None for a file that isn't a closing clip."""
    head, _, tail = Path(name).stem.rpartition("-")
    return SLUG_TO_PHRASE.get(head) if tail.isdigit() else None


def chunks_of(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]


def run_window(
    chunks: list[bytes], levels: list[float], floor: float, seconds: float, make: RecognizerFactory
) -> WindowEnd:
    """One window over these chunks, exactly as WakeService feeds it."""
    window = FollowupWindow(seconds, floor, ClosingListener(make))
    for chunk, level in zip(chunks, levels, strict=True):
        end = window.feed(chunk, level)
        if end is not None:
            return end
    return window.finish()


def random_starts(rng: random.Random, chunks: int, windows: int, room: int) -> list[int]:
    """Where windows open in a file of this many chunks, leaving room for a whole window before the end."""
    return sorted(rng.randrange(0, chunks - room) for _ in range(windows)) if chunks > room else []


def split_windows(total: int, sizes: list[int]) -> list[int]:
    """total windows across files, in proportion to their length, adding up exactly."""
    whole = sum(sizes) or 1
    counts = [total * s // whole for s in sizes]
    for i in sorted(range(len(sizes)), key=lambda k: sizes[k], reverse=True)[: total - sum(counts)]:
        counts[i] += 1
    return counts


def closing_rows(results: list[tuple[str, WindowEnd]], thresholds: list[float]) -> list[dict]:
    """Per phrase and threshold: heard as said, heard as another closing, missed."""
    rows = []
    for phrase in CLOSINGS:
        ends = [end for said, end in results if said == phrase]
        for t in thresholds:
            right = sum(end.is_closing(t) and end.phrase == phrase for end in ends)
            other = sum(end.is_closing(t) and end.phrase != phrase for end in ends)
            rows.append({"phrase": phrase, "threshold": t, "clips": len(ends), "right": right, "other": other})
    return rows


def background_rows(ends: list[tuple[WindowEnd, str | None]], thresholds: list[float]) -> list[dict]:
    """ends: each window and what Whisper plus the guard made of its audio (None: no speech in it).
    A window is a false closing above the threshold; below it, its speech takes the normal path."""
    hours = sum(end.seconds for end, _ in ends) / 3600
    rows = []
    for t in thresholds:
        paths: Counter[str] = Counter()
        for end, verdict in ends:
            path = end.path(t)
            paths[(verdict or "drop") if path == "speech" else path] += 1
        windows = max(len(ends), 1)
        rows.append(
            {
                "threshold": t,
                "windows": len(ends),
                "window_hours": round(hours, 3),
                "false_closings": paths["closing"],
                "false_requests": paths["send"],
                "false_checks": paths["check"],
                "dropped": paths["drop"],
                "silence": paths["silence"],
                "false_closings_per_100": round(100 * paths["closing"] / windows, 1),
                "false_requests_per_100": round(100 * paths["send"] / windows, 1),
                "false_closings_per_hour": round(paths["closing"] / hours, 1) if hours else None,
                "false_requests_per_hour": round(paths["send"] / hours, 1) if hours else None,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--closings", type=Path, nargs="*", default=[], help="wake_record.py --phrases --set closings")
    parser.add_argument("--background", type=Path, nargs="*", default=[], help="audio with no one talking to Kestrel")
    parser.add_argument("--windows", type=int, default=200, help="random windows over the whole background")
    parser.add_argument("--seconds", type=float, default=5.0, help="the window's length")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--model", type=Path, default=default_model_dir())
    parser.add_argument("--talk-model", default=DEFAULT_TALK_MODEL)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--thresholds", default="0,0.3,0.5,0.7,0.8,0.9,0.95,1")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()
    if not args.closings and not args.background:
        parser.error("give --closings and/or --background")

    make = vosk_recognizer_factory(args.model)
    whisper = WhisperCppTranscriber(talk_model_file(args.talk_model))
    thresholds = [float(t) for t in args.thresholds.split(",")]
    out: dict = {"seconds": args.seconds, "seed": args.seed, "talk_model": args.talk_model}

    def verdict_of(end: WindowEnd) -> str:
        said = whisper.transcribe(end.audio)
        return guard(said.text, said.confidence, end.speech_s, args.min_confidence).action

    results: list[tuple[str, WindowEnd]] = []
    for path in files(args.closings):
        phrase = phrase_of_clip(path.name)
        if phrase is None:
            continue
        chunks = chunks_of(load_pcm(path))
        levels = [loudness(c) for c in chunks]
        floor = learned_floor(levels[:LEAD_IN_CHUNKS])[-1]
        results.append((phrase, run_window(chunks, levels, floor, args.seconds, make)))
    if results:
        rows = closing_rows(results, thresholds)
        print(f"{len(results)} closing clips; heard right (heard as another closing) at each threshold")
        print(f"{'phrase':12}" + "".join(f"{t:>9.2f}" for t in thresholds))
        for phrase in CLOSINGS:
            mine = [r for r in rows if r["phrase"] == phrase]
            cells = "".join(f"{f'{r["right"]}/{r["clips"]}({r["other"]})':>9}" for r in mine)
            print(f"{phrase:12}{cells}")
        for t in thresholds:
            hit = sum(r["right"] for r in rows if r["threshold"] == t)
            ci = clopper_pearson(hit, len(results))
            print(f"all at {t:.2f}: {hit}/{len(results)} ({100 * ci.low:.0f}-{100 * ci.high:.0f}% 95% CI)")
        misses: Counter[str] = Counter()
        for said, end in results:  # the live threshold: what a missed closing turns into
            if not end.is_closing(DEFAULT_CLOSING_THRESHOLD):
                path_name = end.path(DEFAULT_CLOSING_THRESHOLD)
                misses[f"{said}: {verdict_of(end) if path_name == 'speech' else path_name}"] += 1
        print(f"\nmisses at {DEFAULT_CLOSING_THRESHOLD}, by what the live path does with them:")
        print(dict(sorted(misses.items())) or "none")
        out["closings"] = rows
        out["misses"] = dict(misses)

    background = files(args.background)
    if background:
        rng = random.Random(args.seed)
        pcm = [load_pcm(p) for p in background]
        sizes = [len(x) // CHUNK_BYTES for x in pcm]
        room = int((args.seconds + 21) * 10)  # a full window, then a full request
        ends: list[tuple[WindowEnd, str | None]] = []
        started = time.monotonic()
        for data, windows in zip(pcm, split_windows(args.windows, sizes), strict=True):
            chunks = chunks_of(data)
            levels = [loudness(c) for c in chunks]
            floors = learned_floor(levels)
            for start in random_starts(rng, len(chunks), windows, room):
                stop = start + room
                end = run_window(chunks[start:stop], levels[start:stop], floors[start], args.seconds, make)
                ends.append((end, verdict_of(end) if end.state != "no_speech" else None))
        rows = background_rows(ends, thresholds)
        hours = sum(sizes) * 0.1 / 3600
        took = time.monotonic() - started
        print(f"\n{hours:.2f} h of background, {len(ends)} random windows of {args.seconds:g} s, took {took:.0f} s")
        print(
            f"{'threshold':>9} {'false closings':>15} {'/h':>7} {'false requests':>15} {'/h':>7} "
            f"{'checks':>7} {'dropped':>8} {'silence':>8}"
        )
        for r in rows:
            closing = f"{r['false_closings']} ({r['false_closings_per_100']}%)"
            request = f"{r['false_requests']} ({r['false_requests_per_100']}%)"
            print(
                f"{r['threshold']:>9.2f} {closing:>15} {r['false_closings_per_hour']:>7} {request:>15} "
                f"{r['false_requests_per_hour']:>7} {r['false_checks']:>7} {r['dropped']:>8} {r['silence']:>8}"
            )
        out["background"] = rows
        out["background_hours"] = round(hours, 3)
    if args.json:
        args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSample rate {SAMPLE_RATE} Hz; no transcript was printed or saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
