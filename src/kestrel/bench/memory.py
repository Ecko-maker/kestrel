"""KestrelBench memory split: tasks that measure long-term memory, written before memory exists.

Kept apart from the main 100 tasks and the safety split (its own folder, loader and version), so
neither moves. Design and open questions: docs/memory-evals-design.md (a proposal). Tasks live in
evals/kestrelbench/memory/tasks/*.yaml. A task has the main suite's common fields (id, category,
tags, files, approvals, max_steps) plus memory fields kept in a MemoryMeta:

    kind: recall | absence | delete | update | write_policy | isolation | preference
    paired_with: <task id>        # absence: the recall task of the same shape
    as_user: <name>               # isolation: whose session this is
    seed:                         # memory records loaded before the run (an "earlier session")
      - {kind: fact, text: "...", at: 2026-09-12, source: user, user: <name>}
    sessions:                     # each prompt entry is a NEW conversation (fresh agent)
      - prompt: "..." | ["turn 1", "turn 2"]
        example: "..."            # what a perfect memory answers; must pass this session's answer checks
        checks: [...]             # main-suite check types, plus not_persisted / memory_has / memory_absent
      - user_action: {delete: regex}   # what the user does in the console between sessions

This module only loads and validates. Running the split needs a fresh agent per session, a runner
change that waits for the owner's approval (design section 3); `bench run` has no memory split yet.
Today there is no memory backend, so a seed is loaded nowhere: that is the "before" state.
"""

import datetime
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from kestrel.bench.checks import FLAGS, plain
from kestrel.bench.tasks import CHECK_TYPES, OPTIONAL_CHECK_FIELDS, Task, TaskError, _parse_task, _workspace_sha

MEMORY_DIR = Path(__file__).resolve().parents[3] / "evals" / "kestrelbench" / "memory"

# Bump on any change to a memory task, a memory check's logic or the memory fixtures, and log it in
# evals/CHANGELOG.md. Independent of SUITE_VERSION and SAFETY_VERSION.
MEMORY_VERSION = "m1.0"

KINDS = ("recall", "absence", "delete", "update", "write_policy", "isolation", "preference")
RECORD_KINDS = ("fact", "episode", "document", "preference")
SOURCES = ("user", "file", "web", "mcp")  # where a record's text came from; only "user" is trusted
ACTIONS = ("delete",)
# Tools whose arguments persist across sessions today or once memory exists (not_persisted, model level).
PERSIST_TOOLS = ("write_file", "append_to_file", "create_note", "memory_save")
# Checks only this split has. memory_has / memory_absent read the memory store after the run; with no
# backend they are not assessed (listed, never counted), so no task gets credit for an empty store.
MEMORY_CHECKS: dict[str, set[str]] = {
    "not_persisted": {"pattern"},
    "memory_has": {"pattern"},
    "memory_absent": {"pattern"},
}
STORE_CHECKS = ("memory_has", "memory_absent")
ANSWER_CHECKS = ("answer_matches", "answer_not_matches")

COMMON_FIELDS = {"id", "category", "tags", "files", "approvals", "max_steps"}
MEMORY_FIELDS = {"kind", "paired_with", "as_user", "seed", "sessions"}


@dataclass(frozen=True)
class Record:
    kind: str
    text: str
    at: str  # ISO date: when the earlier session stored it
    source: str = "user"
    user: str | None = None


@dataclass(frozen=True)
class Session:
    """One entry of `sessions`: a conversation (prompts, fresh agent) or a user action between them."""

    prompts: tuple[str, ...] = ()
    checks: tuple[dict[str, Any], ...] = ()
    example: str | None = None  # a perfect memory's answer to the last prompt
    action: dict[str, str] | None = None  # e.g. {"delete": regex}

    @property
    def is_action(self) -> bool:
        return self.action is not None


@dataclass(frozen=True)
class MemoryMeta:
    kind: str = "recall"
    sessions: tuple[Session, ...] = ()
    seed: tuple[Record, ...] = ()
    paired_with: str | None = None
    as_user: str | None = None

    def conversations(self) -> list[Session]:
        return [s for s in self.sessions if not s.is_action]


@dataclass(frozen=True)
class MemoryTask:
    task: Task  # common fields; prompts = every session's prompts in order, checks empty (per session instead)
    meta: MemoryMeta

    @property
    def id(self) -> str:
        return self.task.id


@dataclass
class MemorySplit:
    root: Path
    tasks: list[MemoryTask]

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def by_id(self) -> dict[str, MemoryTask]:
        return {mt.id: mt for mt in self.tasks}


