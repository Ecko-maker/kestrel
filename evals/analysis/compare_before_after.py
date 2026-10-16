"""Compare a safety-split "before" run with an "after" run (tiers v2), offline.

    uv run python evals/analysis/compare_before_after.py \
        --before safety-before-strict-x3.json safety-before-rubber-attacks.json \
        --after safety-after-strict-x3.json safety-after-rubber-attacks.json

Each side takes one or more KestrelBench safety results files (the strict and rubber-stamp passes
are separate files). Prints, per side: model-level, system-strict and system-rubber-stamp attack
success (an attack counts if ANY repeat succeeded, policy probes left out) as k/n with an exact
Clopper-Pearson 95% interval; the policy probes on their own lines; and the benign tasks one by
one, flagging any that passed before and fail after as POSSIBLE OVER-GATING.

Scoring is the harness's own (kestrel.bench.safety.score); this script only reads the files and
formats. Read-only, no network, no model calls.

The results files carry no split-wide fingerprint, so one is derived here: the split version plus
every task's `task_sha` (its definition and fixture workspace). Different = the two sides graded
different fixtures, and their difference is not the defense's effect alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kestrel.bench.safety import SafetyScore, UserScore, score
from kestrel.bench.stats import Rate

# report name -> (scripted user, level), as in kestrel.bench.safety.LEVELS: model level from strict
LEVELS = {
    "model-level": ("strict", "model"),
    "system-strict": ("strict", "system"),
    "system-rubber-stamp": ("rubber-stamp", "system"),
}
LEVELS_USERS = {user for user, _ in LEVELS.values()}
OVER_GATING = "POSSIBLE OVER-GATING"
MISMATCH = "FINGERPRINT MISMATCH — before/after graded different fixtures"


@dataclass
class Side:
    """One side (before or after): the rows of all its files, each tagged with its scripted user."""

    name: str
    files: list[Path]
    metas: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    fingerprint: str
    problems: list[str] = field(default_factory=list)  # inconsistencies inside this side
    scored: SafetyScore = field(init=False)

    def __post_init__(self) -> None:
        self.scored = score(self.rows)

    def user(self, name: str) -> UserScore | None:
        return self.scored.users.get(name)

    def rate(self, report_level: str) -> Rate | None:
        user, level = LEVELS[report_level]
        u = self.user(user)
        if u is None or not u.attack_repeats:
            return None
        return u.model_asr if level == "model" else u.system_asr


def split_fingerprint(split_version: str, shas: dict[str, str]) -> str:
    """12 hex chars over the split version and every (task id, task_sha) pair, order-free."""
    blob = json.dumps([split_version, sorted(shas.items())])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def load_side(name: str, paths: list[Path]) -> Side:
    """Read the files of one side. Raises ValueError for a file that is not a safety run, or for a
    run that appears in two files (it would be counted twice)."""
    metas: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    shas: dict[str, str] = {}
    seen: dict[tuple[str, int, str], Path] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        if meta.get("split") != "safety":
            raise ValueError(f"{path}: a {meta.get('split', 'main')} split run, not a safety split run")
        metas.append(meta)
        default_user = meta.get("user") if meta.get("user") in LEVELS_USERS else "strict"
        for raw in data.get("tasks", []):
            row = {**raw, "user": raw.get("user") or default_user}  # copy: the files stay untouched
            key = (row["id"], row.get("repeat", 1), row["user"])
            if key in seen:
                raise ValueError(f"{row['id']} repeat {key[1]} ({key[2]}) is in both {seen[key]} and {path}")
            seen[key] = path
            rows.append(row)
            sha = row.get("task_sha") or ""
            if shas.setdefault(row["id"], sha) != sha:
                problems.append(f"{row['id']} has two different task_sha values across the {name} files")
    versions = sorted({str(m.get("split_version")) for m in metas})
    if len(versions) > 1:
        problems.append(f"the {name} files are on different split versions: {', '.join(versions)}")
    if missing := sorted(i for i, s in shas.items() if not s):
        problems.append(f"{len(missing)} {name} task(s) have no task_sha, so the fingerprint can't cover them")
    fingerprint = split_fingerprint(",".join(versions), shas)
    return Side(name, paths, metas, rows, fingerprint, problems)


def fmt_rate(r: Rate | None) -> str:
    """'0/29 (95% CI 0.0-11.9%)', or 'not run'. ASCII, so any console prints it."""
    if r is None:
        return "not run"
    return f"{r.k}/{r.n} (95% CI {r.low * 100:.1f}-{r.high * 100:.1f}%)"


def benign_rows(before: Side, after: Side) -> list[tuple[str, str, str, str]]:
    """(task id, before cell, after cell, flag), strict user. Pass = passed every graded repeat,
    as in the harness's benign rate."""

    def cells(side: Side) -> dict[str, tuple[bool, str]]:
        u = side.user("strict")
        repeats = u.benign_repeats if u else {}
        return {i: (k == n, f"{'pass' if k == n else 'FAIL'} ({k}/{n})") for i, (k, n) in repeats.items()}

    b, a = cells(before), cells(after)
    out = []
    for task in sorted(set(b) | set(a)):
        flag = ""
        if task in b and task in a and b[task][0] and not a[task][0]:
            flag = OVER_GATING
        elif task in b and b[task][0] and task not in a:
            flag = "not graded after (error or skip)"
        out.append((task, b.get(task, (False, "-"))[1], a.get(task, (False, "-"))[1], flag))
    return out


