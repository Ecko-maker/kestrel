"""Measure the wake word on recorded audio, with the same detector code the live wake word uses.

- Detection rate: on your own "Kestrel" clips (scripts/wake_record.py), with a 95% Clopper-Pearson interval.
- False wakes per hour: on background audio with no "Kestrel" in it (a podcast, TV), counting the
  10-second cooldown the live service applies.
- CPU: process time per second of audio.

Every threshold is reported, so the threshold (KESTREL_WAKE_THRESHOLD) can be chosen from the
numbers; docs/voice-wake.md says how. Reads WAV/FLAC/OGG/MP3 (soundfile); any rate, mixed to 16 kHz mono.

    uv run --no-sync python scripts/wake_eval.py --positives data/wake/positives --background data/wake/background
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from kestrel.bench.stats import clopper_pearson
from kestrel.wake.audio import CHUNK_FRAMES
from kestrel.wake.detector import SAMPLE_RATE, after_cooldown, vosk_recognizer_factory, wake_events
from kestrel.wake.service import COOLDOWN_S, default_model_dir

AUDIO = {".wav", ".flac", ".ogg", ".mp3"}


def files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO) if p.is_dir() else [p]
    return out


def load_pcm(path: Path) -> bytes:
    """16 kHz mono 16-bit PCM, in memory."""
    import soundfile as sf

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:  # linear resampling is enough for speech at these rates
        n = int(len(mono) * SAMPLE_RATE / rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--positives", type=Path, nargs="+", required=True, help="clips that each say 'Kestrel'")
    parser.add_argument("--background", type=Path, nargs="*", default=[], help="audio with no 'Kestrel' in it")
    parser.add_argument("--model", type=Path, default=default_model_dir())
    parser.add_argument("--thresholds", default="0,0.3,0.5,0.6,0.7,0.8,0.9,0.95")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()

    make_recognizer = vosk_recognizer_factory(args.model)
    thresholds = [float(t) for t in args.thresholds.split(",")]
    cpu = audio_s = 0.0

    def run(path: Path) -> list[tuple[float, float]]:
        nonlocal cpu, audio_s
        pcm = load_pcm(path)
        audio_s += len(pcm) / 2 / SAMPLE_RATE
        start = time.process_time()
        events = wake_events(make_recognizer, pcm, CHUNK_FRAMES * 2)
        cpu += time.process_time() - start
        return events

    positives = {p.name: run(p) for p in files(args.positives)}
    background = {p.name: run(p) for p in files(args.background)}
    hours = sum(len(load_pcm(p)) / 2 / SAMPLE_RATE for p in files(args.background)) / 3600

    rows = []
    for t in thresholds:
        hit = sum(any(c >= t for _, c in ev) for ev in positives.values())
        rate = clopper_pearson(hit, len(positives))
        false = sum(len(after_cooldown([s for s, c in ev if c >= t], COOLDOWN_S)) for ev in background.values())
        rows.append(
            {
                "threshold": t,
                "detected": hit,
                "clips": len(positives),
                "ci95": [rate.low, rate.high],
                "false_wakes": false,
                "background_hours": round(hours, 3),
                "false_per_hour": round(false / hours, 2) if hours else None,
            }
        )

    share = 100 * cpu / max(audio_s, 1e-9)
    print(f"{len(positives)} 'Kestrel' clips, {hours:.2f} h of background; CPU {share:.1f}% of one core")
    print(f"{'threshold':>9}  {'detected':>14}  {'95% CI':>13}  {'false wakes':>11}  {'per hour':>8}")
    for r in rows:
        per_hour = "-" if r["false_per_hour"] is None else f"{r['false_per_hour']:.2f}"
        ci = f"{100 * r['ci95'][0]:.0f}-{100 * r['ci95'][1]:.0f}%"
        detected = f"{r['detected']}/{r['clips']} ({100 * r['detected'] / max(r['clips'], 1):.0f}%)"
        print(f"{r['threshold']:>9.2f}  {detected:>14}  {ci:>13}  {r['false_wakes']:>11}  {per_hour:>8}")
    misses = [name for name, ev in positives.items() if not ev]
    if misses:
        print(f"never heard as 'kestrel' at any threshold: {', '.join(misses)}")
    if args.json:
        args.json.write_text(json.dumps({"rows": rows, "cpu_share": cpu / max(audio_s, 1e-9)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
