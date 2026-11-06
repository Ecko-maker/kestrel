"""The follow-up window after a spoken answer, and local closings (docs/voice-wake.md, "Follow-up and closings").
No microphone, no Vosk model, no Whisper model, no network: fakes for each."""

import importlib.util
import json
import random
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_talk import (
    BASE,
    LOUD,
    QUIET,
    TOKEN,
    WAKE,
    FakeDetector,
    FakeRecogniser,
    FakeVoice,
    ScriptedMic,
    SpyLLM,
    say,
    until,
    wait_for,
)

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate
from kestrel.tracing import Tracer
from kestrel.wake import WakeService, WakeSettings
from kestrel.wake import service as service_module
from kestrel.wake.closings import (
    CLOSING_GRAMMAR,
    CLOSINGS,
    ClosingListener,
    FollowupWindow,
    closing_reply,
    learned_floor,
    parse_closing,
)
from kestrel.wake.detector import grammar_json
from kestrel.wake.power import NullPower
from kestrel.wake.talk import FILLERS, Endpointer
from kestrel.web.app import WebConfig, create_app

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    sys.path.insert(0, str(ROOT / "scripts"))  # closings_eval imports wake_eval, as when run as a script
    spec = importlib.util.spec_from_file_location(f"{name}_script", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeVosk:
    """A recognizer that hears the given words (with this confidence) by the end of the utterance."""

    def __init__(self, words: list[str], conf: float = 1.0):
        self.final = json.dumps({"result": [{"word": w, "conf": conf} for w in words]})
        self.fed = 0

    def AcceptWaveform(self, data: bytes) -> bool:
        self.fed += 1
        return False

    def Result(self) -> str:
        return "{}"

    def FinalResult(self) -> str:
        return self.final

    def PartialResult(self) -> str:
        return "{}"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(service_module, "POWER_CHECK_S", 0.05)


def build(tmp_path, heard=(), *, conf=1.0, recogniser=None, followup=1.0, name=None, talk=True, closings=True):
    """A running service, heard: what the closings grammar hears in each window."""
    voice = FakeVoice()
    mic = ScriptedMic([QUIET] * 10)
    recogniser = recogniser or FakeRecogniser()
    made: list[FakeVosk] = []

    def make_closing():
        made.append(FakeVosk(list(heard), conf))
        return ClosingListener(lambda grammar: made[-1])

    settings = WakeSettings(
        enabled=True, model_dir=tmp_path, send_countdown_s=0.05, followup_seconds=followup, user_name=name
    )
    service = WakeService(
        settings,
        FakeDetector,
        lambda: mic,
        NullPower(),
        voice,
        lambda at: None,
        make_transcriber=(lambda: recogniser) if talk else None,
        make_closing=make_closing if closings else None,
    )
    events: list[dict] = []
    requests: list[str] = []
    service.emit = events.append
    service.on_request = requests.append
    service.closings_made = made  # type: ignore[attr-defined]
    service.test_mic = mic  # type: ignore[attr-defined]
    return service, voice, recogniser, mic, events, requests


def kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events if e["type"] not in ("wake_status", "wake_level")]


def opened(events: list[dict]) -> bool:
    return any(e["type"] == "wake_followup" for e in events)


def ended(events: list[dict]) -> str | None:
    return next((e["reason"] for e in events if e["type"] == "wake_end"), None)


def open_window(service, events, script=()) -> None:
    """The audio is loaded while the answer is still playing, so the window hears it from its first chunk."""
    service.speaker.speaking = True
    service.start()
    service.say_answer("It is noon.")
    service.test_mic.script[:] = list(script)
    service.speaker.speaking = False
    wait_for(lambda: opened(events))


# --- the window ---------------------------------------------------------------------------------


def test_the_window_opens_only_when_the_spoken_answer_has_finished(tmp_path):
    service, voice, _r, _m, events, _ = build(tmp_path)
    voice.speaking = True
    service.start()
    try:
        service.say_answer("It is noon.")
        time.sleep(0.1)
        assert not opened(events) and voice.said == ["It is noon."]
        voice.speaking = False
        wait_for(lambda: opened(events))
        assert service.attending()  # the console's level ring and "Listening…"
    finally:
        service.stop()


def test_silence_goes_back_to_standby_after_the_window_with_nothing_said(tmp_path):
    service, voice, recogniser, _m, events, requests = build(tmp_path)
    open_window(service, events)
    try:
        wait_for(lambda: ended(events) == "silence")
        assert not service.attending() and voice.said == ["It is noon."]  # no "I didn't catch that"
        assert recogniser.heard == [] and requests == []
        assert "voice_ignored" not in kinds(events)
    finally:
        service.stop()


