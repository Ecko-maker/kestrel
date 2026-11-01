"""The routed safety run's two groups (docs/phase4-plan.md section 8.4, amendment of 2026-11-01).

Router v1 sends a whole turn to the teacher when the tools sent differ from the student's training set
(`RoutedLLM.pre_route`, rule `tool_set`). The student is trained on Kestrel's built-in tools only (#76),
and every safety task registers the `fetch__fetch` stub, so all 48 tasks would be pre-routed and the
routed run would only re-measure the teacher. The amendment splits the tasks by what they need:

- **F** (teacher by rule): the task needs a tool the student never saw. Either it needs `fetch__fetch`
  (it has fetch pages, its vector is `fetch`, or a goal or expected outcome names the tool), or it
  registers an MCP stub. Routed = the teacher on every turn, by the pre-route rule; their result is the
  teacher's measured tiers v2.1 result, not re-run.
- **S** (routed run): everything else. Run with the fetch stub left out (`bench run --safety-group S`):
  none of them needs it, and without it the tools sent are exactly the student's.

Computed from the task data only, so a new or changed task lands in the right group by itself.
"""

from __future__ import annotations

from kestrel.bench.safety import SafetyMeta, SafetySplit
from kestrel.bench.stubs import FETCH

F, S = "F", "S"


def needs_untrained_tool(meta: SafetyMeta) -> str | None:
    """Why the task needs a tool the student never saw (group F), or None (group S)."""
    if meta.pages:
        return f"{FETCH}: the task has fetch pages"
    if meta.vector == "fetch":
        return f"{FETCH}: the attack or content arrives by fetch"
    for entry in (*meta.goals, *meta.expect):
        if FETCH in (entry.get("tool"), entry.get("via")):
            return f"{FETCH}: a goal or expected outcome uses it"
    if meta.mcp:
        return f"MCP stub {', '.join(m.name for m in meta.mcp)}"
    return None


def groups(split: SafetySplit) -> dict[str, dict[str, str]]:
    """{"F": {task id: reason}, "S": {task id: ""}}, in the split's order."""
    out: dict[str, dict[str, str]] = {F: {}, S: {}}
    for st in split.tasks:
        reason = needs_untrained_tool(st.meta)
        out[F if reason else S][st.id] = reason or ""
    return out
