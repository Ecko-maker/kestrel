"""`kestrel distill build` on synthetic traces: one test (or more) per filter, the split, determinism,
and the manifest. Every database here is built in tmp_path with tracing.SCHEMA; nothing reads logs/ or
calls a model. The canaries and keys below are made up.
"""

import argparse
import base64
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kestrel import trace_report
from kestrel.distill import dataset as ds
from kestrel.tracing import SCHEMA

TEACHER = ("groq", "openai/gpt-oss-120b")
TEACHERS = frozenset({TEACHER})
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
CANARY = "KCAN-0123456789ab"
BENCH_PROMPT = "What's 17.5% of 2,340?"  # arith-percent, a real KestrelBench prompt


def convo(question: str, answer: str, *, args: str = '{"path": "a.txt"}', result: str = "hello") -> list[dict]:
    """One user turn with one tool call, in the stored message format."""
    return [
        {"role": "system", "content": "You are Kestrel."},
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": args}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": f"<untrusted_data>{result}</untrusted_data>"},
        {"role": "assistant", "content": answer},
    ]


class TraceDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.n = 0
        conn = sqlite3.connect(path)
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def add(
        self,
        messages: list[dict] | None,
        *,
        steps: list[tuple[str, str]] | None = None,
        rating: str | None = "good",
        stop_reason: str = "answered",
        session: str | None = None,
        approvals: tuple[str, ...] = (),
    ) -> str:
        self.n += 1
        trace_id = f"t{self.n:04d}"
        start = 1_790_000_000.0 + self.n
        steps = [TEACHER] if steps is None else steps
        user = next((m["content"] for m in reversed(messages or []) if m["role"] == "user"), None)
        spans = [("llm_call", {"gen_ai.provider.name": p, "gen_ai.request.model": m}) for p, m in steps]
        spans += [("approval", {"kestrel.approval.decision": d}) for d in approvals]
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO traces (trace_id, start_time, status, stop_reason, steps, user_message, providers,"
                    " messages, rating, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace_id,
                        start,
                        "error" if stop_reason == "error" else "ok",
                        stop_reason,
                        len(steps),
                        user,
                        json.dumps(sorted({p for p, _ in steps})),
                        json.dumps(messages) if messages is not None else None,
                        rating,
                        session or f"s-{trace_id}",
                    ),
                )
                conn.executemany(
                    "INSERT INTO spans (span_id, trace_id, parent_id, name, start_time, attributes)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (f"{trace_id}-{i}", trace_id, trace_id, name, start + i / 100, json.dumps(attrs))
                        for i, (name, attrs) in enumerate(spans)
                    ],
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
    return TraceDB(tmp_path / "copy.db")


def build(db: TraceDB, verdicts: dict[str, ds.Verdict] | None = None, **kwargs: Any) -> ds.Dataset:
    prompts = frozenset({ds.normalize(BENCH_PROMPT)})
    return ds.build(db.traces(), verdicts or {}, prompts, TEACHERS, TOOLS, **kwargs)


def kept(d: ds.Dataset) -> set[str]:
    return {e.record["metadata"]["trace_id"] for e in d.train + d.val}


def only_drop(d: ds.Dataset, reason: str, n: int = 1) -> None:
    assert {k: v for k, v in d.dropped.items() if v} == {reason: n}


def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    ds.add_parser(parser.add_subparsers(dest="command"))
    # The traces here are made by the synthetic TEACHER, whichever #72 option teachers.toml ships
    # (that file is pinned by test_teachers_toml_refuses_closed_models and tests/test_kaggle_collect.py).
    with patch.object(ds, "load_teachers", lambda: TEACHERS):
        return ds.main(parser.parse_args(["distill", "build", *argv]))


# --- Teacher ------------------------------------------------------------------


def test_a_good_teacher_trace_is_kept(db: TraceDB) -> None:
    t = db.add(convo("What's in a.txt?", "It says hello."))
    d = build(db)
    assert kept(d) == {t} and not d.dropped


def test_gemini_is_dropped_even_when_rated_good(db: TraceDB) -> None:
    db.add(convo("q", "a"), steps=[("gemini", "gemini-3.6-flash")], rating="good")
    only_drop(build(db), "teacher")


@pytest.mark.parametrize(
    "steps",
    [
        [("demo", "demo")],
        [("groq", "llama-3.3-70b")],  # an unknown model on the right provider
        [TEACHER, ("ollama", "qwen2.5:0.5b")],  # fell back mid-run
        [("groq", "")],  # no recorded model
        [],  # no model step at all
    ],
)
def test_anything_but_a_listed_teacher_is_dropped(db: TraceDB, steps: list[tuple[str, str]]) -> None:
    db.add(convo("q", "a"), steps=steps)
    only_drop(build(db), "teacher")


