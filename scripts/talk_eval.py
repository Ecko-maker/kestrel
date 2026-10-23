"""Measure spoken requests with the same code the live listener uses (docs/voice-wake.md, "Talking to Kestrel").

1. Your requests (scripts/talk_record.py: WAVs + refs.tsv): word error rate, raw and normalised
   (kestrel.wake.normalize: case, punctuation, contractions, numbers and units), latency per request,
   Whisper's confidence, and what the hallucination guard would do with each.
2. Background-only audio (a podcast, TV; no requests in it): the audio is streamed in 0.1 s chunks
   through the same end-of-speech detector as if Kestrel were listening the whole time; every
   segment it cuts is transcribed and guarded. Segments that pass would have become requests the
   agent never got from you: reported as false requests per hour of listening (an upper bound:
   the live listener only records right after a wake).

    uv run --no-sync python scripts/talk_eval.py --requests data/talk/requests --background data/wake/background
    uv run --no-sync python scripts/talk_eval.py --requests data/talk/requests --model tiny.en-q5_1
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from kestrel.bench.stats import bootstrap_ci
from kestrel.wake.audio import CHUNK_FRAMES
from kestrel.wake.detector import SAMPLE_RATE
from kestrel.wake.normalize import normalize, raw_words, word_errors
from kestrel.wake.service import loudness
from kestrel.wake.talk import DEFAULT_MIN_CONFIDENCE, Endpointer, NoiseFloor, guard
from kestrel.wake.transcribe import DEFAULT_TALK_MODEL, TALK_MODELS, WhisperCppTranscriber, talk_model_file

AUDIO = {".wav", ".flac", ".ogg", ".mp3"}
CHUNK_BYTES = CHUNK_FRAMES * 2


def load_pcm(path: Path) -> bytes:
    import soundfile as sf  # optional dependency: uv sync --extra wake

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(len(mono) * SAMPLE_RATE / rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


def speech_seconds(pcm: bytes, floor_from: float = 0.5) -> float:
    """Speech above the noise floor, as the live guard sees it (floor learned on the first 0.5 s)."""
    floor = NoiseFloor()
    chunks = [pcm[i : i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]
    lead = int(floor_from / 0.1)
    for c in chunks[:lead]:
        floor.update(loudness(c))
    endpoint = Endpointer(floor=floor.value)
    for c in chunks:
        endpoint.feed(loudness(c))
    return endpoint.speech_s


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", type=Path, required=True, help="folder with req-*.wav and refs.tsv")
    parser.add_argument("--background", type=Path, nargs="*", default=[], help="audio with no requests in it")
    parser.add_argument("--model", default=DEFAULT_TALK_MODEL, choices=sorted(TALK_MODELS))
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()

    stt = WhisperCppTranscriber(talk_model_file(args.model))
    refs = dict(
        line.split("\t", 1)
        for line in (args.requests / "refs.tsv").read_text(encoding="utf-8-sig").splitlines()
        if "\t" in line
    )
    rows = []
    for name, ref in sorted(refs.items()):
        pcm = load_pcm(args.requests / name)
        start = time.perf_counter()
        t = stt.transcribe(pcm)
        latency = time.perf_counter() - start
        sent, why = guard(t.text, t.confidence, speech_seconds(pcm), args.min_confidence)
        raw_ref, raw_hyp = raw_words(ref), raw_words(t.text)
        norm_ref, norm_hyp = normalize(ref).split(), normalize(t.text).split()
        rows.append(
            {
                "file": name,
                "reference": ref,
                "transcript": t.text,
                "confidence": t.confidence,
                "latency_s": round(latency, 3),
                "audio_s": round(len(pcm) / 2 / SAMPLE_RATE, 2),
                "raw_errors": word_errors(raw_ref, raw_hyp),
                "raw_words": len(raw_ref),
                "norm_errors": word_errors(norm_ref, norm_hyp),
                "norm_words": len(norm_ref),
                "guard": why,
                "sent": sent is not None,
            }
        )
        print(f"{name}: {t.text!r} (conf {t.confidence}, {latency:.2f} s, guard: {why})")

    def total(key: str) -> int:
        return sum(r[key] for r in rows)

    raw_wer = total("raw_errors") / max(total("raw_words"), 1)
    norm_wer = total("norm_errors") / max(total("norm_words"), 1)
    per_request = bootstrap_ci([r["norm_errors"] / max(r["norm_words"], 1) for r in rows])
    latencies = sorted(r["latency_s"] for r in rows)
    print(f"\n{len(rows)} requests, model {args.model}")
    print(f"  WER raw {100 * raw_wer:.1f}%, normalised {100 * norm_wer:.1f}%", end="")
    if per_request:
        ci = f"{100 * per_request.low:.1f}-{100 * per_request.high:.1f}%"
        print(f" (per-request mean {100 * per_request.value:.1f}%, 95% CI {ci})")
    print(f"  latency median {latencies[len(latencies) // 2]:.2f} s, max {latencies[-1]:.2f} s")
    dropped = [r for r in rows if not r["sent"]]
    reasons = ", ".join(f"{r['file']}: {r['guard']}" for r in dropped)
    print(f"  dropped by the guard: {len(dropped)} of {len(rows)}" + (f" ({reasons})" if dropped else ""))

    false_requests, listened = [], 0.0
    for path in [f for p in args.background for f in (sorted(p.rglob("*")) if p.is_dir() else [p])]:
        if path.suffix.lower() not in AUDIO:
            continue
        pcm = load_pcm(path)
        listened += len(pcm) / 2 / SAMPLE_RATE
        floor, endpoint, segment = NoiseFloor(), None, bytearray()
        for i in range(0, len(pcm), CHUNK_BYTES):
            chunk = pcm[i : i + CHUNK_BYTES]
            level = loudness(chunk)
            if endpoint is None:
                endpoint, segment = Endpointer(floor=floor.value), bytearray()
            segment += chunk
            state = endpoint.feed(level)
            if state in ("waiting", "speaking"):
                continue
            if state != "no_speech":
                t = stt.transcribe(bytes(segment))
                sent, why = guard(t.text, t.confidence, endpoint.speech_s, args.min_confidence)
                if sent is not None:
                    false_requests.append({"file": path.name, "at_s": round(i / 2 / SAMPLE_RATE, 1), "text": sent})
            else:
                floor.update(level)
            endpoint = None
    if listened:
        per_hour = len(false_requests) / (listened / 3600)
        hours = listened / 3600
        print(
            f"\nbackground: {hours:.2f} h, {len(false_requests)} false requests = {per_hour:.1f} per hour of listening"
        )
        for f in false_requests[:20]:
            print(f"  {f['file']} @ {f['at_s']} s: {f['text']!r}")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "min_confidence": args.min_confidence,
                    "wer_raw": raw_wer,
                    "wer_normalised": norm_wer,
                    "requests": rows,
                    "background_hours": listened / 3600,
                    "false_requests": false_requests,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
