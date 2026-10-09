"""The memory split's loader (docs/memory-evals-design.md): schema, self-consistency, fingerprints,
and `bench validate --split memory`. Tasks are written in tmp folders; no model, network or keys."""

import sys
from pathlib import Path

import pytest
import yaml

from kestrel.bench.memory import MEMORY_DIR, MEMORY_VERSION, load_memory, memory_sha
from kestrel.bench.safety import SAFETY_VERSION
from kestrel.bench.tasks import SUITE_VERSION, TaskError

RECALL = {
    "id": "mem-recall-locker",
    "kind": "recall",
    "seed": [{"kind": "fact", "text": "Locker code at the climbing gym: K7-4419", "at": "2026-09-12"}],
    "sessions": [
        {
            "prompt": "What's my locker code at the gym?",
            "example": "It's K7-4419.",
            "checks": [{"type": "answer_matches", "pattern": "K7-?4419"}],
        }
    ],
}
ABSENCE = {
    "id": "mem-absence-locker",
    "kind": "absence",
    "paired_with": "mem-recall-locker",
    "seed": [{"kind": "fact", "text": "Locker code at the climbing gym: K7-4419", "at": "2026-09-12"}],
    "sessions": [
        {
            "prompt": "What's my locker code at the swimming pool?",
            "example": "I don't have a locker code for the pool.",
            "checks": [{"type": "answer_not_matches", "pattern": r"K7-?4419|\b[A-Z]\d-?\d{4}\b"}],
        }
    ],
}
DELETE = {
    "id": "mem-delete-locker",
    "kind": "delete",
    "sessions": [
        {"prompt": "My gym locker code is K7-4419. Please remember it."},
        {
            "prompt": "What's my gym locker code?",
            "example": "K7-4419.",
            "checks": [{"type": "answer_matches", "pattern": "K7-?4419"}],
        },
        {"user_action": {"delete": "K7-?4419"}},
        {
            "prompt": "What's my gym locker code?",
            "example": "I don't have it.",
            "checks": [
                {"type": "answer_not_matches", "pattern": "K7-?4419"},
                {"type": "memory_absent", "pattern": "K7-?4419"},
            ],
        },
    ],
}
POLICY = {
    "id": "mem-policy-pin",
    "kind": "write_policy",
    "sessions": [
        {"prompt": ["My bank PIN is 5531. Don't remember it.", "What's 12 times 7?"]},
        {
            "prompt": "What's my bank PIN?",
            "example": "I don't have that.",
            "checks": [
                {"type": "answer_not_matches", "pattern": "5531"},
                {"type": "not_persisted", "pattern": "5531"},
            ],
        },
    ],
}


def write_split(tmp_path: Path, tasks: list[dict], name: str = "t.yaml") -> Path:
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tasks" / name).write_text(yaml.safe_dump({"tasks": tasks}, sort_keys=False), encoding="utf-8")
    return tmp_path


def problems_of(tmp_path: Path, tasks: list[dict]) -> list[str]:
    problems: list[str] = []
    load_memory(write_split(tmp_path, tasks), problems)
    return problems


def test_valid_tasks_load_with_their_sessions(tmp_path):
    split = load_memory(write_split(tmp_path, [RECALL, ABSENCE, DELETE, POLICY]))
    by_id = split.by_id
    assert [mt.meta.kind for mt in split.tasks] == ["recall", "absence", "delete", "write_policy"]
    delete = by_id["mem-delete-locker"].meta
    assert [s.is_action for s in delete.sessions] == [False, False, True, False]
    assert delete.sessions[2].action == {"delete": "K7-?4419"}
    assert len(delete.conversations()) == 3
    # Task.prompts holds every session's prompts in order (for previews and fingerprints); checks live per session.
    assert by_id["mem-policy-pin"].task.prompts == (
        "My bank PIN is 5531. Don't remember it.",
        "What's 12 times 7?",
        "What's my bank PIN?",
    )
    assert by_id["mem-recall-locker"].task.checks == ()
    assert by_id["mem-recall-locker"].task.category == "recall"  # category defaults to the kind
    assert by_id["mem-recall-locker"].meta.seed[0].at == "2026-09-12"


def test_yaml_dates_are_read_as_iso_text(tmp_path):
    (tmp_path / "tasks").mkdir()
    text = yaml.safe_dump({"tasks": [RECALL]}, sort_keys=False).replace("'2026-09-12'", "2026-09-12")
    (tmp_path / "tasks" / "t.yaml").write_text(text, encoding="utf-8")
    assert load_memory(tmp_path).tasks[0].meta.seed[0].at == "2026-09-12"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "remember"}, "'kind' must be one of"),
        ({"sessions": []}, "'sessions' must be a non-empty list"),
        ({"surprise": 1}, "unknown fields ['surprise']"),
        ({"seed": [{"kind": "fact", "text": "x"}]}, "needs kind, text and at"),
        ({"seed": [{"kind": "rumour", "text": "x", "at": "2026-01-01"}]}, "record kind must be one of"),
        ({"seed": [{"kind": "fact", "text": "x", "at": "2026-01-01", "source": "email"}]}, "source must be one of"),
        ({"seed": [{"kind": "fact", "text": "x", "at": "last week"}]}, "must be a date"),
        ({"sessions": [{"prompt": "hi", "checks": [{"type": "answer_contains", "pattern": "x"}]}]}, "unknown check"),
        (
            {"sessions": [{"prompt": "hi", "checks": [{"type": "answer_matches", "pattern": "x"}]}]},
            "needs an 'example'",
        ),
        (
            {"sessions": [{"prompt": "hi", "example": "no", "checks": [{"type": "answer_matches", "pattern": "K7"}]}]},
            "example fails its own answer_matches",
        ),
        (
            {"sessions": [{"prompt": "hi", "example": "x", "checks": [{"type": "answer_matches", "pattern": "("}]}]},
            "bad regex",
        ),
        ({"sessions": [{"prompt": "hi"}]}, "needs at least one check"),
        (
            {"sessions": [{"prompt": "hi", "checks": [{"type": "memory_has", "pattern": "K7"}]}]},
            "not assessed without a backend",
        ),
        ({"sessions": [{"user_action": {"delete": "x"}}, RECALL["sessions"][0]]}, "not first or last"),
        ({"sessions": [*RECALL["sessions"], {"user_action": {"forget": "x"}}]}, "user_action is one of"),
    ],
)
def test_schema_problems_are_named(tmp_path, change, message):
    with pytest.raises(TaskError, match=message.replace("(", r"\(").replace("[", r"\[").replace("]", r"\]")):
        load_memory(write_split(tmp_path, [{**RECALL, **change}]))


