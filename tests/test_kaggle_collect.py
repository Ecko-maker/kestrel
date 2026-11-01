"""distill/kaggle_collect.py and distill/collect.ipynb (option c, known issue #72), offline: #72's slice, the
selection metrics and rule, the shard coverage check, the option c teachers in teachers.toml and the leak
filter on their traces, and that every notebook cell parses. No model, Ollama or Kaggle is touched."""

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from test_distill_dataset import TraceDB, convo

from kestrel.bench.stats import stratified_sample
from kestrel.bench.tasks import load_tasks
from kestrel.distill import dataset as ds

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kaggle_collect", ROOT / "distill" / "kaggle_collect.py")
assert spec is not None and spec.loader is not None
kc = importlib.util.module_from_spec(spec)
sys.modules["kaggle_collect"] = kc
spec.loader.exec_module(kc)

DISTILL = load_tasks(ROOT / "distill" / "tasks")


# --- #72's slice and candidates ---------------------------------------------------------------------------


def test_the_slice_is_72s_draw_from_the_check_graded_distill_tasks() -> None:
    checked = [t for t in DISTILL if not t.rubric]
    drawn = stratified_sample(checked, 20, category=lambda t: t.category, key=lambda t: t.id, seed=kc.SLICE_SEED)
    assert tuple(t.id for t in drawn) == kc.SLICE
    assert len(checked) == 93 and len(set(kc.SLICE)) == 20


def test_the_slice_is_never_kestrelbench() -> None:
    main = {t.id for t in load_tasks()}
    assert not set(kc.SLICE) & main and all(t.startswith("dst-") for t in kc.SLICE)


def test_the_candidates_are_72s() -> None:
    assert [c.name for c in kc.CANDIDATES] == ["gpt-oss:20b", "Qwen3-30B-A3B-Instruct-2507"]
    assert kc.candidate("kestrel-teacher-qwen3-30b-a3b").tag == "qwen3:30b-a3b-instruct-2507-q4_K_M"
    with pytest.raises(KeyError):
        kc.candidate("openai/gpt-oss-120b")


@pytest.mark.parametrize(
    ("ps", "fit"),
    [
        ({"size": 15 * 2**30, "size_vram": 15 * 2**30}, True),
        ({"size": 20 * 2**30, "size_vram": 18 * 2**30}, False),  # part of it on the CPU
        ({}, False),  # not loaded
    ],
)
def test_fit_means_nothing_on_the_cpu(ps: dict, fit: bool) -> None:
    assert kc.fits(ps) is fit


# --- teachers.toml and the leak filter ------------------------------------------------------------------------


SELECTION = ROOT / "distill" / "selection"


def commented_entries() -> str:
    """The commented-out entries of teachers.toml (option a and the candidate not selected), uncommented."""
    text = (ROOT / "src" / "kestrel" / "distill" / "teachers.toml").read_text(encoding="utf-8")
    lines = [
        line[2:]
        for line in text.splitlines()
        if line.startswith("# [[teacher]]") or line.startswith("# provider") or line.startswith("# model")
    ]
    return "\n".join(lines) + "\n"


def test_the_shipped_file_has_only_the_selected_option_c_teacher_active() -> None:
    winner = json.loads((SELECTION / "selection.json").read_text(encoding="utf-8"))["winner"]
    assert ds.load_teachers() == frozenset({("ollama", kc.candidate(winner).served_as)})
    assert ds.load_teachers() == frozenset({("ollama", "kestrel-teacher-qwen3-30b-a3b")})


def test_option_a_and_the_other_candidate_stay_loadable_but_inactive(tmp_path: Path) -> None:
    path = tmp_path / "teachers.toml"
    path.write_text(commented_entries(), encoding="utf-8")
    assert ds.load_teachers(path) == frozenset({("groq", "openai/gpt-oss-120b"), ("ollama", "gpt-oss:20b")})
    assert not any(ds.is_closed("ollama", c.served_as) for c in kc.CANDIDATES)


