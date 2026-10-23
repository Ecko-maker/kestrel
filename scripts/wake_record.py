"""Record your own "Kestrel" samples (or background audio) for scripts/wake_eval.py.

This is the only Kestrel code that saves audio, and only when you run it: clips go to the folder you
choose (default data/wake/positives/, which git ignores). The live wake word never saves anything.

    uv run --no-sync python scripts/wake_record.py                    # 30 clips of 2 s, Enter before each
    uv run --no-sync python scripts/wake_record.py --count 10 --out data/wake/positives-far
"""

import argparse
import sys
from pathlib import Path

SAMPLE_RATE = 16_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data") / "wake" / "positives")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    import sounddevice as sd  # optional dependencies: uv sync --extra wake
    import soundfile as sf

    args.out.mkdir(parents=True, exist_ok=True)
    start = len(list(args.out.glob("kestrel-*.wav")))
    print(f"Recording {args.count} clips of {args.seconds:g} s into {args.out}. Vary distance, volume and tone.")
    for i in range(start, start + args.count):
        if input(f"[{i - start + 1}/{args.count}] Enter, then say 'Kestrel' (q + Enter to stop): ").strip() == "q":
            break
        clip = sd.rec(int(args.seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        sf.write(args.out / f"kestrel-{i:03d}.wav", clip, SAMPLE_RATE, subtype="PCM_16")
    print(f"done: {len(list(args.out.glob('kestrel-*.wav')))} clips in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
