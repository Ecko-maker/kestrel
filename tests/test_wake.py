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
from kestrel.wake.detector import (
    GRAMMAR,
    GREETINGS,
    KESTREL_ONLY,
    VoskDetector,
    grammar_json,
    heard_greeting,
    wake_confidence,
)
from kestrel.wake.greeting import MAX_NAME_CHARS, clean_name, greeting
from kestrel.wake.power import (
    INPUT,
    INPUT_MOUSE,
    KEEP_AWAKE_REASON,
    MAINS,
    MOUSEEVENTF_MOVE,
    POWER_REQUEST_SYSTEM_REQUIRED,
    SYSTEM_POWER_STATUS,
    PowerState,
    WindowsPower,
    decide,
    read_power_state,
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


def battery(percent: int, saver: bool = False) -> PowerState:
    return PowerState(on_ac=False, percent=percent, saver=saver)


class FakePower:
    """A power source the test changes while the service runs (`current`)."""

    def __init__(self, log: list, current: PowerState = MAINS):
        self.log = log
        self.current = current
        self.reads = 0
        self.awake = False
        self.refuse = False  # Windows refused the power request

    def state(self) -> PowerState:
        self.reads += 1
        return self.current

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
    """Records what would be said; `speaking` can be set by a test (barge-in, the greeting)."""

    def __init__(self):
        self.said: list[str] = []
        self.stops = 0
        self.speaking = False
        self.closed = False

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stops += 1
        self.speaking = False

    def busy(self):
        return self.speaking

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fast_checks(monkeypatch):
    monkeypatch.setattr(service_module, "POWER_CHECK_S", 0.05)
    monkeypatch.setattr(service_module, "RETRY_S", 0.05)


def make_service(
    tmp_path,
    *,
    chunks=(),
    power_state=MAINS,
    enabled=True,
    on_battery=True,
    name=None,
    clock=time.monotonic,
    wall=time.time,
    listen_seconds=8.0,
    source=None,
):
    log: list = []
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    settings = WakeSettings(
        enabled=enabled, on_battery=on_battery, user_name=name, model_dir=tmp_path, listen_seconds=listen_seconds
    )
    detector = FakeDetector()
    sources: list[FakeSource] = []

    def make_source():
        sources.append(source(log) if source else FakeSource(log, chunks))
        return sources[-1]

    speaker = FakeSpeaker()
    power = FakePower(log, power_state)
    service = WakeService(
        settings, lambda: detector, make_source, power, speaker, tracer.record_wake, clock=clock, wall=wall
    )
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
    # 2026-10-26: greetings added (all five checked in the model's vocabulary, "shalom" included)
    assert GRAMMAR == ("kestrel", "hi", "hello", "hey", "shalom", "good morning", "[unk]")
    assert json.loads(grammar_json()) == list(GRAMMAR)
    assert "[unk]" in GRAMMAR and KESTREL_ONLY == ("kestrel", "[unk]")
    assert json.loads(grammar_json("cancel")) == ["cancel", "[unk]"]  # the spoken cancel keeps its own


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


def test_the_cancel_detector_uses_its_own_grammar_and_acts_on_partial_results():
    class PartialRecognizer:
        def __init__(self, grammar: str):
            self.grammar = json.loads(grammar)
            self.data = b""

        def AcceptWaveform(self, data: bytes) -> bool:
            self.data = data
            return False  # the utterance is still open

        def Result(self) -> str:
            return "{}"

        def FinalResult(self) -> str:
            return "{}"

        def PartialResult(self) -> str:
            word = self.data.decode()
            return json.dumps({"partial": word if word in self.grammar else "[unk]"})

    cancel = VoskDetector(PartialRecognizer, 0.0, word="cancel", partial=True)
    assert cancel.recognizer.grammar == ["cancel", "[unk]"]  # type: ignore[attr-defined]
    assert cancel.feed(b"cancel") == 1.0
    assert cancel.feed(b"cancer") is None
    wake = VoskDetector(PartialRecognizer, 0.5)  # the wake word never acts on a partial result
    assert wake.feed(b"kestrel") is None


def test_the_wake_threshold_default_is_0_90(monkeypatch):
    monkeypatch.delenv("KESTREL_WAKE_THRESHOLD", raising=False)
    assert WakeSettings.from_env().threshold == 0.9  # provisional (owner, 2026-10-24)


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
    for name in ("KESTREL_WAKE", "KESTREL_WAKE_ON_BATTERY", "KESTREL_WAKE_MIN_BATTERY", "KESTREL_USER_NAME"):
        monkeypatch.delenv(name, raising=False)
    settings = WakeSettings.from_env()
    assert not settings.enabled and settings.user_name is None
    assert settings.on_battery is True and settings.min_battery == 30  # the battery rule, unless overridden
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


# --- the battery rule -------------------------------------------------------------------------


def test_decide_the_battery_rule_with_hysteresis():
    mains = decide(MAINS, 30, True, was_low=True)
    assert (mains.listen, mains.keep_awake, mains.reason, mains.low) == (True, True, None, False)
    ok = decide(battery(64), 30, True, False)
    assert (ok.listen, ok.keep_awake, ok.reason) == (True, False, None)
    low = decide(battery(29), 30, True, False)
    assert (low.listen, low.reason, low.low) == (False, "battery low (29%)", True)
    assert decide(battery(30), 30, True, False).listen is True  # "at or above" the minimum
    still = decide(battery(33), 30, True, was_low=True)
    assert (still.listen, still.reason) == (False, "battery low (33%)")
    assert decide(battery(34), 30, True, was_low=True).listen is False
    resumed = decide(battery(35), 30, True, was_low=True)
    assert (resumed.listen, resumed.low) == (True, False)
    saver = decide(battery(80, saver=True), 30, True, False)
    assert (saver.listen, saver.reason) == (False, "power saver")
    off = decide(battery(90), 30, allow_battery=False, was_low=False)
    assert (off.listen, off.reason) == (False, "on battery (KESTREL_WAKE_ON_BATTERY=off)")
    assert decide(PowerState(on_ac=True, percent=50, saver=True), 30, True, False).listen  # plugged in: listen


def test_windows_power_status_is_read_into_a_power_state():
    def status(ac, flag, percent, saver):
        return read_power_state(SYSTEM_POWER_STATUS(ac, flag, percent, saver, 0, 0))

    assert status(0, 0, 64, 1) == PowerState(on_ac=False, percent=64, saver=True)
    assert status(1, 8, 64, 0) == PowerState(on_ac=True, percent=64, saver=False)
    assert status(255, 255, 255, 0) == PowerState(on_ac=True, percent=None, saver=False)  # unknown: mains
    assert status(0, 128, 255, 0).on_ac is True  # no system battery: a desktop


def test_windows_power_state_comes_from_getsystempowerstatus():
    class Kernel32:
        def GetSystemPowerStatus(self, pointer):
            s = pointer._obj
            s.ACLineStatus, s.BatteryFlag, s.BatteryLifePercent, s.SystemStatusFlag = 0, 0, 42, 1
            return 1

    assert WindowsPower(FakeDLL(), Kernel32()).state() == PowerState(on_ac=False, percent=42, saver=True)


def test_on_mains_it_listens_and_keeps_the_pc_awake(tmp_path):
    service, _log, *_ = make_service(tmp_path)
    service.start()
    try:
        wait_for(lambda: service.status()["keeping_awake"])
        assert service.status()["listening"] and service.status()["on_ac"] is True
    finally:
        service.stop()


def test_on_battery_at_64_percent_it_listens_without_keeping_the_pc_awake(tmp_path):
    service, log, *_ = make_service(tmp_path, power_state=battery(64))
    service.start()
    try:
        wait_for(lambda: "open" in log)
        time.sleep(0.2)  # several power checks
        status = service.status()
        assert status["listening"] and not status["keeping_awake"]
        assert (status["on_ac"], status["battery"], status["paused"]) == (False, 64, None)
    finally:
        service.stop()
    assert "keep_awake:True" not in log


def test_low_battery_pauses_and_resumes_only_at_35_percent(tmp_path):
    service, log, _, _, power, *_ = make_service(tmp_path, power_state=battery(29))
    service.start()
    try:
        wait_for(lambda: service.status()["paused"] == "battery low (29%)")
        assert "open" not in log
        power.current = battery(33)
        wait_for(lambda: service.status()["paused"] == "battery low (33%)")
        time.sleep(0.2)
        assert "open" not in log and not service.status()["listening"]  # still paused at 33%
        power.current = battery(35)
        wait_for(lambda: service.status()["listening"])
        assert service.status()["paused"] is None and not service.status()["keeping_awake"]
    finally:
        service.stop()


def test_falling_below_the_minimum_while_listening_pauses(tmp_path):
    service, log, _, _, power, *_ = make_service(tmp_path, power_state=battery(31))
    service.start()
    try:
        wait_for(lambda: "open" in log)
        power.current = battery(29)
        wait_for(lambda: service.status()["paused"] == "battery low (29%)" and "close" in log)
        assert not service.status()["listening"]
    finally:
        service.stop()


def test_power_saver_pauses_and_resumes_when_turned_off(tmp_path):
    service, log, _, _, power, *_ = make_service(tmp_path, power_state=battery(80, saver=True))
    service.start()
    try:
        wait_for(lambda: service.status()["paused"] == "power saver")
        assert "open" not in log
        power.current = battery(80)
        wait_for(lambda: service.status()["listening"])
        power.current = battery(80, saver=True)
        wait_for(lambda: service.status()["paused"] == "power saver" and "close" in log)
    finally:
        service.stop()


def test_plugging_in_while_paused_resumes_at_once_and_keeps_the_pc_awake(tmp_path):
    service, _log, _, _, power, *_ = make_service(tmp_path, power_state=battery(12))
    service.start()
    try:
        wait_for(lambda: service.status()["paused"] == "battery low (12%)")
        power.current = PowerState(on_ac=True, percent=12, saver=False)  # still at 12%, but plugged in
        started = time.monotonic()
        wait_for(lambda: service.status()["listening"] and service.status()["keeping_awake"])
        assert time.monotonic() - started < 1.0  # the next 5-second poll (0.05 s here)
    finally:
        service.stop()


def test_the_keep_awake_request_is_released_on_every_switch_to_battery(tmp_path):
    service, log, _, _, power, *_ = make_service(tmp_path)
    service.start()
    try:
        for _ in range(2):
            wait_for(lambda: power.awake)
            power.current = battery(70)
            wait_for(lambda: not power.awake)
            assert service.status()["listening"] and not service.status()["keeping_awake"]
            power.current = MAINS
    finally:
        service.stop()
    assert log.count("keep_awake:True") == 2 and log.count("keep_awake:False") == 2
    assert log.count("open") == 1  # switching power never reopened the microphone


def test_kestrel_wake_on_battery_off_forces_off_on_battery(tmp_path):
    service, log, *_ = make_service(tmp_path, power_state=battery(90), on_battery=False)
    service.start()
    try:
        wait_for(lambda: service.status()["paused"] == "on battery (KESTREL_WAKE_ON_BATTERY=off)")
    finally:
        service.stop()
    assert "open" not in log and "keep_awake:True" not in log


def test_after_standby_the_stream_is_reopened_and_power_read_again(tmp_path):
    wall = [1_000_000.0]
    service, log, _, _, power, *_ = make_service(tmp_path, power_state=battery(64), wall=lambda: wall[0])
    service.start()
    try:
        wait_for(lambda: "open" in log)
        reads = power.reads
        wall[0] += 3600  # an hour of Modern Standby passes between two loop turns
        wait_for(lambda: log.count("open") == 2)
        assert log.index("close") < len(log) - 1 - log[::-1].index("open")  # closed, then opened again
        assert power.reads > reads and service.reopened == 1 and service.status()["listening"]
    finally:
        service.stop()


def test_a_stream_that_stops_delivering_audio_is_reopened(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "STALL_S", 0.2)
    service, log, *_ = make_service(tmp_path, chunks=[b"noise"])  # one chunk, then silence from the device
    service.start()
    try:
        wait_for(lambda: log.count("open") >= 2)
        assert service.reopened >= 1
    finally:
        service.stop()


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
            "on_ac": True,
            "battery": None,
            "talk": False,
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


# --- Listening mode: the live mic level --------------------------------------------------------


class SpeakingSource(FakeSource):
    """A microphone that never stops: a loud 0.1 s chunk every 10 ms of test time."""

    LOUD = b"\x00\x40" * 1600  # 16-bit samples at +16384: about -6 dBFS

    def read(self, timeout: float):
        time.sleep(0.01)
        return self.LOUD


def levels(events):
    return [e for e in events if e["type"] == "wake_level"]


def test_loudness_is_0_for_silence_and_near_1_for_full_scale():
    from kestrel.wake.service import loudness, smooth

    assert loudness(b"\x00\x00" * 1600) == 0.0
    assert loudness(b"") == 0.0
    assert 0.99 <= loudness(b"\xff\x7f" * 1600) <= 1.0  # +32767
    assert 0.85 <= loudness(SpeakingSource.LOUD) <= 0.95  # about -6 dBFS
    assert 0.0 < smooth(0.0, 1.0) < 1.0 and smooth(1.0, 0.0) > smooth(0.0, 1.0) - 1  # it moves, never jumps


def test_level_events_carry_one_number_and_only_while_listening(tmp_path):
    service, _, events, *_ = make_service(tmp_path, source=SpeakingSource, listen_seconds=0.4)
    service.start()
    try:
        time.sleep(0.3)
        assert levels(events) == []  # Standby: the mic is on, but no level leaves the service
        service.on_wake()
        wait_for(lambda: len(levels(events)) >= 5)
        for event in levels(events):
            assert set(event) == {"type", "level"}  # one number, nothing else
            assert isinstance(event["level"], float) and 0.0 <= event["level"] <= 1.0
        wait_for(lambda: not service.attending())  # the 0.4 s window ran out
        time.sleep(0.05)
        before = len(levels(events))
        time.sleep(0.3)
        assert len(levels(events)) == before  # back in Standby: no more level events
    finally:
        service.stop()
    wake = next(e for e in events if e["type"] == "wake")
    assert wake["listen_seconds"] == 0.4


def test_a_slow_trace_write_or_windows_call_does_not_shorten_listening(tmp_path):
    """The window counts from when Listening starts, after the trace write and the two Windows calls."""
    now = [1000.0]
    service, _, events, _, power, *_ = make_service(tmp_path, clock=lambda: now[0], listen_seconds=0.4)

    def slow(seconds):
        def call(*args):
            now[0] += seconds
            return True

        return call

    service.record_wake = slow(0.5)  # longer than the whole window
    power.wake_display, power.bring_to_front = slow(0.1), slow(0.1)
    assert service.on_wake()
    wake = next(e for e in events if e["type"] == "wake")
    assert wake["listen_seconds"] == 0.4
    assert service.attending()
    now[0] += 0.39
    assert service.attending()  # the full announced window, from here
    now[0] += 0.02
    assert not service.attending()


def test_level_events_come_about_ten_times_a_second(tmp_path):
    from kestrel.wake.audio import CHUNK_FRAMES
    from kestrel.wake.detector import SAMPLE_RATE

    assert CHUNK_FRAMES / SAMPLE_RATE == 0.1  # one level per 0.1 s chunk of real audio


def test_typing_or_sending_ends_the_listening_window(tmp_path):
    service, _, _, tracer, *_ = make_service(tmp_path, source=SpeakingSource)
    config = WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))
    app = create_app(lambda e, a: None, tracer, config, wake=service)  # type: ignore[arg-type,return-value]
    with TestClient(app, base_url=BASE) as client, client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        for message in ({"type": "wake_dismiss"}, {"type": "user_message", "text": "hi"}):
            service._last_wake = None
            service.on_wake()
            assert service.attending()
            ws.send_json(message)
            wait_for(lambda: not service.attending())