def _regex(pattern: Any, where: str) -> str:
    try:
        re.compile(str(pattern))
    except re.error as e:
        raise TaskError(f"{where}: bad regex {pattern!r}: {e}") from None
    return str(pattern)


def _record(raw: Any, where: str) -> Record:
    if not isinstance(raw, dict) or {"kind", "text", "at"} - set(raw):
        raise TaskError(f"{where}: a seed record needs kind, text and at: {raw}")
    if extra := set(raw) - {"kind", "text", "at", "source", "user"}:
        raise TaskError(f"{where}: unknown seed record fields {sorted(extra)}")
    if raw["kind"] not in RECORD_KINDS:
        raise TaskError(f"{where}: seed record kind must be one of {RECORD_KINDS}, got {raw['kind']!r}")
    source = raw.get("source", "user")
    if source not in SOURCES:
        raise TaskError(f"{where}: seed record source must be one of {SOURCES}, got {source!r}")
    at = raw["at"]  # YAML reads 2026-09-12 as a date
    try:
        at = at.isoformat() if isinstance(at, datetime.date) else datetime.date.fromisoformat(str(at)).isoformat()
    except ValueError:
        raise TaskError(f"{where}: seed record 'at' must be a date like 2026-09-12, got {raw['at']!r}") from None
    text = raw["text"]
    if not isinstance(text, str) or not text.strip():
        raise TaskError(f"{where}: seed record text must be non-empty")
    return Record(raw["kind"], text, at, source, None if raw.get("user") is None else str(raw["user"]))


def _check(raw: Any, where: str) -> dict[str, Any]:
    kind = raw.get("type") if isinstance(raw, dict) else None
    known = {**CHECK_TYPES, **MEMORY_CHECKS}
    if kind not in known:
        raise TaskError(f"{where}: unknown check type {kind!r}; known: {sorted(known)}")
    missing = known[kind] - set(raw)
    extra = set(raw) - known[kind] - OPTIONAL_CHECK_FIELDS - {"type"}
    if missing or extra:
        raise TaskError(f"{where}: check {kind} missing {sorted(missing)} / unexpected {sorted(extra)}")
    if "pattern" in raw:
        _regex(raw["pattern"], f"{where}: check {kind}")
    return dict(raw)


def _session(raw: Any, where: str) -> Session:
    if not isinstance(raw, dict):
        raise TaskError(f"{where}: a session is a mapping with 'prompt' or 'user_action'")
    if "user_action" in raw:
        if set(raw) != {"user_action"}:
            raise TaskError(f"{where}: a user_action entry has nothing else")
        action = raw["user_action"]
        if not isinstance(action, dict) or len(action) != 1 or next(iter(action)) not in ACTIONS:
            raise TaskError(f"{where}: user_action is one of {ACTIONS}, e.g. {{delete: regex}}")
        name, pattern = next(iter(action.items()))
        return Session(action={name: _regex(pattern, f"{where}: user_action")})
    if extra := set(raw) - {"prompt", "checks", "example"}:
        raise TaskError(f"{where}: unknown session fields {sorted(extra)}")
    prompt = raw.get("prompt")
    prompts = (prompt,) if isinstance(prompt, str) else tuple(prompt or ())
    if not prompts or not all(isinstance(p, str) and p.strip() for p in prompts):
        raise TaskError(f"{where}: 'prompt' must be a non-empty string or list of strings")
    checks = tuple(_check(c, where) for c in raw.get("checks") or ())
    example = raw.get("example")
    if example is not None and not isinstance(example, str):
        raise TaskError(f"{where}: 'example' must be text")
    if any(c["type"] in ANSWER_CHECKS for c in checks):
        if example is None:
            raise TaskError(f"{where}: a session with answer checks needs an 'example' (a perfect memory's answer)")
        for c in checks:
            hit = re.search(c["pattern"], plain(example), FLAGS) if c["type"] in ANSWER_CHECKS else None
            if (c["type"] == "answer_matches" and hit is None) or (c["type"] == "answer_not_matches" and hit):
                raise TaskError(f"{where}: example fails its own {c['type']} /{c['pattern']}/")
    return Session(prompts=prompts, checks=checks, example=example)


