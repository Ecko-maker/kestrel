"""scripts/wake_record.py --background and scripts/talk_eval.py's threshold sweep (no mic, no model)."""

import importlib.util
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wake_record = load("wake_record")
talk_eval = load("talk_eval")

SECOND = b"\x01\x00" * 16_000  # one second of 16 kHz mono 16-bit audio


def seconds_in(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16_000)
        return f.getnframes() / f.getframerate()


def test_background_writes_chunks_and_shows_progress(tmp_path):
    lines: list[str] = []
    files = wake_record.record_background(tmp_path, 3, 1, lambda: SECOND, lines.append)
    assert [f.name for f in files] == ["room-000.wav", "room-001.wav", "room-002.wav"]
    assert [seconds_in(f) for f in files] == [60, 60, 60]
    assert lines[0].startswith("recording, minute 1 of 3 (room-000.wav)")
    assert lines[-1].startswith("recording, minute 3 of 3 (room-002.wav)")


def test_ctrl_c_keeps_finished_and_partial_files(tmp_path):
    calls = 0

    def reader() -> bytes:
        nonlocal calls
        calls += 1
        if calls > 90:  # 1.5 chunks in, the user presses Ctrl+C
            raise KeyboardInterrupt
        return SECOND

    lines: list[str] = []
    files = wake_record.record_background(tmp_path, 60, 1, reader, lines.append)
    assert [seconds_in(f) for f in files] == [60, 30]
    assert lines[-1].startswith("stopped early")


def test_a_second_session_never_overwrites(tmp_path):
    wake_record.record_background(tmp_path, 1, 1, lambda: SECOND, lambda line: None)
    (tmp_path / "room-notes.wav").write_bytes(b"")  # not numbered: ignored
    files = wake_record.record_background(tmp_path, 1, 1, lambda: SECOND, lambda line: None)
    assert [f.name for f in files] == ["room-001.wav"]


def row(transcript: str, confidence: float, norm_errors: int = 0, speech_s: float = 2.0) -> dict:
    return {
        "speech_s": speech_s,
        "no hint": {"transcript": transcript, "confidence": confidence, "norm_errors": norm_errors},
    }


def test_sweep_counts_requests_and_background_per_threshold():
    rows = [
        row("what time is it", 0.9),
        row("draft a note to priya", 0.5, norm_errors=1),  # misheard, sent while the threshold is <= 0.5
        row("list my files", 0.35),
        row("Thank you.", 0.9),  # filler (whisper invents it on silence): always dropped
    ]
    heard = [
        {"text": "and then the host said", "confidence": 0.6, "speech_s": 2.0},
        {"text": "welcome back to the show", "confidence": 0.4, "speech_s": 2.0},
    ]
    table = {t["threshold"]: t for t in talk_eval.sweep(rows, "no hint", heard, 0.5, "")}
    assert [t for t in talk_eval.SWEEP] == sorted(table)
    low, mid, high = table[0.30], table[0.50], table[0.80]
    assert (low["send"], low["check"], low["drop"], low["misheard_sent"]) == (3, 0, 1, 1)
    assert (mid["send"], mid["check"], mid["drop"], mid["misheard_sent"]) == (2, 1, 1, 1)
    assert (high["send"], high["check"], high["drop"], high["misheard_sent"]) == (1, 2, 1, 0)
    assert (low["false_requests_per_hour"], low["false_checks_per_hour"]) == (4.0, 0.0)
    assert (mid["false_requests_per_hour"], mid["false_checks_per_hour"]) == (2.0, 2.0)
    assert (high["false_requests_per_hour"], high["false_checks_per_hour"]) == (0.0, 4.0)


def test_sweep_without_background_has_no_hourly_rates():
    table = talk_eval.sweep([row("what time is it", 0.9)], "no hint", [], 0.0, "")
    assert all(t["false_requests_per_hour"] is None for t in table)


def test_background_is_scored_like_the_server(monkeypatch):
    from kestrel.wake import WakeSettings

    monkeypatch.delenv("KESTREL_TALK_HINT", raising=False)
    assert WakeSettings.from_env().talk_prompt == ""  # hint off by default, so background runs without it


@pytest.mark.parametrize("name", ["wake_record", "talk_eval", "wake_eval"])
def test_voice_scripts_never_import_network_modules(name):
    source = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
    for module in ("httpx", "requests", "urllib", "socket", "http.client", "ddgs"):
        assert f"import {module}" not in source and f"from {module}" not in source
