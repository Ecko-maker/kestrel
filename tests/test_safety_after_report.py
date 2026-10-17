"""Before the safety after-run: the report separates model errors, shows benign rates with and without
noted tasks, puts model and system level side by side with the cards tiers v2 raised, and compare
refuses files whose task fingerprints differ. No network, no keys."""

import copy
from pathlib import Path

import pytest

from kestrel.bench.guards import make_grader
from kestrel.bench.runner import run_suite
from kestrel.bench.safety import compare_safety, error_kind, load_safety, task_changes
from kestrel.bench.safety_report import markdown_safety
from kestrel.bench.scripted import ScriptedSafetyModel
from kestrel.bench.stubs import make_setup

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "safety"
GROQ_ERROR = (
    "Sorry, I couldn't get an answer from any model. all providers: groq: stream broke off: Tool call validation "
    "failed: tool call validation failed: attempted to call tool 'commentary' which was not in request.tools"
)


def run(model: str, user: str = "strict") -> dict:
    split = load_safety(FIXTURE)
    llm = ScriptedSafetyModel(model)
    results = run_suite(
        [st.task for st in split.tasks],
        llm,
        base_workspace=split.workspace,
        setup=make_setup(split, user, llm.begin),
        grader=make_grader(split, user),
    )
    rows = [{**r.to_dict(), "user": user} for r in results]
    return {"meta": {"split_version": "s1.0", "user": user, "repeat": 1, "provider": "scripted"}, "tasks": rows}


def test_an_unknown_tool_is_model_behaviour_not_an_outage():
    assert error_kind(GROQ_ERROR) == ("model called an unknown tool", "commentary")
    assert error_kind("groq: 429 Too Many Requests") is None and error_kind(None) is None


def test_the_report_puts_unknown_tool_errors_on_their_own_line_with_what_ran():
    data = run("never-falls")
    row = next(r for r in data["tasks"] if r["id"] == "ph-attack-search-leak")
    row.update(status="error", error=GROQ_ERROR, safety=None, tool_log=['- read_file({"path":"a"}) [ran] -> x'])
    text = markdown_safety(data)
    line = next(ln for ln in text.splitlines() if ln.startswith("| Errored: model called an unknown tool"))
    assert "tool 'commentary'" in line and "ran read_file x1" in line
    assert "system level 0 (no gated or network call ran)" in line and "model level unknown" in line
    row["tool_log"].append('- web_search({"query":"q"}) [ran] -> []')
    assert "system level: check (web_search ran before the error)" in markdown_safety(data)


def test_runs_keep_the_gates_audit_and_the_report_reads_it():
    data = run("reads-then-falls")
    multistep = next(r for r in data["tasks"] if r["id"] == "ph-attack-multistep")
    assert multistep["approvals"] == [
        {"tool": "fetch__fetch", "decision": "rejected", "escalated_by": ["taint:read_file"]}
    ]
    text = markdown_safety(data)
    table = text.split("## Attacks: model level vs system level", 1)[1]
    row = next(ln for ln in table.splitlines() if ln.startswith("| ph-attack-multistep |"))
    assert "1 of 1 | 0 of 1 | taint:read_file x1 → rejected x1 | blocked an attempt" in row
    send = next(ln for ln in table.splitlines() if ln.startswith("| ph-attack-file-send |"))
    assert send.endswith("| none | stopped without escalation |")  # an ordinary send card, rejected


def test_files_without_an_audit_say_so_instead_of_guessing():
    data = run("reads-then-falls")
    for r in data["tasks"]:
        r.pop("approvals")
    table = markdown_safety(data).split("## Attacks: model level vs system level", 1)[1]
    row = next(ln for ln in table.splitlines() if ln.startswith("| ph-attack-multistep |"))
    assert "n/a (no audit in the results) | stopped (no audit in the results to say how)" in row


def test_compare_refuses_a_changed_task_but_not_an_errored_row(monkeypatch):
    a = run("never-falls")
    b = copy.deepcopy(a)
    assert task_changes(a, b) == [] and compare_safety(a, b)
    b["tasks"][0]["task_sha"] = "000000000000"
    with pytest.raises(ValueError, match="different fingerprint"):
        compare_safety(a, b)
    b["tasks"][0].update(status="error", safety=None)  # errored rows carry the main suite's kind of sha
    assert task_changes(a, b) == []


def test_benign_rates_are_shown_with_and_without_noted_tasks(monkeypatch):
    monkeypatch.setattr("kestrel.bench.safety_report.BENIGN_NOTED", {"ph-benign-note": "noted"})
    data = run("never-falls")
    next(r for r in data["tasks"] if r["id"] == "ph-benign-note")["status"] = "fail"
    text = markdown_safety(data)
    assert "| Benign tasks that passed every repeat (strict user) | 75% (3/4" in text
    assert "| Benign tasks that passed every repeat without ph-benign-note | 100% (3/3" in text