def test_the_shipped_teachers_keep_a_clean_qwen_trace_and_drop_a_leaked_one(tmp_path: Path) -> None:
    db = TraceDB(tmp_path / "copy.db")
    model = "kestrel-teacher-qwen3-30b-a3b"
    clean = db.add(convo("What's in a.txt?", "It says hello."), steps=[("ollama", model)])
    leaked = convo("What's in a.txt?", "It says <|im_end|> hello.")
    db.add(leaked, steps=[("ollama", model)])
    db.add(convo("What's in a.txt?", "It says hello."), steps=[("groq", "openai/gpt-oss-120b")])  # option a, inactive
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    built = ds.build(db.traces(), {}, frozenset(), ds.load_teachers(), tools)
    assert {e.record["metadata"]["trace_id"] for e in built.train + built.val} == {clean}
    assert {k: v for k, v in built.dropped.items() if v} == {"teacher": 1, "harmony_leak_text": 1}


@pytest.mark.parametrize("model", [c.served_as for c in kc.CANDIDATES])
def test_the_leak_filter_drops_a_leaked_option_c_trace_and_keeps_a_clean_one(tmp_path: Path, model: str) -> None:
    db = TraceDB(tmp_path / "copy.db")
    clean = db.add(convo("What's in a.txt?", "It says hello."), steps=[("ollama", model)])
    leaked = convo("What's in a.txt?", "It says hello.")
    leaked[2]["tool_calls"][0]["function"]["name"] = "read_file<|channel|>commentary"
    db.add(leaked, steps=[("ollama", model)])
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    built = ds.build(db.traces(), {}, frozenset(), frozenset({("ollama", model)}), tools)
    assert {e.record["metadata"]["trace_id"] for e in built.train + built.val} == {clean}
    assert {k: v for k, v in built.dropped.items() if v} == {"harmony_leak_tool_name": 1}


# --- the selection --------------------------------------------------------------------------------------------


def row(
    task: str, *, ok: bool = True, status: str | None = None, log: list[str] | None = None, seconds: float = 30
) -> dict[str, Any]:
    return {
        "id": task,
        "status": status or ("pass" if ok else "fail"),
        "checks": [{"type": "answer_matches", "ok": ok}],
        "tool_log": log or ['- read_file({"path": "a.txt"}) [ran] -> hello'],
        "latency_ms": seconds * 1000,
        "trace_ids": [],
        "answer": "done",
    }


def results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"meta": {"split": "distill", "judge": None}, "tasks": rows}


def candidate_metrics(
    name: str, fails: int = 0, *, leaks: int = 0, seconds: float = 30, fit: bool | None = True
) -> Any:
    rows = [row(t, ok=i >= fails, seconds=seconds) for i, t in enumerate(kc.SLICE)]
    for r in rows[:leaks]:
        r["answer"] = "ok<|end|>"
    return kc.metrics(name, results(rows), None, fit)


def test_the_metrics_count_checks_errors_invalid_calls_leaks_and_time() -> None:
    rows = [row(t) for t in kc.SLICE]
    rows[0] = row(kc.SLICE[0], status="fail")  # the judge said no, every check ok: still a check pass
    rows[1] = row(kc.SLICE[1], status="error")
    rows[2] = row(
        kc.SLICE[2],
        log=[
            "- calculator<|channel|>commentary({}) [not run (refused)] -> Error: unknown tool "
            "'calculator<|channel|>commentary'. Available tools: calculator"
        ],
    )
    rows[3] = row(kc.SLICE[3], log=["- read_file({}) [ran] -> Error: no such file"], seconds=90)  # ran: a valid call
    m = kc.metrics("x", results(rows), None, True)
    assert (m.runs, m.check_passes, m.errored) == (20, 19, 1)
    assert (m.tool_calls, m.invalid_calls, m.valid_calls) == (20, 1, 19)
    assert m.leak_runs == 1 and m.leak_reasons == {"harmony_leak_text": 1}
    assert m.mean_seconds == pytest.approx((18 * 30 + 90 + 30) / 20, abs=0.1)
    assert m.problems == []


