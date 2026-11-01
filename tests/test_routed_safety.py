"""The routed safety run's groups (docs/phase4-plan.md 8.7, amendment of 2026-11-01): F (teacher by the
router's pre-route rule) and S (routed, without the fetch stub), computed from the task data. Proved on each
task's real registry with RoutedLLM.pre_route; no model calls."""

import argparse
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kestrel import tools
from kestrel.bench import cli, routed_safety
from kestrel.bench.safety import SAFETY_DIR, SafetyMeta, load_safety
from kestrel.bench.stubs import FETCH, build_registry, make_setup
from kestrel.distill.train_format import tools_fingerprint
from kestrel.router import RoutedLLM, RouterConfig

ROOT = Path(__file__).resolve().parents[1]
SPLIT = load_safety(SAFETY_DIR)
GROUPS = routed_safety.groups(SPLIT)


def router() -> RoutedLLM:
    """A router whose student was trained on Kestrel's built-in tools (what `kestrel distill build` writes;
    tests/test_distill_export.py checks the dataset against the same registry)."""
    backend: Any = SimpleNamespace(model="m")
    return RoutedLLM(backend, backend, tools_fingerprint(tools.registry.schemas()), RouterConfig())


def pre_route(meta: SafetyMeta, fetch_stub: bool = True) -> str | None:
    return router().pre_route([{"role": "user", "content": "hi"}], build_registry(meta, [], fetch_stub).schemas())


def test_every_task_is_in_exactly_one_group() -> None:
    f, s = set(GROUPS[routed_safety.F]), set(GROUPS[routed_safety.S])
    assert not f & s and f | s == {st.id for st in SPLIT.tasks}
    assert (len(f), len(s)) == (19, 29)


def test_as_registered_every_task_is_pre_routed_to_the_teacher() -> None:
    """The problem the amendment fixes: with the fetch stub, all 48 tasks go to the teacher."""
    for st in SPLIT.tasks:
        reason = pre_route(st.meta)
        assert reason is not None and reason.startswith("tool_set:"), st.id


def test_group_f_is_pre_routed_even_without_the_fetch_stub_or_needs_it() -> None:
    for st in SPLIT.tasks:
        if st.id not in GROUPS[routed_safety.F]:
            continue
        why = routed_safety.needs_untrained_tool(st.meta)
        assert why is not None
        if why.startswith("MCP stub"):
            assert (pre_route(st.meta, fetch_stub=False) or "").startswith("tool_set:"), st.id
        else:  # it needs fetch: without the stub the task can't be done, so it keeps it and the rule applies
            assert st.meta.pages or st.meta.vector == "fetch" or FETCH in str(st.meta.goals + st.meta.expect)


def test_group_s_reaches_the_student_without_the_fetch_stub() -> None:
    for st in SPLIT.tasks:
        if st.id in GROUPS[routed_safety.S]:
            assert pre_route(st.meta, fetch_stub=False) is None, st.id
            assert not st.meta.pages and not st.meta.mcp and st.meta.vector != "fetch"


def test_the_plan_lists_the_computed_groups() -> None:
    """docs/phase4-plan.md 8.7 lists F and S by hand; they must be what the task data gives."""
    plan = (ROOT / "docs" / "phase4-plan.md").read_text(encoding="utf-8")
    for name in (routed_safety.F, routed_safety.S):
        line = re.search(rf"^- \*\*Group {name} \((\d+)\):\*\* (.+)$", plan, re.MULTILINE)
        assert line is not None, name
        ids = re.findall(r"`([^`]+)`", line.group(2))
        assert ids == list(GROUPS[name]) and int(line.group(1)) == len(ids)


def test_a_new_task_lands_in_its_group_by_its_data() -> None:
    assert routed_safety.needs_untrained_tool(SafetyMeta(vector="file")) is None
    assert routed_safety.needs_untrained_tool(SafetyMeta(pages={"https://x.example": "t"})) is not None
    leak = {"type": "leak", "via": FETCH, "args": {"url": "u"}}
    assert routed_safety.needs_untrained_tool(SafetyMeta(vector="search", goals=(leak,))) is not None


# --- the command -----------------------------------------------------------------------------------


def run_args(**kw: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command"))
    argv = ["bench", "run", "--split", "safety", "--safety-group", "S", "--dry-run"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return parser.parse_args(argv)


def test_safety_group_s_runs_only_group_s(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_run(run_args()) == 0
    out = capsys.readouterr().out
    assert "29 tasks x 1" in out and "would run atk-exfil-fetch-plain" not in out
    assert cli.cmd_run(run_args(user="rubber-stamp", kinds="attack")) == 0
    assert "18 tasks x 1" in capsys.readouterr().out


def test_group_s_setup_leaves_the_fetch_stub_out() -> None:
    task = next(st.task for st in SPLIT.tasks if st.id in GROUPS[routed_safety.S])
    with_stub, _, _ = make_setup(SPLIT)(task)
    without, _, _ = make_setup(SPLIT, fetch_stub=False)(task)
    assert FETCH in with_stub.tools and FETCH not in without.tools


def test_safety_group_needs_the_safety_split() -> None:
    args = run_args()
    args.split = "main"
    with pytest.raises(SystemExit, match="--safety-group applies to --split safety"):
        cli.cmd_run(args)
