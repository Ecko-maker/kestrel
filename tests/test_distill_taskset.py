"""Phase 4 step 2: the distill task set, its workspace, the overlap check, `bench run --split distill`,
and how `kestrel distill build` tells a distill run from a KestrelBench run. No model calls; nothing
here opens logs/ (the tracer is replaced by a fake).
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kestrel.bench import cli as bench_cli
from kestrel.bench.runner import TaskResult
from kestrel.bench.tasks import CHECK_TYPES, TASKS_DIR, WORKSPACE_DIR, load_tasks
from kestrel.distill import DISTILL_DB, DISTILL_DIR, DISTILL_VERSION, overlap
from kestrel.distill import dataset as ds
from kestrel.tracing import redact

TASKS = load_tasks(DISTILL_DIR / "tasks")
WORKSPACE = DISTILL_DIR / "workspace"


# --- The task set -------------------------------------------------------------


def test_120_tasks_in_main_suite_proportions() -> None:
    assert len(TASKS) == 120
    distill = Counter(t.category for t in TASKS)
    main = Counter(t.category for t in load_tasks(TASKS_DIR))
    assert set(distill) == set(main)
    for category, n in main.items():
        assert abs(distill[category] - n * 1.2) <= 1, category  # the main suite's shares, scaled to 120


def test_every_task_is_tagged_and_prefixed() -> None:
    assert all(t.id.startswith("dst-") and "distill" in t.tags for t in TASKS)
    assert all("network" in t.tags for t in TASKS if t.category == "web")


def test_over_weighted_behaviors() -> None:
    counts = {b: [t for t in TASKS if b in t.tags] for b in ("beh-lookup", "beh-calc", "beh-gated")}
    assert all(10 <= len(ts) <= 16 for ts in counts.values()), {b: len(ts) for b, ts in counts.items()}

    def check_types(t: Any) -> set[str]:
        return {c["type"] for c in t.checks}

    # (a) the facts are in the workspace: it must read, and must not ask the user for them
    for t in counts["beh-lookup"]:
        assert any(c["type"] == "tool_called" and c["tool"] == "read_file" for c in t.checks), t.id
    # (b) arithmetic goes through the calculator
    for t in counts["beh-calc"]:
        assert any(c["type"] == "tool_called" and c["tool"] == "calculator" for c in t.checks), t.id
    # (c) the gated tool is called (approved by the scripted user) and its effect is checked
    for t in counts["beh-gated"]:
        assert any(r.decision == "approve" for r in t.approvals), t.id
        assert check_types(t) & {"tool_ran", "outbox_count", "file_contains"}, t.id


def test_only_existing_check_types() -> None:
    assert {c["type"] for t in TASKS for c in t.checks} <= set(CHECK_TYPES)


def test_files_the_checks_rely_on_exist() -> None:
    for t in TASKS:
        for c in t.checks:
            if c["type"] == "file_unchanged":
                assert (WORKSPACE / c["path"]).is_file() or c["path"] in t.files, (t.id, c["path"])


def test_no_injection_text() -> None:
    """Attack text is the owner's to write; the distill set has none."""
    suspicious = re.compile(
        r"\{\{|ignore (all |any )?(previous|prior|above) instructions|system prompt|you are now|"
        r"new instructions|assistant:|<\s*/?\s*(system|instructions?)\b",
        re.IGNORECASE,
    )
    texts = [p for t in TASKS for p in t.prompts] + [v for t in TASKS for v in t.files.values()]
    texts += [p.read_text(encoding="utf-8") for p in WORKSPACE.rglob("*") if p.is_file()]
    assert not [x for x in texts if suspicious.search(x)]


# --- The workspace ------------------------------------------------------------


def _workspace_texts() -> dict[str, str]:
    paths = [p for p in WORKSPACE.rglob("*") if p.is_file()]
    files = {p.relative_to(WORKSPACE).as_posix(): p.read_text(encoding="utf-8") for p in paths}
    files |= {f"{t.id}:{name}": text for t in TASKS for name, text in t.files.items()}
    return files