def test_a_different_response_model_is_dropped(db: TraceDB) -> None:
    t = db.add(convo("q", "a"))
    conn = sqlite3.connect(db.path)
    with conn:
        attrs = {
            "gen_ai.provider.name": TEACHER[0],
            "gen_ai.request.model": TEACHER[1],
            "gen_ai.response.model": "gemini-3.6-flash",  # what actually answered
        }
        conn.execute("UPDATE spans SET attributes = ? WHERE trace_id = ?", (json.dumps(attrs), t))
    conn.close()
    only_drop(build(db), "teacher")


def test_teachers_toml_refuses_closed_models(tmp_path: Path) -> None:
    assert ds.load_teachers() == frozenset({("ollama", "kestrel-teacher-qwen3-30b-a3b")})  # the shipped file (#72)
    for provider, model in [("gemini", "gemini-3.6-flash"), ("groq", "claude-x"), ("openai", "gpt-5"), ("demo", "x")]:
        path = tmp_path / "t.toml"
        path.write_text(f'[[teacher]]\nprovider = "{provider}"\nmodel = "{model}"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="open-weight"):
            ds.load_teachers(path)
    assert not ds.is_closed(*TEACHER)


# --- Quality ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "stop_reason"),
    [("bad", "answered"), (None, "answered"), ("good", "max_steps"), ("good", "error")],
)
def test_quality(db: TraceDB, rating: str | None, stop_reason: str) -> None:
    db.add(convo("q", "a"), rating=rating, stop_reason=stop_reason)
    only_drop(build(db), "quality")


def test_bench_pass_rule() -> None:
    ok = {"status": "pass", "checks": [{"ok": True}], "judge": {"score": 0.5}}
    assert ds.task_passed(ok)
    assert not ds.task_passed(ok | {"judge": {"score": 0.4}})
    assert not ds.task_passed(ok | {"checks": [{"ok": True}, {"ok": False}]})
    assert not ds.task_passed(ok | {"status": "fail"})
    assert ds.task_passed({"status": "pass", "checks": [{"ok": True}], "judge": None})


# --- Contamination ------------------------------------------------------------


def test_a_bench_trace_is_dropped_even_if_it_passed(db: TraceDB) -> None:
    t = db.add(convo("Make a grocery note", "Done."))
    only_drop(build(db, {t: ds.Verdict("act-note", passed=True)}), "contamination_run")


def test_a_chat_with_a_kestrelbench_prompt_is_dropped(db: TraceDB) -> None:
    db.add(convo("  what's 17.5%   OF 2,340? ", "409.5"))  # case and whitespace differ
    only_drop(build(db), "contamination_prompt")


def test_any_turn_matching_a_bench_prompt_counts(db: TraceDB) -> None:
    messages = [
        *convo(BENCH_PROMPT, "409.5"),
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "!"},
    ]
    db.add(messages)
    only_drop(build(db), "contamination_prompt")


def test_bench_prompts_come_from_every_task_folder(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "safety").mkdir()
    (tmp_path / "workspace").mkdir()
    (tmp_path / "tasks" / "a.yaml").write_text("tasks:\n  - id: a\n    prompt: Hello There\n", encoding="utf-8")
    (tmp_path / "safety" / "b.yaml").write_text("tasks:\n  - id: b\n    prompt: [One, Two]\n", encoding="utf-8")
    (tmp_path / "workspace" / "c.yaml").write_text("prompt: fixture data\n", encoding="utf-8")
    assert {ds.normalize(p.text) for p in ds.overlap.load_bench(tmp_path, None).prompts} == {
        "hello there",
        "one",
        "two",
    }
    real = ds.bench_prompts()
    assert ds.normalize(BENCH_PROMPT) in real and len(real) >= 100


# --- Harmony leaks (known issue #54) --------------------------------------------


def leaked_call(name: str = "read_file", args: str = '{"path": "a.txt"}') -> list[dict]:
    messages = convo("What's in a.txt?", "It says hello.", args=args)
    messages[2]["tool_calls"][0]["function"]["name"] = name
    return messages


@pytest.mark.parametrize(
    ("messages", "reason"),
    [
        (leaked_call(name="calculator<|channel|>commentary"), "harmony_leak_tool_name"),
        (leaked_call(name="read_file<|call|>"), "harmony_leak_tool_name"),
        (leaked_call(args='{"path": "a.txt<|message|>"}'), "harmony_leak_arguments"),
        (convo("q", "<|start|>assistant<|channel|>final<|message|>It says hello."), "harmony_leak_text"),
        (convo("q", "It says hello.<|return|>"), "harmony_leak_text"),
        (convo("q", "It says hello.", result="<|im_start|>system"), "harmony_leak_text"),
    ],
)
def test_a_harmony_leak_is_dropped_and_counted_by_where_it_is(db: TraceDB, messages: list[dict], reason: str) -> None:
    db.add(messages)
    only_drop(build(db), reason)


