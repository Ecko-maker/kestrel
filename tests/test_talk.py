"""Talking to Kestrel (docs/voice-wake.md): end of speech, the hallucination guard, what is spoken,
the whole flow through the web app, and the privacy and safety rules. Fakes for the microphone,
the recogniser and the voice; no audio device, model or Windows API is used."""

import builtins
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer
from kestrel.wake import WakeService, WakeSettings
from kestrel.wake import service as service_module
from kestrel.wake.normalize import normalize, raw_words, word_errors
from kestrel.wake.power import NullPower
from kestrel.wake.talk import (
    APPROVAL_SENTENCE,
    CHECK_ON_SCREEN,
    DIDNT_CATCH,
    END_SILENCE_S,
    MAX_REQUEST_S,
    REST_ON_SCREEN,
    START_TIMEOUT_S,
    Endpointer,
    NoiseFloor,
    for_speech,
    guard,
    spoken_answer,
)
from kestrel.wake.transcribe import (
    MAX_PROMPT_CHARS,
    VOCAB_WORDS,
    Transcript,
    WhisperCppTranscriber,
    read_vocab,
    talk_model_file,
    talk_prompt,
)
from kestrel.web.app import WebConfig, create_app

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"
QUIET = b"\x08\x00" * 1600  # ~ -72 dBFS: loudness 0
LOUD = b"\x00\x40" * 1600  # ~ -6 dBFS: speech
WAKE = b"kestrel"  # the fake detector's trigger chunk


def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


# --- fakes -------------------------------------------------------------------------------------


class FakeDetector:
    def feed(self, pcm: bytes) -> float | None:
        return 1.0 if pcm == WAKE else None


class ScriptedMic:
    """Plays a script of chunks (fast), then quiet forever."""

    def __init__(self, script: list[bytes]):
        self.script = list(script)
        self.opened = 0

    def open(self):
        self.opened += 1

    def read(self, timeout: float):
        time.sleep(0.002)
        return self.script.pop(0) if self.script else QUIET

    def close(self):
        pass


class FakeVoice:
    def __init__(self):
        self.said: list[str] = []
        self.stops = 0
        self.speaking = False

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stops += 1
        self.speaking = False

    def busy(self):
        return self.speaking

    def close(self):
        pass


class FakeRecogniser:
    def __init__(self, text: str = "What time is it in Tokyo?", confidence: float | None = 0.8):
        self.text, self.confidence = text, confidence
        self.heard: list[int] = []  # lengths only

    def transcribe(self, pcm: bytes) -> Transcript:
        self.heard.append(len(pcm))
        return Transcript(self.text, self.confidence)


def spoken_request(quiet_before: int = 3, speech: int = 8) -> list[bytes]:
    """A room's quiet, "Kestrel", a short pause, the request, then the quiet that ends it."""
    return [QUIET] * 10 + [WAKE] + [QUIET] * quiet_before + [LOUD] * speech + [QUIET] * 15


def make(tmp_path, *, script=(), recogniser=None, voice=None, talk=True, countdown=0.05, cancel=None):
    voice = voice or FakeVoice()
    recogniser = recogniser or FakeRecogniser()
    mic = ScriptedMic(list(script))
    service = WakeService(
        WakeSettings(enabled=True, model_dir=tmp_path, send_countdown_s=countdown),
        FakeDetector,
        lambda: mic,
        NullPower(),
        voice,
        lambda at: None,
        make_transcriber=(lambda: recogniser) if talk else None,
        make_cancel=cancel,
    )
    events: list[dict] = []
    requests: list[str] = []
    service.emit = events.append
    service.on_request = requests.append
    service.checked = []  # type: ignore[attr-defined]  # low-confidence transcripts sent for checking
    service.on_check = service.checked.append  # type: ignore[attr-defined]
    return service, voice, recogniser, mic, events, requests


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(service_module, "POWER_CHECK_S", 0.05)


# --- end of speech -------------------------------------------------------------------------------


def feed(endpoint: Endpointer, levels: list[float]) -> str:
    state = endpoint.state
    for level in levels:
        state = endpoint.feed(level)
    return state


