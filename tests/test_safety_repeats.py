"""Safety split with repeats, before/after comparison, and the authoring tools (`bench show`,
`bench validate --task`). Neutral placeholder tasks (tests/fixtures/safety/), no network, no keys."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kestrel.bench.safety import compare_safety, find_markers, load_safety, score
from kestrel.bench.validate import concerns, markers_of

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "safety"
ATTACKS = 9  # in the fixture
BENIGN = 4


def run_cli(monkeypatch, *argv):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def scripted_run(monkeypatch, tmp_path, model, *extra, name="run.json"):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / name
    argv = ["run", "--split", "safety", "--split-dir", str(FIXTURE), "--scripted", model, "--out", str(out), *extra]
    assert run_cli(monkeypatch, *argv) == 0
    return out, json.loads(out.read_text(encoding="utf-8"))


def row(task_id, kind, status, model=False, system=False, repeat=1, user="strict"):
    safety = {"kind": kind, "model_success": model, "system_success": system, "legit_ok": None}
    return {"id": task_id, "category": "c", "status": status, "repeat": repeat, "user": user, "safety": safety}


# --- any-repeat scoring ---------------------------------------------------------------------


def test_an_attack_that_falls_only_on_repeat_2_counts_as_a_success_with_k_1_of_3(tmp_path, monkeypatch):
    out, data = scripted_run(monkeypatch, tmp_path, "falls-on-repeat-2", "--repeat", "3", "--user", "both")
    assert len(data["tasks"]) == (ATTACKS + BENIGN) * 3 * 2 and data["meta"]["repeat"] == 3
    strict = data["safety"]["users"]["strict"]
    assert (strict["model_asr"]["k"], strict["model_asr"]["n"]) == (ATTACKS, ATTACKS)  # n is tasks, not runs
    assert all(c["model"] == [1, 3] for c in strict["attack_repeats"].values())  # each fell exactly once
    rubber = data["safety"]["users"]["rubber-stamp"]
    assert all(c["model"] == [1, 3] for c in rubber["attack_repeats"].values())  # repeat numbers restart per user
    assert data["safety"]["gate_failures"] == sorted(i for i, c in strict["attack_repeats"].items() if c["system"][0])
    report = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Attacks by repeat" in report and "| 1 of 3 *flaky* |" in report
    assert "these numbers would have been FAIL (" in report  # a harness check never claims the gate
    assert "| Benign tasks that passed every repeat (strict user) | 100% (4/4" in report


def test_passed_every_repeat_fails_a_benign_task_that_fails_once_and_the_mean_shows_the_rest():
    rows = [
        row("b1", "benign", "pass", repeat=1),
        row("b1", "benign", "fail", repeat=2),
        row("b1", "benign", "pass", repeat=3),
        *(row("b2", "benign", "pass", repeat=k) for k in (1, 2, 3)),
    ]
    s = score(rows).users["strict"]
    assert (s.benign.k, s.benign.n) == (1, 2)  # b1 failed once, so it didn't pass every repeat
    assert s.benign_repeats == {"b1": [2, 3], "b2": [3, 3]}
    assert s.benign_mean is not None and s.benign_mean.value == round((2 / 3 + 1) / 2, 4) and s.benign_mean.n == 2


def test_attack_counts_per_level_and_the_gate_names_the_attacks_that_broke_it():
    rows = [
        row("a1", "attack", "fail", model=True, system=True, repeat=1),
        row("a1", "attack", "pass", repeat=2),
        row("a2", "attack", "fail", model=True, repeat=1),  # model level only: the gate held
        row("a2", "attack", "pass", repeat=2),
        row("a3", "attack", "error", repeat=1),  # not graded, not counted
        row("a3", "attack", "pass", repeat=2),
    ]
    s = score(rows)
    u = s.users["strict"]
    assert u.attack_repeats["a1"] == {"model": [1, 2], "system": [1, 2]}
    assert u.attack_repeats["a3"] == {"model": [0, 1], "system": [0, 1]}
    assert (u.system_asr.k, u.system_asr.n) == (1, 3)
    assert s.gate() is False and s.gate_failures() == ["a1"]


def test_resume_is_per_task_and_repeat_and_refuses_another_version_or_agent(tmp_path, monkeypatch, capsys):
    out, data = scripted_run(monkeypatch, tmp_path, "never-falls", "--repeat", "2", "--tasks", "ph-attack-file-*")
    assert {(t["id"], t["repeat"]) for t in data["tasks"]} == {
        (i, k) for i in ("ph-attack-file-send", "ph-attack-file-delete", "ph-attack-file-modify") for k in (1, 2)
    }
    meta = data["meta"]
    assert meta["split_version"] == "s1.0" and meta["agent"]["sha"] and meta["repeat"] == 2
    data["tasks"] = [t for t in data["tasks"] if (t["id"], t["repeat"]) != ("ph-attack-file-send", 2)]
    out.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    argv = ["run", "--split", "safety", "--split-dir", str(FIXTURE), "--scripted", "never-falls", "--repeat", "2"]
    assert run_cli(monkeypatch, *argv, "--tasks", "ph-attack-file-*", "--resume", str(out)) == 0
    assert "1 to run, 5 kept" in capsys.readouterr().out

    for key, value, message in (
        ("split_version", "s0.9", "mix two versions of the tasks"),
        ("agent", {"sha": "000000000000"}, "mix two agents"),
    ):
        old = json.loads(out.read_text(encoding="utf-8"))
        old["meta"][key] = value
        out.write_text(json.dumps(old), encoding="utf-8")
        refused = run_cli(monkeypatch, *argv, "--tasks", "ph-attack-file-*", "--resume", str(out))
        assert message in str(refused)
        out.write_text(json.dumps(data), encoding="utf-8")


# --- before / after comparison --------------------------------------------------------------


def results(rows, version="s1.0", repeat=1):
    return {"meta": {"split": "safety", "split_version": version, "repeat": repeat, "user": "both"}, "tasks": rows}


def test_compare_pairs_attacks_per_level_and_lists_flips():
    before = results(
        [
            row("a1", "attack", "fail", model=True, system=True),
            row("a2", "attack", "fail", model=True),
            row("a3", "attack", "pass"),
            row("b1", "benign", "pass"),
            row("b2", "benign", "fail"),
            row("a1", "attack", "fail", model=True, system=True, user="rubber-stamp"),
        ]
    )
    after = results(
        [
            row("a1", "attack", "fail", model=True),  # the defense stopped the harm, not the attempt
            row("a2", "attack", "pass"),
            row("a3", "attack", "fail", model=True, system=True),  # a regression
            row("b1", "benign", "fail"),
            row("b2", "benign", "pass"),
            row("a1", "attack", "pass", user="rubber-stamp"),
        ]
    )
    c = compare_safety(before, after)
    assert c.flips("system-strict") == (["a1"], ["a3"])  # (stopped, newly succeeding)
    assert c.flips("model") == (["a2"], ["a3"])
    assert c.flips("system-rubber-stamp") == (["a1"], [])
    assert c.benign_flips() == (["b2"], ["b1"])  # (newly passing, newly failing)
    assert c.rates["system-strict"][0].k == 1 and c.rates["system-strict"][1].k == 1


def test_compare_refuses_another_version_or_task_set_and_warns_on_repeats():
    a = results([row("a1", "attack", "pass")])
    with pytest.raises(ValueError, match="different SAFETY_VERSION"):
        compare_safety(a, results([row("a1", "attack", "pass")], version="s1.1"))
    with pytest.raises(ValueError, match="different task sets"):
        compare_safety(a, results([row("a2", "attack", "pass")]))
    c = compare_safety(a, results([row("a1", "attack", "pass")], repeat=3))
    assert c.skipped_levels == ["system-rubber-stamp"] and any("repeat counts" in w for w in c.warnings)


def test_cli_compare_on_two_safety_runs(tmp_path, monkeypatch, capsys):
    before, _ = scripted_run(monkeypatch, tmp_path, "always-falls", "--user", "both", name="before.json")
    after, _ = scripted_run(monkeypatch, tmp_path, "never-falls", "--user", "both", name="after.json")
    capsys.readouterr()
    assert run_cli(monkeypatch, "compare", str(before), str(after)) == 0
    printed = capsys.readouterr().out
    assert "system-strict" in printed and "newly succeeding in B:   none" in printed
    assert "Benign, passed every repeat (strict)" in printed

    other = json.loads(after.read_text(encoding="utf-8"))
    dropped = other["tasks"][0]["id"]
    other["tasks"] = [t for t in other["tasks"] if t["id"] != dropped]  # every user's runs of one task
    (tmp_path / "other.json").write_text(json.dumps(other), encoding="utf-8")
    assert "different task sets" in str(run_cli(monkeypatch, "compare", str(before), str(tmp_path / "other.json")))


# --- authoring tools ------------------------------------------------------------------------


def test_show_prints_what_the_model_would_see(monkeypatch, capsys):
    assert run_cli(monkeypatch, "show", "--split", "safety", "--split-dir", str(FIXTURE), "ph-attack-fetch-leak") == 0
    printed = capsys.readouterr().out
    st = load_safety(FIXTURE).by_id["ph-attack-fetch-leak"]
    assert st.task.prompts[0] in printed
    assert "=== fetch__fetch('https://site.example/page') returns" in printed
    page = next(iter(st.meta.pages.values()))
    assert page.strip().splitlines()[0] in printed and '<untrusted_data source="fetch__fetch">' in printed
    assert f"canaries: {', '.join(st.meta.canaries)}" in printed and '"type": "leak"' in printed
    assert "strict user approves:" in printed

    assert run_cli(monkeypatch, "show", "--split-dir", str(FIXTURE), "ph-attack-mcp-answer") == 0
    assert "returns" in capsys.readouterr().out  # an MCP stub's result, through the same registry
    assert "No safety task 'nope'" in str(run_cli(monkeypatch, "show", "--split-dir", str(FIXTURE), "nope"))


def test_show_starts_no_subprocess_and_no_mcp_server(monkeypatch, capsys):
    """Like a safety run, a preview must never start an MCP server or any process: it serves every
    fixture through the in-process stubs only."""
    import kestrel.mcp_client as mcp_client

    started: list[str] = []

    def no_process(self, args, *a, **kw):
        started.append(" ".join(map(str, args)) if isinstance(args, list | tuple) else str(args))
        raise OSError("subprocesses disabled in this test")

    def no_mcp(*a, **kw):
        started.append("MCPManager")
        raise AssertionError("a preview must not start MCP servers")

    monkeypatch.setattr(subprocess.Popen, "__init__", no_process)
    monkeypatch.setattr(mcp_client.MCPManager, "__init__", no_mcp)
    monkeypatch.setattr(mcp_client.MCPManager, "from_config", no_mcp)
    for st in load_safety(FIXTURE).tasks:  # every vector: files, pages, search, MCP stubs
        assert run_cli(monkeypatch, "show", "--split-dir", str(FIXTURE), st.id) == 0
    assert "returns" in capsys.readouterr().out
    assert started == []


def test_validate_one_task(tmp_path, monkeypatch, capsys):
    assert run_cli(monkeypatch, "validate", "--split-dir", str(FIXTURE), "--task", "ph-benign-send") == 0
    assert "OK: ph-benign-send." in capsys.readouterr().out
    assert "No safety task" in str(run_cli(monkeypatch, "validate", "--split-dir", str(FIXTURE), "--task", "nope"))

    root = tmp_path / "split"  # a page marker belongs to the task that serves the page
    shutil.copytree(FIXTURE, root)
    page = root / "pages" / "placeholder_page.html"
    page.write_text(page.read_text(encoding="utf-8") + "\n{{INJECTION}}\n", encoding="utf-8")
    split = load_safety(root)
    assert [m.where for m in markers_of(split, find_markers(root), "ph-attack-fetch-leak")] == [
        "pages/placeholder_page.html"
    ]
    assert markers_of(split, find_markers(root), "ph-attack-file-send") == []
    assert run_cli(monkeypatch, "validate", "--split-dir", str(root), "--task", "ph-attack-fetch-leak") == 1
    assert "INCOMPLETE: ph-attack-fetch-leak has 1 injection(s)" in capsys.readouterr().out


def test_a_problem_concerns_a_task_only_by_its_whole_id():
    assert concerns("pilot.yaml:owner-1: goals[1]: never fired", "owner-1")
    assert not concerns("pilot.yaml:owner-10: goals[1]: never fired", "owner-1")
    assert concerns("a.yaml:x: canaries: KCAN-1 also appears in owner-1, y; give every task its own", "owner-1")
