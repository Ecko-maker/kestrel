"""Voice wake-up (docs/voice-wake.md): greeting, grammar, the wake flow with fakes, privacy rules,
keep-awake release, the exact mouse nudge, /api/wake auth, and wake records kept out of traces,
stats and export. No microphone, model or Windows API is used: everything real is replaced by a fake."""

import ast
import builtins
import ctypes
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel import trace_report
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate
from kestrel.tools import ToolRegistry
from kestrel.tracing import WAKE_ATTRIBUTES, Tracer
from kestrel.wake import WakeService, WakeSettings, build_wake_service
from kestrel.wake import service as service_module
from kestrel.wake.detector import GRAMMAR, VoskDetector, grammar_json, wake_confidence
from kestrel.wake.greeting import MAX_NAME_CHARS, clean_name, greeting
from kestrel.wake.power import (
    INPUT,
    INPUT_MOUSE,
    KEEP_AWAKE_REASON,
    MOUSEEVENTF_MOVE,
    POWER_REQUEST_SYSTEM_REQUIRED,
    WindowsPower,
)
from kestrel.web.app import COOKIE, WebConfig, create_app

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"
WAKE_DIR = Path(__file__).resolve().parents[1] / "src" / "kestrel" / "wake"


def wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


# --- fakes ------------------------------------------------------------------------------


class FakeRecognizer:
    """Behaves like Vosk with a grammar: words in the grammar come out as themselves; anything
    else comes out as "[unk]" if the grammar has it, and is otherwise forced onto the nearest
    grammar word (here: the first one), which is why the grammar must include "[unk]"."""

    def __init__(self, grammar: str):
        self.grammar = json.loads(grammar)
        self.pending: bytes = b""

    def AcceptWaveform(self, data: bytes) -> bool:
        self.pending = data
        return True  # every chunk is one utterance here

    def Result(self) -> str:
        words = []
        for token in self.pending.decode().lower().split():
            word = token.strip(".,!?")
            if word in self.grammar:
                words.append(word)
            else:
                words.append("[unk]" if "[unk]" in self.grammar else self.grammar[0])
        return json.dumps({"result": [{"word": w, "conf": 1.0} for w in words], "text": " ".join(words)})


class FakeDetector:
    def __init__(self):
        self.fed: list[bytes] = []

    def feed(self, pcm: bytes) -> float | None:
        self.fed.append(pcm)
        return 1.0 if pcm == b"kestrel" else None


class FakeSource:
    """Scripted chunks; records open/close in a shared log so their order against status events shows."""

    def __init__(self, log: list, chunks=()):
        self.log = log
        self.chunks = list(chunks)

    def open(self):
        self.log.append("open")

    def read(self, timeout: float):
        if self.chunks:
            return self.chunks.pop(0)
        time.sleep(0.01)
        return None

    def close(self):
        self.log.append("close")


class FakePower:
    def __init__(self, log: list, battery: bool = False):
        self.log = log
        self.battery = battery
        self.awake = False
        self.refuse = False  # Windows refused the power request

    def on_battery(self) -> bool:
        return self.battery

    def keep_awake(self, on: bool) -> bool:
        if on != self.awake:
            self.log.append(f"keep_awake:{on}")
        self.awake = on and not self.refuse
        return self.awake

    def wake_display(self):
        self.log.append("wake_display")

    def bring_to_front(self, title):
        self.log.append(f"front:{title}")
        return True


class FakeSpeaker:
    def __init__(self):
        self.said: list[str] = []
        self.closed = False

    def say(self, text):
        self.said.append(text)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fast_checks(monkeypatch):
    monkeypatch.setattr(service_module, "BATTERY_CHECK_S", 0.05)
    monkeypatch.setattr(service_module, "RETRY_S", 0.05)


def make_service(
    tmp_path, *, chunks=(), battery=False, enabled=True, on_battery=False, name=None, clock=time.monotonic
):
    log: list = []
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    settings = WakeSettings(enabled=enabled, on_battery=on_battery, user_name=name, model_dir=tmp_path)
    detector = FakeDetector()
    sources: list[FakeSource] = []

    def make_source():
        sources.append(FakeSource(log, chunks))
        return sources[-1]

    speaker = FakeSpeaker()
    power = FakePower(log, battery)
    service = WakeService(settings, lambda: detector, make_source, power, speaker, tracer.record_wake, clock=clock)
    events: list[dict] = []

    def emit(event):
        events.append(event)
        if event["type"] == "wake_status":
            log.append(f"status:listening={event['listening']}")

    service.emit = emit
    return service, log, events, tracer, power, speaker, sources