def test_end_of_speech_after_one_second_of_quiet():
    e = Endpointer(floor=0.1)
    assert feed(e, [0.1] * 5 + [0.6] * 8) == "speaking"
    assert feed(e, [0.1] * int(END_SILENCE_S / 0.1 - 1)) == "speaking"
    assert feed(e, [0.1]) == "done"
    assert e.speech_s == pytest.approx(0.8)


def test_nothing_said_within_three_seconds():
    e = Endpointer(floor=0.1)
    assert feed(e, [0.12] * int(START_TIMEOUT_S / 0.1 - 1)) == "waiting"  # below floor + margin
    assert feed(e, [0.12]) == "no_speech"


def test_a_request_is_cut_at_twenty_seconds():
    e = Endpointer(floor=0.1)
    assert feed(e, [0.7] * int(MAX_REQUEST_S / 0.1)) == "max"


def test_the_noise_floor_follows_quiet_fast_and_loud_slowly():
    floor = NoiseFloor(start=0.2)
    for _ in range(30):
        floor.update(0.05)
    assert floor.value == pytest.approx(0.05, abs=0.01)
    for _ in range(10):
        floor.update(0.9)  # someone talks for a second
    assert floor.value < 0.15


# --- the hallucination guard -----------------------------------------------------------------------


def test_guard_three_paths_send_check_drop():
    sent = guard("Turn off the lights.", 0.60, 2.0, 0.45)
    assert (sent.action, sent.text, sent.reason) == ("send", "Turn off the lights.", "ok")
    check = guard("Turn off the lights.", 0.30, 2.0, 0.45)
    assert (check.action, check.text, check.reason) == ("check", "Turn off the lights.", "low confidence")
    dropped = guard("Turn off the lights.", 0.9, 0.2, 0.45)
    assert (dropped.action, dropped.text, dropped.reason) == ("drop", None, "too little speech")
    assert guard("Turn off the lights.", None, 2.0, 0.45).action == "send"  # confidence unknown: sent


def test_guard_drops_under_0_4_seconds_of_speech():
    assert guard("Hello there", 0.9, 0.3, 0.45).action == "drop"
    assert guard("Hello there", 0.9, 0.4, 0.45).text == "Hello there"
    assert guard("Hello there", 0.1, 0.3, 0.45).action == "drop", "a hard drop wins over 'check first'"


def test_guard_drops_the_vocabulary_hint_echoed_back():
    prompt = talk_prompt(["Priya"])
    assert guard("todo list, workspace, file", 0.9, 2.0, 0.45, prompt).reason == "prompt echo"
    assert guard("Names: Priya.", 0.9, 2.0, 0.45, prompt).reason == "prompt echo"
    assert guard("Email Priya about the workspace", 0.9, 2.0, 0.45, prompt).action == "send"  # real request
    assert guard("weather", 0.9, 2.0, 0.45, prompt).action == "send"  # one word isn't taken for an echo


@pytest.mark.parametrize(
    "text",
    ["Thank you for watching!", "thanks for watching.", "You", "you.", "...", "?", "[BLANK_AUDIO]", "(dramatic music)"],
)
def test_guard_drops_filler_hallucinations(text):
    verdict = guard(text, 0.9, 2.0, 0.45)
    assert verdict.action == "drop" and verdict.text is None and verdict.reason in ("filler", "empty")
    assert guard(text, 0.1, 2.0, 0.45).action == "drop", "low confidence never rescues a filler"


def test_fillers_match_the_whole_transcript_only():
    assert (
        guard("Thank you for watching the match with me", 0.9, 2.0, 0.45).text
        == "Thank you for watching the match with me"
    )
    assert guard("Tell you what, list my files", 0.9, 2.0, 0.45).text == "Tell you what, list my files"
    assert (
        guard("[BLANK_AUDIO] List my files", 0.9, 2.0, 0.45).text == "List my files"
    )  # the label goes, the request stays


# --- what is spoken ----------------------------------------------------------------------------------


def test_long_answers_speak_two_sentences_then_the_rest_is_on_screen():
    answer = "It is 9 pm in Tokyo. That is 13 hours ahead of you. Tomorrow is Friday there."
    assert spoken_answer(answer) == f"It is 9 pm in Tokyo. That is 13 hours ahead of you. {REST_ON_SCREEN}"
    assert spoken_answer("Done.") == "Done."