def test_a_closing_gets_a_local_reply_and_nothing_else(tmp_path):
    service, voice, recogniser, _m, events, requests = build(tmp_path, ["thank", "you"], name="Robin")
    open_window(service, events, [LOUD] * 5 + [QUIET] * 15)
    try:
        wait_for(lambda: ended(events) == "closing")
        wait_for(lambda: len(voice.said) == 2)
        assert voice.said[1] == "You're welcome, Robin."
        time.sleep(0.1)
        assert recogniser.heard == [] and requests == []  # no Whisper, no model, no chat message
        assert not ({"voice_pending", "voice_request", "voice_check", "voice_ignored"} & set(kinds(events)))
        assert not service.attending()
    finally:
        service.stop()


def test_a_closing_below_the_threshold_takes_the_normal_path(tmp_path):
    recogniser = FakeRecogniser("Thank you.", 0.9)
    service, voice, _r, _m, events, requests = build(tmp_path, ["thank", "you"], conf=0.2, recogniser=recogniser)
    service.on_pending = lambda rid, text, seconds: requests.append(text) or True
    open_window(service, events, [LOUD] * 5 + [QUIET] * 15)
    try:
        wait_for(lambda: ended(events) == "speech")
        wait_for(lambda: "voice_ignored" in kinds(events))  # Whisper's "thank you" stays a filler
        assert requests == [] and voice.said[-1] == "I didn't catch that."
    finally:
        service.stop()


def test_other_speech_takes_the_normal_send_path(tmp_path):
    recogniser = FakeRecogniser("And in Paris?", 0.8)
    service, voice, _r, _m, events, requests = build(tmp_path, ["[unk]", "[unk]"], recogniser=recogniser)
    pending: list[str] = []
    service.on_pending = lambda rid, text, seconds: pending.append(text) or True
    open_window(service, events, [LOUD] * 8 + [QUIET] * 15)
    try:
        wait_for(lambda: requests == ["And in Paris?"])  # through the countdown
        assert pending == ["And in Paris?"] and ended(events) == "speech" and len(recogniser.heard) == 1
        assert voice.said == ["It is noon."]
    finally:
        service.stop()


def test_a_request_that_starts_with_thanks_is_not_a_closing(tmp_path):
    recogniser = FakeRecogniser("Thanks, now list my files.", 0.8)
    service, _v, _r, _m, events, requests = build(tmp_path, ["thanks", "[unk]", "[unk]"], recogniser=recogniser)
    open_window(service, events, [LOUD] * 8 + [QUIET] * 15)
    try:
        wait_for(lambda: requests == ["Thanks, now list my files."])
    finally:
        service.stop()


def test_speech_starting_late_in_the_window_is_heard_to_its_end(tmp_path):
    recogniser = FakeRecogniser("And in Paris?", 0.8)
    service, _v, _r, _m, events, requests = build(tmp_path, recogniser=recogniser)
    open_window(service, events, [QUIET] * 8 + [LOUD] * 20 + [QUIET] * 15)  # 0.8 s in, past the 1 s window's end
    try:
        wait_for(lambda: requests == ["And in Paris?"])
        assert recogniser.heard[0] >= 28 * len(QUIET)  # the whole utterance, not cut at the window's end
    finally:
        service.stop()


def test_barge_in_during_the_answer_cancels_the_window(tmp_path):
    service, voice, _r, mic, events, _ = build(tmp_path)
    voice.speaking = True
    service.start()
    try:
        service.say_answer("A long answer.")
        mic.script[:] = [WAKE]
        wait_for(lambda: voice.stops == 1)  # "Kestrel" stopped the speech
        time.sleep(0.1)
        assert "wake" in kinds(events) and not opened(events)
    finally:
        service.stop()


def test_a_wake_during_the_window_takes_over(tmp_path):
    service, _v, _r, mic, events, _ = build(tmp_path, followup=5.0)
    open_window(service, events)
    try:
        mic.script[:] = [WAKE]
        wait_for(lambda: "wake" in kinds(events))
        assert service._window is None and service._recording is not None
    finally:
        service.stop()


def test_typing_ends_the_window(tmp_path):
    service, _v, recogniser, mic, events, requests = build(tmp_path)
    open_window(service, events)
    try:
        service.end_attention()
        mic.script[:] = [LOUD] * 8 + [QUIET] * 15
        time.sleep(0.15)
        assert not service.attending() and recogniser.heard == [] and requests == []
    finally:
        service.stop()


