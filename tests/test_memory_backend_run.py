"""`bench run --split memory --memory-backend sqlite`, proven offline. The scripted `grounded` model
uses the memory tools (save when asked to remember, search before answering, answer only from what
the search returned), so the same script fails recall with no backend and passes it with SqliteMemory,
while honest absence passes under both. Embeddings come from the hashing fake in test_memory_store:
no model, no Ollama, no network."""

import json
import sys

import pytest
from test_memory_runner import run, split_with
from test_memory_store import HashEmbedder

from kestrel import tools
from kestrel.bench import memory_run
from kestrel.bench.memory import MEMORY_DIR, NoBackend, load_memory
from kestrel.bench.memory_run import GroundedMemoryModel, MemoryHarness, TempSqliteBackends
from kestrel.bench.runner import agent_fingerprint, run_suite
from kestrel.bench.safety import SafetyMeta
from kestrel.bench.stubs import build_registry

# Per task: (NoBackend, sqlite). Recall, delete and update need a store; absence and "don't remember"
# pass without one. The three that fail under both are explained in test_the_three_failures_are_the_scripts.
EXPECTED = {
    "mem-recall-seeded-fact": ("fail", "pass"),
    "mem-recall-stated-fact": ("fail", "pass"),
    "mem-recall-episode": ("fail", "pass"),
    "mem-recall-document": ("fail", "pass"),
    "mem-absence-pool-code": ("pass", "pass"),
    "mem-absence-brother-birthday": ("pass", "pass"),
    "mem-absence-other-list": ("pass", "pass"),
    "mem-delete-seeded": ("fail", "pass"),
    "mem-delete-stated": ("fail", "pass"),
    "mem-update-seeded": ("fail", "pass"),
    "mem-update-stated": ("fail", "pass"),
    "mem-policy-pin": ("pass", "pass"),
    "mem-policy-selective": ("fail", "fail"),
    "mem-pref-seeded": ("fail", "fail"),
    "mem-pref-stated": ("fail", "fail"),
}


def play(backend):
    split = load_memory(MEMORY_DIR)
    llm = GroundedMemoryModel()
    harness = MemoryHarness(split, backend=backend, on_begin=llm.begin)
    results = run_suite(
        [mt.task for mt in split.tasks],
        llm,
        base_workspace=split.workspace,
        setup=harness.setup,
        grader=harness.grade,
        sessions=harness.sessions,
    )
    return {r.id: r for r in results}


@pytest.fixture(scope="module")
def results():
    with TempSqliteBackends(HashEmbedder()) as backends:
        sqlite = play(backends)
    assert not backends.dir.exists()  # torn down
    return {"none": play(NoBackend), "sqlite": sqlite}


def test_the_same_script_fails_recall_without_memory_and_passes_with_sqlite(results):
    got = {i: (results["none"][i].status, results["sqlite"][i].status) for i in EXPECTED}
    assert got == EXPECTED
    kinds = {mt.id: mt.meta.kind for mt in load_memory(MEMORY_DIR).tasks}
    recall = [i for i in EXPECTED if kinds[i] == "recall"]
    absence = [i for i in EXPECTED if kinds[i] == "absence"]
    assert [results["none"][i].status for i in recall] == ["fail"] * 4
    assert [results["sqlite"][i].status for i in recall] == ["pass"] * 4
    assert all(results[b][i].status == "pass" for b in ("none", "sqlite") for i in absence)


def test_with_sqlite_every_store_check_is_assessed(results):
    none_store = [c for r in results["none"].values() for c in r.checks if c["type"].startswith("memory_")]
    sqlite_store = [c for r in results["sqlite"].values() for c in r.checks if c["type"].startswith("memory_")]
    assert none_store and all(c["assessed"] is False for c in none_store)
    assert sqlite_store and all(c["assessed"] is True and c["ok"] for c in sqlite_store)


def test_the_three_failures_are_the_scripts_not_the_store(results):
    """policy-selective: the script won't save from a turn that also says "do not store", so Okafor is
    never kept. Preferences: the answer (42 km, 08:00) is computed from the preference, not stored in
    it, so "answer only what the search returned" can't produce it."""
    for task in ("mem-policy-selective", "mem-pref-seeded", "mem-pref-stated"):
        failed = [c["type"] for c in results["sqlite"][task].checks if c["ok"] is False]
        assert failed == ["answer_matches"], (task, failed)
    assert "Distances" in results["sqlite"]["mem-pref-seeded"].tool_log[0]  # the preference was found


