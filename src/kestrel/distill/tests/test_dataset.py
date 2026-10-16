"""The SFT dataset builder on synthetic traces: every filter, the split, and the files it writes.

Each test builds its own small traces database in tmp_path with tracing.SCHEMA, so nothing here reads
logs/ or calls a model. The canaries and keys below are made up for these tests.
"""

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kestrel.distill import dataset as ds
from kestrel.distill.dataset import Config, Verdict
from kestrel.tracing import SCHEMA

TEACHER = ("groq", "openai/gpt-oss-120b")
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
FAKE_CANARY = "KCAN-0123456789ab"
FAKE_GROQ_KEY = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
FAKE_ASSIGNMENT = "GROQ_API_KEY=" + "abcdefgh" + "12345678"  # split so gitleaks sees no literal


def convo(question: str, answer: str, call_id: str = "call_1") -> list[dict[str, Any]]:
    """A one-turn conversation with one tool call, in the stored (cleaned) message format."""
    return [
        {"role": "system", "content": "You are Kestrel."},
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "<untrusted_data>hello</untrusted_data>"},
        {"role": "assistant", "content": answer},
    ]


class TraceDB:
    """A traces database in the tracing.py format, filled by the test."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.n = 0
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
        conn.close()

    def add(
        self,
        messages: list[dict] | None,
        *,
        steps: list[tuple[str, str]] | None = None,
        status: str = "ok",
        rating: str | None = None,
    ) -> str:
        self.n += 1
        trace_id = f"t{self.n:03d}"
        steps = [TEACHER] if steps is None else steps
        user = next((m["content"] for m in messages or [] if m["role"] == "user"), "")
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO traces (trace_id, start_time, status, steps, user_message, providers, messages,"
                    " rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace_id,
                        1_790_000_000.0 + self.n,
                        status,
                        len(steps),
                        user,
                        json.dumps(sorted({p for p, _ in steps})),
                        json.dumps(messages) if messages is not None else None,
                        rating,
                    ),
                )
                for i, (provider, model) in enumerate(steps):
                    attrs = {"gen_ai.provider.name": provider, "gen_ai.request.model": model}
                    conn.execute(
                        "INSERT INTO spans (span_id, trace_id, parent_id, name, start_time, attributes)"
                        " VALUES (?, ?, ?, 'llm_call', ?, ?)",
                        (f"{trace_id}-s{i}", trace_id, trace_id, 1_790_000_000.0 + self.n + i / 10, json.dumps(attrs)),
                    )
        finally:
            conn.close()
        return trace_id

    def traces(self) -> list[dict[str, Any]]:
        conn = ds.open_readonly(self.path)
        try:
            return ds.read_traces(conn)
        finally:
            conn.close()


@pytest.fixture
def db(tmp_path: Path) -> TraceDB:
    return TraceDB(tmp_path / "traces.db")


def passed(task_id: str, split: str = "main") -> Verdict:
    return Verdict(task_id, "pass", 1.0, split, "results.json")


def build(db: TraceDB, verdicts: dict[str, Verdict], **config: Any) -> ds.Dataset:
    return ds.build(db.traces(), verdicts, TOOLS, Config(**config))


def kept_ids(d: ds.Dataset) -> set[str]:
    return {e.trace_id for e in d.train + d.val}


# --- Grader -------------------------------------------------------------------


def test_only_passed_runs_are_kept(db: TraceDB) -> None:
    good = db.add(convo("What is in a.txt?", "It says hello."))
    bad = db.add(convo("Summarize a.txt", "It is about cats."))
    ungraded = db.add(convo("Hi", "Hello!"))
    verdicts = {good: passed("read-a"), bad: Verdict("sum-a", "fail", 0.4, "main", "results.json")}
    d = build(db, verdicts)
    assert kept_ids(d) == {good}
    assert d.dropped["failed_grader"] == 1 and d.dropped["not_graded"] == 1
    assert ungraded not in kept_ids(d)


@pytest.mark.parametrize("status", ["error", "excluded", "skipped"])
def test_ungraded_bench_statuses_are_not_passes(db: TraceDB, status: str) -> None:
    t = db.add(convo("q", "a"))
    d = build(db, {t: Verdict("x", status, 0.0, "main", "r.json")})
    assert kept_ids(d) == set() and d.dropped["failed_grader"] == 1


def test_min_score(db: TraceDB) -> None:
    t = db.add(convo("q", "a"))
    verdict = {t: Verdict("x", "pass", 0.8, "main", "r.json")}
    assert kept_ids(build(db, verdict)) == {t}
    assert kept_ids(build(db, verdict, min_score=0.9)) == set()


def test_rated_good_chat_traces_need_the_flag(db: TraceDB) -> None:
    good = db.add(convo("q1", "a1"), rating="good")
    db.add(convo("q2", "a2"), rating="bad")
    assert kept_ids(build(db, {})) == set()
    d = build(db, {}, include_rated=True)
    assert kept_ids(d) == {good}
    assert d.train[0].metadata["source"] == "rated-good"


def test_no_messages_errors_and_demo_are_dropped(db: TraceDB) -> None:
    ids = [
        db.add(None),
        db.add(convo("q", "a"), status="error"),
        db.add(convo("q", "b"), steps=[("demo", "demo")]),
    ]
    d = build(db, {t: passed(f"t{i}") for i, t in enumerate(ids)})
    assert kept_ids(d) == set()
    assert d.dropped["no_messages"] == 1 and d.dropped["error_or_demo"] == 2


# --- Teacher ------------------------------------------------------------------


def test_only_the_teacher_is_kept(db: TraceDB) -> None:
    teacher = db.add(convo("q1", "a1"))
    ollama = db.add(convo("q2", "a2"), steps=[("ollama", "qwen2.5:0.5b")])
    mixed = db.add(convo("q3", "a3"), steps=[TEACHER, ("ollama", "qwen2.5:0.5b")])  # fell back mid-run
    d = build(db, {t: passed(t) for t in (teacher, ollama, mixed)})
    assert kept_ids(d) == {teacher}
    assert d.dropped["not_teacher"] == 2


@pytest.mark.parametrize(
    "step",
    [("gemini", "gemini-3.6-flash"), ("anthropic", "claude-opus-5-5"), ("openai", "gpt-5"), ("groq", "gemini-x")],
)
def test_closed_models_are_refused_even_when_allowed(db: TraceDB, step: tuple[str, str]) -> None:
    t = db.add(convo("q", "a"), steps=[step])
    d = build(db, {t: passed("x")}, providers=frozenset({step[0]}), models=frozenset({step[1]}))
    assert kept_ids(d) == set() and d.dropped["closed_teacher"] == 1


def test_gpt_oss_is_open_weight() -> None:
    assert not ds.is_closed("groq", "openai/gpt-oss-120b")
    assert ds.is_closed("groq", "openai/gpt-5") and ds.is_closed("openrouter", "anthropic/claude-x")


def test_cli_refuses_a_closed_teacher(tmp_path: Path, db: TraceDB) -> None:
    with pytest.raises(SystemExit, match="design decision 3"):
        ds.main(["--db", str(db.path), "--out", str(tmp_path / "out"), "--provider", "gemini"])


def test_safety_split_needs_the_flag(db: TraceDB) -> None:
    t = db.add(convo("Summarize the email", "Done."))
    verdicts = {t: passed("ben-email", split="safety")}
    assert build(db, verdicts).dropped["safety_split"] == 1
    assert kept_ids(build(db, verdicts, include_safety=True)) == {t}


# --- Secret scrub -------------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        f"The code is {FAKE_CANARY}.",  # as written
        f"code={base64.b64encode(FAKE_CANARY.encode()).decode()}",  # hidden in base64
        "https://x.example/?q=KCAN%2D0123456789ab",  # URL-encoded
        "first half kcan-012345, the rest later",  # split: only the prefix survives
    ],
)
def test_canaries_are_dropped_in_any_form(db: TraceDB, leak: str) -> None:
    t = db.add(convo("What does notes/task.txt say?", leak))
    d = build(db, {t: passed("x")})
    assert kept_ids(d) == set() and d.dropped["secret_canary"] == 1


def test_a_canary_in_a_tool_result_is_dropped_too(db: TraceDB) -> None:
    messages = convo("q", "Nothing important.")
    messages[3]["content"] = f"<untrusted_data>token {FAKE_CANARY}</untrusted_data>"
    t = db.add(messages)
    assert build(db, {t: passed("x")}).dropped["secret_canary"] == 1


@pytest.mark.parametrize("leak", [f"key: {FAKE_GROQ_KEY}", FAKE_ASSIGNMENT, "it was [REDACTED]"])
def test_keys_and_redaction_markers_are_dropped(db: TraceDB, leak: str) -> None:
    t = db.add(convo("q", leak))
    assert build(db, {t: passed("x")}).dropped["secret_key"] == 1


def test_secret_env_values_are_dropped(db: TraceDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TEST_TOKEN", "zz-not-a-real-value-42")
    t = db.add(convo("q", "the value is zz-not-a-real-value-42"))
    assert build(db, {t: passed("x")}).dropped["secret_key"] == 1


def test_real_emails_are_dropped_but_example_addresses_kept(db: TraceDB) -> None:
    fixture = db.add(convo("Email priya@example.com and dana@team.example", "Sent to both."))
    real = db.add(convo("Email someone.real@gmail.com", "Sent."))
    d = build(db, {fixture: passed("a"), real: passed("b")})
    assert kept_ids(d) == {fixture} and d.dropped["pii"] == 1


def test_deny_list(db: TraceDB) -> None:
    t = db.add(convo("Ask Jordan Realname about it", "Done."))
    assert kept_ids(build(db, {t: passed("x")}, deny=("jordan realname",))) == set()


def test_scrubbed_output_never_contains_a_canary(tmp_path: Path, db: TraceDB) -> None:
    clean = db.add(convo("q1", "a1"))
    leaky = db.add(convo("q2", f"here: {FAKE_CANARY}"))
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "meta": {"split": "main"},
                "tasks": [
                    {"id": "clean", "status": "pass", "score": 1.0, "trace_ids": [clean]},
                    {"id": "leaky", "status": "pass", "score": 1.0, "trace_ids": [leaky]},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert ds.main(["--db", str(db.path), "--results", str(results), "--out", str(out), "--val-fraction", "0"]) == 0
    written = (out / "train.jsonl").read_text(encoding="utf-8") + (out / "val.jsonl").read_text(encoding="utf-8")
    assert "KCAN" not in written.upper() and clean in written
    s = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert s["examples"] == 1 and s["dropped"]["secret_canary"] == 1 and s["dropped_by_secret_scrub"] == 1


# --- Length, dedupe, format, split --------------------------------------------


def test_too_long(db: TraceDB) -> None:
    t = db.add(convo("q", "x" * 20_000))
    assert build(db, {t: passed("x")}, max_tokens=1_000).dropped["too_long"] == 1


def test_duplicates_ignore_tool_call_ids(db: TraceDB) -> None:
    a = db.add(convo("q", "a", call_id="call_A"))
    b = db.add(convo("q", "a", call_id="call_B"))
    d = build(db, {a: passed("x"), b: passed("x")})
    assert kept_ids(d) == {a} and d.dropped["duplicate"] == 1


def test_only_the_last_turn_is_trained(db: TraceDB) -> None:
    messages = [*convo("q1", "a1"), {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]
    t = db.add(messages)
    out = build(db, {t: passed("x")}).train[0].messages
    weights = [m.get("weight") for m in out if m["role"] == "assistant"]
    assert weights == [0, 0, 1]


def test_split_keeps_each_task_on_one_side(db: TraceDB) -> None:
    verdicts = {}
    for task in range(10):
        for repeat in range(3):
            verdicts[db.add(convo(f"task {task}", f"answer {repeat}"))] = passed(f"task-{task}")
    d = build(db, verdicts, val_fraction=0.2)
    train, val = {e.group for e in d.train}, {e.group for e in d.val}
    assert not train & val
    assert len(val) == 2 and len(d.val) == 6
    assert {e.trace_id for e in build(db, verdicts, val_fraction=0.2).val} == {e.trace_id for e in d.val}


def test_split_edge_cases() -> None:
    assert ds.split_groups(["only"], 0.5, "s") == set()
    assert len(ds.split_groups(["a", "b"], 0.01, "s")) == 1  # never empty by rounding
    assert len(ds.split_groups(["a", "b", "c"], 1.0, "s")) == 2  # never empties train


def test_stats_and_unknown_tools(db: TraceDB) -> None:
    messages = convo("q", "a")
    messages[2]["tool_calls"][0]["function"]["name"] = "fetch__fetch"
    t = db.add(messages)
    s = ds.stats(build(db, {t: passed("x")}))
    assert s["examples"] == 1 and s["per_task"] == {"x": 1}
    assert s["unknown_tools"] == {"fetch__fetch": 1}
    assert s["tokens"]["min"] == s["tokens"]["max"] > 0
    lines: list[str] = []
    ds.print_stats(s, out=lines.append)
    assert any("secret scrub" in line for line in lines) and any("WARNING" in line for line in lines)


def test_the_database_is_opened_read_only(db: TraceDB) -> None:
    db.add(convo("q", "a"))
    conn = ds.open_readonly(db.path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM traces")
    finally:
        conn.close()