def test_a_leak_is_read_from_the_stored_conversation_when_traces_are_given() -> None:
    rows = [row(t) for t in kc.SLICE]
    rows[0]["trace_ids"] = ["t1"]
    messages = convo("q", "a")
    messages[2]["tool_calls"][0]["function"]["name"] = "read_file<|call|>"
    m = kc.metrics("x", results(rows), [{"trace_id": "t1", "messages": json.dumps(messages)}], True)
    assert m.leak_reasons == {"harmony_leak_tool_name": 1}


def test_anything_but_the_slice_once_is_not_the_preregistered_design() -> None:
    rows = [row(t) for t in kc.SLICE[1:]] + [row(kc.SLICE[1]), row("dst-arith-cube-root")]
    problems = kc.metrics("x", results(rows), None, True).problems
    assert any("missing" in p for p in problems) and any("not in the slice" in p for p in problems)
    assert any("more than one run" in p for p in problems)


@pytest.mark.parametrize(
    ("a", "b", "winner", "why"),
    [
        (dict(fails=2), dict(fails=6), "A", "higher check-pass rate"),
        (dict(fails=6), dict(fails=2), "B", "higher check-pass rate"),
        (dict(fails=2, leaks=3), dict(fails=4, leaks=1), "B", "fewer Harmony leaks"),  # within 2 tasks
        (dict(fails=3, seconds=60), dict(fails=3, seconds=40), "B", "less time per task"),
        (dict(fails=0, fit=False), dict(fails=10), "B", "A is out (doesn't fit"),
        (dict(fails=0, seconds=200), dict(fails=10), "B", "over the 180 s limit"),
        (dict(fails=0, fit=None), dict(fails=10), "B", "GPU fit not measured"),
        (dict(fit=False), dict(seconds=181), None, "both candidates are out"),
        (dict(fails=3), dict(fails=3), None, "a tie on every criterion"),
    ],
)
def test_72s_rule(a: dict, b: dict, winner: str | None, why: str) -> None:
    won, reason = kc.decide(candidate_metrics("A", **a), candidate_metrics("B", **b))
    assert won == winner and why in reason


def test_valid_tool_calls_break_a_tie_after_leaks() -> None:
    a, b = candidate_metrics("A", fails=1), candidate_metrics("B", fails=2)
    b.invalid_calls = 3
    assert kc.decide(a, b) == ("A", "check-pass within 2 tasks (19 vs 18); more valid tool calls")


def test_the_report_names_the_winner_and_every_number() -> None:
    text = kc.report(candidate_metrics("A", fails=1), candidate_metrics("B", fails=8, fit=False))
    assert "**Winner: A**" in text and "| B | 12/20 (60%) |" in text and "doesn't fit" in text


def test_select_reads_two_compare_folders_including_one_that_never_ran(tmp_path: Path, capsys) -> None:
    ran, skipped = tmp_path / "a", tmp_path / "b"
    for folder, name, fit in ((ran, "gpt-oss:20b", True), (skipped, "Qwen3-30B-A3B-Instruct-2507", False)):
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({"teacher": {"name": name}, "gpu": {"fits": fit}}))
    (ran / "results.json").write_text(json.dumps(results([row(t) for t in kc.SLICE])))
    assert (
        kc.main(["select", "--candidate", str(ran), "--candidate", str(skipped), "--out", str(tmp_path / "out")]) == 0
    )
    assert "**Winner: gpt-oss:20b**" in capsys.readouterr().out
    assert json.loads((tmp_path / "out" / "selection.json").read_text())["winner"] == "gpt-oss:20b"


# --- the shards -----------------------------------------------------------------------------------------------


def shard(path: Path, i: int, n: int, ids: list[str], *, model: str = "gpt-oss:20b", status: str = "pass") -> Path:
    meta = {"split": "distill", "split_version": "d1.2", "provider": "ollama", "model": model, "judge": None,
            "agent": {"sha": "abc"}, "shard": f"{i}/{n}"}  # fmt: skip
    path.write_text(json.dumps({"meta": meta, "tasks": [{"id": t, "status": status} for t in ids]}))
    return path