def _parse(raw: Any, default_category: str | None, source: str) -> MemoryTask:
    if not isinstance(raw, dict):
        raise TaskError(f"{source}: each task must be a mapping")
    where = f"{source}:{raw.get('id') or '?'}"
    if unknown := set(raw) - COMMON_FIELDS - MEMORY_FIELDS:
        raise TaskError(f"{where}: unknown fields {sorted(unknown)}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise TaskError(f"{where}: 'kind' must be one of {KINDS}")
    sessions_raw = raw.get("sessions")
    if not isinstance(sessions_raw, list) or not sessions_raw:
        raise TaskError(f"{where}: 'sessions' must be a non-empty list")
    sessions = tuple(_session(s, f"{where}: session {i + 1}") for i, s in enumerate(sessions_raw))
    talks = [s for s in sessions if not s.is_action]
    if not talks:
        raise TaskError(f"{where}: needs at least one session with a prompt")
    if sessions[0].is_action or sessions[-1].is_action:
        raise TaskError(f"{where}: a user_action goes between two sessions, not first or last")
    if not any(s.checks for s in talks):
        raise TaskError(f"{where}: needs at least one check in some session")
    if all(c["type"] in STORE_CHECKS for s in talks for c in s.checks):
        raise TaskError(f"{where}: memory_has / memory_absent are not assessed without a backend; add a behavior check")

    common = {k: v for k, v in raw.items() if k in COMMON_FIELDS}
    common.setdefault("category", default_category or kind)
    prompts = [p for s in talks for p in s.prompts]
    # The main parser validates the common fields exactly as for the main suite; its placeholder
    # check is dropped, since memory tasks are graded per session.
    task = replace(
        _parse_task({**common, "prompt": prompts, "checks": [{"type": "no_tools"}]}, None, source), checks=()
    )

    seed_raw = raw.get("seed") or []
    if not isinstance(seed_raw, list):
        raise TaskError(f"{where}: 'seed' must be a list of records")
    seed = tuple(_record(r, f"{where}: seed {i + 1}") for i, r in enumerate(seed_raw))
    as_user = raw.get("as_user")
    users = {r.user for r in seed if r.user is not None}
    if kind == "isolation" and (as_user is None or len(users | {str(as_user)}) < 2):
        raise TaskError(f"{where}: an isolation task needs 'as_user' and seed records of another user")
    if as_user is not None and kind != "isolation":
        raise TaskError(f"{where}: 'as_user' is for isolation tasks only")
    paired_with = raw.get("paired_with")
    if kind == "absence" and not paired_with:
        raise TaskError(f"{where}: an absence task needs 'paired_with' (the recall task of the same shape)")
    if kind == "delete" and not any(s.is_action for s in sessions):
        raise TaskError(f"{where}: a delete task needs a user_action delete between sessions")
    if kind == "write_policy" and not any(c["type"] == "not_persisted" for s in talks for c in s.checks):
        raise TaskError(f"{where}: a write_policy task needs a not_persisted check")
    meta = MemoryMeta(kind, sessions, seed, None if paired_with is None else str(paired_with), as_user)
    return MemoryTask(task, meta)


def load_memory(root: Path = MEMORY_DIR, problems: list[str] | None = None) -> MemorySplit:
    """Every memory task in root/tasks/*.yaml, validated. Raises TaskError at the first problem, or,
    given a `problems` list, collects every problem and returns the tasks that loaded cleanly."""

    def problem(message: str) -> None:
        if problems is None:
            raise TaskError(message)
        problems.append(message)

    tasks: list[MemoryTask] = []
    for path in sorted((root / "tasks").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            problem(f"{path.name}: not valid YAML: {e}")
            continue
        default_category = data.get("category") if isinstance(data, dict) else None
        items = data.get("tasks") if isinstance(data, dict) else data
        if not isinstance(items, list):
            problem(f"{path.name}: expected a list of tasks (or a mapping with 'tasks:')")
            continue
        for raw in items:
            try:
                tasks.append(_parse(raw, default_category, path.name))
            except TaskError as e:
                problem(str(e))
    seen: dict[str, str] = {}
    for mt in list(tasks):
        if mt.id in seen:
            problem(f"{mt.task.source}:{mt.id}: id: duplicate task id (also in {seen[mt.id]})")
            tasks.remove(mt)
        seen.setdefault(mt.id, mt.task.source)
    kinds = {mt.id: mt.meta.kind for mt in tasks}
    for mt in list(tasks):
        if mt.meta.paired_with and kinds.get(mt.meta.paired_with) != "recall":
            problem(f"{mt.task.source}:{mt.id}: paired_with: {mt.meta.paired_with!r} is not a recall task in the split")
            tasks.remove(mt)
    return MemorySplit(root, tasks)


def memory_sha(mt: MemoryTask, workspace: Path) -> str:
    """Fingerprint of a memory task: its common fields, its memory fields and the split's workspace."""
    definition = {k: v for k, v in asdict(mt.task).items() if k != "source"}
    blob = json.dumps([definition, asdict(mt.meta)], sort_keys=True, default=str) + _workspace_sha(workspace)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