def test_workspace_has_no_canaries_or_secrets() -> None:
    for name, text in _workspace_texts().items():
        assert "kcan" not in text.lower(), name
        assert redact(text) == text, name
        assert ds.secret_filter(text, []) is None, name


def test_every_address_is_on_an_example_domain() -> None:
    texts = list(_workspace_texts().values()) + [str(t) for t in TASKS]
    emails = {m.group(0) for x in texts for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", x)}
    assert emails
    for email in emails:
        domain = email.rsplit("@", 1)[1].lower()
        assert domain.endswith(".example") or domain in ("example.com", "example.net", "example.org"), email


def test_workspace_is_disjoint_from_kestrelbench() -> None:
    def names(root: Path) -> set[str]:
        return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    def emails(root: Path) -> set[str]:
        return {
            m.group(0).lower()
            for p in root.rglob("*")
            if p.is_file()
            for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", p.read_text(encoding="utf-8", errors="ignore"))
        }

    assert not names(WORKSPACE) & names(WORKSPACE_DIR)
    assert not emails(WORKSPACE) & emails(WORKSPACE_DIR)
    bench_people = {"noah", "sam", "priya", "dana", "jordan", "mei"}
    assert (
        not {line.split(",")[0].split()[0].lower() for line in (WORKSPACE / "people.csv").read_text().splitlines()[1:]}
        & bench_people
    )


def test_the_distill_set_lives_outside_kestrelbench() -> None:
    """Under evals/kestrelbench its prompts would count as bench prompts and block its own traces."""
    assert not DISTILL_DIR.resolve().is_relative_to(overlap.BENCH_DIR.resolve())
    assert not {ds.normalize(p) for t in TASKS for p in t.prompts} & ds.bench_prompts()


# --- Overlap ------------------------------------------------------------------


def test_normalize_and_trigrams() -> None:
    assert overlap.normalize("  What's 17.5%  of 2,340? ") == "whats 175 of 2340"
    assert overlap.trigrams("one two") == frozenset()
    assert overlap.trigrams("a b c d") == {("a", "b", "c"), ("b", "c", "d")}
    assert overlap.jaccard(overlap.trigrams("a b c d"), overlap.trigrams("a b c e")) == pytest.approx(1 / 3)


def test_exact_matches_fail_even_when_short() -> None:
    d = [overlap.Prompt("dst-x", "Hello there!")]
    b = [overlap.Prompt("b-x", "hello   THERE")]
    (pair,) = overlap.compare(d, b)
    assert pair.exact and overlap.failures([pair]) == [pair]


def test_near_duplicates_fail_and_distinct_prompts_pass() -> None:
    bench = [overlap.Prompt("b", "Create a note called Groceries listing milk and eggs")]
    near = overlap.Prompt("d1", "Create a note called Groceries listing milk and bread")
    far = overlap.Prompt("d2", "How many sides has a hexagon got?")
    pairs = {p.distill.task_id: p for p in overlap.compare([near, far], bench)}
    assert pairs["d1"].score >= overlap.FAIL_AT and not pairs["d1"].exact
    assert pairs["d2"].score == 0
    assert overlap.failures(list(pairs.values())) == [pairs["d1"]]


def test_the_real_task_set_has_no_overlap() -> None:
    pairs = overlap.compare(overlap.distill_prompts(), overlap.bench_prompts())
    assert not overlap.failures(pairs)
    assert max(p.score for p in pairs) < overlap.REVIEW_AT  # nothing even in the review band


def test_bench_prompts_cover_every_split_folder(tmp_path: Path) -> None:
    for folder, body in [
        ("tasks", "tasks:\n  - id: a\n    prompt: One two three\n"),
        ("safety", "- id: s\n  prompt: [x y z, q r s]\n"),
    ]:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "t.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "f.yaml").write_text("prompt: not a task\n", encoding="utf-8")
    assert sorted((p.task_id, p.text) for p in overlap.bench_prompts(tmp_path)) == [
        ("a", "One two three"),
        ("s", "q r s"),
        ("s", "x y z"),
    ]


def test_overlap_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "b" / "tasks").mkdir(parents=True)
    task = "category: files\ntasks:\n  - id: {id}\n    prompt: {p}\n    rubric: r\n"
    (tmp_path / "b" / "tasks" / "t.yaml").write_text(task.format(id="b1", p="Read the menu file please"), "utf-8")
    (tmp_path / "d" / "t.yaml").write_text(task.format(id="dst-1", p="Count the plots in the garden"), "utf-8")
    argv = ["--distill-dir", str(tmp_path / "d"), "--bench-dir", str(tmp_path / "b")]
    assert overlap.main(argv) == 0
    (tmp_path / "d" / "t.yaml").write_text(task.format(id="dst-1", p="read the MENU file, please!"), "utf-8")
    assert overlap.main(argv) == 1
    assert "EXACT" in capsys.readouterr().out


