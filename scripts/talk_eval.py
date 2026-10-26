"""Measure spoken requests with the same code the live listener uses (docs/voice-wake.md, "Talking to Kestrel").

1. Your requests (scripts/talk_record.py: WAVs + refs.tsv), each transcribed twice: without and with the
   vocabulary hint (kestrel.wake.transcribe.talk_prompt, the server's own prompt). For both: word error
   rate, raw and normalised (kestrel.wake.normalize: case, punctuation, contractions, numbers and units),
   latency, Whisper's confidence, and what the guard would do: send, check first (low confidence:
   shown unsent, "Did I hear that right?") or drop ("I didn't catch that").
2. Background-only audio (a podcast, TV; no requests in it), transcribed the way the server runs (the
   hint only if KESTREL_TALK_HINT=on; off by default), measured two ways (docs/voice-wake.md):
   a. End to end (the number that matters): the wake detector runs over the background; after each
      false wake (with the 10 s cooldown) the audio the live service would record (the end-of-speech
      rule, at most 20 s) is transcribed and guarded. False requests / checks / drops per hour, for
      each wake threshold.
   b. "Background speech that would pass the guard if recorded": every speech segment is transcribed
      and guarded as if Kestrel recorded all the time. Only a measure of the guard on its own; real
      use records only after a wake, so this is far higher than (a).
3. A threshold sweep, for choosing KESTREL_TALK_MIN_CONFIDENCE: at each candidate value, how many of
   your requests would be sent, checked or dropped, how many sent ones were misheard, and both
   background numbers per hour (end to end at the server's wake threshold, KESTREL_WAKE_THRESHOLD).

    uv run --no-sync python scripts/talk_eval.py                        # data/talk/requests
    uv run --no-sync python scripts/talk_eval.py --set new              # data/talk/requests-new
    uv run --no-sync python scripts/talk_eval.py --background data/wake/background --model tiny.en-q5_1
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from kestrel.bench.stats import bootstrap_ci
from kestrel.wake import WakeSettings
from kestrel.wake.audio import CHUNK_FRAMES
from kestrel.wake.detector import SAMPLE_RATE, after_cooldown, vosk_recognizer_factory, wake_events
from kestrel.wake.normalize import normalize, raw_words, word_errors
from kestrel.wake.service import COOLDOWN_S, default_model_dir, loudness
from kestrel.wake.talk import DEFAULT_MIN_CONFIDENCE, Endpointer, NoiseFloor, guard
from kestrel.wake.transcribe import (
    DEFAULT_TALK_MODEL,
    TALK_MODELS,
    WhisperCppTranscriber,
    read_vocab,
    talk_model_file,
    talk_prompt,
    vocab_file,
)

AUDIO = {".wav", ".flac", ".ogg", ".mp3"}
CHUNK_BYTES = CHUNK_FRAMES * 2
SETS = {"first": Path("data") / "talk" / "requests", "new": Path("data") / "talk" / "requests-new"}
MODES = ("no hint", "hint")


SWEEP = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
WAKE_THRESHOLDS = "0.5,0.6,0.7,0.8,0.9,0.95"


def load_pcm(path: Path) -> bytes:
    import numpy as np  # optional dependencies (with soundfile): uv sync --extra wake
    import soundfile as sf

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
    for c in chunks[: int(floor_from / 0.1)]:
        floor.update(loudness(c))
    endpoint = Endpointer(floor=floor.value)
    for c in chunks:
        endpoint.feed(loudness(c))
    return endpoint.speech_s


def score_requests(
    stt: WhisperCppTranscriber, folder: Path, prompt: str, min_conf: float, shown: str = "no hint"
) -> list[dict]:
    refs = dict(
        line.split("\t", 1)
        for line in (folder / "refs.tsv").read_text(encoding="utf-8-sig").splitlines()
        if "\t" in line
    )
    rows = []
    for name, ref in sorted(refs.items()):
        pcm = load_pcm(folder / name)
        speech_s = speech_seconds(pcm)
        row: dict = {
            "file": name,
            "reference": ref,
            "audio_s": round(len(pcm) / 2 / SAMPLE_RATE, 2),
            "speech_s": round(speech_s, 1),
        }
        for mode in MODES:
            stt.prompt = prompt if mode == "hint" else None
            start = time.perf_counter()
            t = stt.transcribe(pcm)
            latency = time.perf_counter() - start
            verdict = guard(t.text, t.confidence, speech_s, min_conf, prompt if mode == "hint" else "")
            row[mode] = {
                "transcript": t.text,
                "confidence": None if t.confidence is None else round(t.confidence, 3),
                "latency_s": round(latency, 3),
                "raw_errors": word_errors(raw_words(ref), raw_words(t.text)),
                "raw_words": len(raw_words(ref)),
                "norm_errors": word_errors(normalize(ref).split(), normalize(t.text).split()),
                "norm_words": len(normalize(ref).split()),
                "path": verdict.action,
                "reason": verdict.reason,
            }
        cell = row[shown]
        print(f"{name}: {cell['transcript']!r} (conf {cell['confidence']}, {cell['path']})")
        rows.append(row)
    return rows


def summary(rows: list[dict], mode: str) -> dict:
    cells = [r[mode] for r in rows]

    def total(key: str) -> int:
        return sum(c[key] for c in cells)

    per_request = bootstrap_ci([c["norm_errors"] / max(c["norm_words"], 1) for c in cells])
    latencies = sorted(c["latency_s"] for c in cells)
    confidences = sorted(c["confidence"] for c in cells if c["confidence"] is not None)
    return {
        "wer_raw": total("raw_errors") / max(total("raw_words"), 1),
        "wer_normalised": total("norm_errors") / max(total("norm_words"), 1),
        "per_request_ci": None if per_request is None else [per_request.low, per_request.high],
        "latency_median_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_max_s": latencies[-1] if latencies else None,
        "paths": dict(Counter(c["path"] for c in cells)),
        "drop_reasons": dict(Counter(c["reason"] for c in cells if c["path"] == "drop")),
        "confidence_min": confidences[0] if confidences else None,
        "confidence_median": confidences[len(confidences) // 2] if confidences else None,
    }


def background(stt: WhisperCppTranscriber, paths: list[Path], prompt: str, min_conf: float) -> dict:
    stt.prompt = prompt or None
    found: list[dict] = []
    heard: list[dict] = []  # every transcribed segment (for the threshold sweep)
    listened = 0.0
    for path in audio_files(paths):
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
            if state == "no_speech":
                floor.update(level)
            else:
                t = stt.transcribe(bytes(segment))
                heard.append({"text": t.text, "confidence": t.confidence, "speech_s": endpoint.speech_s})
                verdict = guard(t.text, t.confidence, endpoint.speech_s, min_conf, prompt)
                if verdict.action != "drop":
                    found.append(
                        {
                            "file": path.name,
                            "at_s": round(i / 2 / SAMPLE_RATE, 1),
                            "path": verdict.action,
                            "text": verdict.text,
                        }
                    )
            endpoint = None
    hours = listened / 3600
    sends = sum(f["path"] == "send" for f in found)
    checks = sum(f["path"] == "check" for f in found)
    return {
        "hours": round(hours, 3),
        "false_requests": sends,
        "false_checks": checks,
        "false_requests_per_hour": round(sends / hours, 2) if hours else None,
        "false_checks_per_hour": round(checks / hours, 2) if hours else None,
        "segments": found,
        "heard": heard,
    }


def audio_files(paths: list[Path]) -> list[Path]:
    return [f for p in paths for f in (sorted(p.rglob("*")) if p.is_dir() else [p]) if f.suffix.lower() in AUDIO]


def wake_segments(pcm: bytes, wake_times: list[float]) -> list[dict]:
    """What the live service records after each wake (WakeService.on_wake / _record): from the next
    0.1 s chunk, through the end-of-speech rule (Endpointer, with the noise floor learned from the
    audio before), until done, 20 s, or no speech within 3 s. The greeting isn't in recorded audio, so
    recording starts right after the wake (live, chunks are skipped while the greeting plays)."""
    starts = {round(t / 0.1) for t in wake_times}
    floor, endpoint, segment, at = NoiseFloor(), None, bytearray(), 0.0
    out: list[dict] = []
    for index, i in enumerate(range(0, len(pcm), CHUNK_BYTES)):
        chunk = pcm[i : i + CHUNK_BYTES]
        level = loudness(chunk)
        if endpoint is None:
            floor.update(level)  # live: the floor learns only while not recording
            if index + 1 in starts:
                endpoint, segment, at = Endpointer(floor=floor.value), bytearray(), (index + 1) * 0.1
            continue
        segment += chunk
        state = endpoint.feed(level)
        if state in ("waiting", "speaking"):
            continue
        out.append({"at_s": round(at, 1), "state": state, "audio": bytes(segment), "speech_s": endpoint.speech_s})
        endpoint = None
    if endpoint is not None:  # the file ended mid-request: what was recorded so far
        out.append({"at_s": round(at, 1), "state": "cut", "audio": bytes(segment), "speech_s": endpoint.speech_s})
    return out


def end_to_end(
    stt: WhisperCppTranscriber,
    make_recognizer,
    paths: list[Path],
    thresholds: list[float],
    min_conf: float,
    prompt: str,
) -> dict:
    """Per wake threshold: false wakes, and what each one's recording would do (send, check, drop)."""
    stt.prompt = prompt or None
    hours = 0.0
    per: dict[float, dict] = {t: {"wakes": 0, "no_speech": 0, "heard": []} for t in thresholds}
    for path in audio_files(paths):
        pcm = load_pcm(path)
        hours += len(pcm) / 2 / SAMPLE_RATE / 3600
        events = wake_events(make_recognizer, pcm, CHUNK_BYTES)
        cache: dict[float, dict] = {}  # a segment heard at several thresholds is transcribed once
        for t in thresholds:
            times = after_cooldown([s for s, c, _ in events if c >= t], COOLDOWN_S)
            per[t]["wakes"] += len(times)
            for seg in wake_segments(pcm, times):
                if seg["state"] == "no_speech":
                    per[t]["no_speech"] += 1
                    continue
                if seg["at_s"] not in cache:
                    tr = stt.transcribe(seg["audio"])
                    cache[seg["at_s"]] = {
                        "file": path.name,
                        "at_s": seg["at_s"],
                        "text": tr.text,
                        "confidence": tr.confidence,
                        "speech_s": seg["speech_s"],
                    }
                per[t]["heard"].append(cache[seg["at_s"]])
    rows = []
    for t in thresholds:
        heard = per[t]["heard"]
        paths_ = Counter(guard(h["text"], h["confidence"], h["speech_s"], min_conf, prompt).action for h in heard)
        rows.append(
            {
                "wake_threshold": t,
                "false_wakes": per[t]["wakes"],
                "no_speech": per[t]["no_speech"],
                "send": paths_["send"],
                "check": paths_["check"],
                "drop": paths_["drop"],
                "false_wakes_per_hour": round(per[t]["wakes"] / hours, 2) if hours else None,
                "false_requests_per_hour": round(paths_["send"] / hours, 2) if hours else None,
                "false_checks_per_hour": round(paths_["check"] / hours, 2) if hours else None,
                "heard": heard,
            }
        )
    return {"hours": round(hours, 3), "rows": rows}