def test_kestrel_wake_listen_seconds(monkeypatch):
    monkeypatch.setenv("KESTREL_WAKE_LISTEN_SECONDS", "12")
    assert WakeSettings.from_env().listen_seconds == 12.0
    monkeypatch.setenv("KESTREL_WAKE_LISTEN_SECONDS", "999")
    assert WakeSettings.from_env().listen_seconds == 60.0
    monkeypatch.delenv("KESTREL_WAKE_LISTEN_SECONDS")
    assert WakeSettings.from_env().listen_seconds == 8.0


def test_level_code_never_writes_or_logs_audio():
    """The level path is two pure functions on bytes: no file, print or logging call anywhere in it."""
    import inspect

    from kestrel.wake import service as module

    for fn in (module.loudness, module.smooth, module.WakeService._send_level):
        body = inspect.getsource(fn)
        for banned in ("open(", "print(", "logging", "write", "record_wake"):
            assert banned not in body, f"{fn.__name__} contains {banned}"


# --- greetings before "Kestrel" (2026-10-26) ----------------------------------------------------------


class GrammarRecognizer:
    """Like Vosk with a grammar of words and phrases: words from the grammar come out as themselves
    (a phrase like "good morning" as two words), anything else as "[unk]". "kestrel" gets conf 0.95."""

    def __init__(self, grammar: str):
        self.words = {w for entry in json.loads(grammar) for w in entry.split()}
        self.pending = b""

    def AcceptWaveform(self, data: bytes) -> bool:
        self.pending = data
        return True

    def Result(self) -> str:
        tokens = [t.strip(".,!?") for t in self.pending.decode().lower().split()]
        words = [t if t in self.words else "[unk]" for t in tokens]
        return json.dumps({"result": [{"word": w, "conf": 0.95 if w == "kestrel" else 1.0} for w in words]})