def test_spoken_answers_leave_out_markdown_code_and_links():
    md = "## Result\n**Saved** your note: [ideas](https://x.example/n).\n```python\nprint(1)\n```\n| a | b |\n|---|---|"
    assert spoken_answer(md) == "Result Saved your note: ideas."
    assert spoken_answer("```\ncode only\n```") == "The answer is on screen."


def test_kestrel_never_says_its_own_name():
    assert for_speech("Kestrel saved it. kestrel's note is ready.") == "I saved it. I's note is ready."


# --- the flow, in the wake service -------------------------------------------------------------------


def test_wake_then_speech_becomes_one_request(tmp_path):
    service, voice, recogniser, _, events, requests = make(tmp_path, script=spoken_request())
    service.start()
    try:
        wait_for(lambda: requests)
    finally:
        service.stop()
    assert requests == ["What time is it in Tokyo?"]
    assert recogniser.heard and recogniser.heard[0] <= int(MAX_REQUEST_S * 32000)  # the request, not more
    assert {"type": "wake_end", "reason": "done"} in events
    assert voice.said[0].startswith("I'm here")


def test_an_empty_transcript_sends_nothing_and_says_it_didnt_catch_that(tmp_path):
    service, voice, _, _, events, requests = make(tmp_path, script=spoken_request(), recogniser=FakeRecogniser(""))
    service.start()
    try:
        wait_for(lambda: DIDNT_CATCH in voice.said)
    finally:
        service.stop()
    assert requests == []
    assert {"type": "voice_ignored", "reason": "empty"} in events


def test_silence_after_the_wake_sends_nothing(tmp_path):
    script = [QUIET] * 10 + [WAKE] + [QUIET] * 40
    service, voice, recogniser, _, events, requests = make(tmp_path, script=script)
    service.start()
    try:
        wait_for(lambda: DIDNT_CATCH in voice.said)
    finally:
        service.stop()
    assert requests == [] and recogniser.heard == []  # nothing was even transcribed
    assert {"type": "wake_end", "reason": "no_speech"} in events


def test_a_dropped_transcript_behaves_like_an_empty_one(tmp_path):
    service, voice, _, _, events, requests = make(
        tmp_path, script=spoken_request(), recogniser=FakeRecogniser("Thanks for watching!", 0.9)
    )
    service.start()
    try:
        wait_for(lambda: DIDNT_CATCH in voice.said)
    finally:
        service.stop()
    assert requests == [] and {"type": "voice_ignored", "reason": "filler"} in events


def test_kestrels_own_greeting_is_not_recorded_as_the_request(tmp_path):
    voice = FakeVoice()
    voice.speaking = True  # the greeting plays during the first chunks after the wake
    script = [QUIET] * 10 + [WAKE] + [LOUD] * 20 + [QUIET] * 40  # its voice is loud, then silence
    service, _, recogniser, _, _, requests = make(tmp_path, script=script, voice=voice)
    original_stop = voice.stop
    voice.stop = lambda: None  # the barge-in stop() at the wake mustn't end this greeting
    service.start()
    try:
        time.sleep(0.15)
        voice.speaking = False  # greeting over; only quiet follows
        wait_for(lambda: DIDNT_CATCH in voice.said)
    finally:
        voice.stop = original_stop
        service.stop()
    assert requests == [] and recogniser.heard == []


def test_barge_in_saying_kestrel_stops_the_speech(tmp_path):
    service, voice, *_ = make(tmp_path)
    voice.speaking = True  # an answer is being read out
    assert service.on_wake() is True
    assert voice.stops == 1 and not voice.speaking


def test_typing_drops_the_recording(tmp_path):
    service, _, recogniser, _, _, requests = make(tmp_path, script=[QUIET] * 10 + [WAKE] + [LOUD] * 400)
    service.start()
    try:
        wait_for(lambda: service._recording is not None and len(service._recording) > 0)
        service.end_attention()  # the console reported typing
        time.sleep(0.2)
    finally:
        service.stop()
    assert requests == [] and recogniser.heard == [] and service._recording is None


