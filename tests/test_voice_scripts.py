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
    e2e = heard[:1]  # only one of them came after a false wake
    table = {t["threshold"]: t for t in talk_eval.sweep(rows, "no hint", heard, 0.5, "", e2e, 0.5)}
    assert [t for t in talk_eval.SWEEP] == sorted(table)
    low, mid, high = table[0.30], table[0.50], table[0.80]
    assert (low["send"], low["check"], low["drop"], low["misheard_sent"]) == (3, 0, 1, 1)
    assert (mid["send"], mid["check"], mid["drop"], mid["misheard_sent"]) == (2, 1, 1, 1)
    assert (high["send"], high["check"], high["drop"], high["misheard_sent"]) == (1, 2, 1, 0)
    # if every segment were recorded (the old number, renamed)
    assert (low["if_recorded_requests_per_hour"], low["if_recorded_checks_per_hour"]) == (4.0, 0.0)
    assert (mid["if_recorded_requests_per_hour"], mid["if_recorded_checks_per_hour"]) == (2.0, 2.0)
    assert (high["if_recorded_requests_per_hour"], high["if_recorded_checks_per_hour"]) == (0.0, 4.0)
    # end to end: only the recording after the false wake counts
    assert (low["false_requests_per_hour"], low["false_checks_per_hour"]) == (2.0, 0.0)
    assert (high["false_requests_per_hour"], high["false_checks_per_hour"]) == (0.0, 2.0)


def test_sweep_without_background_has_no_hourly_rates():
    table = talk_eval.sweep([row("what time is it", 0.9)], "no hint", [], 0.0, "")
    assert all(t["false_requests_per_hour"] is None and t["if_recorded_requests_per_hour"] is None for t in table)


# --- end to end: what the live service records after a false wake --------------------------------------

QUIET = b"\x08\x00" * 1600  # one 0.1 s chunk, loudness 0
LOUD = b"\x00\x40" * 1600  # one 0.1 s chunk of "speech"


def test_after_cooldown_keeps_one_wake_per_ten_seconds():
    from kestrel.wake.detector import after_cooldown

    assert after_cooldown([30.0, 1.0, 5.0, 11.0, 12.0], 10.0) == [1.0, 11.0, 30.0]
    assert after_cooldown([], 10.0) == []


def test_wake_events_are_timed_at_the_end_of_the_chunk_the_live_detector_fires_on():
    import json

    from kestrel.wake.detector import wake_events

    class Rec:
        def __init__(self, grammar):
            self.last = b""

        def AcceptWaveform(self, data):
            self.last = data
            return data == LOUD

        def Result(self):
            return json.dumps({"result": [{"word": "kestrel", "conf": 0.93}]})

        def FinalResult(self):
            return "{}"

    pcm = QUIET * 4 + LOUD + QUIET * 5
    assert wake_events(Rec, pcm, len(QUIET)) == [(0.5, 0.93, None)]


def test_after_a_wake_the_recording_follows_the_end_of_speech_rule():
    # 2 s of quiet, a wake at 2.0 s, 1 s of speech, then quiet: recorded until 1 s of quiet after the speech
    pcm = QUIET * 20 + LOUD * 10 + QUIET * 30
    [seg] = talk_eval.wake_segments(pcm, [2.0])
    assert (seg["at_s"], seg["state"], seg["speech_s"]) == (2.0, "done", 1.0)
    assert len(seg["audio"]) == len(QUIET) * 20  # 1 s of speech + 1 s of quiet that ended it


def test_a_wake_followed_by_quiet_records_nothing():
    [seg] = talk_eval.wake_segments(QUIET * 60, [1.0])
    assert seg["state"] == "no_speech"


def test_a_recording_stops_at_twenty_seconds():
    [seg] = talk_eval.wake_segments(QUIET * 10 + LOUD * 300, [1.0])
    assert seg["state"] == "max" and len(seg["audio"]) == len(QUIET) * 200