@pytest.mark.parametrize(
    ("said", "greeting_heard"),
    [
        ("Kestrel", None),
        ("Hi Kestrel", "hi"),
        ("Hello Kestrel", "hello"),
        ("Hey Kestrel", "hey"),
        ("Shalom Kestrel", "shalom"),
        ("Good morning Kestrel", "good morning"),
        ("Howdy Kestrel", None),  # not in the grammar: "[unk] kestrel" still wakes, plain greeting
    ],
)
def test_each_phrase_wakes(said, greeting_heard):
    detector = VoskDetector(GrammarRecognizer, threshold=0.9)
    assert detector.feed(said.encode()) == 0.95
    assert detector.greeting == greeting_heard


def test_the_wake_rule_is_kestrel_anywhere_in_the_utterance():
    """What Vosk returns for "hi kestrel" said with an unknown greeting, and for a known one."""
    unk_then_kestrel = json.dumps({"result": [{"word": "[unk]", "conf": 1.0}, {"word": "kestrel", "conf": 0.93}]})
    hello_kestrel = json.dumps({"result": [{"word": "hello", "conf": 1.0}, {"word": "kestrel", "conf": 0.97}]})
    assert wake_confidence(unk_then_kestrel) == 0.93
    assert wake_confidence(hello_kestrel) == 0.97
    assert heard_greeting(unk_then_kestrel) is None and heard_greeting(hello_kestrel) == "hello"