def test_no_audio_is_written_to_disk(tmp_path, monkeypatch):
    real_open = builtins.open

    def no_writes(file, mode="r", *args, **kwargs):
        if any(m in mode for m in "wax+"):
            raise AssertionError(f"talk code tried to write {file}")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_writes)
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write")))
    before = sorted(tmp_path.iterdir())
    service, _, _, _, events, requests = make(tmp_path, script=spoken_request())
    service.start()
    try:
        wait_for(lambda: requests)
    finally:
        service.stop()
    assert sorted(tmp_path.iterdir()) == before
    for event in events:  # no event carries audio: only numbers, labels and text
        assert not any(isinstance(v, (bytes, bytearray)) for v in event.values())


# --- through the web app ----------------------------------------------------------------------------------


class SpyLLM:
    supports_streaming = True
    last_provider = "spy"
    model = "spy-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.inputs: list[list[dict]] = []

    def chat(self, messages, tools=None, on_text=None):
        self.inputs.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)


def say(text):
    return {"role": "assistant", "content": text}


def app_with(tmp_path, llm, registry=None, script=(), recogniser=None, countdown=0.05):
    service, voice, recogniser, _mic, _events, _ = make(
        tmp_path, script=script, recogniser=recogniser, countdown=countdown
    )
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})

    def make_agent(on_event, approver):
        kwargs = {"tools": registry} if registry is not None else {}
        gate = ApprovalGate(approver, log_path=tmp_path / "audit.jsonl")
        return Agent(llm, on_event=on_event, tracer=tracer, gate=gate, **kwargs)

    config = WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))
    return create_app(make_agent, tracer, config, wake=service), service, voice


def until(ws, kind: str, limit: int = 400) -> dict:
    for _ in range(limit):
        event = ws.receive_json()
        if event["type"] == kind:
            return event
    raise AssertionError(f"no {kind}")


def test_spoken_request_is_sent_like_typed_text_and_the_answer_is_spoken(tmp_path):
    llm = SpyLLM(say("Kestrel checked: it is 9 pm in Tokyo. It's Friday there. Have a nice evening."))
    app, _service, voice = app_with(tmp_path, llm, script=spoken_request())
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        request = until(ws, "voice_request")
        assert request == {"type": "voice_request", "text": "What time is it in Tokyo?"}  # text only
        ws.send_json({"type": "user_message", "text": request["text"], "voice": True, "speak": True})
        until(ws, "done")
        wait_for(lambda: len(voice.said) >= 2)
    assert voice.said[-1] == f"I checked: it is 9 pm in Tokyo. It's Friday there. {REST_ON_SCREEN}"


def test_typed_messages_are_never_spoken(tmp_path):
    llm = SpyLLM(say("Typed answer."))
    app, _, voice = app_with(tmp_path, llm)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"type": "user_message", "text": "hi"})
        until(ws, "done")
    assert voice.said == []


def test_speak_answers_off_means_nothing_is_spoken(tmp_path):
    llm = SpyLLM(say("A spoken-request answer."))
    app, _, voice = app_with(tmp_path, llm)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"type": "user_message", "text": "hi", "voice": True, "speak": False})
        until(ws, "done")
    assert voice.said == []


def test_an_approval_card_is_announced_with_one_fixed_sentence_and_voice_cant_resolve_it(tmp_path):
    ran: list[str] = []
    registry = ToolRegistry()

    @registry.register(risk="confirm", preview=lambda a: f"+{a['text']}")
    def save(text: str) -> str:
        """Save text."""
        ran.append(text)
        return "saved"

    call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "save", "arguments": '{"text": "PIN 4321"}'}}
        ],
    }
    llm = SpyLLM(call, say("Saved."))
    app, _service, voice = app_with(tmp_path, llm, registry=registry)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"type": "user_message", "text": "save my pin", "voice": True, "speak": True})
        card = until(ws, "approval_required")
        wait_for(lambda: APPROVAL_SENTENCE in voice.said)
        assert voice.said == [APPROVAL_SENTENCE]  # nothing about the card: not the tool, not "PIN 4321"
        # a second spoken request saying "approve" is just chat text, and Kestrel is still busy
        ws.send_json({"type": "user_message", "text": "approve it", "voice": True, "speak": True})
        assert until(ws, "error")["message"].startswith("Kestrel is still working")
        time.sleep(0.2)
        assert ran == []  # still waiting for a click
        ws.send_json({"type": "approval_response", "approval_id": card["approval_id"], "decision": "approve"})
        until(ws, "done")
    assert ran == ["PIN 4321"]