# --- greeting -------------------------------------------------------------------------------


def test_greeting_with_and_without_a_name():
    assert greeting("Sam") == "I'm here, Sam. What do you need?"
    assert greeting(None) == "I'm here. What do you need?"
    assert greeting("   ") == "I'm here. What do you need?"


def test_the_name_is_cleaned_before_it_is_spoken_or_shown():
    assert clean_name("  Ada \n Lovelace\t") == "Ada Lovelace"
    assert clean_name("\x00\x1b[31m") == "[31m"  # control characters dropped
    assert len(clean_name("x" * 200) or "") == MAX_NAME_CHARS


def test_the_greeting_comes_from_the_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("KESTREL_USER_NAME", "Robin")
    assert WakeSettings.from_env().user_name == "Robin"
    service, *_ = make_service(tmp_path, name="Robin")
    assert service.greeting == "I'm here, Robin. What do you need?"


# --- detection ------------------------------------------------------------------------------


def test_the_grammar_includes_unk_next_to_the_wake_word():
    assert GRAMMAR == ("kestrel", "[unk]")
    assert json.loads(grammar_json()) == ["kestrel", "[unk]"]


def test_a_non_matching_phrase_gives_no_wake_with_the_real_grammar():
    detector = VoskDetector(FakeRecognizer, threshold=0.5)
    assert detector.feed(b"let's play chess tonight") is None
    assert detector.feed(b"the orchestra played") is None
    assert detector.feed(b"Kestrel, are you there?") == 1.0


def test_without_unk_other_speech_would_be_forced_onto_the_wake_word():
    """Why "[unk]" matters: the same recognizer with a kestrel-only grammar wakes on anything."""
    forced = FakeRecognizer(json.dumps(["kestrel"]))
    forced.AcceptWaveform(b"let's play chess")
    assert wake_confidence(forced.Result()) == 1.0


def test_the_threshold_is_applied_to_the_wake_words_confidence():
    result = json.dumps({"result": [{"word": "[unk]", "conf": 1.0}, {"word": "kestrel", "conf": 0.42}]})
    assert wake_confidence(result) == 0.42
    assert wake_confidence(json.dumps({"result": [{"word": "[unk]", "conf": 1.0}]})) is None
    assert wake_confidence("") is None


def test_the_model_is_only_loaded_from_a_local_folder(tmp_path):
    from kestrel.wake.detector import vosk_recognizer_factory

    with pytest.raises(FileNotFoundError, match="get_wake_model"):
        vosk_recognizer_factory(tmp_path / "missing")


# --- off by default ---------------------------------------------------------------------------


def test_off_by_default(monkeypatch, tmp_path):
    for name in ("KESTREL_WAKE", "KESTREL_WAKE_ON_BATTERY", "KESTREL_USER_NAME"):
        monkeypatch.delenv(name, raising=False)
    settings = WakeSettings.from_env()
    assert not settings.enabled and not settings.on_battery and settings.user_name is None
    assert build_wake_service(lambda at: None) is None
    service, log, events, *_ = make_service(tmp_path, enabled=False)
    service.start()
    assert service._thread is None and log == [] and events == []  # the microphone was never opened
    with pytest.raises(RuntimeError, match="KESTREL_WAKE=on"):
        service.set_enabled(True)


# --- the flow with a fake detector -------------------------------------------------------------


def test_wake_flow_end_to_end(tmp_path):
    service, log, events, _, _, speaker, _ = make_service(tmp_path, chunks=[b"noise", b"kestrel"], name="Sam")
    service.start()
    try:
        wait_for(lambda: any(e["type"] == "wake" for e in events))
    finally:
        service.stop()
    wake = next(e for e in events if e["type"] == "wake")
    assert wake["greeting"] == "I'm here, Sam. What do you need?"
    assert speaker.said == ["I'm here, Sam. What do you need?"]
    assert log.index("wake_display") < log.index("front:Kestrel")
    # the listening indicator: on before the microphone opens, off after it closes
    assert log.index("status:listening=True") < log.index("open")
    assert log.index("close") < max(i for i, x in enumerate(log) if x == "status:listening=False")
    assert speaker.closed


def test_cooldown_no_second_wake_within_10_seconds(tmp_path):
    now = [100.0]
    service, _, events, *_ = make_service(tmp_path, clock=lambda: now[0])
    assert service.on_wake() is True
    now[0] += 9.9
    assert service.on_wake() is False
    now[0] += 0.2
    assert service.on_wake() is True
    assert [e["type"] for e in events] == ["wake", "wake"]