def test_kestrel_inside_a_longer_sentence_still_wakes():
    """Accepted behaviour (docs/voice-wake.md): the word anywhere in an utterance wakes Kestrel."""
    detector = VoskDetector(GrammarRecognizer, threshold=0.9)
    assert detector.feed(b"I saw a kestrel over the field today") == 0.95
    assert detector.greeting is None


def test_a_greeting_without_kestrel_never_wakes():
    detector = VoskDetector(GrammarRecognizer, threshold=0.5)
    for said in (b"hello", b"hi there", b"good morning everyone", b"shalom"):
        assert detector.feed(said) is None


def test_the_threshold_still_applies_with_a_greeting():
    detector = VoskDetector(GrammarRecognizer, threshold=0.96)  # kestrel comes out at 0.95
    assert detector.feed(b"hello kestrel") is None


def test_heard_greeting_needs_the_whole_phrase():
    def result(*words):
        return json.dumps({"result": [{"word": w, "conf": 1.0} for w in words]})

    assert heard_greeting(result("good", "morning", "kestrel")) == "good morning"
    assert heard_greeting(result("good", "kestrel")) is None
    assert heard_greeting(result("[unk]", "hey", "kestrel")) == "hey"
    assert heard_greeting("") is None
    assert set(GREETINGS) == {"hi", "hello", "hey", "shalom", "good morning"}


