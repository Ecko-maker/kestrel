"""The console week's voice fixes: the Speaker switch reaches the server, a stricter wake while Kestrel speaks,
the wake rule B, and New conversation. Offline: scripted models, a fake microphone and speaker."""

import importlib.util
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_talk import (
    BASE,
    QUIET,
    TOKEN,
    WAKE,
    FakeRecogniser,
    FakeVoice,
    ScriptedMic,
    SpyLLM,
    app_with,
    make,
    say,
    spoken_request,
    until,
    wait_for,
)

from kestrel.bench.runner import agent_fingerprint
from kestrel.wake import WakeService, WakeSettings
from kestrel.wake import service as service_module
from kestrel.wake.detector import wake_confidence
from kestrel.wake.power import NullPower
from kestrel.wake.service import SELF_WAKE_TAIL_S, SELF_WAKE_THRESHOLD

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(service_module, "POWER_CHECK_S", 0.05)


def speaker_status(ws, on: bool, limit: int = 50) -> dict:
    """The next wake_status that carries this speaker value (every console also gets one on connect)."""
    for _ in range(limit):
        event = until(ws, "wake_status")
        if event.get("speaker") is on:
            return event
    raise AssertionError(f"no wake_status with speaker={on}")


def connect(app):
    return TestClient(app, base_url=BASE).websocket_connect(f"/ws?token={TOKEN}", headers={"origin": BASE})


# --- the Speaker switch ------------------------------------------------------------------------------------


def test_switching_the_speaker_off_during_a_turn_silences_its_answer(tmp_path):
    gate = threading.Event()

    class Slow(SpyLLM):
        def chat(self, messages, tools=None, **kwargs):
            gate.wait(5)
            return super().chat(messages, tools, **kwargs)

    app, _service, voice = app_with(tmp_path, Slow(say("It is noon.")))
    with connect(app) as ws:
        until(ws, "ready")
        ws.send_json({"type": "user_message", "text": "What time is it?", "voice": True, "speak": True})
        ws.send_json({"type": "speaker", "on": False})  # flipped while the model works
        until(ws, "wake_status")
        gate.set()
        until(ws, "done")
    assert voice.said == []


def test_speaker_off_stops_what_is_playing(tmp_path):
    app, _service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    voice.speaking = True
    with connect(app) as ws:
        until(ws, "ready")
        ws.send_json({"type": "speaker", "on": False})
        status = speaker_status(ws, False)
    assert voice.stops == 1 and status["speaker"] is False


def test_speaker_off_silences_the_greeting_and_the_fixed_sentences(tmp_path):
    service, voice, _r, _m, events, requests = make(tmp_path, script=spoken_request(), recogniser=FakeRecogniser(""))
    service.set_speaker(False)
    service.start()
    try:
        wait_for(lambda: any(e["type"] == "voice_ignored" for e in events))
        assert voice.said == [] and requests == []  # the wake and "didn't catch" happened, silently
        assert any(e["type"] == "wake" for e in events)
    finally:
        service.stop()


def test_speaker_on_again_speaks_again_and_every_console_sees_the_setting(tmp_path):
    app, _service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    with connect(app) as first, connect(app) as second:
        until(first, "ready")
        until(second, "ready")
        first.send_json({"type": "speaker", "on": False})
        assert speaker_status(second, False)["speaker"] is False  # one setting for every console
        first.send_json({"type": "speaker", "on": True})
        assert speaker_status(second, True)["speaker"] is True
        second.send_json({"type": "user_message", "text": "What time is it?", "voice": True, "speak": True})
        until(second, "done")
    assert voice.said == ["It is noon."]


def test_typed_questions_are_still_never_spoken(tmp_path):
    app, _service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    with connect(app) as ws:
        until(ws, "ready")
        ws.send_json({"type": "speaker", "on": True})
        ws.send_json({"type": "user_message", "text": "What time is it?"})
        until(ws, "done")
    assert voice.said == []


# --- a stricter wake while Kestrel speaks --------------------------------------------------------------------


class Scored:
    """Fires on the WAKE chunk with a given score, like VoskDetector above its threshold."""

    def __init__(self, score):
        self.score = score

    def feed(self, pcm):
        return self.score if pcm == WAKE else None


def service_with(tmp_path, score, voice, script, clock=time.monotonic):
    mic = ScriptedMic(script)
    service = WakeService(
        WakeSettings(enabled=True, model_dir=tmp_path, cooldown_s=0.0),
        lambda: Scored(score),
        lambda: mic,
        NullPower(),
        voice,
        lambda at, **_: None,
        clock=clock,
    )
    events: list[dict] = []
    service.emit = events.append
    return service, events


def test_the_status_says_whether_the_server_has_a_voice(tmp_path):
    """The console's Speaker switch is always shown; `speech: false` makes it disabled with its reason."""
    from kestrel.wake.speech import NullSpeaker

    with_voice, _ = service_with(tmp_path, 0.9, FakeVoice(), [])
    without, _ = service_with(tmp_path, 0.9, NullSpeaker(), [])
    assert with_voice.status()["speech"] is True and without.status()["speech"] is False


def woke(events):
    return any(e["type"] == "wake" for e in events)