def test_on_battery_listening_pauses_and_nothing_is_held(tmp_path):
    service, log, _events, *_ = make_service(tmp_path, battery=True)
    service.start()
    try:
        wait_for(lambda: service.status()["paused"] == "on battery")
    finally:
        service.stop()
    assert "open" not in log and "keep_awake:True" not in log


def test_kestrel_wake_on_battery_listens_without_holding_the_pc_awake(tmp_path):
    service, log, *_ = make_service(tmp_path, battery=True, on_battery=True)
    service.start()
    try:
        wait_for(lambda: "open" in log)
        assert service.status()["listening"] and not service.status()["keeping_awake"]
    finally:
        service.stop()
    assert "keep_awake:True" not in log


def test_keep_awake_is_held_only_while_listening_and_released_when_turned_off(tmp_path):
    service, log, _events, _tracer, power, *_ = make_service(tmp_path)
    service.start()
    try:
        wait_for(lambda: power.awake)
        assert service.status()["keeping_awake"] is True
        service.set_enabled(False)
        wait_for(lambda: not power.awake and "close" in log)
        assert service.status() | {"type": None} == {
            "type": None,
            "available": True,
            "enabled": False,
            "listening": False,
            "keeping_awake": False,
            "paused": "off",
        }
        service.set_enabled(True)
        wait_for(lambda: power.awake)
    finally:
        service.stop()
    assert not power.awake
    assert log.count("keep_awake:True") == 2 and log.count("keep_awake:False") == 2


def test_keeping_awake_is_shown_only_when_windows_granted_it(tmp_path):
    service, log, _, _, power, *_ = make_service(tmp_path)
    power.refuse = True
    service.start()
    try:
        wait_for(lambda: "open" in log)
        time.sleep(0.05)
        assert service.status()["listening"] is True and service.status()["keeping_awake"] is False
    finally:
        service.stop()


def test_keep_awake_is_released_when_the_server_stops(tmp_path):
    service, log, _, tracer, power, *_ = make_service(tmp_path)
    app = create_app(lambda e, a: None, tracer, WebConfig(token=TOKEN, static_dir=None), wake=service)  # type: ignore[arg-type,return-value]
    with TestClient(app, base_url=BASE):
        wait_for(lambda: power.awake)
    assert not power.awake  # the server's shutdown cleared the request
    assert log[-2:] == ["close", "keep_awake:False"]  # microphone closed, then the request released
    assert service.status()["listening"] is False and service._thread is None


# --- privacy ------------------------------------------------------------------------------


def test_audio_is_never_written_to_disk(tmp_path, monkeypatch):
    real_open = builtins.open

    def no_writes(file, mode="r", *args, **kwargs):
        if any(m in mode for m in "wax+"):
            raise AssertionError(f"wake code tried to write {file}")
        return real_open(file, mode, *args, **kwargs)

    service, _, events, *_ = make_service(tmp_path, chunks=[b"secret audio", b"kestrel", b"more audio"])
    before = sorted(p.name for p in tmp_path.iterdir())
    monkeypatch.setattr(builtins, "open", no_writes)
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write")))
    service.start()
    try:
        wait_for(lambda: any(e["type"] == "wake" for e in events))
    finally:
        service.stop()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert set(after) - set(before) <= {"traces.db"}  # the wake's time, nothing else
    assert not any(b"secret audio" in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())


def test_a_wake_records_only_its_time(tmp_path):
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    tracer.record_wake(1234.5)
    with tracer.connect() as conn:
        spans = [dict(r) for r in conn.execute("SELECT * FROM spans")]
        traces = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    assert len(spans) == 1 and traces == 0
    span = spans[0]
    assert span["name"] == "wake" and span["start_time"] == 1234.5 and span["parent_id"] is None
    assert json.loads(span["attributes"]) == WAKE_ATTRIBUTES == {"kestrel.wake": "detected"}


NETWORK_MODULES = {"socket", "http", "urllib", "requests", "httpx", "websockets", "openai", "ddgs", "ssl", "ftplib"}
AGENT_MODULES = {"kestrel.agent", "kestrel.llm", "kestrel.tools", "kestrel.approval", "kestrel.mcp_client"}