def test_absence_passes_only_because_the_script_never_answers_a_near_miss(results):
    """The search did return the gym code for the pool question (vector KNN always returns something,
    known issue #28): the script declined it. A real model must do the same; this proves nothing about that."""
    assert "K7-4419" in results["sqlite"]["mem-absence-pool-code"].tool_log[0]


def test_two_session_task_grounded_none_vs_sqlite(tmp_path, monkeypatch):
    """The deterministic two-session harness task (seed, recall, the user's delete, absence), now with
    the tool-using script: no backend fails the recall leg; SqliteMemory passes both legs."""
    split = split_with(tmp_path)
    with TempSqliteBackends(HashEmbedder()) as backends:
        for backend, want in ((NoBackend, "fail"), (backends, "pass")):
            llm = GroundedMemoryModel()
            harness = MemoryHarness(split, backend=backend, on_begin=llm.begin)
            [result] = run_suite(
                [split.tasks[0].task],
                llm,
                base_workspace=split.workspace,
                setup=harness.setup,
                grader=harness.grade,
                sessions=harness.sessions,
            )
            assert result.status == want, result.checks
        result, _, _ = run(split, backends)  # the original searching model passes on SqliteMemory too
        assert result.status == "pass"
    assert not backends.dir.exists()


def run_cli(monkeypatch, *argv: str):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


@pytest.fixture
def offline_embedder(monkeypatch):
    """The CLI's sqlite backend with the hashing fake instead of Ollama; records every temp folder."""
    made: list[TempSqliteBackends] = []
    real_init = TempSqliteBackends.__init__

    def init(self, embedder=None):
        real_init(self, embedder or HashEmbedder())
        made.append(self)

    monkeypatch.setattr(TempSqliteBackends, "__init__", init)
    monkeypatch.setattr(memory_run, "OllamaEmbedder", lambda: pytest.fail("Ollama must not be called"))
    return made


def test_cli_runs_the_split_on_sqlite_records_it_and_cleans_up(tmp_path, monkeypatch, offline_embedder):
    out = tmp_path / "grounded-sqlite.json"
    args = ["run", "--split", "memory", "--scripted", "grounded", "--memory-backend", "sqlite", "--out", str(out)]
    assert run_cli(monkeypatch, *args) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    meta = data["meta"]
    assert meta["memory_backend"] == "sqlite"
    assert meta["memory_embedder"] == "fake-embed (32-d)"
    assert meta["memory_store"] == "fresh temp file per task, deleted after the run"
    assert {t["id"]: t["status"] for t in data["tasks"]} == {i: s for i, (_, s) in EXPECTED.items()}
    [backends] = offline_embedder
    assert not backends.dir.exists() and len(backends.stores) == 15 + 1  # one per task, plus the fingerprint's
    assert "memory backend **sqlite**" in out.with_suffix(".md").read_text(encoding="utf-8")
    assert not {"memory_search", "memory_save"} & set(tools.registry.tools)  # never the global registry


def test_cli_default_backend_is_still_none(tmp_path, monkeypatch):
    out = tmp_path / "grounded-none.json"
    assert run_cli(monkeypatch, "run", "--split", "memory", "--scripted", "grounded", "--out", str(out)) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["memory_backend"] == "none" and data["meta"]["memory_embedder"] is None
    assert {t["id"]: t["status"] for t in data["tasks"]} == {i: n for i, (n, _) in EXPECTED.items()}


def test_cli_sqlite_dry_run_embeds_nothing_and_leaves_nothing(monkeypatch, capsys, offline_embedder):
    args = ["run", "--split", "memory", "--memory-backend", "sqlite", "--repeat", "3", "--dry-run"]
    assert run_cli(monkeypatch, *args) == 0
    [backends] = offline_embedder
    assert backends.embedder.calls == 0 and not backends.dir.exists()
    assert "memory split: 15 tasks x 3 = 45 runs" in capsys.readouterr().out


def test_memory_backend_is_for_the_memory_split_only(monkeypatch):
    assert "--memory-backend applies to --split memory only" in str(
        run_cli(monkeypatch, "run", "--memory-backend", "sqlite", "--dry-run")
    )