def test_the_model_sees_the_same_input_typed_or_spoken(tmp_path):
    llm = SpyLLM(say("ok"), say("ok"))
    app, _, _ = app_with(tmp_path, llm)
    with TestClient(app, base_url=BASE) as client:
        for voice in (False, True):
            with client.websocket_connect(f"/ws?token={TOKEN}") as ws:  # a fresh conversation each time
                message = {"type": "user_message", "text": "What time is it in Tokyo?"}
                if voice:
                    message |= {"voice": True, "speak": True}
                ws.send_json(message)
                until(ws, "done")
    typed, spoken = llm.inputs
    assert typed == spoken
    assert "voice" not in json.dumps(spoken).lower() and "spoken" not in json.dumps(spoken).lower()


def test_a_spoken_request_goes_to_the_most_recently_active_console_only(tmp_path):
    app, service, _ = app_with(tmp_path, SpyLLM())
    with TestClient(app, base_url=BASE) as client:
        with (
            client.websocket_connect(f"/ws?token={TOKEN}") as first,
            client.websocket_connect(f"/ws?token={TOKEN}") as second,
        ):
            first.receive_json(), second.receive_json()  # each one's opening status
            first.send_json({"type": "wake_dismiss"})  # the first console is now the active one
            time.sleep(0.1)
            service.on_request("List my files.")
            assert until(first, "voice_request")["text"] == "List my files."
            service.set_enabled(False)  # a broadcast both consoles get
            event = second.receive_json()
            while event["type"] == "wake_level":
                event = second.receive_json()
            assert event["type"] == "wake_status"  # the second console never got the request


def test_with_no_console_open_kestrel_says_so(tmp_path):
    app, service, voice = app_with(tmp_path, SpyLLM())
    with TestClient(app, base_url=BASE):
        service.on_request("List my files.")
    assert voice.said == ["Open the console first."]


# --- the recogniser and the normaliser -----------------------------------------------------------------


def test_the_recogniser_only_opens_a_local_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="get_talk_model"):
        WhisperCppTranscriber(tmp_path / "missing.bin")
    with pytest.raises(ValueError, match="unknown talk model"):
        talk_model_file("large-v3")
    assert talk_model_file("tiny.en-q5_1", tmp_path).name == "ggml-tiny.en-q5_1.bin"


def test_talk_settings(monkeypatch):
    for name in ("KESTREL_TALK", "KESTREL_TALK_MODEL", "KESTREL_TALK_MIN_CONFIDENCE"):
        monkeypatch.delenv(name, raising=False)
    s = WakeSettings.from_env()
    assert (s.talk, s.talk_model, s.min_confidence) == (True, "base.en-q5_1", 0.50)  # owner, 2026-10-24
    monkeypatch.setenv("KESTREL_TALK_MODEL", "tiny.en-q5_1")
    assert WakeSettings.from_env().talk_model == "tiny.en-q5_1"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("What's 17.5% of 2,340?", "what is seventeen and a half percent of two thousand three hundred forty"),
        ("Write to Sam: I'll be 10 minutes late.", "write to sam i will be ten minutes late"),
        ("It costs $5, OK?", "it costs five dollars okay"),
        ("a hundred and twenty-one", "121"),
        ("[BLANK_AUDIO] three point one four", "3.14"),
    ],
)
def test_the_normaliser_makes_equivalent_transcripts_equal(a, b):
    assert normalize(a) == normalize(b)


def test_raw_and_normalised_word_errors():
    ref, hyp = "what is seventeen percent", "What's 17%?"
    assert word_errors(raw_words(ref), raw_words(hyp)) == 4  # raw: every word differs
    assert word_errors(normalize(ref).split(), normalize(hyp).split()) == 0