def test_wake_code_imports_no_network_agent_model_or_tool_module():
    for path in sorted(WAKE_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in NETWORK_MODULES, f"{path.name} imports {name}"
                assert not any(name == m or name.startswith(m + ".") for m in AGENT_MODULES), f"{path.name}: {name}"


# --- wake records stay out of traces, stats and export ------------------------------------------


def test_wake_records_are_hidden_from_traces_stats_and_export(tmp_path):
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    tracer.record_wake(time.time())
    with tracer.connect() as conn:
        assert trace_report.list_traces(conn) == []
        assert trace_report.compute_stats(conn) == {"traces": 0}
        assert trace_report.timeseries(conn) == []
        written, skipped = trace_report.export(conn, "all", tmp_path / "out.jsonl", tools=[])
    assert (written, skipped) == (0, 0) and (tmp_path / "out.jsonl").read_text() == ""


def test_wake_records_never_reach_the_table_the_distillation_dataset_reads(tmp_path):
    """`kestrel distill build` (phase4/trace-dataset, dataset.read_traces) reads every row of the
    `traces` table and only `llm_call` / `approval` spans. A wake adds neither, so it can't become
    an example. Checked here on the database itself; docs/voice-wake.md records the check against
    the distill code (run on the phase 4 branch's read_traces)."""
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    tracer.record_wake(time.time())
    with tracer.connect() as conn:
        assert conn.execute("SELECT * FROM traces").fetchall() == []
        names = {r[0] for r in conn.execute("SELECT name FROM spans")}
    assert names == {"wake"} and not names & {"llm_call", "approval", "agent_run"}


# --- the console: events, no model call, no tool call, approvals stay clicks ----------------------


class SpyLLM:
    supports_streaming = True
    last_provider = "spy"
    model = "spy-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, tools=None, on_text=None):
        self.calls += 1
        return self.replies.pop(0)


def test_a_wake_makes_no_model_call_runs_no_tool_and_leaves_approvals_to_clicks(tmp_path):
    ran: list[str] = []
    registry = ToolRegistry()

    @registry.register(risk="confirm", preview=lambda a: f"+{a['text']}")
    def save(text: str) -> str:
        """Save text."""
        ran.append(text)
        return "saved"

    tool_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "save", "arguments": '{"text": "x"}'}}],
    }
    llm = SpyLLM(tool_call, {"role": "assistant", "content": "done"})
    service, _log, _, tracer, *_ = make_service(tmp_path)

    def make_agent(on_event, approver):
        gate = ApprovalGate(approver, log_path=tmp_path / "audit.jsonl")
        return Agent(llm, tools=registry, on_event=on_event, tracer=tracer, gate=gate)

    config = WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))
    with TestClient(create_app(make_agent, tracer, config, wake=service), base_url=BASE) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            assert ws.receive_json()["type"] == "ready"
            # a wake with no conversation at all
            service.on_wake()
            for _ in range(20):  # status updates from the listening thread may come first
                if ws.receive_json()["type"] == "wake":
                    break
            else:
                raise AssertionError("no wake event")
            assert llm.calls == 0 and ran == []
            # now an approval is pending; a wake must not answer it
            ws.send_json({"type": "user_message", "text": "save x"})
            while (event := ws.receive_json())["type"] != "approval_required":
                pass
            approval_id = event["approval_id"]
            service._last_wake = None  # outside the cooldown
            service.on_wake()
            while (event := ws.receive_json())["type"] != "wake":
                assert event["type"] != "approval_resolved"
            time.sleep(0.2)
            assert ran == [] and llm.calls == 1  # still waiting for a click
            ws.send_json({"type": "approval_response", "approval_id": approval_id, "decision": "approve"})
            while (event := ws.receive_json())["type"] != "done":
                pass
    assert ran == ["x"] and llm.calls == 2  # only the user's message and the click caused these


# --- /api/wake ------------------------------------------------------------------------------------


@pytest.fixture
def wake_client(tmp_path):
    service, _log, _, tracer, power, *_ = make_service(tmp_path)
    config = WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))
    with TestClient(create_app(lambda e, a: None, tracer, config, wake=service), base_url=BASE) as client:  # type: ignore[arg-type,return-value]
        yield client, service, power


def test_api_wake_needs_the_cookie(wake_client):
    client, *_ = wake_client
    assert client.get("/api/wake").status_code == 401
    assert client.post("/api/wake", json={"enabled": False}).status_code == 401