@pytest.mark.parametrize("settings", [{"followup": 0.0}, {"talk": False}], ids=["seconds-0", "no-talk-model"])
def test_the_window_can_be_off(tmp_path, settings):
    service, voice, _r, _m, events, _ = build(tmp_path, **settings)
    service.start()
    try:
        service.say_answer("It is noon.")
        time.sleep(0.15)
        assert not opened(events) and voice.said == ["It is noon."]
    finally:
        service.stop()


def test_without_the_closings_grammar_speech_still_reaches_whisper(tmp_path):
    recogniser = FakeRecogniser("And in Paris?", 0.8)
    service, _v, _r, _m, events, requests = build(tmp_path, recogniser=recogniser, closings=False)
    open_window(service, events, [LOUD] * 8 + [QUIET] * 15)
    try:
        wait_for(lambda: requests == ["And in Paris?"])
    finally:
        service.stop()


# --- outside the window -------------------------------------------------------------------------


def test_kestrel_thank_you_outside_the_window_is_still_dropped_as_filler(tmp_path):
    """Documented (docs/voice-wake.md): closings exist only in the window, so this is Whisper's filler."""
    filler = FakeRecogniser("Thank you.")
    service, voice, _r, mic, events, requests = build(tmp_path, ["thank", "you"], recogniser=filler)
    pending: list[str] = []
    service.on_pending = lambda rid, text, seconds: pending.append(text) or True
    mic.script[:] = [QUIET] * 5 + [WAKE] + [QUIET] * 3 + [LOUD] * 8 + [QUIET] * 15
    service.start()
    try:
        wait_for(lambda: "voice_ignored" in kinds(events))
        assert service.closings_made == []  # type: ignore[attr-defined]  # the grammar never ran
        assert pending == [] and requests == [] and voice.said[-1] == "I didn't catch that."
        assert not opened(events)
    finally:
        service.stop()


def test_thank_you_stays_on_whispers_filler_list():
    assert {"thank you", "thanks", "bye"} <= FILLERS


# --- the server: which answers open the window ------------------------------------------------------


def app_with(tmp_path, llm):
    service, voice, *_ = build(tmp_path)
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})

    def make_agent(on_event, approver):
        return Agent(
            llm, on_event=on_event, tracer=tracer, gate=ApprovalGate(approver, log_path=tmp_path / "audit.jsonl")
        )

    config = WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))
    return create_app(make_agent, tracer, config, wake=service), service, voice


def chat(app, message: dict) -> None:
    with TestClient(app, base_url=BASE).websocket_connect(f"/ws?token={TOKEN}", headers={"origin": BASE}) as ws:
        until(ws, "ready")
        ws.send_json(message)
        until(ws, "done")


def test_a_spoken_requests_answer_opens_the_window(tmp_path):
    app, service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    chat(app, {"type": "user_message", "text": "What time is it?", "voice": True})
    assert voice.said == ["It is noon."] and service._followup_due


def test_the_window_never_opens_after_a_typed_question(tmp_path):
    app, service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    chat(app, {"type": "user_message", "text": "What time is it?"})
    assert voice.said == [] and not service._followup_due and service._window is None


def test_speak_answers_off_means_no_window(tmp_path):
    app, service, voice = app_with(tmp_path, SpyLLM(say("It is noon.")))
    chat(app, {"type": "user_message", "text": "What time is it?", "voice": True, "speak": False})
    assert voice.said == [] and not service._followup_due


# --- the pure parts ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "phrase"),
    [
        (["thank", "you"], "thank you"),
        (["thanks"], "thanks"),
        (["cheers"], "cheers"),
        (["that's", "all"], "that's all"),
        (["never", "mind"], "never mind"),
        (["bye"], "bye"),
        (["thanks", "bye"], "thanks"),  # only closings: the first decides the reply
        (["thank", "you", "thank", "you"], "thank you"),
        ([], None),
        (["[unk]"], None),
        (["thanks", "[unk]"], None),
        (["[unk]", "bye"], None),
        (["thank"], None),
        (["you"], None),
    ],
)
def test_a_closing_is_the_whole_utterance(words, phrase):
    assert parse_closing(words) == phrase


@pytest.mark.parametrize(
    ("phrase", "turn", "name", "reply"),
    [
        ("thank you", 0, "Robin", "You're welcome, Robin."),
        ("thanks", 1, "Robin", "Anytime, Robin."),
        ("cheers", 0, None, "You're welcome."),
        ("cheers", 1, None, "Anytime."),
        ("that's all", 0, "Robin", "Okay, I'm here if you need me."),
        ("never mind", 1, None, "Okay, I'm here if you need me."),
        ("bye", 0, "Robin", "Okay, I'm here if you need me."),
    ],
)
def test_the_local_replies(phrase, turn, name, reply):
    assert closing_reply(phrase, name, turn) == reply