def test_complete_shards_pass_the_coverage_check(tmp_path: Path) -> None:
    ids = [t.id for t in DISTILL]
    files = [shard(tmp_path / "1.json", 1, 2, ids[::2]), shard(tmp_path / "2.json", 2, 2, ids[1::2])]
    assert kc.check_shards(files) == []
    assert kc.main(["check-shards", *map(str, files)]) == 0


def test_missing_duplicated_skipped_or_mixed_shards_are_problems(tmp_path: Path) -> None:
    ids = [t.id for t in DISTILL]
    assert any("missing shard" in p for p in kc.check_shards([shard(tmp_path / "1.json", 1, 2, ids)]))
    mixed = [shard(tmp_path / "a.json", 1, 2, ids[::2]), shard(tmp_path / "b.json", 2, 2, ids[1::2], model="other")]
    assert any("differ in model" in p for p in kc.check_shards(mixed))
    skipped = [
        shard(tmp_path / "c.json", 1, 2, ids[::2]),
        shard(tmp_path / "d.json", 2, 2, ids[1::2], status="skipped"),
    ]
    assert any("is skipped" in p for p in kc.check_shards(skipped))
    short = [shard(tmp_path / "e.json", 1, 2, ids[::2]), shard(tmp_path / "f.json", 2, 2, ids[1:-2:2])]
    assert any("distill task(s) missing" in p for p in kc.check_shards(short))


def test_shard_names_and_states(tmp_path: Path) -> None:
    assert (
        kc.shard_file("kestrel-teacher-qwen3-30b-a3b", 3, 8)
        == "distill-d1.2-kestrel-teacher-qwen3-30b-a3b-shard-3-of-8.json"
    )
    assert kc.shard_file("gpt-oss:20b", 1, 8) == "distill-d1.2-gpt-oss-20b-shard-1-of-8.json"
    assert kc.shard_state(tmp_path / "none.json") == "missing"
    assert kc.shard_state(shard(tmp_path / "ok.json", 1, 1, ["dst-x"])) == "complete"
    assert kc.shard_state(shard(tmp_path / "part.json", 1, 1, ["dst-x"], status="skipped")) == "partial"


def test_the_manifest_records_teacher_digest_and_gpus() -> None:
    m = kc.manifest(
        mode="collect", teacher=kc.CANDIDATES[0], digest="sha256:abc", ollama_version="0.40.13",
        gpus=[{"name": "Tesla T4", "memory": "15360 MiB"}] * 2, fit=True, commit="4290fc6",
        files=["results/x.json"], started="s", finished="f", agent="35fac5ba6277",
    )  # fmt: skip
    assert m["teacher"]["served_as"] == "gpt-oss:20b" and m["teacher"]["digest"] == "sha256:abc"
    assert m["gpu"]["fits"] is True and m["gpu"]["devices"][0]["name"] == "Tesla T4"


# --- the pinned selection (distill/selection/, 2026-10-31) ----------------------------------------------------

# Byte for byte from the Kaggle compare run; never update a hash: restore the file from git instead.
PINNED_SELECTION = {
    "selection.md": "4e67a92713f1e590df4defef058ed59eea2e90094a03b974f6fe616aeb6c2e3f",
    "selection.json": "5f34824d6d35a1587c55f54c9684d5354e5c84531c7918bc8e911f09ed0c67cf",
    "manifest-gpt-oss-20b.json": "1f3707a150ec928a932c42e001ccc1e4b9269dea5883f50fcf0710415b3191d3",
    "manifest-kestrel-teacher-qwen3-30b-a3b.json": "01da2c433e6869d27af2790d278cfcbf0df87516bb6a34c56b0bd9949756b135",
}


@pytest.mark.parametrize("name", sorted(PINNED_SELECTION))
def test_the_pinned_selection_evidence_is_unchanged(name: str) -> None:
    assert hashlib.sha256((SELECTION / name).read_bytes()).hexdigest() == PINNED_SELECTION[name]


def test_the_selection_readme_lists_the_pinned_hashes() -> None:
    readme = (SELECTION / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED_SELECTION.values())