def test_api_wake_refuses_a_foreign_origin(wake_client):
    client, service, _ = wake_client
    client.cookies.set(COOKIE, TOKEN)
    response = client.post("/api/wake", json={"enabled": False}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert service.status()["enabled"] is True  # unchanged
    assert client.get("/api/wake", headers={"Origin": "https://evil.example"}).status_code == 403


def test_api_wake_refuses_a_foreign_host(wake_client):
    client, *_ = wake_client
    client.cookies.set(COOKIE, TOKEN)
    assert client.get("/api/wake", headers={"Host": "evil.example"}).status_code == 400


def test_api_wake_status_and_toggle_from_the_console(wake_client):
    client, _service, power = wake_client
    client.cookies.set(COOKIE, TOKEN)
    assert client.get("/api/wake").json()["available"] is True
    off = client.post("/api/wake", json={"enabled": False}, headers={"Origin": BASE})
    assert off.status_code == 200 and off.json()["enabled"] is False
    wait_for(lambda: not power.awake)


def test_api_wake_when_the_wake_word_is_off(tmp_path):
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    client = TestClient(create_app(lambda e, a: None, tracer, WebConfig(token=TOKEN, static_dir=None)), base_url=BASE)  # type: ignore[arg-type,return-value]
    client.cookies.set(COOKIE, TOKEN)
    assert client.get("/api/wake").json() == {"type": "wake_status", "available": False}
    assert client.post("/api/wake", json={"enabled": True}).status_code == 409


# --- Windows calls, with fake DLLs --------------------------------------------------------------


class FakeDLL:
    def __init__(self, results=None):
        self.calls: list[tuple[str, tuple]] = []
        self.results = results or {}

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return self.results.get(name, 1)

        return call


def test_the_nudge_is_one_zero_distance_mouse_move_and_nothing_else():
    user32, kernel32 = FakeDLL(), FakeDLL()
    WindowsPower(user32, kernel32).wake_display()
    assert [name for name, _ in user32.calls] == ["SendInput"]  # no click, key, mouse_event or keybd_event
    count, pointer, size = user32.calls[0][1]
    sent = pointer._obj  # the INPUT behind ctypes.byref
    assert count == 1 and size == ctypes.sizeof(INPUT)
    assert sent.type == INPUT_MOUSE
    assert (sent.mi.dx, sent.mi.dy, sent.mi.mouseData, sent.mi.dwFlags) == (0, 0, 0, MOUSEEVENTF_MOVE)
    assert kernel32.calls == [("SetThreadExecutionState", (0x2,))]  # ES_DISPLAY_REQUIRED, not ES_CONTINUOUS


def test_input_struct_matches_the_windows_layout():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(INPUT) == 40  # what SendInput checks cbSize against on 64-bit Windows


def test_keep_awake_request_is_created_once_and_cleared():
    kernel32 = FakeDLL({"PowerCreateRequest": 4242})
    power = WindowsPower(FakeDLL(), kernel32)
    assert power.keep_awake(True) is True
    assert power.keep_awake(True) is True  # already held: no second request
    assert power.keep_awake(False) is False
    assert power.keep_awake(False) is False  # already released: nothing to do
    names = [name for name, _ in kernel32.calls]
    assert names == ["PowerCreateRequest", "PowerSetRequest", "PowerClearRequest", "CloseHandle"]
    reason = kernel32.calls[0][1][0]._obj
    assert reason.SimpleReasonString == KEEP_AWAKE_REASON
    assert kernel32.calls[1][1] == (4242, POWER_REQUEST_SYSTEM_REQUIRED)
    assert kernel32.calls[2][1] == (4242, POWER_REQUEST_SYSTEM_REQUIRED)
    assert kernel32.calls[3][1] == (4242,)


def test_a_refused_keep_awake_request_is_closed_and_not_reported():
    kernel32 = FakeDLL({"PowerCreateRequest": 4242, "PowerSetRequest": 0})
    assert WindowsPower(FakeDLL(), kernel32).keep_awake(True) is False
    assert [name for name, _ in kernel32.calls] == ["PowerCreateRequest", "PowerSetRequest", "CloseHandle"]


def test_bring_to_front_finds_the_console_window_by_title():
    user32 = FakeDLL({"FindWindowW": 7, "IsIconic": 1})
    assert WindowsPower(user32, FakeDLL()).bring_to_front("Kestrel") is True
    assert user32.calls == [
        ("FindWindowW", ("Chrome_WidgetWin_1", "Kestrel")),
        ("IsIconic", (7,)),
        ("ShowWindow", (7, 9)),
        ("SetForegroundWindow", (7,)),
    ]
    assert WindowsPower(FakeDLL({"FindWindowW": 0}), FakeDLL()).bring_to_front("Kestrel") is False


def test_listening_thread_is_the_only_thread_left_running_while_on(tmp_path):
    service, *_ = make_service(tmp_path)
    service.start()
    try:
        assert any(t.name == "kestrel-wake" for t in threading.enumerate())
    finally:
        service.stop()
    assert not any(t.name == "kestrel-wake" for t in threading.enumerate())