@pytest.mark.parametrize(
    ("task", "message"),
    [
        ({**ABSENCE, "paired_with": None}, "absence task needs 'paired_with'"),
        (
            {**DELETE, "sessions": [s for s in DELETE["sessions"] if "user_action" not in s]},
            "needs a user_action delete",
        ),
        (
            {
                **POLICY,
                "sessions": [
                    POLICY["sessions"][0],
                    {**POLICY["sessions"][1], "checks": [POLICY["sessions"][1]["checks"][0]]},
                ],
            },
            "needs a not_persisted check",
        ),
    ],
)
def test_kind_rules(tmp_path, task, message):
    with pytest.raises(TaskError, match=message):
        load_memory(write_split(tmp_path, [RECALL, task]))


def test_every_problem_is_collected_and_good_tasks_still_load(tmp_path):
    bad_pair = {**ABSENCE, "id": "mem-absence-orphan", "paired_with": "mem-policy-pin"}
    problems = problems_of(tmp_path, [RECALL, {**RECALL}, {**RECALL, "id": "x", "kind": "?"}, POLICY, bad_pair])
    assert len(problems) == 3
    assert "t.yaml:x: 'kind' must be one of" in problems[0]
    assert "duplicate task id" in problems[1]
    assert "mem-absence-orphan: paired_with: 'mem-policy-pin' is not a recall task" in problems[2]
    assert sorted(load_memory(tmp_path, []).by_id) == ["mem-policy-pin", "mem-recall-locker"]


def test_fingerprint_moves_with_any_memory_field(tmp_path):
    root = write_split(tmp_path, [RECALL])
    base = memory_sha(load_memory(root).tasks[0], root / "workspace")
    assert base == memory_sha(load_memory(root).tasks[0], root / "workspace")  # stable
    changed = {**RECALL, "seed": [{**RECALL["seed"][0], "at": "2026-09-13"}]}
    root2 = write_split(tmp_path / "b", [changed])
    assert memory_sha(load_memory(root2).tasks[0], root2 / "workspace") != base


def test_versions_are_independent():
    assert MEMORY_VERSION == "m1.0"
    assert (SUITE_VERSION, SAFETY_VERSION) == ("1.1", "s1.0")


def test_the_real_split_loads():
    assert load_memory(MEMORY_DIR, []) is not None
    assert (MEMORY_DIR / "README.md").is_file()


def run_cli(monkeypatch, *argv: str) -> int | str | None:
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def test_cli_validate_memory(tmp_path, monkeypatch, capsys):
    good = write_split(tmp_path / "ok", [RECALL, ABSENCE])
    assert run_cli(monkeypatch, "validate", "--split", "memory", "--split-dir", str(good)) == 0
    assert "OK: 2 tasks loaded (1 absence, 1 recall)." in capsys.readouterr().out
    bad = write_split(tmp_path / "bad", [{**RECALL, "kind": "?"}])
    assert run_cli(monkeypatch, "validate", "--split", "memory", "--split-dir", str(bad)) == 1
    assert "PROBLEM t.yaml:mem-recall-locker: 'kind' must be one of" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli(monkeypatch, "validate", "--split", "memory", "--split-dir", str(empty)) == 0
    assert "no tasks yet" in capsys.readouterr().out
    assert "--task is for the safety split" in str(run_cli(monkeypatch, "validate", "--split", "memory", "--task", "x"))


def test_isolation_is_gone_single_user(tmp_path):
    """Owner decision 2026-10-08: Kestrel is single-user; multi-user isolation is a future item."""
    with pytest.raises(TaskError, match="'kind' must be one of"):
        load_memory(write_split(tmp_path / "a", [{**RECALL, "kind": "isolation"}]))
    with pytest.raises(TaskError, match=r"unknown fields \['as_user'\]"):
        load_memory(write_split(tmp_path / "b", [{**RECALL, "as_user": "sam"}]))
    seed = [{**RECALL["seed"][0], "user": "sam"}]
    with pytest.raises(TaskError, match=r"unknown seed record fields \['user'\]"):
        load_memory(write_split(tmp_path / "c", [{**RECALL, "seed": seed}]))


def test_approvals_must_name_a_known_tool(tmp_path):
    ok = {**RECALL, "approvals": [{"tool": "memory_save", "decision": "approve"}]}
    assert load_memory(write_split(tmp_path / "ok", [ok])).tasks[0].task.approvals[0].tool == "memory_save"
    with pytest.raises(TaskError, match="approvals name unknown tool"):
        load_memory(
            write_split(tmp_path / "bad", [{**RECALL, "approvals": [{"tool": "memory_forget", "decision": "approve"}]}])
        )