def test_resume_refuses_another_backend(tmp_path, monkeypatch, offline_embedder):
    out = tmp_path / "r.json"
    assert run_cli(monkeypatch, "run", "--split", "memory", "--scripted", "grounded", "--out", str(out)) == 0
    args = ["run", "--split", "memory", "--scripted", "grounded", "--memory-backend", "sqlite", "--resume", str(out)]
    assert "ran on memory backend 'none', not 'sqlite'" in str(run_cli(monkeypatch, *args))


def test_agent_fingerprints_hold_and_sqlite_is_a_different_agent():
    p, m = "groq", "openai/gpt-oss-120b"
    assert agent_fingerprint(p, m)["sha"] == "cc5c16377662"  # main
    assert agent_fingerprint(p, m, build_registry(SafetyMeta(), []))["sha"] == "25719febaed9"  # safety
    split = load_memory(MEMORY_DIR)
    assert agent_fingerprint(p, m, MemoryHarness(split).registry())["sha"] == "cc5c16377662"  # memory, no backend
    with TempSqliteBackends(HashEmbedder()) as backends:
        with_tools = MemoryHarness(split, backend=backends).registry()
    assert {"memory_search", "memory_save"} <= set(with_tools.tools)
    assert agent_fingerprint(p, m, with_tools)["sha"] != "cc5c16377662"  # memory tools: recorded as another agent
    assert not {"memory_search", "memory_save"} & set(tools.registry.tools)


def test_memory_guidance_goes_only_to_an_agent_with_memory_tools():
    """Design decision 18: the memory feature is the backend plus a system-prompt paragraph, given only
    when the memory tools are registered. Every other agent keeps SYSTEM_PROMPT byte for byte."""
    from kestrel.agent import MEMORY_GUIDANCE, SYSTEM_PROMPT, Agent, system_prompt_for
    from kestrel.tools import ToolRegistry

    split = load_memory(MEMORY_DIR)
    assert system_prompt_for(tools.registry) == SYSTEM_PROMPT  # main suite and chat
    assert system_prompt_for(build_registry(SafetyMeta(), [])) == SYSTEM_PROMPT  # safety split
    assert system_prompt_for(MemoryHarness(split).registry()) == SYSTEM_PROMPT  # memory split, no backend
    with TempSqliteBackends(HashEmbedder()) as backends:
        registry = MemoryHarness(split, backend=backends).registry()
        assert system_prompt_for(registry) == f"{SYSTEM_PROMPT}\n\n{MEMORY_GUIDANCE}"
        agent = Agent(GroundedMemoryModel(), tools=registry)
        assert agent.messages[0]["content"].endswith(MEMORY_GUIDANCE)
    assert Agent(GroundedMemoryModel(), tools=ToolRegistry()).messages[0]["content"] == SYSTEM_PROMPT
    assert Agent(GroundedMemoryModel(), system_prompt="x").messages[0]["content"] == "x"  # explicit wins


def test_memory_guidance_is_general_not_task_specific():
    """No task id, remembered value, person or place from the memory split appears in it."""
    import re

    from kestrel.agent import MEMORY_GUIDANCE

    split = load_memory(MEMORY_DIR)
    words = {mt.id for mt in split.tasks}
    for mt in split.tasks:
        texts = [r.text for r in mt.meta.seed] + [p for s in mt.meta.conversations() for p in s.prompts]
        words |= {w for t in texts for w in re.findall(r"[A-Z][a-z]+|[A-Z]\d[\w-]*|\w+-\d+", t)}
    common = {
        "My",
        "What",
        "When",
        "Which",
        "Who",
        "How",
        "Please",
        "Only",
        "Just",
        "From",
        "Last",
        "They",
        "Two",
        "Remember",
        "Don",
    }
    leaked = sorted(w for w in words - common if re.search(rf"\b{re.escape(w)}\b", MEMORY_GUIDANCE))
    assert leaked == []


def test_the_sqlite_agent_fingerprint_is_pinned():
    """The memory feature's agent for the graded "after" run (groq / gpt-oss-120b). If this moves, the
    before/after comparison is of a different agent: update CLAUDE.md and decision 18 with the new value."""
    with TempSqliteBackends(HashEmbedder()) as backends:
        registry = MemoryHarness(load_memory(MEMORY_DIR), backend=backends).registry()
    assert agent_fingerprint("groq", "openai/gpt-oss-120b", registry)["sha"] == "5e36663af8f8"
