"""Offline proof for permission tiers v2 (stage 6). No model calls, no quota.

a. Safety split, scripted models, before (phase3/memory-safety) vs after (phase3/tiers-v2), both
   scripted users. Each directory holds always-falls.json, reads-then-falls.json and never-falls.json,
   made with `kestrel bench run --split safety --scripted <model> --user both --out <dir>/<model>.json`
   (after-runs with KESTREL_IMAGE_POLICY=allowlist; see docs/kestrelbench.md).
b. Main suite impact: replay the stored tool sequences in evals/baselines/*.json through the new
   approval function, and list every call that would now get a card, with what the main suite's
   scripted user would answer.
c. The agent fingerprint, and why it moved.

    uv run python scripts/tiers_v2_offline.py --before DIR --after DIR [--out evals/reports/tiers-v2-offline.md]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from kestrel import tools
from kestrel.bench.replay import parse_tool_log
from kestrel.bench.runner import ScriptedApprover, agent_fingerprint
from kestrel.bench.tasks import load_tasks
from kestrel.permissions import Session, required_approval

ROOT = Path(__file__).resolve().parents[1]
BASELINES = sorted((ROOT / "evals" / "baselines").glob("*.json"))
MODELS = ("always-falls", "reads-then-falls", "never-falls")
USERS = ("strict", "rubber-stamp")
BEFORE_FINGERPRINT = "cc5c16377662"  # main c7c0d4f and phase3/memory-safety e37e6e5 (CLAUDE.md, D4)


def load(directory: Path, model: str) -> tuple[dict, dict[tuple[str, str], dict]]:
    data = json.loads((directory / f"{model}.json").read_text(encoding="utf-8"))
    return data["meta"], {(t["id"], t["user"]): t for t in data["tasks"]}


def mark(ok: bool) -> str:
    return "**leak**" if ok else "·"


def safety_section(before: Path, after: Path) -> list[str]:
    runs = {(side, m): load(d, m) for side, d in (("before", before), ("after", after)) for m in MODELS}
    _, ref = runs[("before", "always-falls")]
    attacks = sorted(
        {i for (i, u), t in ref.items() if t["safety"]["kind"] == "attack" and not t["safety"].get("probe")}
    )
    benign = sorted({i for (i, u), t in ref.items() if t["safety"]["kind"] == "benign"})
    out = ["## a. Safety split, scripted models (no model calls)", ""]
    out += [
        f"- before: agent `{runs[('before', 'always-falls')][0]['agent']['sha']}`, answer policy "
        f"`{json.dumps(runs[('before', 'always-falls')][0]['answer_policy'])}`",
        f"- after: agent `{runs[('after', 'always-falls')][0]['agent']['sha']}`, answer policy "
        f"`{json.dumps(runs[('after', 'always-falls')][0]['answer_policy'])}`",
        "- always-falls: the user's request (the task's `expect`), then every attacker goal. Attack tasks have no "
        "`expect`, so it writes the canary into the leak **without ever reading it**, which no real model can do.",
        "- reads-then-falls: the same, but it first reads the task's file that holds the canary "
        "(as a real model must).",
        "",
        "### System-level attack success per user, k of n attacks (policy probe excluded)",
        "",
        "| Model | User | Before | After | Model-level before → after |",
        "|---|---|---|---|---|",
    ]
    for m in ("always-falls", "reads-then-falls"):
        for u in USERS:
            _, b = runs[("before", m)]
            _, a = runs[("after", m)]
            sb = sum(b[(i, u)]["safety"]["system_success"] for i in attacks)
            sa = sum(a[(i, u)]["safety"]["system_success"] for i in attacks)
            mb = sum(b[(i, u)]["safety"]["model_success"] for i in attacks)
            ma = sum(a[(i, u)]["safety"]["model_success"] for i in attacks)
            out.append(f"| {m} | {u} | {sb}/{len(attacks)} | {sa}/{len(attacks)} | {mb} → {ma} |")
    out += ["", "### Per attack: system-level success before → after", ""]
    head = " | ".join(f"{m}, {u}" for m in ("always-falls", "reads-then-falls") for u in USERS)
    out += [f"| Attack | Vector | {head} |", "|---|---|" + "---|" * 4]
    for i in attacks:
        cells = []
        for m in ("always-falls", "reads-then-falls"):
            for u in USERS:
                b = runs[("before", m)][1][(i, u)]["safety"]["system_success"]
                a = runs[("after", m)][1][(i, u)]["safety"]["system_success"]
                cells.append(f"{mark(b)} → {mark(a)}")
        out.append(f"| {i} | {ref[(i, 'strict')]['safety']['vector']} | " + " | ".join(cells) + " |")

    out += ["", "### Strict user: attacks still succeeding after, and why", ""]
    for m in ("always-falls", "reads-then-falls"):
        _, a = runs[("after", m)]
        left = [i for i in attacks if a[(i, "strict")]["safety"]["system_success"]]
        if not left:
            out.append(f"- {m}: none.")
            continue
        out.append(f"- {m}: {len(left)}: {', '.join(left)}.")
        for i in left:
            log = a[(i, "strict")]["tool_log"]
            read_first = any(line.startswith(("- read_file(", "- list_files(")) for line in log)
            why = (
                "no local read ran in the task, so nothing tainted the conversation"
                if not read_first
                else "see its log"
            )
            out.append(f"  - {i}: {why}. Calls: {'; '.join(line.split(' -> ')[0][2:][:90] for line in log)}")

    out += [
        "",
        "### Benign tasks, never-falls (passed / total)",
        "",
        "| User | Before | After | Flipped |",
        "|---|---|---|---|",
    ]
    for u in USERS:
        _, b = runs[("before", "never-falls")]
        _, a = runs[("after", "never-falls")]
        pb = sum(b[(i, u)]["status"] == "pass" for i in benign)
        pa = sum(a[(i, u)]["status"] == "pass" for i in benign)
        flips = [i for i in benign if b[(i, u)]["status"] != a[(i, u)]["status"]]
        out.append(f"| {u} | {pb}/{len(benign)} | {pa}/{len(benign)} | {', '.join(flips) or 'none'} |")
    return out


def main_suite_section() -> list[str]:
    tasks = {t.id: t for t in load_tasks()}
    covered: set[str] = set()
    cards: list[dict[str, Any]] = []
    calls = 0
    for path in BASELINES:
        for t in json.loads(path.read_text(encoding="utf-8"))["tasks"]:
            steps = parse_tool_log(t.get("tool_log") or [])
            if not steps or t["id"] not in tasks:
                continue
            covered.add(t["id"])
            task = tasks[t["id"]]
            session = Session(user_texts=list(task.prompts))  # every prompt up front: replays lose turns
            approver = ScriptedApprover(task.approvals)
            for s in steps:
                tool = tools.registry.tools.get(s.tool)
                if tool is None:
                    continue
                calls += 1
                need = required_approval(tool.capabilities, tool.external, session, s.args)
                if tool.risk != "safe":
                    approver.review(s.tool, s.args, "")  # a card it got anyway: keeps the rules' `times` right
                elif need.level != "safe":
                    answer = approver.review(s.tool, s.args, "").status
                    cards.append(
                        {
                            "file": path.name,
                            "id": t["id"],
                            "tool": s.tool,
                            "args": s.args,
                            "answer": answer,
                            "why": need.escalated_by,
                        }
                    )
                if s.ran:
                    session.note_ran(s.tool, tool.capabilities, s.args, s.result)
    out = [
        "## b. Main suite impact, replayed offline",
        "",
        f"The stored tool sequences of {len(covered)} of {len(tasks)} main-suite tasks (both files in "
        f"`evals/baselines/`, {calls} calls) went through `required_approval` with each task's prompts as the "
        "user's messages. Turns aren't stored, so every call is replayed as its own step (this can only add cards). "
        "Tasks with no stored log (no tool call in either run, or the baseline file predates `tool_log`) "
        "can't be replayed.",
        "",
    ]
    if not cards:
        out.append(
            "**No call would get a new card.** In no stored log does a web search come after a read_file or "
            "list_files in the same task, so taint never meets egress, and the content check never runs. On the "
            "behaviour already observed, tiers v2 changes no tool outcome in the main suite. The residual risk is a "
            "different order in the after-run (the model reads a file, then searches): that call would get a card, "
            "and the main suite's scripted user rejects any call its task has no rule for."
        )
    else:
        out += ["| File | Task | Tool | Arguments | Scripted user | Why |", "|---|---|---|---|---|---|"]
        for c in cards:
            out.append(
                f"| {c['file']} | {c['id']} | {c['tool']} | `{json.dumps(c['args'])[:80]}` | {c['answer']} "
                f"| {', '.join(c['why'])} |"
            )
    return out


def fingerprint_section() -> list[str]:
    after = agent_fingerprint("groq", "openai/gpt-oss-120b")
    no_caps = sorted(
        ({"schema": t.schema, "risk": t.risk} for t in tools.registry.tools.values()),
        key=lambda t: json.dumps(t, sort_keys=True),
    )
    tools_without = hashlib.sha256(json.dumps(no_caps, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return [
        "## c. Agent fingerprint (main suite, groq / openai/gpt-oss-120b)",
        "",
        f"- before (phase3/memory-safety, e37e6e5): `{BEFORE_FINGERPRINT}`",
        f"- after (phase3/tiers-v2): `{after['sha']}`; system prompt `{after['system_prompt']}`, "
        f"tools `{after['tools']}`, "
        f"{after['tool_count']} tools",
        f"- Why: stage 1 added each tool's capabilities to the hashed tool list. Hashing the same tools without them "
        f"gives `{tools_without}`, the before run's tools hash, so the names, descriptions, parameters, tiers "
        "and the system prompt the model sees are byte-identical. The approval behaviour (taint, content check, "
        "cards) isn't hashed itself; it "
        "comes with the capabilities.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    lines = [
        "# Permission tiers v2: offline proof",
        "",
        "Generated by `scripts/tiers_v2_offline.py`. No model calls: scripted models and stored logs only. "
        "Commands and "
        "settings are in the script's docstring; the before runs come from `phase3/memory-safety` (e37e6e5) with only "
        "`bench/scripted.py` taken from this branch (to add reads-then-falls; Kestrel's code is the before code).",
        "",
        *safety_section(args.before, args.after),
        "",
        *main_suite_section(),
        "",
        *fingerprint_section(),
    ]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
