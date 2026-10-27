"""Rule C verdict for the tiers-v2.1 main-suite after-run (docs/tiers-v2-design.md section 5, owner
decision 2026-10-21). Offline: reads results files, makes no model calls.

- Primary: zero worse flips against the baseline caused by a tiers-v2 card, by decision A(3)'s test: an
  escalated card (`escalated_by` set) that the scripted user rejected, and without which the run would
  have passed its checks. The first two parts are in the results; the third needs a person reading the
  run, so every worse flip with such a card is a candidate until `--card-review TASK=caused|not-caused`
  decides it. A worse flip with no rejected escalated card in any failing run is not card-caused.
- Secondary: the baseline and run 2 as per-task repeats (`stats.merge_references`) against the after-run
  x3; the paired 95% lower bound of (after - references) must be above -6 points. The same number as
  `kestrel bench compare <baseline> <after> --second-reference <run2> --margin 0.06`.
- Gate not met (primary pass, secondary fail), amended 2026-10-27: (a) with 0 `escalated_by` entries in the
  after-run the model saw the same agent as the baseline, so merge and report "non-inferiority not shown at
  6 points"; (b) with 1 or more, each escalated task is reviewed (`--escalation-review TASK=changed|unchanged`:
  did a card plausibly change its outcome?). Any "changed": no merge, fix and re-measure; all "unchanged":
  as (a).

A file that isn't finished (skipped rows, fewer than 3 graded repeats of a task, or a `.lock` beside it)
is reported as "PARTIAL: plumbing check, not a verdict", and `--out` refuses to write it.

    uv run --no-sync python scripts/rule_c_verdict.py ..\\kestrel-runs\\evals\\results\\main-after-v21-x3.json \\
        [--card-review TASK=not-caused] [--escalation-review TASK=unchanged] [--out evals/reports/rule-c-verdict.md]
    uv run --no-sync python scripts/rule_c_verdict.py --template --out evals/reports/rule-c-verdict.md
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kestrel.bench.stats import GRADED, Estimate, compare, judge_label, merge_references, per_task_pass

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evals" / "baselines" / "baseline-v1.1-judge-v2-final.json"
RUN2 = ROOT / "evals" / "baselines" / "run2-v1.1-judge-v2-final.json"
# evals/baselines/README.md; tests/test_baselines_pinned.py guards the files themselves
PINNED = {
    BASELINE.name: "bfd6bdeb6c3ce6b456bf184a5d99ed9eea45dbedbc25df164f5aaa65a5bc8ba3",
    RUN2.name: "29a607859aee5595cf1ce1d7768e6e7caa601598c7d75447e9bca59b0d03afa4",
}
MARGIN = 0.06
REPEATS = 3
AGENT = "35fac5ba6277"  # the main-suite agent rule C was fixed for (approval rules tiers-v2.1)
PARTIAL = "PARTIAL: plumbing check, not a verdict"
COMMAND = (
    "uv run --no-sync python scripts/rule_c_verdict.py "
    "..\\kestrel-runs\\evals\\results\\main-after-v21-x3.json --out evals/reports/rule-c-verdict.md"
)
TBD = "_(to fill)_"


@dataclass
class Input:
    role: str
    name: str
    sha256: str
    agent: str
    graded: int
    runs: int
    judge: str


@dataclass
class Flip:
    task: str
    reference: float  # the reference's per-task pass rate
    after: str  # "k/n" passed
    cards: list[str]  # rejected escalated cards in the failing after-runs: "run 2: send_message (taint:read_file)"
    cause: str  # "no card", "candidate", "card-caused", "not card-caused (owner review)"


@dataclass
class Verdict:
    partial: list[str]  # why the after-run isn't finished; empty = final
    inputs: list[Input]
    secondary: Estimate
    ref_rate: Estimate
    after_rate: Estimate
    worse: list[Flip]
    better: list[str]
    run2_worse: list[Flip]
    run2_better: list[str]
    escalations: list[tuple[str, int, str, str, str, str]]  # task, repeat, tool, decision, escalated_by, status
    errors: list[str]
    warnings: list[str] = field(default_factory=list)
    escalation_review: dict[str, str] = field(default_factory=dict)  # task -> "changed" | "unchanged"

    @property
    def escalated_tasks(self) -> list[str]:
        return sorted({e[0] for e in self.escalations})

    @property
    def unreviewed(self) -> list[str]:
        return [t for t in self.escalated_tasks if t not in self.escalation_review]

    @property
    def gate_not_met(self) -> str:
        """Which gate-not-met consequence applies (section 5, amended 2026-10-27), as the report states it."""
        if not self.escalations:
            return 'gate not met (a): 0 escalations; merge after #14; "non-inferiority not shown at 6 points"'
        if self.unreviewed:
            return f"gate not met (b): PENDING review of the escalated tasks ({', '.join(self.unreviewed)})"
        if changed := [t for t in self.escalated_tasks if self.escalation_review[t] == "changed"]:
            return f"gate not met (b): a card plausibly changed {', '.join(changed)}; no merge, fix and re-measure"
        return (
            'gate not met (b): no card changed an outcome; as (a), merge after #14; "non-inferiority not shown '
            'at 6 points"'
        )

    @property
    def candidates(self) -> list[str]:
        return [f.task for f in self.worse if f.cause == "candidate"]

    @property
    def primary(self) -> str:
        if any(f.cause == "card-caused" for f in self.worse):
            return "fail"
        return "review" if self.candidates else "pass"

    @property
    def secondary_pass(self) -> bool:
        return self.secondary.low > -MARGIN

    @property
    def outcome(self) -> str:
        if self.partial:
            return "no verdict (partial file)"
        if self.primary == "fail":
            return "FAIL"
        if self.primary == "review":
            return f"PENDING: review the candidate card-caused flips ({', '.join(self.candidates)})"
        return "PASS" if self.secondary_pass else self.gate_not_met


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        sys.exit(f"No such results file: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def describe(role: str, path: Path, data: dict[str, Any]) -> Input:
    rows, meta = data["tasks"], data.get("meta", {})
    return Input(
        role=role,
        name=path.name,
        sha256=sha256(path),
        agent=(meta.get("agent") or {}).get("sha") or "not recorded",
        graded=sum(r["status"] in GRADED for r in rows),
        runs=len(rows),
        judge=judge_label(meta),
    )


def partial_reasons(path: Path, after: dict[str, Any]) -> list[str]:
    reasons = []
    if path.with_name(path.name + ".lock").exists():
        reasons.append("a .lock file is beside it: the run is still live")
    rows = after["tasks"]
    if skipped := sum(r["status"] == "skipped" for r in rows):
        reasons.append(f"{skipped} of {len(rows)} runs not done yet")
    graded: dict[str, int] = {}
    for r in rows:
        graded[r["id"]] = graded.get(r["id"], 0) + (r["status"] in GRADED)
    if short := sorted(t for t, n in graded.items() if n < REPEATS):
        reasons.append(f"{len(short)} tasks have fewer than {REPEATS} graded runs")
    return reasons


def rejected_cards(rows: list[dict[str, Any]], task: str) -> list[str]:
    """Rejected escalated cards in the task's failing after-runs: decision A(3)'s first two parts."""
    out = []
    for r in rows:
        if r["id"] != task or r["status"] != "fail":
            continue
        for e in r.get("approvals", []):
            if e.get("escalated_by") and e.get("decision") == "rejected":
                out.append(f"run {r.get('repeat', 1)}: {e.get('tool')} ({', '.join(e['escalated_by'])})")
    return out


def flips(
    reference: dict[str, Any], after: dict[str, Any], review: dict[str, str]
) -> tuple[list[Flip], list[str], Estimate, Estimate, Estimate, list[str]]:
    c = compare(reference, after)
    if c.diff is None or c.a_rate is None or c.b_rate is None:
        sys.exit("No task was graded in both files.")
    ref, aft = per_task_pass(reference["tasks"]), per_task_pass(after["tasks"])
    worse = []
    for t in c.worse_in_b:
        runs = [r for r in after["tasks"] if r["id"] == t and r["status"] in GRADED]
        cards = rejected_cards(after["tasks"], t)
        if not cards:
            cause = "no card"
        elif review.get(t) == "caused":
            cause = "card-caused"
        elif review.get(t) == "not-caused":
            cause = "not card-caused (owner review)"
        else:
            cause = "candidate"
        passed = round(aft[t] * len(runs))
        worse.append(Flip(t, ref[t], f"{passed}/{len(runs)}", cards, cause))
    return worse, c.better_in_b, c.diff, c.a_rate, c.b_rate, c.warnings


def verdict(
    after_path: Path,
    review: dict[str, str],
    baseline: Path = BASELINE,
    run2: Path = RUN2,
    escalation_review: dict[str, str] | None = None,
) -> Verdict:
    for p in (baseline, run2):
        if p.name in PINNED and sha256(p) != PINNED[p.name]:
            sys.exit(f"{p.name} doesn't match its pinned SHA-256 (evals/baselines/README.md): refusing.")
    base, second, after = load(baseline), load(run2), load(after_path)
    if (after.get("meta", {}).get("split") or "main") != "main":
        sys.exit("Rule C is about the main split.")
    inputs = [
        describe("reference 1 (baseline)", baseline, base),
        describe("reference 2 (run 2)", run2, second),
        describe(f"after-run x{REPEATS}", after_path, after),
    ]
    warnings = []
    if inputs[2].agent != AGENT:
        warnings.append(f"the after-run's agent is {inputs[2].agent}; rule C was fixed for {AGENT}")
    worse, better, _, _, _, w1 = flips(base, after, review)
    unknown = sorted(set(review) - {f.task for f in worse if f.cards})
    if unknown:
        sys.exit(f"--card-review names tasks that aren't candidate flips: {', '.join(unknown)}")
    run2_worse, run2_better, _, _, _, _ = flips(second, after, review)
    c = compare(merge_references(base, second), after)
    assert c.diff is not None and c.a_rate is not None and c.b_rate is not None
    escalations = [
        (
            r["id"],
            r.get("repeat", 1),
            e.get("tool", "?"),
            e.get("decision", "?"),
            ", ".join(e["escalated_by"]),
            r["status"],
        )
        for r in after["tasks"]
        for e in r.get("approvals", [])
        if e.get("escalated_by")
    ]
    escalation_review = escalation_review or {}
    if unknown := sorted(set(escalation_review) - {e[0] for e in escalations}):
        sys.exit(f"--escalation-review names tasks with no escalated_by entry: {', '.join(unknown)}")
    errors = [
        f"{r['id']} run {r.get('repeat', 1)}: {r.get('error') or 'error'}"
        for r in after["tasks"]
        if r["status"] == "error"
    ]
    return Verdict(
        partial=partial_reasons(after_path, after),
        inputs=inputs,
        secondary=c.diff,
        ref_rate=c.a_rate,
        after_rate=c.b_rate,
        worse=worse,
        better=better,
        run2_worse=run2_worse,
        run2_better=run2_better,
        escalations=escalations,
        errors=errors,
        warnings=list(dict.fromkeys(warnings + w1 + c.warnings)),
        escalation_review=escalation_review,
    )


def _flip_rows(fs: list[Flip]) -> list[str]:
    if not fs:
        return ["| none | | | | |"]
    return [f"| `{f.task}` | {f.reference:.0%} | {f.after} | {'; '.join(f.cards) or 'none'} | {f.cause} |" for f in fs]


def render(v: Verdict | None) -> str:
    """The report. v=None gives the template with placeholders, in the same layout."""
    head = (
        "Status: **template**, filled by the command below when the run ends."
        if v is None
        else (f"Status: **{PARTIAL}** ({'; '.join(v.partial)})." if v.partial else "Status: **final**.")
    )
    out = [
        "# Rule C verdict: tiers v2.1, main suite",
        "",
        head,
        "",
        "Rule C (`docs/tiers-v2-design.md` section 5, owner decision 2026-10-21, fixed before any v2.1 result):",
        "- **Primary:** zero worse flips against the baseline caused by a tiers-v2 card (decision A(3): an "
        "escalated card the scripted user rejected, without which the run would have passed its checks).",
        f"- **Secondary:** baseline + run 2 as per-task repeats against the after-run x{REPEATS}; the paired "
        f"95% lower bound of (after - references) is above -{MARGIN * 100:.0f} points.",
        "- Primary fail = **FAIL**; both pass = **PASS**; primary pass and secondary fail = **gate not met** "
        "(flips examined and reported; no re-interpretation).",
        "",
        "What each outcome leads to (owner, section 5, 2026-10-27, before the final result; the partial 198/300 "
        "dry run had been seen; the gate-not-met consequence was amended twice that day, see section 5):",
        "- **PASS:** tiers-v2 merges into `main`, after PR #14.",
        "- **FAIL:** no merge; fix the cause, re-measure.",
        "- **Gate not met (a), 0 `escalated_by` entries in the after-run:** the model saw the same agent as the "
        "baseline (`evals/reports/fingerprint-diff-v1.1-to-v2.1.md`), so tiers-v2 can't have caused the gap. "
        'Merge after #14; the report states "non-inferiority not shown at 6 points" with the interval and cites '
        "the fingerprint diff; open a known issue on run-to-run drift above the margin; a same-day control (old "
        "agent, CI subset, ~28k, approval line first) is proposed for later, not run now.",
        "- **Gate not met (b), 1 or more entries:** each escalated task is reviewed (table below). If a card "
        "plausibly changed its outcome: no merge, fix and re-measure. Otherwise as (a).",
        "",
        "## Command",
        "",
        "```powershell",
        COMMAND,
        "```",
        "",
        "Offline (no model calls). It refuses baselines that don't match their pinned SHA-256 and won't write "
        "this file while the after-run is unfinished. Cross-check of the secondary number:",
        "`uv run --no-sync kestrel bench compare evals/baselines/baseline-v1.1-judge-v2-final.json "
        "..\\kestrel-runs\\evals\\results\\main-after-v21-x3.json --second-reference "
        "evals/baselines/run2-v1.1-judge-v2-final.json --margin 0.06`.",
        "",
        "## Inputs",
        "",
        "| Role | File | SHA-256 | Agent | Graded / runs | Judge |",
        "|---|---|---|---|---|---|",
    ]
    if v is None:
        out += [
            f"| reference 1 (baseline) | `{BASELINE.name}` | `{PINNED[BASELINE.name]}` | not recorded | 100 / 100 | "
            "groq/openai/gpt-oss-120b@v2 |",
            f"| reference 2 (run 2) | `{RUN2.name}` | `{PINNED[RUN2.name]}` | cc5c16377662 | 100 / 100 | "
            "groq/openai/gpt-oss-120b@v2 |",
            f"| after-run x{REPEATS} | `main-after-v21-x3.json` | {TBD} | {AGENT} (expected) | {TBD} / 300 | {TBD} |",
        ]
    else:
        out += [
            f"| {i.role} | `{i.name}` | `{i.sha256}` | {i.agent} | {i.graded} / {i.runs} | {i.judge} |"
            for i in v.inputs
        ]
    out += [
        "",
        "## Secondary: paired lower bound",
        "",
        "| References (mean of 2) | After-run x3 | After - references (paired, 95% CI) | Lower bound | Needed "
        "| Result |",
        "|---|---|---|---|---|---|",
    ]
    if v is None:
        out.append(f"| {TBD} | {TBD} | {TBD} | {TBD} | above -{MARGIN * 100:.0f} points | {TBD} |")
    else:
        out.append(
            f"| {v.ref_rate.fmt()} | {v.after_rate.fmt()} | {v.secondary.fmt(signed=True)} | "
            f"{v.secondary.low * 100:+.1f} points | above -{MARGIN * 100:.0f} points | "
            f"{'–' if v.partial else ('pass' if v.secondary_pass else 'fail')} |"
        )
    flip_head = [
        "| Task | Reference | After passed | Rejected escalated cards in failing runs | Cause |",
        "|---|---|---|---|---|",
    ]
    out += ["", "## Primary: worse flips against the baseline", "", *flip_head]
    out += [f"| {TBD} | | | | |"] if v is None else _flip_rows(v.worse)
    if v is not None:
        out += ["", f"Better against the baseline: {', '.join(f'`{t}`' for t in v.better) or 'none'}."]
    out += ["", "## Run 2's flips (listed, not the verdict)", "", *flip_head]
    out += [f"| {TBD} | | | | |"] if v is None else _flip_rows(v.run2_worse)
    if v is not None:
        out += ["", f"Better against run 2: {', '.join(f'`{t}`' for t in v.run2_better) or 'none'}."]
    out += [
        "",
        "## Every `escalated_by` entry in the after-run",
        "",
        "| Task | Run | Tool | Decision | Escalated by | Run status | Cause (filled by hand) |",
        "|---|---|---|---|---|---|---|",
    ]
    if v is None:
        out.append(f"| {TBD} | | | | | | |")
    elif not v.escalations:
        out.append("| none | | | | | | |")
    else:
        out += [f"| `{t}` | {k} | {tool} | {d} | {by} | {s} | |" for t, k, tool, d, by, s in v.escalations]
    out += [
        "",
        "## Escalated tasks for review (gate not met, branch b)",
        "",
        "Did a card plausibly change the task's outcome? Recorded with `--escalation-review TASK=changed|unchanged`.",
        "",
        "| Task | Entries | Decisions | After passed | Baseline | Review |",
        "|---|---|---|---|---|---|",
    ]
    if v is None:
        out.append(f"| {TBD} | | | | | |")
    elif not v.escalations:
        out.append("| none: 0 entries, so branch (a) applies | | | | | |")
    else:
        for t in v.escalated_tasks:
            mine = [e for e in v.escalations if e[0] == t]
            decisions = ", ".join(f"{d} x{n}" for d, n in sorted(Counter(e[3] for e in mine).items()))
            runs = {(e[1], e[5]) for e in mine}
            after = f"{sum(s == 'pass' for _, s in runs)}/{len(runs)} escalated runs"
            base = next((f"{f.reference:.0%}" for f in v.worse if f.task == t), "not worse")
            out.append(
                f"| `{t}` | {len(mine)} | {decisions} | {after} | {base} | {v.escalation_review.get(t, 'to review')} |"
            )
    out += ["", "## Verdict", "", "| Primary | Secondary | `escalated_by` entries | Outcome |", "|---|---|---|---|"]
    if v is None:
        out.append(f"| {TBD} | {TBD} | {TBD} | {TBD} |")
    else:
        sec = "–" if v.partial else ("pass" if v.secondary_pass else "fail")
        prim = "–" if v.partial else v.primary
        count = f"{len(v.escalations)} in {len(v.escalated_tasks)} tasks"
        out.append(f"| {prim} | {sec} | {count} | **{v.outcome}** |")
    out += ["", "## Notes", ""]
    if v is None:
        out.append(f"Errors, warnings and each flip's cause: {TBD}")
    else:
        out += [f"- Error: {e}" for e in v.errors] or ["- Errors: none."]
        out += [f"- Warning: {w}" for w in v.warnings]
    return "\n".join(out) + "\n"


def parse_review(items: list[str], flag: str = "--card-review", values: tuple[str, str] = ("caused", "not-caused")):
    review = {}
    for item in items:
        task, _, value = item.partition("=")
        if value not in values:
            sys.exit(f"{flag} takes TASK={values[0]} or TASK={values[1]}, not {item!r}")
        review[task] = value
    return review


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("after", type=Path, nargs="?", help="the after-run x3 results file")
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--run2", type=Path, default=RUN2)
    ap.add_argument("--card-review", action="append", default=[], metavar="TASK=caused|not-caused")
    ap.add_argument(
        "--escalation-review",
        action="append",
        default=[],
        metavar="TASK=changed|unchanged",
        help="gate not met, branch (b): did a card plausibly change this escalated task's outcome?",
    )
    ap.add_argument("--out", type=Path, help="write the Markdown report here (refused for a partial file)")
    ap.add_argument("--template", action="store_true", help="the report with placeholders, no inputs read")
    args = ap.parse_args(argv)
    if args.template:
        text = render(None)
    else:
        if args.after is None:
            ap.error("give the after-run results file, or --template")
        esc = parse_review(args.escalation_review, "--escalation-review", ("changed", "unchanged"))
        v = verdict(args.after, parse_review(args.card_review), args.baseline, args.run2, esc)
        if v.partial and args.out:
            sys.exit(f"{PARTIAL}: not writing {args.out} ({'; '.join(v.partial)}).")
        text = render(v)
        if v.partial:
            text = f"{PARTIAL}\n\n{text}\n{PARTIAL}\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