@pytest.mark.parametrize(("score", "wakes"), [(0.95, False), (SELF_WAKE_THRESHOLD, True), (0.995, True)])
def test_while_kestrel_speaks_only_a_clear_kestrel_wakes_it(tmp_path, score, wakes):
    voice = FakeVoice()
    voice.speaking = True
    service, events = service_with(tmp_path, score, voice, [QUIET] * 3 + [WAKE] + [QUIET] * 3)
    service.start()
    try:
        time.sleep(0.15)
        assert woke(events) is wakes
        if wakes:
            assert voice.stops == 1  # barge-in still works
    finally:
        service.stop()


def test_the_stricter_rule_lasts_one_second_after_the_speech_then_stops(tmp_path):
    now = [100.0]
    voice = FakeVoice()
    service, _events = service_with(tmp_path, 0.95, voice, [], clock=lambda: now[0])
    voice.speaking = True
    service._spoke_at = now[0]
    voice.speaking = False
    now[0] += SELF_WAKE_TAIL_S - 0.1
    assert not service._not_its_own_voice(0.95)
    now[0] += 0.2
    assert service._not_its_own_voice(0.95)
    assert service._not_its_own_voice(SELF_WAKE_THRESHOLD)


def test_when_kestrel_is_quiet_the_usual_threshold_applies(tmp_path):
    service, events = service_with(tmp_path, 0.91, FakeVoice(), [QUIET] * 3 + [WAKE] + [QUIET] * 3)
    service.start()
    try:
        wait_for(lambda: woke(events))
    finally:
        service.stop()


# --- rule B --------------------------------------------------------------------------------------------------


def result(*words, conf=0.95):
    return json.dumps({"result": [{"word": w, "conf": conf if w == "kestrel" else 1.0} for w in words]})


@pytest.mark.parametrize(
    ("words", "wakes"),
    [
        (("kestrel",), True),
        (("kestrel", "[unk]", "[unk]"), True),  # "Kestrel, what time is it?" in one breath
        (("hi", "kestrel"), True),
        (("good", "morning", "kestrel"), True),
        (("[unk]", "kestrel"), False),  # an unknown word first: "Howdy Kestrel", "I saw a kestrel"
        (("[unk]", "[unk]", "kestrel", "[unk]"), False),  # in the middle of other speech
        (("hello", "[unk]", "kestrel"), False),
    ],
)
def test_rule_b_kestrel_wakes_only_with_nothing_unknown_before_it(words, wakes):
    assert (wake_confidence(result(*words)) is not None) is wakes


def test_rule_b_doesnt_touch_the_spoken_cancel():
    assert (
        wake_confidence(json.dumps({"result": [{"word": "[unk]"}, {"word": "cancel", "conf": 0.4}]}), "cancel") == 0.4
    )


# --- New conversation ------------------------------------------------------------------------------------------


def test_a_new_conversation_is_a_new_session_with_no_history_and_the_traces_stay(tmp_path):
    llm = SpyLLM(say("First answer."), say("Second answer."))
    app, _service, _voice = app_with(tmp_path, llm)
    with connect(app) as ws:
        first = until(ws, "ready")["session_id"]
        ws.send_json({"type": "user_message", "text": "Remember the word heron."})
        until(ws, "done")
    with connect(app) as ws:  # New conversation: the console clears the chat and reconnects
        second = until(ws, "ready")["session_id"]
        ws.send_json({"type": "user_message", "text": "Which word?"})
        until(ws, "done")
    assert first != second
    assert not any("heron" in json.dumps(m) for m in llm.inputs[1])  # nothing from the first conversation
    with sqlite3.connect(tmp_path / "traces.db") as conn:
        sessions = {r[0] for r in conn.execute("SELECT session_id FROM traces")}
    assert sessions == {first, second}  # both conversations' traces are kept


# --- nothing here changes the agent --------------------------------------------------------------------------------


def test_the_agent_fingerprint_is_unchanged():
    assert agent_fingerprint("groq", "openai/gpt-oss-120b")["sha"] == "35fac5ba6277"


# --- the playback script ------------------------------------------------------------------------------------


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_script", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_playback_script_speaks_what_the_console_would_and_prints_no_text(tmp_path, capsys, monkeypatch):
    play_answers = load_script("play_answers")
    db = tmp_path / "traces.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE traces (start_time REAL, final_answer TEXT)")
        conn.executemany(
            "INSERT INTO traces VALUES (?, ?)",
            [(1.0, "Too old."), (10.0, "Kestrel found it. Second. Third."), (20.0, None), (30.0, "**Done.**")],
        )
    texts = play_answers.spoken_texts(db, since=5.0)
    assert texts == ["I found it. Second. The rest is on screen.", "Done."]  # never its own name
    said: list[str] = []
    monkeypatch.setattr(play_answers.time, "sleep", lambda s: None)
    play_answers.play(texts, said.append, gap_s=0)
    assert said == texts
    printed = capsys.readouterr().out
    assert "found" not in printed and "Done" not in printed and "answer 2 of 2" in printed


def test_a_server_without_the_wake_word_says_so_and_how_to_turn_it_on():
    """11-07: "Kestrel" got no answer and the console showed nothing, because the server ran without KESTREL_WAKE."""
    from kestrel import WAKE_OFF_NOTICE
    from kestrel.wake import build_wake_service

    assert "KESTREL_WAKE=on" in WAKE_OFF_NOTICE
    assert build_wake_service(lambda at, **_: None) is None  # off unless asked for (the test env doesn't set it)