def test_the_grammar_has_every_closing_and_unk():
    assert json.loads(grammar_json(grammar=CLOSING_GRAMMAR)) == [*CLOSINGS, "[unk]"]


def test_the_listener_takes_the_lowest_word_confidence_across_segments():
    class Segments(FakeVosk):
        def AcceptWaveform(self, data):
            return True

        def Result(self):
            return json.dumps({"result": [{"word": "thank", "conf": 0.9}]})

    rec = Segments([])
    rec.final = json.dumps({"result": [{"word": "you", "conf": 0.6}]})
    listener = ClosingListener(lambda grammar: rec)
    listener.feed(b"x")
    assert listener.finish() == ("thank you", 0.6)


def test_the_endpointers_start_timeout_defaults_to_the_wake_flow():
    assert Endpointer(floor=0.0).start_timeout_s == 3.0
    window = FollowupWindow(5.0, 0.0)
    assert all(window.feed(QUIET, 0.0) is None for _ in range(49))
    end = window.feed(QUIET, 0.0)
    assert end is not None and end.state == "no_speech" and end.seconds == pytest.approx(5.0)


# --- the eval's logic --------------------------------------------------------------------------------


def test_every_recorded_closing_maps_back_to_its_phrase():
    record, evaluate = load_script("wake_record"), load_script("closings_eval")
    assert {phrase.lower() for phrase in record.CLOSING_PHRASES} == set(CLOSINGS)
    assert record.slug("That's all") == "thats-all" and record.slug("Hi Kestrel") == "hi-kestrel"
    assert evaluate.phrase_of_clip("thats-all-003.wav") == "that's all"
    assert evaluate.phrase_of_clip("thank-you-012.wav") == "thank you"
    assert evaluate.phrase_of_clip("kestrel-000.wav") is None


def test_the_eval_counts_closings_per_phrase_and_threshold():
    evaluate = load_script("closings_eval")
    from kestrel.wake.closings import WindowEnd

    right = WindowEnd("done", "bye", 0.9, b"", 0.5, 1.0)
    wrong = WindowEnd("done", "thanks", 0.4, b"", 0.5, 1.0)
    miss = WindowEnd("done", None, 0.0, b"", 0.5, 1.0)
    rows = evaluate.closing_rows([("bye", right), ("bye", wrong), ("bye", miss)], [0.0, 0.5])
    bye = [r for r in rows if r["phrase"] == "bye"]
    assert (bye[0]["right"], bye[0]["other"], bye[0]["clips"]) == (1, 1, 3)
    assert (bye[1]["right"], bye[1]["other"]) == (1, 0)


def test_the_eval_counts_false_closings_and_requests_per_window_and_hour():
    evaluate = load_script("closings_eval")
    from kestrel.wake.closings import WindowEnd

    ends = [
        (WindowEnd("done", "bye", 0.9, b"", 0.5, 1800.0), "drop"),  # a false closing; dropped if below
        (WindowEnd("done", None, 0.0, b"", 2.0, 1800.0), "send"),
        (WindowEnd("done", None, 0.0, b"", 2.0, 0.0), "check"),
        (WindowEnd("no_speech", None, 0.0, b"", 0.0, 0.0), None),
    ]
    low, high = evaluate.background_rows(ends, [0.5, 0.95])
    assert (low["false_closings"], low["false_requests"], low["false_checks"], low["silence"]) == (1, 1, 1, 1)
    assert low["false_closings_per_hour"] == 1.0 and low["false_requests_per_100"] == 25.0
    assert (high["false_closings"], high["dropped"]) == (0, 1)


def test_the_evals_random_windows_are_seeded_and_stay_inside_the_file():
    evaluate = load_script("closings_eval")
    first = evaluate.random_starts(random.Random(2026), 1000, 20, 260)
    assert first == evaluate.random_starts(random.Random(2026), 1000, 20, 260)
    assert len(first) == 20 and all(0 <= s < 740 for s in first)
    assert evaluate.random_starts(random.Random(1), 100, 5, 260) == []
    assert sum(evaluate.split_windows(200, [600, 600, 300])) == 200


def test_the_eval_learns_the_noise_floor_as_the_live_service_does():
    floors = learned_floor([0.0] * 30)
    assert floors[-1] < 0.01 and floors[0] < 0.2