def test_no_wake_means_no_recording():
    assert talk_eval.wake_segments(QUIET * 10 + LOUD * 50, []) == []


def test_background_is_scored_like_the_server(monkeypatch):
    from kestrel.wake import WakeSettings

    monkeypatch.delenv("KESTREL_TALK_HINT", raising=False)
    assert WakeSettings.from_env().talk_prompt == ""  # hint off by default, so background runs without it


@pytest.mark.parametrize("name", ["wake_record", "talk_eval", "wake_eval"])
def test_voice_scripts_never_import_network_modules(name):
    source = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
    for module in ("httpx", "requests", "urllib", "socket", "http.client", "ddgs"):
        assert f"import {module}" not in source and f"from {module}" not in source


# --- wake_record.py: 3 s clips, a countdown and a check for the problems found in the first misses -----


def tone(chunks: int, value: int = 0x2000) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * 1600 * chunks


def test_a_good_clip_has_no_problem():
    pcm = tone(5, 4) + tone(10) + tone(15, 4)  # quiet, the word, quiet
    assert wake_record.clip_problem(pcm) is None


def test_a_near_silent_clip_is_too_quiet():
    assert wake_record.clip_problem(tone(30, 40)).startswith("too quiet")  # about -58 dBFS


def test_speech_at_either_edge_is_flagged_as_cut_off():
    assert "start" in wake_record.clip_problem(tone(4) + tone(26, 4))
    assert "end" in wake_record.clip_problem(tone(26, 4) + tone(4))


def test_the_countdown_starts_recording_before_now():
    shown: list[str] = []
    wake_record.countdown(shown.append, lambda: shown.append("<recording starts>"), sleep=lambda s: None)
    assert shown == ["3", "2", "<recording starts>", "1", "now: say 'Kestrel'"]


# --- greetings: wake_record.py --phrases and wake_eval.py per phrase -----------------------------------


def test_the_phrase_list_and_file_names():
    assert wake_record.PHRASES == ("Kestrel", "Hi Kestrel", "Hello Kestrel", "Hey Kestrel", "Shalom Kestrel")
    assert [wake_record.slug(p) for p in wake_record.PHRASES[:2]] == ["kestrel", "hi-kestrel"]
    shown: list[str] = []
    wake_record.countdown(shown.append, lambda: None, sleep=lambda s: None, say="Hi Kestrel")
    assert shown[-1] == "now: say 'Hi Kestrel'"


wake_eval = load("wake_eval")


def test_wake_eval_reads_the_phrase_back_from_the_file_name():
    assert wake_eval.phrase_of("hi-kestrel-003.wav") == "hi kestrel"
    assert wake_eval.phrase_of("kestrel-012.wav") == "kestrel"
    assert wake_eval.phrase_of("shalom-kestrel-000.wav") == "shalom kestrel"
    assert wake_eval.expected_greeting("shalom kestrel") == "shalom"
    assert wake_eval.expected_greeting("kestrel") is None


def test_wake_eval_counts_detection_greetings_and_false_wakes():
    clips = {
        "hello-kestrel-000.wav": [(1.0, 0.95, "hello")],
        "hello-kestrel-001.wav": [(1.0, 0.95, None)],  # woke, greeting not heard
        "hello-kestrel-002.wav": [(1.0, 0.60, "hello")],  # below 0.9
        "hello-kestrel-003.wav": [],
    }
    assert wake_eval.detected(clips, 0.9) == 2 and wake_eval.detected(clips, 0.5) == 3
    assert wake_eval.greeted(clips, 0.9) == 1 and wake_eval.greeted(clips, 0.5) == 2
    background = {"room-000.wav": [(10.0, 0.95, None), (15.0, 0.95, None), (30.0, 0.95, None)]}
    assert wake_eval.false_wakes(background, 0.9) == 2  # the 10 s cooldown, as live
