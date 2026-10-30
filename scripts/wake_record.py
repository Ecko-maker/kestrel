"""Record your own "Kestrel" samples, or background audio, for scripts/wake_eval.py and scripts/talk_eval.py.

This is the only Kestrel code that saves audio, and only when you run it: files go to the folder you
choose (defaults under data/, which git ignores). Nothing is sent anywhere. The live wake word never
saves anything.

    uv run --no-sync python scripts/wake_record.py                    # 30 clips of 3 s, Enter before each
    uv run --no-sync python scripts/wake_record.py --count 10 --out data/wake/positives-far
    uv run --no-sync python scripts/wake_record.py --phrases           # 5 clips each of 5 phrases, 3 s
    uv run --no-sync python scripts/wake_record.py --background        # 60 min of room audio, 10-min files

Each clip: Enter, a short countdown ("3, 2, 1, now"; recording starts at "1", so an early start
isn't cut), then 3 s. Each clip is checked right away: too quiet, or speech touching the start or the
end of the clip (cut off), is shown with an offer to record it again. These were the causes of 10 of
the 11 clips first missed at 0.90 (docs/voice-wake.md).

--phrases prompts a fixed list ("Kestrel", "Hi Kestrel", "Hello Kestrel", "Hey Kestrel", "Shalom
Kestrel"), --count clips each (default 5), into data/wake/phrases/ as hi-kestrel-000.wav etc., for
wake_eval.py --phrases (detection per phrase, and whether the greeting was heard right).

--background records continuously (no Enter between files) into room-NNN.wav files of --chunk-minutes
each, 16 kHz mono 16-bit, showing "recording, minute X of N". Ctrl+C stops early: the files already
finished are kept, and so is the part of the current file recorded so far.
"""

import argparse
import array
import math
import queue
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path

SAMPLE_RATE = 16_000
BLOCK_S = 1  # the background recorder reads and writes one second at a time
CHUNK_BYTES = 3200  # 0.1 s of 16-bit mono, the live service's chunk
QUIET_DB = -45.0  # the loudest 0.1 s of a clip below this: too quiet (the four near-silent misses peaked lower)
EDGE_S = 0.2  # speech in the first or last 0.2 s: probably cut off
EDGE_DB = 15.0  # "speech" at an edge: within this of the clip's loudest 0.1 s


def chunk_db(pcm: bytes) -> list[float]:
    """RMS level of each 0.1 s, in dBFS."""
    out = []
    for i in range(0, len(pcm) - 1, CHUNK_BYTES):
        samples = array.array("h")
        samples.frombytes(pcm[i : i + CHUNK_BYTES][: (min(CHUNK_BYTES, len(pcm) - i) // 2) * 2])
        rms = math.sqrt(sum(x * x for x in samples) / max(len(samples), 1))
        out.append(20 * math.log10(max(rms, 1e-3) / 32768))
    return out


def clip_problem(pcm: bytes) -> str | None:
    """Why a 'Kestrel' clip is likely unusable, or None. Only a hint for re-recording; never edits it."""
    levels = chunk_db(pcm)
    if not levels:
        return "empty"
    loudest = max(levels)
    if loudest < QUIET_DB:
        return f"too quiet (loudest {loudest:.0f} dBFS): speak closer or louder"
    edge = max(1, round(EDGE_S / 0.1))
    if max(levels[:edge]) >= loudest - EDGE_DB:
        return "speech at the very start: it may be cut off (wait for 'now')"
    if max(levels[-edge:]) >= loudest - EDGE_DB:
        return "speech at the very end: it may be cut off (say it right after 'now')"
    return None


PHRASES = ("Kestrel", "Hi Kestrel", "Hello Kestrel", "Hey Kestrel", "Shalom Kestrel")


def slug(phrase: str) -> str:
    """'Hi Kestrel' -> 'hi-kestrel' (the file prefix wake_eval.py reads the phrase back from)."""
    return "-".join(phrase.lower().split())


def countdown(
    show: Callable[[str], None],
    start: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
    say: str = "Kestrel",
) -> None:
    """3, 2, then recording starts at 1 (a short lead-in), then 'now'."""
    for label in ("3", "2"):
        show(label)
        sleep(0.5)
    start()
    show("1")
    sleep(0.5)
    show(f"now: say '{say}'")


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
    parser.add_argument("--out", type=Path, help="default data/wake/positives (or .../phrases, .../background)")
    parser.add_argument("--count", type=int, help="clips (default 30; with --phrases, 5 per phrase)")
    parser.add_argument("--phrases", action="store_true", help="5 phrases ('Hi Kestrel'...), --count clips each")
    parser.add_argument("--seconds", type=float, default=3.0)
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

    if args.phrases:
        out = args.out or Path("data") / "wake" / "phrases"
        count = args.count or 5
        print(f"{len(PHRASES)} phrases x {count} clips of {args.seconds:g} s into {out}. Say each one naturally.")
        for phrase in PHRASES:
            print(f"\n== {phrase} ==")
            if not record_clips(out, slug(phrase), phrase, count, args.seconds):
                break
        return 0
    out = args.out or Path("data") / "wake" / "positives"
    record_clips(out, "kestrel", "Kestrel", args.count or 30, args.seconds)
    return 0


def record_clips(out: Path, prefix: str, phrase: str, count: int, seconds: float) -> bool:
    """count clips of prefix-NNN.wav, each after a countdown and checked at once. False if stopped (q)."""
    import sounddevice as sd  # optional dependencies: uv sync --extra wake
    import soundfile as sf

    out.mkdir(parents=True, exist_ok=True)
    first = next_index(out, prefix)
    i = first
    while i < first + count:
        prompt = f"[{i - first + 1}/{count}] Enter, then say '{phrase}' at 'now' (q + Enter to stop): "
        if input(prompt).strip() == "q":
            return False
        frames = int(seconds * SAMPLE_RATE)
        holder: list = []
        countdown(
            lambda label: print(f"  {label}", flush=True),
            lambda holder=holder, frames=frames: holder.append(
                sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            ),
            say=phrase,
        )
        sd.wait()
        clip = holder[0]
        problem = clip_problem(clip.tobytes())
        if problem and input(f"  {problem}. Record it again? (Enter = again, k + Enter = keep): ").strip() != "k":
            continue
        sf.write(out / f"{prefix}-{i:03d}.wav", clip, SAMPLE_RATE, subtype="PCM_16")
        i += 1
    print(f"done: {len(list(out.glob(f'{prefix}-*.wav')))} '{phrase}' clips in {out}")
    return True


if __name__ == "__main__":
    sys.exit(main())
