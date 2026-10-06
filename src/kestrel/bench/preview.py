"""`kestrel bench show --split safety <task-id>`: what the model would see in one safety task, offline.

For task authors checking an attack before any model runs it. Every fixture goes through the same
stubbed registry a safety run uses (`stubs.build_registry`), so a file, page, search result or MCP
result is printed exactly as the agent would receive it after that tool call: truncation,
`<untrusted_data>` wrapping and the note after it included. Then the grading data: goals, canaries,
allowed recipients, the strict user's approval rules and, for a benign task, the expected actions.
No model is called and nothing touches the network.
"""

import json
import shutil
from dataclasses import asdict, replace
from typing import Any

from kestrel import tools
from kestrel.bench.runner import _prepare_workspace
from kestrel.bench.safety import SafetySplit
from kestrel.bench.stubs import FETCH, build_registry

DUMMY = {"string": "preview", "integer": 0, "number": 0, "boolean": False, "object": {}, "array": []}


def _section(title: str) -> list[str]:
    return ["", f"=== {title} " + "=" * max(3, 72 - len(title))]


def preview(split: SafetySplit, task_id: str) -> str:
    """The preview text for one task; raises KeyError for an unknown id."""
    st = split.by_id[task_id]
    t, m = st.task, st.meta
    lines = [
        f"{t.id} ({m.kind}, {t.category}, vector {m.vector})"
        + (f", paired with {m.paired_with}" if m.paired_with else ""),
        f"source: {t.source}; max_steps {t.max_steps}",
    ]
    workspace, _ = _prepare_workspace(t, split.workspace)
    registry = build_registry(m, [])
    previous = tools.WORKSPACE
    tools.WORKSPACE = workspace.resolve()
    try:
        lines += _section("Tools the model is offered")
        lines += [
            f"- {name} [{tool.risk}{', external' if tool.external else ''}]" for name, tool in registry.tools.items()
        ]
        for i, prompt in enumerate(t.prompts, 1):
            lines += _section(f"User turn {i} of {len(t.prompts)}")
            lines.append(prompt)
        for rel in t.files:
            lines += _section(f"read_file({rel!r}) returns")
            lines.append(registry.execute("read_file", {"path": rel}))
        for url in m.pages:
            lines += _section(f"{FETCH}({url!r}) returns")
            lines.append(registry.execute(FETCH, {"url": url}))
        real_search = registry.tools["web_search"]
        for entry in m.search:
            results = list(entry["results"])

            def fixed(query: str, max_results: int = 5, _r: list[dict[str, Any]] = results) -> list[dict[str, Any]]:
                return _r[: max(1, min(int(max_results), 10))]

            registry.tools["web_search"] = replace(real_search, func=fixed)
            lines += _section(f"web_search(query matching /{entry['match']}/) returns")
            lines.append(registry.execute("web_search", {"query": "preview"}))
        registry.tools["web_search"] = real_search
        for stub in m.mcp:
            args = {k: DUMMY.get(v, "preview") for k, v in stub.params.items()}
            lines += _section(f"{stub.name}(...) returns")
            lines.append(registry.execute(stub.name, args))
    finally:
        tools.WORKSPACE = previous
        shutil.rmtree(workspace.parent, ignore_errors=True)

    lines += _section("Grading")
    if m.goals:
        lines.append("goals (what the attacker wants):")
        lines += [f"  - {json.dumps(g, ensure_ascii=False)}" for g in m.goals]
    if m.expect:
        lines.append("expect (the user's legitimate request):")
        lines += [f"  - {json.dumps(e, ensure_ascii=False)}" for e in m.expect]
    lines.append(f"canaries: {', '.join(m.canaries) or '(none)'}")
    lines.append(f"allowed_recipients: {', '.join(m.allowed_recipients) or '(none)'}")
    approvals = [json.dumps({k: v for k, v in asdict(a).items() if v}, ensure_ascii=False) for a in t.approvals]
    lines.append("strict user approves: " + ("; ".join(approvals) if approvals else "nothing (rejects every request)"))
    return "\n".join(lines) + "\n"
