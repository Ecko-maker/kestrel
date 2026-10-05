"""`kestrel bench validate --split safety`: check safety tasks before anyone runs a model on them.

For task authors. Every problem is collected (not just the first) and named as
`file:task-id: field: message`. Four groups of checks:
1. schema: everything the loader checks (fields, types, regexes, args vs match, pairing, ...);
2. canaries: each task's canaries live in its own `files`, are unique across tasks, and appear in
   no other task's fixtures; the shared workspace holds none (a canary there belongs to every task,
   so a leak couldn't be traced to one);
3. fixtures: every file in the split folder is visible to git (no `data/` folder, no `.env*` name);
4. behavior, with the scripted models and the strict user: always-falls trips every goal of every
   attack, never-falls trips no goal or guard and passes every benign task, refuse-all fails every
   benign task. A goal that never fires, or a guard that fires on the honest path, is a task bug.

No model is called and nothing touches the network (the stubs serve everything).
"""

import shutil
import subprocess
from pathlib import Path

from kestrel.bench import canaries as cn
from kestrel.bench.guards import goal_label, make_grader
from kestrel.bench.runner import TaskResult, run_suite
from kestrel.bench.safety import SafetySplit, SafetyTask, _attacker_texts, _read_tree, load_safety
from kestrel.bench.scripted import ScriptedSafetyModel
from kestrel.bench.stubs import make_setup


def _where(st: SafetyTask) -> str:
    return f"{st.task.source}:{st.id}"


def check_canaries(split: SafetySplit) -> list[str]:
    problems = []
    shared = _read_tree(split.workspace)
    for rel, text in shared.items():
        for payload in sorted(cn.find_canaries(text)):
            problems.append(
                f"workspace/{rel}: canaries: KCAN-{payload} is in the shared workspace, so every task would share it; "
                "move it into the 'files' of the one task that uses it"
            )
    own: dict[str, set[str]] = {}  # task id -> payloads in its own files
    fixtures: dict[str, list[str]] = {}  # task id -> every text it brings (files, pages, search, mcp, prompts)
    for st in split.tasks:
        own[st.id] = {p for t in st.task.files.values() for p in cn.find_canaries(t)}
        fixtures[st.id] = [*st.task.files.values(), *_attacker_texts(st.meta), *st.task.prompts]
    for st in split.tasks:
        for payload in sorted(own[st.id]):
            others = sorted(
                o.id
                for o in split.tasks
                if o.id != st.id and any(payload in cn.find_canaries(t) for t in fixtures[o.id])
            )
            if others:
                problems.append(
                    f"{_where(st)}: canaries: KCAN-{payload} also appears in {', '.join(others)}; "
                    "give every task its own canary"
                )
    return problems


def check_git_visibility(root: Path) -> list[str]:
    """Every file in the split folder must be committable: .gitignore hides `data/` and `.env*`."""
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    problems = [
        f"{p.relative_to(root).as_posix()}: path: a folder named 'data' is git-ignored; rename it"
        for p in files
        if "data" in p.relative_to(root).parts[:-1]
    ]
    if not files or shutil.which("git") is None:
        return problems
    rel = [str(p.resolve()) for p in files]
    out = subprocess.run(
        ["git", "check-ignore", "--stdin", "-v", "-z"],  # -z: NUL-separated, no quoting of Windows paths
        input="\0".join(rel).encode(),
        capture_output=True,
        cwd=root if root.is_dir() else None,
    )
    if out.returncode not in (0, 1):  # 128: not a git repository, say; nothing to check against
        return problems
    fields = out.stdout.decode().split("\0")
    for source, line, pattern, path in zip(*[iter(fields)] * 4, strict=False):  # 4 fields per ignored path
        name = Path(path).resolve()
        shown = name.relative_to(root.resolve()).as_posix() if name.is_relative_to(root.resolve()) else path
        problems.append(f"{shown}: path: ignored by git ({source}:{line}:{pattern}); it would never be committed")
    return problems


def _run(split: SafetySplit, tasks: list[SafetyTask], model: str) -> dict[str, TaskResult]:
    llm = ScriptedSafetyModel(model)
    results = run_suite(
        [st.task for st in tasks],
        llm,
        base_workspace=split.workspace,
        setup=make_setup(split, "strict", llm.begin),
        grader=make_grader(split, "strict"),
    )
    return {r.id: r for r in results}


def check_behavior(split: SafetySplit) -> list[str]:
    problems = []
    attacks = [st for st in split.tasks if st.meta.kind == "attack"]
    benign = [st for st in split.tasks if st.meta.kind == "benign"]
    falls = _run(split, attacks, "always-falls")
    for st in attacks:
        r = falls[st.id]
        if r.status == "error":
            problems.append(f"{_where(st)}: always-falls: the run errored: {r.error}")
            continue
        fired = {h["check"] for h in (r.safety or {}).get("hits", []) if h["model"]}
        for i, goal in enumerate(st.meta.goals):
            if goal_label(i, goal) not in fired:
                problems.append(
                    f"{_where(st)}: goals[{i + 1}] ({goal['type']}): never fired under always-falls, which sends "
                    f"exactly its args; check them against the check's rules, and max_steps ({st.task.max_steps})"
                )
    honest = _run(split, attacks + benign, "never-falls")
    for st in attacks + benign:
        r = honest[st.id]
        hits = [h["check"] for h in (r.safety or {}).get("hits", [])]
        if hits:
            problems.append(
                f"{_where(st)}: never-falls (only the expected actions) tripped {', '.join(hits)}: "
                "an expect entry looks like the attack, or the attack's match is too broad"
            )
        if st.meta.kind == "benign" and r.status != "pass" and not hits:
            failed = [e["detail"] for e in (r.safety or {}).get("expect", []) if not e["ok"]]
            problems.append(
                f"{_where(st)}: expect: never-falls did not pass ({'; '.join(failed)[:200] or r.error}); "
                "check the approvals approve the expected calls and the args reach the tool"
            )
    refuse = _run(split, benign, "refuse-all")
    for st in benign:
        if refuse[st.id].status == "pass":
            problems.append(
                f"{_where(st)}: expect: refuse-all passed this benign task, so it can't detect over-refusal; "
                "add the requested action as a tool entry"
            )
    return problems


def validate(root: Path) -> tuple[SafetySplit, list[str]]:
    """All problems in a safety split folder, schema first."""
    problems: list[str] = []
    split = load_safety(root, problems)
    problems += check_canaries(split)
    problems += check_git_visibility(root)
    problems += check_behavior(split)
    return split, problems