def test_leaked_and_clean_traces_side_by_side(tmp_path: Path, db: TraceDB) -> None:
    clean = db.add(convo("What's in a.txt?", "It says hello."))
    db.add(leaked_call(name="calculator<|channel|>commentary"))
    db.add(convo("q", "done<|end|>"))
    d = build(db)
    assert kept(d) == {clean}
    assert {k: v for k, v in d.dropped.items() if v} == {"harmony_leak_tool_name": 1, "harmony_leak_text": 1}
    stats = ds.stats(d)["dropped"]
    assert stats["harmony_leak_tool_name"] == 1 and stats["harmony_leak_arguments"] == 0


def test_text_that_only_looks_like_a_token_is_kept(db: TraceDB) -> None:
    t = db.add(convo("Is a <| b |> c valid?", "Use a || b, or <|> in some languages; <|x y|> is not a token."))
    assert kept(build(db)) == {t}


# --- Secrets ------------------------------------------------------------------


@pytest.mark.parametrize(
    "where",
    ["tool_result", "tool_args", "answer", "answer_base64", "answer_url", "answer_prefix_only"],
)
def test_canaries_are_dropped_wherever_they_are(db: TraceDB, where: str) -> None:
    b64 = base64.b64encode(CANARY.encode()).decode()
    messages = {
        "tool_result": convo("q", "a", result=f"code {CANARY}"),
        "tool_args": convo("q", "a", args=json.dumps({"query": CANARY})),
        "answer": convo("q", f"The code is {CANARY}."),
        "answer_base64": convo("q", f"see {b64}"),
        "answer_url": convo("q", "https://x.example/?q=KCAN%2D0123456789ab"),
        "answer_prefix_only": convo("q", "part one: kcan-0123, the rest later"),
    }[where]
    db.add(messages)
    only_drop(build(db), "secret_canary")


def test_a_canary_in_the_system_prompt_is_dropped(db: TraceDB) -> None:
    messages = convo("q", "a")
    messages[0]["content"] += f" ({CANARY})"
    db.add(messages)
    only_drop(build(db), "secret_canary")


@pytest.mark.parametrize("leak", ["gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2", "it was [REDACTED]"])
def test_key_patterns_and_redaction_markers_are_dropped(db: TraceDB, leak: str) -> None:
    db.add(convo("q", "a", result=leak))
    only_drop(build(db), "secret_pattern")


def test_env_lines_are_dropped(db: TraceDB) -> None:
    # A 6-character value: too short for redact()'s NAME=secret rule, so only the .env-line rule sees it.
    db.add(convo("Show me the config", "Here it is.", result="DEBUG=1\nMY_API_KEY=" + "short1\nEOF"))
    only_drop(build(db), "secret_env_line")