def test_the_echoed_greeting_with_and_without_a_name():
    assert greeting("Sam", "hello") == "Hello, Sam. I'm here, what do you need?"
    assert greeting("Sam", "hi") == "Hi, Sam. I'm here, what do you need?"
    assert greeting("Sam", "hey") == "Hey, Sam. I'm here, what do you need?"
    assert greeting(None, "shalom") == "Shalom. I'm here, what do you need?"
    assert greeting("  ", "good morning") == "Good morning. I'm here, what do you need?"
    assert greeting("Sam", None) == "I'm here, Sam. What do you need?"  # no greeting heard: unchanged
    assert "kestrel" not in greeting("Sam", "hello").lower()  # it would wake itself


class GreetingDetector:
    """Fires on b"kestrel" like FakeDetector, and reports a greeting heard with it."""

    def __init__(self, heard):
        self.heard = heard
        self.greeting = None

    def feed(self, pcm: bytes) -> float | None:
        if pcm != b"kestrel":
            return None
        self.greeting = self.heard
        return 1.0


def test_a_greeted_wake_is_answered_with_the_same_greeting(tmp_path):
    service, _, events, _, _, speaker, _ = make_service(tmp_path, chunks=[b"kestrel"], name="Sam")
    service.make_detector = lambda: GreetingDetector("hello")
    service.start()
    try:
        wait_for(lambda: speaker.said)
    finally:
        service.stop()
    assert speaker.said == ["Hello, Sam. I'm here, what do you need?"]
    assert [e["greeting"] for e in events if e["type"] == "wake"] == ["Hello, Sam. I'm here, what do you need?"]


def test_a_plain_wake_keeps_the_normal_greeting(tmp_path):
    service, _, _, _, _, speaker, _ = make_service(tmp_path, chunks=[b"kestrel"])
    service.make_detector = lambda: GreetingDetector(None)
    service.start()
    try:
        wait_for(lambda: speaker.said)
    finally:
        service.stop()
    assert speaker.said == ["I'm here. What do you need?"]


def test_the_cooldown_is_unchanged_for_greeted_wakes(tmp_path):
    now = [100.0]
    service, _, events, *_ = make_service(tmp_path, clock=lambda: now[0])
    assert service_module.COOLDOWN_S == 10.0
    assert service.on_wake("hello") is True
    now[0] += 9.9
    assert service.on_wake("hi") is False and service.on_wake() is False
    now[0] += 0.1
    assert service.on_wake("hey") is True
    assert len([e for e in events if e["type"] == "wake"]) == 2