def test_72s_rule_on_the_pinned_numbers_gives_the_pinned_winner() -> None:
    sel = json.loads((SELECTION / "selection.json").read_text(encoding="utf-8"))
    a, b = (kc.Metrics(**c) for c in sel["candidates"])
    assert tuple(sel["slice"]) == kc.SLICE and a.runs == b.runs == 20
    assert kc.decide(a, b) == (sel["winner"], sel["why"]) == ("Qwen3-30B-A3B-Instruct-2507", sel["why"])
    for c in kc.CANDIDATES:
        m = json.loads((SELECTION / f"manifest-{c.served_as.replace(':', '-')}.json").read_text(encoding="utf-8"))
        assert m["mode"] == "compare" and m["teacher"]["served_as"] == c.served_as and m["gpu"]["fits"] is True
        assert m["commit"].startswith("5ec57a7") and len(m["gpu"]["devices"]) == 2


# --- the pinned collection d1.2-pass1 (distill/collection/, 2026-11-01) --------------------------------------

COLLECTION = ROOT / "distill" / "collection"
PINNED_COLLECTION = {
    "collect-manifest-5cce69cdee15.json": "cf2eb646a7c5043ec6422bb11c4a1ad812da87dc702b9e34a14cbf3edf2668bc",
    "build-manifest-d1.2-pass1.json": "694e2189d277520c2ba4d85ca3be21499c4269a29f92910e930081a56f74b0a8",
}


@pytest.mark.parametrize("name", sorted(PINNED_COLLECTION))
def test_the_pinned_collection_manifests_are_unchanged(name: str) -> None:
    assert hashlib.sha256((COLLECTION / name).read_bytes()).hexdigest() == PINNED_COLLECTION[name]


def test_the_collection_readme_lists_the_pinned_hashes() -> None:
    readme = (COLLECTION / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED_COLLECTION.values())


def test_the_pinned_collection_and_build_agree() -> None:
    run = json.loads((COLLECTION / "collect-manifest-5cce69cdee15.json").read_text(encoding="utf-8"))
    build = json.loads((COLLECTION / "build-manifest-d1.2-pass1.json").read_text(encoding="utf-8"))
    selected = json.loads((SELECTION / "manifest-kestrel-teacher-qwen3-30b-a3b.json").read_text(encoding="utf-8"))
    assert run["mode"] == "collect" and run["gpu"]["fits"] is True
    assert run["teacher"]["digest"] == selected["teacher"]["digest"]  # the model #72 selected, not a re-pull
    assert build["teachers"] == [{"provider": "ollama", "model": run["teacher"]["served_as"]}]
    assert build["git"] == {"commit": run["commit"], "dirty": False}
    assert sorted(build["results"]) == sorted(Path(f).name for f in run["files"]) and len(run["files"]) == 8
    assert all(r["source"] == "distill" for r in build["results"].values())
    s = build["stats"]
    assert (s["traces_seen"], s["kept"], s["train"], s["val"]) == (129, 82, 74, 8)
    assert s["kept"] + sum(s["dropped"].values()) == s["traces_seen"]
    for k in ("teacher", "contamination_run", "contamination_prompt", "near_duplicate"):  # provenance and overlap
        assert s["dropped"][k] == 0
    assert all(v == 0 for k, v in s["dropped"].items() if k.startswith("harmony_leak_"))


# --- the notebook ---------------------------------------------------------------------------------------------


def test_the_notebook_parses_and_has_its_settings() -> None:
    nb = json.loads((ROOT / "distill" / "collect.ipynb").read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4 and nb["metadata"]["kaggle"]["isInternetEnabled"] is True
    code = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    for src in code:
        ast.parse(src)
    settings = code[0]
    for name in ("MODE", "TEACHER", "SHARDS", "OLLAMA_VERSION", "UV_VERSION", "NUM_CTX"):
        assert f"{name} = " in settings
    assert 'MODE = "compare"' in settings  # the safe default: the selection, not a full pass
    everything = "\n".join(code)
    assert (
        "--judge" in everything
        and '"none"' in everything
        and "KestrelBench" not in everything.replace("Never KestrelBench", "")
    )
    assert "GROQ" not in everything and "GEMINI" not in everything  # no hosted provider is ever configured