def probe_lines(before: Side, after: Side) -> list[str]:
    """One line per policy probe and scripted user: [k, n] at model and system level."""
    lines = []
    for user in ("strict", "rubber-stamp"):
        pb = (u.probes if (u := before.user(user)) else {}) or {}
        pa = (u.probes if (u := after.user(user)) else {}) or {}
        for probe in sorted(set(pb) | set(pa)):
            lines.append(f"  {probe} ({user}): before {_probe_cell(pb, probe)} | after {_probe_cell(pa, probe)}")
    return lines


def _probe_cell(probes: dict[str, dict[str, list[int]]], probe: str) -> str:
    if probe not in probes:
        return "not run"
    m, s = probes[probe]["model"], probes[probe]["system"]
    return f"model {m[0]}/{m[1]}, system {s[0]}/{s[1]} repeats"


def _run_counts(side: Side) -> str:
    statuses: dict[str, int] = {}
    for r in side.rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(statuses.items()))


def _describe(side: Side) -> list[str]:
    lines = [f"{side.name}: split fingerprint {side.fingerprint}  ({_run_counts(side)})"]
    for path, meta in zip(side.files, side.metas, strict=True):
        agent = (meta.get("agent") or {}).get("sha", "?")
        lines.append(
            f"  {path.name}: {meta.get('split_version')}, user {meta.get('user')}, kinds {meta.get('kinds')}, "
            f"repeat {meta.get('repeat', 1)}, {meta.get('provider')}/{meta.get('model')}, agent {agent}"
        )
    lines += [f"  WARNING: {p}" for p in side.problems]
    return lines


def render(before: Side, after: Side) -> str:
    out: list[str] = []
    if before.fingerprint != after.fingerprint:
        bar = "!" * len(MISMATCH)
        out += [bar, MISMATCH, f"before {before.fingerprint} vs after {after.fingerprint}", bar, ""]
    out += ["KestrelBench safety split: before vs after", ""]
    out += [*_describe(before), *_describe(after), ""]

    out.append("Attack success rate (an attack counts if any repeat succeeded; policy probes excluded)")
    width = max(len(n) for n in LEVELS)
    out.append(f"  {'level':<{width}}  {'before':<28}  after")
    for level in LEVELS:
        out.append(f"  {level:<{width}}  {fmt_rate(before.rate(level)):<28}  {fmt_rate(after.rate(level))}")
    out.append("")

    out.append("Policy probes (graded like attacks; outside the ASR, its interval and the gate)")
    out += probe_lines(before, after) or ["  none in either side"]
    out.append("")

    rows = benign_rows(before, after)
    out.append("Benign tasks (strict user; pass = passed every repeat)")
    if rows:
        tw = max(len(r[0]) for r in rows)
        out.append(f"  {'task':<{tw}}  {'before':<12}  {'after':<12}")
        out += [f"  {t:<{tw}}  {b:<12}  {a:<12}  {flag}".rstrip() for t, b, a, flag in rows]
    else:
        out.append("  none graded under the strict user")
    flagged = [r[0] for r in rows if r[3] == OVER_GATING]
    out.append(f"  {OVER_GATING}: {len(flagged)} task(s){': ' + ', '.join(flagged) if flagged else ''}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--before", type=Path, nargs="+", required=True, help="the before side's results file(s)")
    parser.add_argument("--after", type=Path, nargs="+", required=True, help="the after side's results file(s)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # a narrow console encoding must not crash the report
        sys.stdout.reconfigure(errors="replace")
    try:
        before, after = load_side("before", args.before), load_side("after", args.after)
    except (OSError, ValueError, KeyError) as e:  # json.JSONDecodeError is a ValueError
        print(f"Can't compare: {e}", file=sys.stderr)
        return 2
    print(render(before, after))
    return 0


if __name__ == "__main__":
    sys.exit(main())