def per_hour(heard: list[dict], hours: float, threshold: float, prompt: str) -> tuple[float | None, float | None]:
    if not hours:
        return None, None
    actions = Counter(guard(h["text"], h["confidence"], h["speech_s"], threshold, prompt).action for h in heard)
    return round(actions["send"] / hours, 2), round(actions["check"] / hours, 2)


def sweep(
    rows: list[dict],
    mode: str,
    heard: list[dict],
    hours: float,
    prompt: str,
    e2e_heard: list[dict] | None = None,
    e2e_hours: float = 0.0,
) -> list[dict]:
    """What each candidate threshold would do, on your requests (in mode) and on the background:
    end to end (e2e_heard: recordings after false wakes) and if every segment were recorded (heard)."""
    out = []
    for threshold in SWEEP:
        paths = Counter()
        misheard_sent = 0
        for r in rows:
            cell = r[mode]
            verdict = guard(cell["transcript"], cell["confidence"], r["speech_s"], threshold, prompt)
            paths[verdict.action] += 1
            misheard_sent += verdict.action == "send" and cell["norm_errors"] > 0
        if_sends, if_checks = per_hour(heard, hours, threshold, prompt)
        e2e_sends, e2e_checks = per_hour(
            e2e_heard or [], e2e_hours if e2e_heard is not None else 0.0, threshold, prompt
        )
        out.append(
            {
                "threshold": threshold,
                "send": paths["send"],
                "check": paths["check"],
                "drop": paths["drop"],
                "misheard_sent": misheard_sent,
                "false_requests_per_hour": e2e_sends,  # end to end
                "false_checks_per_hour": e2e_checks,
                "if_recorded_requests_per_hour": if_sends,
                "if_recorded_checks_per_hour": if_checks,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--set", choices=sorted(SETS), default="first", help="which recorded set (talk_record.py --set)"
    )
    parser.add_argument("--requests", type=Path, help="a folder with req-*.wav and refs.tsv (overrides --set)")
    parser.add_argument("--background", type=Path, nargs="*", default=[], help="audio with no requests in it")
    parser.add_argument("--model", default=DEFAULT_TALK_MODEL, choices=sorted(TALK_MODELS))
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--vocab-file", type=Path, default=vocab_file(), help="names for the hint (git-ignored)")
    parser.add_argument("--wake-model", type=Path, default=default_model_dir(), help="the Vosk model (end to end)")
    parser.add_argument("--wake-thresholds", default=WAKE_THRESHOLDS, help="end to end: wake thresholds to report")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()

    folder = args.requests or SETS[args.set]
    prompt = talk_prompt(read_vocab(args.vocab_file))
    server_prompt = WakeSettings.from_env().talk_prompt  # what the live server uses (KESTREL_TALK_HINT)
    server_mode = "hint" if server_prompt else "no hint"
    server_wake = WakeSettings.from_env().threshold  # KESTREL_WAKE_THRESHOLD (default 0.9)
    print(f"set: {folder}; model {args.model}; min confidence {args.min_confidence}\nhint: {prompt!r}\n")
    stt = WhisperCppTranscriber(talk_model_file(args.model))
    rows = score_requests(stt, folder, prompt, args.min_confidence, shown=server_mode)
    results = {"set": str(folder), "model": args.model, "min_confidence": args.min_confidence, "prompt": prompt}
    print(f"\n{len(rows)} requests")
    head = ("", "WER raw", "WER norm", "95% CI (norm, per request)", "latency", "send", "check", "drop")
    widths = (10, 8, 9, 27, 8, 5, 6, 5)
    print(" ".join(f"{h:>{w}}" if i else f"{h:{w}}" for i, (h, w) in enumerate(zip(head, widths, strict=True))))
    for mode in MODES:
        s = summary(rows, mode)
        results[mode] = s
        ci = (
            "-"
            if s["per_request_ci"] is None
            else f"{100 * s['per_request_ci'][0]:.1f}-{100 * s['per_request_ci'][1]:.1f}%"
        )
        paths = s["paths"]
        counts = f"{paths.get('send', 0):>5} {paths.get('check', 0):>6} {paths.get('drop', 0):>5}"
        wer = f"{100 * s['wer_raw']:>7.1f}% {100 * s['wer_normalised']:>8.1f}%"
        print(f"{mode:10} {wer} {ci:>27} {s['latency_median_s'] or 0:>7.2f}s {counts}")
    live = results[server_mode]
    low, median = live["confidence_min"], live["confidence_median"]
    print(f"\nconfidence ({server_mode}, as the server runs): min {low}, median {median}")
    changed = [r for r in rows if r["no hint"]["norm_errors"] != r["hint"]["norm_errors"]]
    if changed:
        print("\nrequests the hint changed (normalised errors without -> with):")
        for r in changed:
            before, after = r["no hint"]["norm_errors"], r["hint"]["norm_errors"]
            print(f"  {r['file']}: {before} -> {after}  {r['hint']['transcript']!r}")
    hours, heard = 0.0, []
    e2e_heard: list[dict] | None = None
    e2e_hours = 0.0
    if args.background:
        thresholds = sorted({float(t) for t in args.wake_thresholds.split(",")} | {server_wake})
        e2e = end_to_end(
            stt,
            vosk_recognizer_factory(args.wake_model),
            args.background,
            thresholds,
            args.min_confidence,
            server_prompt,
        )
        e2e_hours = e2e["hours"]
        print(f"\nfalse requests per hour (end to end), {e2e_hours:.2f} h of background, ", end="")
        print(f"min confidence {args.min_confidence}:")
        head = f"{'wake thr':>8} {'false wakes':>11} {'per h':>6} {'no speech':>9} {'send':>5} {'check':>6} {'drop':>5}"
        print(f"{head} {'false req/h':>12} {'checks/h':>9}")
        for r in e2e["rows"]:
            req_h = "-" if r["false_requests_per_hour"] is None else f"{r['false_requests_per_hour']:.2f}"
            chk_h = "-" if r["false_checks_per_hour"] is None else f"{r['false_checks_per_hour']:.2f}"
            wake_h = "-" if r["false_wakes_per_hour"] is None else f"{r['false_wakes_per_hour']:.2f}"
            mark = " <- server" if r["wake_threshold"] == server_wake else ""
            counts = f"{r['no_speech']:>9} {r['send']:>5} {r['check']:>6} {r['drop']:>5}"
            print(
                f"{r['wake_threshold']:>8.2f} {r['false_wakes']:>11} {wake_h:>6} {counts} {req_h:>12} {chk_h:>9}{mark}"
            )
            if r["wake_threshold"] == server_wake:
                e2e_heard = r["heard"]
        for h in (e2e_heard or [])[:20]:
            print(f"  {h['file']} @ {h['at_s']} s (conf {h['confidence']}): {h['text']!r}")
        results["end_to_end"] = {
            "hours": e2e_hours,
            "rows": [{k: v for k, v in r.items() if k != "heard"} for r in e2e["rows"]],
            "server_wake_threshold": server_wake,
            "heard_at_server_threshold": e2e_heard,
        }

        bg = background(stt, args.background, server_prompt, args.min_confidence)
        hours, heard = bg["hours"], bg.pop("heard")
        results["background_if_recorded"] = bg
        print(f"\nbackground speech that would pass the guard if recorded ({server_mode}): {bg['hours']:.2f} h")
        print("  (every speech segment, as if Kestrel recorded all the time; real use records only after a wake)")
        print(f"  {bg['false_requests']} would be sent ({bg['false_requests_per_hour']} per hour)")
        print(f"  {bg['false_checks']} would be checked ({bg['false_checks_per_hour']} per hour)")
        for f in bg["segments"][:10]:
            print(f"  {f['file']} @ {f['at_s']} s [{f['path']}]: {f['text']!r}")
    table = sweep(rows, server_mode, heard, hours, server_prompt, e2e_heard, e2e_hours)
    results["sweep"] = table
    print(f"\nthreshold sweep ({server_mode}): your requests sent / checked / dropped, misheard but sent;")
    print(f"background per hour: end to end at wake threshold {server_wake}, and if every segment were recorded")
    header = f"{'threshold':>9} {'send':>5} {'check':>6} {'drop':>5} {'misheard sent':>14}"
    print(f"{header} {'e2e req/h':>10} {'e2e chk/h':>10} {'if-rec req/h':>13} {'if-rec chk/h':>13}")

    def cell(value: float | None, width: int) -> str:
        return f"{'-' if value is None else f'{value:.2f}':>{width}}"

    for t in table:
        row = f"{t['threshold']:>9.2f} {t['send']:>5} {t['check']:>6} {t['drop']:>5} {t['misheard_sent']:>14}"
        e2e_cells = f"{cell(t['false_requests_per_hour'], 10)} {cell(t['false_checks_per_hour'], 10)}"
        if_cells = f"{cell(t['if_recorded_requests_per_hour'], 13)} {cell(t['if_recorded_checks_per_hour'], 13)}"
        print(f"{row} {e2e_cells} {if_cells}")
    results["requests"] = rows
    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
