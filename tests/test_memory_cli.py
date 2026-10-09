"""`bench run|validate|report --split memory` with scripted models: repeats, resume, the matrix and its
requirements, the per-session estimate. No model calls, no network, no keys."""

import json
import sys

import pytest
from test_memory_split import ABSENCE, DELETE, POLICY, RECALL, write_split

from kestrel.bench.memory import MEMORY_DIR, load_memory
from kestrel.bench.memory_run import EXPECTED, matrix


def run_cli(monkeypatch, *argv: str) -> int | str | None:
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def test_the_real_split_meets_every_requirement():
    table, problems = matrix(load_memory(MEMORY_DIR))
    assert problems == []
    n = {k: v[1] for k, v in table["perfect"].items()}
    assert {k: v[0] for k, v in table["perfect"].items()} == n  # every task is passable
    for model, wants in EXPECTED.items():
        for kind, must_pass in wants.items():
            assert table[model][kind][0] == (n[kind] if must_pass else 0), (model, kind)


def test_validate_flags_an_absence_task_a_never_forgetting_memory_passes(tmp_path, monkeypatch, capsys):
    # no near-miss value forbidden: recall-all's dump of the gym code doesn't fail it
    weak = {
        **ABSENCE,
        "sessions": [{**ABSENCE["sessions"][0], "checks": [{"type": "answer_not_matches", "pattern": "Z9-0000"}]}],
    }
    root = write_split(tmp_path, [RECALL, weak])
    assert run_cli(monkeypatch, "validate", "--split", "memory", "--split-dir", str(root)) == 1
    out = capsys.readouterr().out
    assert "| absence | 1/1 pass | 1/1 pass | 1/1 pass | 1/1 pass |" in out
    assert "behavior: recall-all must fail a absence task but passed" in out


def test_scripted_run_with_repeats_report_and_resume(tmp_path, monkeypatch, capsys):
    root = write_split(tmp_path / "split", [RECALL, ABSENCE, DELETE, POLICY])
    out = tmp_path / "forget.json"
    args = ["run", "--split", "memory", "--split-dir", str(root), "--scripted", "forget-all", "--repeat", "3"]
    assert run_cli(monkeypatch, *args, "--out", str(out)) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    meta, mem = data["meta"], data["memory"]
    assert (meta["split"], meta["split_version"], meta["memory_backend"], meta["user"]) == (
        "memory",
        "m1.0",
        "none",
        "strict",
    )
    assert meta["agent"]["sha"]  # recorded, as for every split
    assert len(data["tasks"]) == 12  # 4 tasks x 3 repeats
    assert mem["task_repeats"]["mem-recall-locker"] == [0, 3]
    assert mem["task_repeats"]["mem-absence-locker"] == [3, 3]
    assert mem["failures"]["recall"] == ["mem-recall-locker"] and mem["failures"]["absence"] == []
    assert mem["pairs"] == [
        {"absence": "mem-absence-locker", "recall": "mem-recall-locker", "absence_pass": True, "recall_pass": False}
    ]
    assert mem["not_assessed"] == ["mem-delete-locker"]
    report = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "| mem-absence-locker | yes | mem-recall-locker | NO |" in report
    assert "memory backend **none**" in report
    capsys.readouterr()
    assert run_cli(monkeypatch, *args, "--resume", str(out)) == 0
    assert "0 to run, 12 kept" in capsys.readouterr().out
    assert run_cli(monkeypatch, "report", str(out)) == 0
    assert "Utility" in capsys.readouterr().out


def test_dry_run_counts_sessions(tmp_path, monkeypatch, capsys):
    root = write_split(tmp_path, [RECALL, DELETE])  # 1 + 3 conversations
    assert run_cli(monkeypatch, "run", "--split", "memory", "--split-dir", str(root), "--repeat", "3", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "memory split: 2 tasks x 3 = 6 runs" in out
    # 3 x (1 + 3) sessions x 3,300 raw tokens, 75% billable; 3 requests a session
    assert "~39,600 tokens, ~29,700 billable (uncached), ~36 requests" in out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--user", "rubber-stamp"], "strict user only"),
        (["--scripted", "always-falls"], "not a scripted model of the memory split"),
        (["--reuse", "x.json"], "--reuse is for the main split"),
    ],
)
def test_refusals(monkeypatch, extra, message):
    assert message in str(run_cli(monkeypatch, "run", "--split", "memory", "--dry-run", *extra))


def test_compare_refuses_memory_files(tmp_path, monkeypatch):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"meta": {"split": "memory"}, "tasks": []}), encoding="utf-8")
    assert "no memory-split pairing yet" in str(run_cli(monkeypatch, "compare", str(f), str(f)))
