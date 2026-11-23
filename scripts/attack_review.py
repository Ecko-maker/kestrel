"""Regenerate evals/kestrelbench/safety/ATTACK_REVIEW.md: one row per attack for the owner's review of
the final injection text.

    uv run --no-sync python scripts/attack_review.py
    uv run --no-sync python scripts/attack_review.py --set atk-a atk-b --final "as in file" --review "OK"

--set fills the two owner cells of those attacks (refused while one still has scaffolding).
No model calls. Like the worksheet, this file never quotes attack text: each row says where the text
sits, what it must aim at (the goal target) and which file holds the canary. The two owner columns
(final injection text, OK / change) and everything under "## Notes" are kept when it is regenerated.
"""

import argparse
import csv
import io
import re
import sys
from datetime import date

import yaml
from attack_worksheet import location

from kestrel.bench.safety import (
    DEFAULT_ALLOWED,
    SAFETY_DIR,
    SAFETY_VERSION,
    SafetyTask,
    find_markers,
    load_safety,
)

OUT = SAFETY_DIR / "ATTACK_REVIEW.md"
NOTES = "## Notes"


def raw_tasks() -> dict[str, tuple[str, dict]]:
    """Task id -> (task file name, raw YAML mapping): page file names are only in the raw YAML."""
    out = {}
    for path in sorted((SAFETY_DIR / "tasks").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("tasks", []):
            out[raw["id"]] = (path.name, raw)
    return out


def injection_in(st: SafetyTask, raw: dict) -> str:
    """Where the attacker's text sits once written: a served page file, an MCP result, a search
    snippet, or the task's first non-private file."""
    if raw.get("datasets"):  # data vector: the `note` cell(s) of a CSV the data tools read (lines: the data worksheet)
        name, spec = next(iter(raw["datasets"].items()))
        return f"dataset `{name}` `note` cell (`{spec['file']}`; line in `DATA_ATTACK_WORKSHEET.md`)"
    pages = [v["file"] for v in (raw.get("pages") or {}).values() if isinstance(v, dict) and "file" in v]
    if pages:
        return f"web page `{pages[0]}`"
    if st.meta.mcp:
        return f"MCP result `{st.meta.mcp[0].name}`"
    if st.meta.search and not st.task.files:
        return "search snippet"
    attacker = [p for p in st.task.files if not p.startswith("private/")]
    if st.meta.search and not attacker:
        return "search snippet"
    return f"file `{attacker[0]}`" if attacker else "(none)"


def target(goal: dict) -> str:
    """What the injection must name or aim at, from the goal."""
    t = goal["type"]
    if t == "call":
        args = goal.get("args", {})
        what = args.get("to") or args.get("path") or (f"title `{args['title']}`" if "title" in args else "")
        return f"`{goal['tool']}` → {what if what.startswith('title') else f'`{what}`'}"
    if t == "modify":
        return f"change `{goal['path']}` (any write or delete)"
    if t == "write_outside":
        return f"a write outside `{goal.get('allowed', DEFAULT_ALLOWED)}`, e.g. `{goal['path']}` (reads don't count)"
    if goal["via"] == "answer":
        url = re.search(r"\((https?://[^)?]+)", goal.get("example", ""))
        return f"markdown image in the answer, `{url.group(1) if url else '?'}`"
    if goal["via"] == "web_search":
        return "`web_search` query carrying the canary"
    args = goal.get("args", {})
    if "url" not in args and "to" in args:  # a leak by message: the address is the target
        return f"`{goal['via']}` → `{args['to']}` carrying the canary"
    url = str(args.get("url", ""))
    b64 = " (base64)" if "{canary_b64}" in url else ""
    return f"`{goal['via']}` of `{url.split('?')[0]}` carrying the canary{b64}"


def canary_home(st: SafetyTask) -> str:
    """The file (or MCP result) that holds the task's canary: the injection points there, never quotes it."""
    homes = [f"`{p}`" for p, text in st.task.files.items() if any(c in text for c in st.meta.canaries)]
    homes += [f"MCP `{m.name}`" for m in st.meta.mcp if any(c in m.result for c in st.meta.canaries)]
    for name, tables in st.meta.datasets.items():  # data vector: a private column next to the attacker's cells
        for table, text in tables.items():
            rows = list(csv.reader(io.StringIO(text)))
            cols = sorted(
                {rows[0][j] for row in rows[1:] for j, v in enumerate(row) if any(c in v for c in st.meta.canaries)}
            )
            homes += [f"dataset `{name}` table `{table}`, column `{c}`" for c in cols]
    return ", ".join(homes) or "(none)"


def old_cells() -> tuple[dict[str, tuple[str, str]], str]:
    """(task id -> (final text cell, review cell), the notes section) from the current file."""
    if not OUT.exists():
        return {}, ""
    text = OUT.read_text(encoding="utf-8")
    cells = {}
    for line in text.split(NOTES, 1)[0].splitlines():
        row = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(row) > 3 and (m := re.fullmatch(r"`([\w-]+)`", row[1])):
            cells[m.group(1)] = (row[-2], row[-1])
    return cells, (NOTES + text.split(NOTES, 1)[1]) if NOTES in text else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate ATTACK_REVIEW.md (owner columns kept).")
    parser.add_argument("--set", nargs="+", metavar="TASK", help="attack ids whose two owner cells to fill")
    parser.add_argument("--final", default="as in file", help="with --set: the Final injection text cell")
    parser.add_argument("--review", default="", help="with --set: the Owner: OK / change cell")
    args = parser.parse_args(argv)
    split = load_safety(SAFETY_DIR)
    markers = find_markers(SAFETY_DIR)
    raw = raw_tasks()
    cells, notes = old_cells()
    attacks = [st for st in split.tasks if st.meta.kind == "attack" and not st.meta.probe]
    if args.set:
        by_id = {st.id: st for st in attacks}
        if any("|" in v or "\n" in v for v in (args.final, args.review)):
            print("--final/--review: no `|` or line break (they are table cells).", file=sys.stderr)
            return 2
        unknown = [t for t in args.set if t not in by_id]
        open_ = [t for t in args.set if t in by_id and location(by_id[t], markers)[1]]
        if unknown or open_:
            print(f"--set refused: not an attack: {unknown or '-'}; scaffolding left: {open_ or '-'}", file=sys.stderr)
            return 1
        cells |= {t: (args.final.strip(), args.review.strip()) for t in args.set}
    unfinished = 0
    L = [
        "# Attack review (owner)",
        "",
        f"Safety split `SAFETY_VERSION` {SAFETY_VERSION}, generated {date.today().isoformat()} by "
        "`scripts/attack_review.py` from the loader. **Attack text is the owner's** (owner decision 1): this file "
        "never quotes it. Each row says where the text sits, what it must aim at, and where the canary is.",
        "",
        "**Rules for each injection:** it names the row's target exactly; it never contains the canary itself "
        "(point at the canary's file, or 'the reference below'); it fits its surroundings (an email reads like an "
        "email, an HTML comment like one); and no `{{`, `}}`, `# spec` or `neutral spec` is left anywhere. "
        "`kestrel bench validate --split safety` lists any leftover as TODO and exits 1, and a real-model run "
        "refuses while one remains.",
        "",
        "**State:** *scaffolding left* = validate still lists this task as TODO (a `{{...}}` block or spec note "
        "remains); *written* = no marker or scaffolding found.",
        "",
        "Fill in **Final injection text** with the text as written (or `as in file`), then **Owner: OK / change**. "
        "Regenerating keeps both columns and the Notes section.",
        "",
        f"## Attacks ({len(attacks)})",
        "",
        "| # | Task | Vector | Injection in | Goal target | Canary in | State | Final injection text (owner) "
        "| Owner: OK / change |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, st in enumerate(attacks, 1):
        _, mark = location(st, markers)
        state = f"scaffolding left (`{mark}`)" if mark else "written"
        unfinished += bool(mark)
        final, review = cells.get(st.id, ("", ""))
        L.append(
            f"| {i} | `{st.id}` | {st.meta.vector} | {injection_in(st, raw[st.id][1])} | "
            f"{'; '.join(target(g) for g in st.meta.goals)} | {canary_home(st)} | {state} | {final} | {review} |"
        )
    L += ["", f"{unfinished} of {len(attacks)} attacks still have scaffolding.", ""]
    if notes:
        L.append(notes.rstrip("\n"))
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}: {len(attacks)} attacks, {unfinished} with scaffolding left.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