# --- "check first" and the vocabulary hint -----------------------------------------------------------------


def test_low_confidence_goes_to_check_not_to_the_agent(tmp_path):
    service, voice, _, _, _events, requests = make(
        tmp_path, script=spoken_request(), recogniser=FakeRecogniser("List the files in my workspace.", 0.2)
    )
    service.start()
    try:
        wait_for(lambda: CHECK_ON_SCREEN in voice.said)
    finally:
        service.stop()
    assert requests == []  # nothing sent
    assert service.checked == ["List the files in my workspace."]  # type: ignore[attr-defined]
    assert DIDNT_CATCH not in voice.said


def test_a_check_goes_to_the_most_recent_console_and_the_agent_gets_nothing(tmp_path):
    llm = SpyLLM()  # no replies: any model call would fail the test
    app, service, _voice = app_with(tmp_path, llm)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.receive_json()
        service.on_check("Email Priya the notes.")
        assert until(ws, "voice_check") == {"type": "voice_check", "text": "Email Priya the notes."}
        time.sleep(0.1)
    assert llm.inputs == []


def test_the_vocabulary_prompt_is_short_and_built_from_kestrel_words_and_the_name_list(tmp_path):
    base = talk_prompt()
    assert base.startswith("Kestrel: ") and all(word in base for word in VOCAB_WORDS)
    assert len(base) < MAX_PROMPT_CHARS
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("# contacts\nPriya\n\nSam  # work\nPriya\n" + "x" * 50 + "\n", encoding="utf-8")
    assert read_vocab(vocab) == ["Priya", "Sam"]  # comments, blanks, duplicates and long lines left out
    assert read_vocab(tmp_path / "missing.txt") == []
    with_names = talk_prompt(read_vocab(vocab))
    assert with_names.endswith("Names: Priya, Sam.")
    many = talk_prompt([f"Person{i}" for i in range(100)])
    assert len(many) <= MAX_PROMPT_CHARS and "Person0" in many and "Person99" not in many


def test_the_vocabulary_file_comes_from_the_setting(tmp_path, monkeypatch):
    vocab = tmp_path / "names.txt"
    vocab.write_text("Priya\n", encoding="utf-8")
    monkeypatch.setenv("KESTREL_TALK_VOCAB_FILE", str(vocab))
    monkeypatch.setenv("KESTREL_TALK_HINT", "on")
    assert WakeSettings.from_env().talk_prompt.endswith("Names: Priya.")


def test_the_hint_is_passed_to_whisper_cpp_and_can_be_turned_off():
    calls = []

    class FakeModel:
        def transcribe(self, audio, **params):
            calls.append(params)
            return []

    stt = object.__new__(WhisperCppTranscriber)
    stt.model, stt.prompt = FakeModel(), talk_prompt()
    stt.transcribe(b"\x00\x10" * 16000)
    stt.prompt = None
    stt.transcribe(b"\x00\x10" * 16000)
    # always passed explicitly ("" = none): pywhispercpp keeps parameters from one call to the next
    assert calls[0]["initial_prompt"] == talk_prompt() and calls[1]["initial_prompt"] == ""


def test_the_vocabulary_hint_is_off_by_default_and_can_be_switched_on(monkeypatch):
    """Owner decision 2026-10-24 (known issue #49): pooled over both recorded sets the hint was worse."""
    monkeypatch.delenv("KESTREL_TALK_HINT", raising=False)
    assert WakeSettings.from_env().talk_prompt == ""
    assert WakeSettings().talk_prompt == ""
    monkeypatch.setenv("KESTREL_TALK_HINT", "on")
    assert WakeSettings.from_env().talk_prompt.startswith("Kestrel: ")
    monkeypatch.setenv("KESTREL_TALK_HINT", "off")
    assert WakeSettings.from_env().talk_prompt == ""
    assert guard("todo list, workspace, file", 0.9, 2.0, 0.45, "").action == "send"  # no hint: no echo rule


# --- the send countdown ---------------------------------------------------------------------------------