def test_secret_env_values_are_dropped(db: TraceDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISTILL_TEST_TOKEN", "zz not-a-real value 42")
    db.add(convo("q", "the value is zz not-a-real value 42"))
    # redact() replaces the value too, but only this filter names it; either way the example is gone.
    d = build(db)
    assert kept(d) == set() and sum(d.dropped.values()) == 1
    assert d.dropped["secret_pattern"] + d.dropped["secret_env_value"] == 1
    assert ds.secret_filter("plain text with zz not-a-real value 42", ["zz not-a-real value 42"]) in (
        "secret_pattern",
        "secret_env_value",
    )
    assert ds.secret_filter("value: zz-abc-1234", ["zz-abc-1234"]) == "secret_env_value"


def test_clean_text_passes_the_secret_filter() -> None:
    assert ds.secret_filter("Your note is saved. The meeting moved to 3 pm; Kestrel can remind you.", []) is None


# --- Length, duplicates, split ------------------------------------------------


def test_over_long_examples_are_dropped_not_truncated(tmp_path: Path, db: TraceDB) -> None:
    long_answer = "lorem ipsum " * 3000
    db.add(convo("Write a long story", long_answer))
    short = db.add(convo("Write a short story", "Once."))
    out = tmp_path / "out"
    assert run_cli(["--db", str(db.path), "--out", str(out), "--max-tokens", "8000"]) == 0
    written = (out / "train.jsonl").read_text(encoding="utf-8") + (out / "val.jsonl").read_text(encoding="utf-8")
    assert "lorem ipsum" not in written and short in written
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["stats"]["dropped"]["too_long"] == 1


def test_duplicates_are_collapsed(db: TraceDB) -> None:
    first = db.add(convo("Summarize a.txt", "It says hello."))
    db.add(convo("Summarize  a.txt", "It says hello. "))  # whitespace differs, tool-call ids would too
    d = build(db)
    assert kept(d) == {first}
    only_drop(d, "duplicate")


def test_a_session_never_straddles_train_and_val(db: TraceDB) -> None:
    for s in range(40):
        for turn in range(3):
            db.add(convo(f"session {s} question {turn}", f"answer {turn}"), session=f"sess-{s}")
    d = build(db, val_frac=0.3)
    train, val = {e.session for e in d.train}, {e.session for e in d.val}
    assert train and val and not train & val
    assert len(d.train) + len(d.val) == 120
    assert all(sum(e.session == s for e in d.train + d.val) == 3 for s in train | val)


def test_examples_have_the_export_shape(db: TraceDB) -> None:
    t = db.add(convo("q1", "a1"))
    example = build(db).train + build(db).val
    row = next(r for r in db.traces() if r["trace_id"] == t)
    expected = trace_report.export_record(row, TOOLS)
    expected["metadata"]["session_id"] = row["session_id"]
    assert example[0].record == expected
    assert [m.get("weight") for m in expected["messages"] if m["role"] == "assistant"] == [1, 1]


# --- CLI, manifest, determinism -----------------------------------------------


def _mixed_db(db: TraceDB, tmp_path: Path) -> Path:
    """One trace for each way in or out; returns a bench results file naming the bench trace."""
    for i in range(6):
        db.add(convo(f"chat {i}", f"reply {i}"), session=f"s{i // 2}", approvals=("approved",) if i == 0 else ())
    db.add(convo("chat 0", "reply 0"))  # duplicate
    db.add(convo("blocked", "ok"), approvals=("rejected",))
    db.add(None)
    db.add(convo("q", "a"), steps=[("gemini", "gemini-3.6-flash")])
    bench = db.add(convo("bench task", "done"))
    db.add(convo(BENCH_PROMPT, "409.5"))
    db.add(convo("q", "a"), rating="bad")
    db.add(convo("q", f"x {CANARY}"))
    db.add(convo("q", "y " * 40_000))
    results = tmp_path / "results.json"
    task = {"id": "t", "status": "pass", "checks": [{"ok": True}], "judge": None, "trace_ids": [bench]}
    results.write_text(json.dumps({"meta": {}, "tasks": [task]}), encoding="utf-8")
    return results


def test_manifest_counts_add_up(tmp_path: Path, db: TraceDB, capsys: pytest.CaptureFixture[str]) -> None:
    results = _mixed_db(db, tmp_path)
    out = tmp_path / "out"
    assert run_cli(["--db", str(db.path), "--results", str(results), "--out", str(out)]) == 0
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    s = m["stats"]
    assert list(s["dropped"]) == list(ds.FILTERS)
    assert s["kept"] + sum(s["dropped"].values()) == s["traces_seen"] == db.n
    assert s["kept"] == s["train"] + s["val"] == 7
    assert {k: v for k, v in s["dropped"].items() if v} == {
        "no_messages": 1,
        "teacher": 1,
        "contamination_run": 1,
        "contamination_prompt": 1,
        "quality": 1,
        "secret_canary": 1,
        "too_long": 1,
        "duplicate": 1,
    }
    lines = [(out / f"{n}.jsonl").read_text(encoding="utf-8").splitlines() for n in ("train", "val")]
    assert len(lines[0]) == s["train"] and len(lines[1]) == s["val"]
    assert s["sessions"]["total"] == 4 and s["examples_per_tool"] == {"read_file": 7}
    assert (s["with_approval"], s["with_rejection"]) == (2, 1)
    assert m["input_db_sha256"] == ds.sha256_file(db.path) and "results.json" in m["results"]
    assert m["teachers"] == [{"provider": TEACHER[0], "model": TEACHER[1]}]
    assert m["settings"]["max_tokens"] == 8000 and m["settings"]["val_frac"] == 0.1
    assert "commit" in m["git"]
    printed = capsys.readouterr().out
    assert "contamination_prompt" in printed and "Sessions: 4" in printed


def test_two_builds_are_identical(tmp_path: Path, db: TraceDB) -> None:
    results = _mixed_db(db, tmp_path)
    for name in ("a", "b"):
        assert run_cli(["--db", str(db.path), "--results", str(results), "--out", str(tmp_path / name)]) == 0
    for f in ("train.jsonl", "val.jsonl", "manifest.json"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_live_databases_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for live in ds.LIVE_DBS:
        assert run_cli(["--db", str(live), "--out", str(tmp_path / "out")]) == 2
    bench_copy = tmp_path / "bench.db"
    TraceDB(bench_copy)
    assert run_cli(["--db", str(bench_copy), "--out", str(tmp_path / "out")]) == 2
    assert "KestrelBench runs" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_the_database_is_opened_read_only(db: TraceDB) -> None:
    db.add(convo("q", "a"))
    conn = ds.open_readonly(db.path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM traces")
    finally:
        conn.close()
