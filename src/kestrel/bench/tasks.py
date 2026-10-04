"""KestrelBench task definitions: what to ask, how to approve, and how to check the result.

Tasks live in evals/kestrelbench/tasks/*.yaml as lists. A task looks like:

    - id: notes-create-basic
      category: actions
      tags: [ci]
      prompt: "Create a note called 'Groceries' listing milk and eggs"
      files: {"todo.md": "..."}          # extra workspace files for this task (optional)
      approvals:                         # what the scripted user says to risky calls
        - {tool: create_note, decision: approve}
      checks:                            # deterministic checks, all must pass
        - {type: tool_ran, tool: create_note}
        - {type: file_contains, path: notes/groceries.md, pattern: "milk"}
      rubric: "Confirms the note was created."   # optional: graded by the LLM judge

A prompt can also be a list of strings: several user turns in one conversation.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

TASKS_DIR = Path(__file__).resolve().parents[3] / "evals" / "kestrelbench" / "tasks"
WORKSPACE_DIR = Path(__file__).resolve().parents[3] / "evals" / "kestrelbench" / "workspace"

CHECK_TYPES = {
    "tool_called": {"tool"},
    "tool_not_called": {"tool"},
    "tool_ran": {"tool"},
    "tool_not_ran": {"tool"},
    "approval_requested": {"tool"},
    "no_tools": set(),
    "answer_matches": {"pattern"},
    "answer_not_matches": {"pattern"},
    "file_contains": {"path", "pattern"},
    "file_absent": {"path"},
    "file_unchanged": {"path"},
    "outbox_count": {"count"},
    "stop_reason": {"is"},
    "max_steps": {"steps"},
}
OPTIONAL_CHECK_FIELDS = {"args", "to", "why"}


class TaskError(ValueError):
    """A task file is malformed. The message says which task and what's wrong."""


@dataclass(frozen=True)
class ApprovalRule:
    tool: str
    decision: Literal["approve", "reject"]
    reason: str = ""
    args: dict[str, str] = field(default_factory=dict)  # argument name -> regex it must match
    times: int | None = None  # apply to the first N matching requests only


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    prompts: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]
    tags: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)
    approvals: tuple[ApprovalRule, ...] = ()
    rubric: str | None = None
    max_steps: int = 8
    source: str = ""  # file it came from, for error messages

    @property
    def prompt(self) -> str:
        return self.prompts[-1]


def _parse_task(raw: Any, default_category: str | None, source: str) -> Task:
    if not isinstance(raw, dict):
        raise TaskError(f"{source}: each task must be a mapping, got {type(raw).__name__}")
    task_id = raw.get("id")
    where = f"{source}:{task_id or '?'}"
    if not isinstance(task_id, str) or not task_id:
        raise TaskError(f"{where}: missing 'id'")
    unknown = set(raw) - {"id", "category", "tags", "prompt", "files", "approvals", "checks", "rubric", "max_steps"}
    if unknown:
        raise TaskError(f"{where}: unknown fields {sorted(unknown)}")

    prompt = raw.get("prompt")
    prompts = (prompt,) if isinstance(prompt, str) else tuple(prompt or ())
    if not prompts or not all(isinstance(p, str) and p.strip() for p in prompts):
        raise TaskError(f"{where}: 'prompt' must be a non-empty string or list of strings")

    checks = raw.get("checks") or []
    if not checks and not raw.get("rubric"):
        raise TaskError(f"{where}: needs at least one check or a rubric")
    for check in checks:
        kind = check.get("type") if isinstance(check, dict) else None
        if kind not in CHECK_TYPES:
            raise TaskError(f"{where}: unknown check type {kind!r}; known: {sorted(CHECK_TYPES)}")
        missing = CHECK_TYPES[kind] - set(check)
        extra = set(check) - CHECK_TYPES[kind] - OPTIONAL_CHECK_FIELDS - {"type"}
        if missing or extra:
            raise TaskError(f"{where}: check {kind} missing {sorted(missing)} / unexpected {sorted(extra)}")

    approvals = []
    for rule in raw.get("approvals") or []:
        if rule.get("decision") not in ("approve", "reject") or not rule.get("tool"):
            raise TaskError(f"{where}: approval rules need 'tool' and decision approve|reject: {rule}")
        approvals.append(
            ApprovalRule(
                rule["tool"], rule["decision"], rule.get("reason", ""), dict(rule.get("args") or {}), rule.get("times")
            )
        )

    category = raw.get("category") or default_category
    if not category:
        raise TaskError(f"{where}: missing 'category'")
    return Task(
        id=task_id,
        category=category,
        prompts=prompts,
        checks=tuple(checks),
        tags=tuple(raw.get("tags") or ()),
        files={str(k): str(v) for k, v in (raw.get("files") or {}).items()},
        approvals=tuple(approvals),
        rubric=raw.get("rubric"),
        max_steps=int(raw.get("max_steps", 8)),
        source=source,
    )


def load_tasks(directory: Path = TASKS_DIR) -> list[Task]:
    """Every task in every YAML file, validated. Ids must be unique across files."""
    tasks: list[Task] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        default_category = data.get("category") if isinstance(data, dict) else None
        items = data.get("tasks") if isinstance(data, dict) else data
        for raw in items or []:
            tasks.append(_parse_task(raw, default_category, path.name))
    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            raise TaskError(f"duplicate task id {task.id!r}")
        seen.add(task.id)
    return tasks


def select(
    tasks: list[Task],
    subset: str | None = None,
    category: str | None = None,
    ids: list[str] | None = None,
    skip_tags: tuple[str, ...] = (),
) -> list[Task]:
    """subset="ci" keeps tasks tagged ci; skip_tags drops e.g. "network" tasks."""
    picked = [
        t
        for t in tasks
        if (subset in (None, "all") or subset in t.tags)
        and (category is None or t.category == category)
        and (not ids or t.id in ids)
        and not set(skip_tags) & set(t.tags)
    ]
    return picked