class FakeCancel:
    """Hears "cancel" on the chunk b"cancel"; records every chunk it was fed."""

    def __init__(self):
        self.fed: list[bytes] = []

    def feed(self, pcm: bytes) -> float | None:
        self.fed.append(pcm)
        return 1.0 if pcm == b"cancel" else None


def test_the_countdown_shows_sending_then_sends(tmp_path):
    llm = SpyLLM(say("It is 9 pm."))
    app, _service, _ = app_with(tmp_path, llm, script=spoken_request(), countdown=0.5)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        pending = until(ws, "voice_pending")
        shown = time.monotonic()
        assert (pending["text"], pending["seconds"]) == ("What time is it in Tokyo?", 0.5)
        assert until(ws, "voice_request") == {"type": "voice_request", "text": "What time is it in Tokyo?"}
        assert time.monotonic() - shown >= 0.4  # not before the countdown ran out
    assert llm.inputs == []  # the console sends it (like typed text); nothing went to the model on its own


@pytest.mark.parametrize("by", ["esc", "button"])
def test_esc_or_the_cancel_button_stops_the_countdown_and_returns_the_text(tmp_path, by):
    llm = SpyLLM()  # no replies: any model call would fail the test
    app, service, _ = app_with(tmp_path, llm, script=spoken_request(), countdown=1.0)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        pending = until(ws, "voice_pending")
        ws.send_json({"type": "voice_cancel", "id": pending["id"], "by": by})
        cancelled = until(ws, "voice_cancelled")
        assert cancelled == {
            "type": "voice_cancelled",
            "id": pending["id"],
            "text": "What time is it in Tokyo?",
            "by": by,
        }
        time.sleep(1.2)  # past the end of the countdown
        assert service.pending() is None
        assert service.commit(pending["id"]) is False  # the timer finds nothing to send
    assert llm.inputs == []


def test_saying_cancel_stops_the_countdown_and_listens_only_during_it(tmp_path):
    cancel = FakeCancel()
    made: list[int] = []

    def make_cancel():
        made.append(1)
        return cancel

    script = spoken_request() + [QUIET] * 3 + [b"cancel"]
    service, _, _, _, _, requests = make(tmp_path, script=script, countdown=5.0, cancel=make_cancel)
    cancelled: list[tuple[str, str]] = []
    service.on_cancelled = lambda request_id, text, by: cancelled.append((text, by))
    service.start()
    try:
        wait_for(lambda: cancelled)
    finally:
        service.stop()
    assert cancelled == [("What time is it in Tokyo?", "voice")]
    assert requests == [] and service.pending() is None
    assert made == [1]  # one recognizer, made when the countdown started
    assert WAKE not in cancel.fed and LOUD not in cancel.fed  # it never heard the wake or the request


def test_with_no_console_the_countdown_never_starts(tmp_path):
    app, service, voice = app_with(tmp_path, SpyLLM())
    with TestClient(app, base_url=BASE):
        service._hold_request("List my files.")
        assert service.pending() is None
    assert voice.said == ["Open the console first."]


def test_cancel_can_never_answer_an_approval_card(tmp_path):
    ran: list[str] = []
    registry = ToolRegistry()

    @registry.register(risk="confirm", preview=lambda a: f"+{a['text']}")
    def save(text: str) -> str:
        """Save text."""
        ran.append(text)
        return "saved"

    call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "save", "arguments": '{"text": "x"}'}}],
    }
    llm = SpyLLM(call, say("Saved."))
    app, service, _ = app_with(tmp_path, llm, registry=registry)
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"type": "user_message", "text": "save x", "voice": True, "speak": True})
        card = until(ws, "approval_required")
        # the card's own id sent as a voice cancel, and a spoken "cancel": neither answers the card
        ws.send_json({"type": "voice_cancel", "id": card["approval_id"], "by": "esc"})
        assert service.cancel(card["approval_id"], "voice") is False
        time.sleep(0.3)
        assert ran == []
        ws.send_json({"type": "approval_response", "approval_id": card["approval_id"], "decision": "approve"})
        resolved = until(ws, "approval_resolved")
        assert resolved["decision"] == "approved"  # resolved by the click, the only way
        until(ws, "done")
    assert ran == ["x"]
