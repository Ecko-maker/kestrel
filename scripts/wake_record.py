"""Record your own "Kestrel" samples, or background audio, for scripts/wake_eval.py and scripts/talk_eval.py.

This is the only Kestrel code that saves audio, and only when you run it: files go to the folder you
choose (defaults under data/, which git ignores). Nothing is sent anywhere. The live wake word never
saves anything.

    uv run --no-sync python scripts/wake_record.py                    # 30 clips of 2 s, Enter before each
    uv run --no-sync python scripts/wake_record.py --count 10 --out data/wake/positives-far
    uv run --no-sync python scripts/wake_record.py --background        # 60 min of room audio, 10-min files

--background records continuously (no Enter between files) into room-NNN.wav files of --chunk-minutes
each, 16 kHz mono 16-bit, showing "recording, minute X of N". Ctrl+C stops early: the files already
finished are kept, and so is the part of the current file recorded so far.
"""

import argparse
import queue
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path

SAMPLE_RATE = 16_000
BLOCK_S = 1  # the background recorder reads and writes one second at a time


def next_index(folder: Path, prefix: str) -> int:
    """The next free number for prefix-NNN.wav, so a second session never overwrites the first."""
    numbers = [
        int(p.stem.rsplit("-", 1)[1]) for p in folder.glob(f"{prefix}-*.wav") if p.stem.rsplit("-", 1)[1].isdigit()
    ]
    return max(numbers, default=-1) + 1


def record_background(
    out: Path,
    minutes: float,
    chunk_minutes: float,
    read_second: Callable[[], bytes],
    show: Callable[[str], None] = lambda line: print(line, end="\r", flush=True),
) -> list[Path]:
    """Record minutes of audio in files of chunk_minutes, one second (16-bit mono PCM) at a time.
    Ctrl+C (KeyboardInterrupt) stops early and keeps everything written so far. Returns the files."""
    out.mkdir(parents=True, exist_ok=True)
    total_s = round(minutes * 60)
    chunk_s = max(1, round(chunk_minutes * 60))
    total_min = max(1, round(minutes))
    index = next_index(out, "room")
    written: list[Path] = []
    current: wave.Wave_write | None = None
    try:
        for second in range(total_s):
            if second % chunk_s == 0:
                if current is not None:
                    current.close()
                path = out / f"room-{index:03d}.wav"
                index += 1
                current = wave.open(str(path), "wb")  # noqa: SIM115 (one file per chunk; closed below)
                current.setnchannels(1)
                current.setsampwidth(2)
                current.setframerate(SAMPLE_RATE)
                written.append(path)
            current.writeframes(read_second())  # type: ignore[union-attr]
            show(f"recording, minute {second // 60 + 1} of {total_min} ({written[-1].name})   ")
    except KeyboardInterrupt:
        show("stopped early (Ctrl+C); what was recorded is kept                        ")
    finally:
        if current is not None:
            current.close()
    return written


def mic_seconds() -> tuple[Callable[[], bytes], Callable[[], None]]:
    """The microphone as one-second blocks of 16 kHz mono 16-bit PCM, and a function to close it."""
    import sounddevice as sd  # optional dependency: uv sync --extra wake

    blocks: queue.Queue[bytes] = queue.Queue()
    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=SAMPLE_RATE * BLOCK_S,
        callback=lambda indata, frames, t, status: blocks.put(bytes(indata)),
    )
    stream.start()

    def read() -> bytes:
        while True:  # short waits, so Ctrl+C is noticed at once
            try:
                return blocks.get(timeout=0.2)
            except queue.Empty:
                continue

    def close() -> None:
        stream.stop()
        stream.close()

    return read, close


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="default data/wake/positives (or data/wake/background)")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--background", action="store_true", help="record continuously: room audio, no Enter")
    parser.add_argument("--minutes", type=float, default=60.0, help="--background: how long in all")
    parser.add_argument("--chunk-minutes", type=float, default=10.0, help="--background: minutes per file")
    args = parser.parse_args()

    if args.background:
        out = args.out or Path("data") / "wake" / "background"
        read, close = mic_seconds()
        print(f"Recording {args.minutes:g} min of background into {out} ({args.chunk_minutes:g}-min files).")
        print("Play your podcast or video out loud now; don't say 'Kestrel'. Ctrl+C stops early.")
        start = time.monotonic()
        try:
            files = record_background(out, args.minutes, args.chunk_minutes, read)
        finally:
            close()
        print(f"\ndone: {len(files)} file(s), {(time.monotonic() - start) / 60:.1f} min in {out}")
        return 0

    import sounddevice as sd  # optional dependencies: uv sync --extra wake
    import soundfile as sf

    out = args.out or Path("data") / "wake" / "positives"
    out.mkdir(parents=True, exist_ok=True)
    start_index = len(list(out.glob("kestrel-*.wav")))
    print(f"Recording {args.count} clips of {args.seconds:g} s into {out}. Vary distance, volume and tone.")
    for i in range(start_index, start_index + args.count):
        if (
            input(f"[{i - start_index + 1}/{args.count}] Enter, then say 'Kestrel' (q + Enter to stop): ").strip()
            == "q"
        ):
            break
        clip = sd.rec(int(args.seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        sf.write(out / f"kestrel-{i:03d}.wav", clip, SAMPLE_RATE, subtype="PCM_16")
    print(f"done: {len(list(out.glob('kestrel-*.wav')))} clips in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