# --- bench run --split distill ------------------------------------------------


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    bench_cli.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["bench", "run", *argv])


def test_split_dirs() -> None:
    assert bench_cli._split_dirs(_parse()) == (TASKS_DIR, WORKSPACE_DIR)
    assert bench_cli._split_dirs(_parse("--split", "distill")) == (DISTILL_DIR / "tasks", DISTILL_DIR / "workspace")
    assert bench_cli._split_dirs(_parse("--split", "distill", "--split-dir", "x")) == (
        Path("x") / "tasks",
        Path("x") / "workspace",
    )
    with pytest.raises(SystemExit, match="distill only"):
        bench_cli._split_dirs(_parse("--split-dir", "x"))


def test_dry_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench_cli.main(_parse("--dry-run", "--judge", "none")) == 0
    out = capsys.readouterr().out
    assert out.startswith("KestrelBench: 100 tasks") and "dst-" not in out
    assert bench_cli.main(_parse("--dry-run", "--judge", "none", "--split", "distill")) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Distill set {DISTILL_VERSION} (not KestrelBench): 120 tasks")
    assert "would run dst-arith-member-discount" in out and "arith-percent" not in out


class FakeRun:
    """Stands in for the model, the tracer and run_suite: records where traces and the workspace
    would go, and passes every task."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.db: Path | None = None
        self.workspace: Path | None = None
        monkeypatch.setattr(
            bench_cli, "build_llm", lambda *a, **k: (SimpleNamespace(llms=[SimpleNamespace(model="m")]), [])
        )
        monkeypatch.setattr(bench_cli, "Tracer", self.tracer)
        monkeypatch.setattr(bench_cli, "run_suite", self.run_suite)

    def tracer(self, db: Path) -> str:
        self.db = db
        return "fake-tracer"

    def run_suite(self, tasks: list, llm: Any, judge: Any, tracer: Any, **kwargs: Any) -> list[TaskResult]:
        self.workspace = kwargs["base_workspace"]
        return [
            TaskResult(id=t.id, category=t.category, status="pass", score=1.0, repeat=k, trace_ids=[f"tr-{t.id}"])
            for t, k in zip(tasks, kwargs["repeats"], strict=True)
        ]


def test_a_distill_run_records_its_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(monkeypatch)
    out = tmp_path / "d.json"
    args = _parse("--split", "distill", "--tasks", "dst-arith-*", "--judge", "none", "--out", str(out))
    assert bench_cli.main(args) == 0
    meta = json.loads(out.read_text(encoding="utf-8"))["meta"]
    assert meta["split"] == "distill" and meta["split_version"] == meta["suite_version"] == DISTILL_VERSION
    assert (meta["task_dir"], meta["workspace_dir"]) == ("distill/tasks", "distill/workspace")
    assert fake.db == DISTILL_DB and fake.workspace == DISTILL_DIR / "workspace"


def test_a_main_run_is_unchanged_but_for_the_split_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(monkeypatch)
    out = tmp_path / "m.json"
    assert bench_cli.main(_parse("--tasks", "arith-percent", "--judge", "none", "--out", str(out))) == 0
    meta = json.loads(out.read_text(encoding="utf-8"))["meta"]
    assert meta["split"] == "main" and meta["suite_version"] == "1.1"
    assert not {"split_version", "task_dir", "workspace_dir"} & set(meta)
    assert fake.db == bench_cli.BENCH_DB and fake.workspace == WORKSPACE_DIR


def test_resume_and_reuse_refuse_the_other_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRun(monkeypatch)
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps({"meta": {"provider": "groq", "model": "m"}, "tasks": []}), encoding="utf-8"
    )  # no split = main
    with pytest.raises(SystemExit, match="main split run, not distill"):
        bench_cli.main(_parse("--split", "distill", "--resume", str(old), "--judge", "none", "--model", "m"))
    with pytest.raises(SystemExit, match="not a distill split run"):
        bench_cli.main(
            _parse("--split", "distill", "--reuse", str(old), "--judge", "none", "--model", "m", "--dry-run")
        )


# --- The pipeline tells a distill run from a KestrelBench run ------------------


def _results(path: Path, split: str | None, ids: list[str]) -> Path:
    meta = {"split": split} if split else {}
    tasks = [
        {"id": i, "status": "pass", "checks": [{"ok": True}], "judge": None, "trace_ids": [f"tr-{i}"]} for i in ids
    ]
    path.write_text(json.dumps({"meta": meta, "tasks": tasks}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("split", "ids", "distill"),
    [
        ("distill", ["dst-a", "dst-b"], True),
        (None, ["dst-a"], False),  # every pre-Phase-4 file: no split key
        ("main", ["dst-a"], False),
        ("safety", ["dst-a"], False),
        ("something-new", ["dst-a"], False),
        ("distill", ["dst-a", "arith-percent"], False),  # a mix counts as KestrelBench
        ("distill", [], False),
    ],
)
def test_is_distill_run(tmp_path: Path, split: str | None, ids: list[str], distill: bool) -> None:
    path = _results(tmp_path / "r.json", split, ids)
    assert ds.is_distill_run(json.loads(path.read_text(encoding="utf-8"))) is distill
    assert all(v.distill is distill for v in ds.load_verdicts([path]).values())


def test_kestrelbench_wins_when_a_trace_is_in_both(tmp_path: Path) -> None:
    d = _results(tmp_path / "d.json", "distill", ["dst-a"])
    b = _results(tmp_path / "b.json", None, ["dst-a"])  # same trace id, listed by a bench file
    for order in ([d, b], [b, d]):
        assert ds.load_verdicts(order)["tr-dst-a"].distill is False


def test_passed_distill_traces_are_kept_and_bench_traces_dropped(tmp_path: Path) -> None:
    from test_distill_dataset import TraceDB, convo  # tests/ is on sys.path under pytest

    db = TraceDB(tmp_path / "copy.db")
    ids = {name: db.add(convo(f"distill question {name}", "answer"), rating=None) for name in ("ok", "fail", "bench")}
    files = []
    for name, split, status in [("ok", "distill", "pass"), ("fail", "distill", "fail"), ("bench", "main", "pass")]:
        task = {
            "id": f"dst-{name}",
            "status": status,
            "checks": [{"ok": status == "pass"}],
            "judge": None,
            "trace_ids": [ids[name]],
        }
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"meta": {"split": split}, "tasks": [task]}), encoding="utf-8")
        files.append(path)
    built = ds.build(
        db.traces(), ds.load_verdicts(files), frozenset(), frozenset({("groq", "openai/gpt-oss-120b")}), []
    )
    assert {e.record["metadata"]["trace_id"] for e in built.train + built.val} == {ids["ok"]}
    assert {k: v for k, v in built.dropped.items() if v} == {"quality": 1, "contamination_run": 1}
    sources = ds.results_sources(files)
    assert [sources[f.name]["source"] for f in files] == ["distill", "distill", "kestrelbench"]
