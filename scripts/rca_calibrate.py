"""Calibrate the data tools' confidence label offline (docs/data-rca-design.md 3.3). No model, no network.

    uv run --no-sync python scripts/rca_calibrate.py tune     # dev seeds 1-1000 per scenario -> tuning.json
    uv run --no-sync python scripts/rca_calibrate.py report   # held-out seeds 10001-11000, frozen cut-offs

`tune` reads development seeds only and prints the cut-offs to freeze in src/kestrel/data/analysis.py (CUTOFFS);
it never reads a held-out seed. `report` uses the frozen CUTOFFS on the held-out seeds and writes each target with
its Clopper-Pearson 95% interval; a missed target is reported, not re-tuned.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from kestrel.data import analysis as an
from kestrel.data import calibration as cal
from kestrel.data import generator as gen

OUT = Path(__file__).resolve().parents[1] / "evals" / "kestrelbench" / "rca" / "calibration"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["tune", "report", "render"])
    parser.add_argument("--seeds", type=int, help="use only the first N seeds of the split (a quick look; not saved)")
    args = parser.parse_args()
    if args.command == "render":  # rebuild heldout.md from heldout.json, no recomputation
        text = render(json.loads((OUT / "heldout.json").read_text(encoding="utf-8")))
        (OUT / "heldout.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    split = "dev" if args.command == "tune" else "held-out"
    seeds = list(cal.seeds_for(split))
    if args.seeds:
        seeds = seeds[: args.seeds]
    started = time.time()
    rows = cal.collect(gen.SCENARIOS, seeds)
    print(
        f"{len(rows):,} candidates from {len(seeds)} seeds x {len(gen.SCENARIOS)} scenarios "
        f"({time.time() - started:.0f} s)"
    )
    if args.command == "tune":
        result = cal.tune(rows)
        result |= {"split": split, "seeds": [seeds[0], seeds[-1]], "grid": cal.GRID, "start": asdict(an.START)}
        print(json.dumps({k: result[k] for k in ("cutoffs", "targets", "met", "start_met")}, indent=2))
        if not args.seeds:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "tuning.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Wrote {OUT / 'tuning.json'}. Freeze these cut-offs in src/kestrel/data/analysis.py (CUTOFFS).")
        return 0
    if an.CUTOFFS == an.START and not (OUT / "tuning.json").is_file():
        sys.exit("Tune first: the held-out report uses the frozen cut-offs.")
    t = cal.targets(rows, an.CUTOFFS)
    met = cal.met(t)
    report: dict = {
        "split": split,
        "seeds": [seeds[0], seeds[-1]],
        "cutoffs": asdict(an.CUTOFFS),
        "frozen": an.FROZEN,
        "targets": {},
    }
    for key, target in TARGETS.items():
        k, n = t[key]["k"], t[key]["n"]
        low, high = cal.clopper_pearson(k, n)
        report["targets"][key] = {
            "k": k,
            "n": n,
            "rate": k / n if n else None,
            "ci_low": low,
            "ci_high": high,
            "target": target,
            "met": met[target],
        }
    text = render(report)
    print(text)
    if not args.seeds:
        (OUT / "heldout.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (OUT / "heldout.md").write_text(text, encoding="utf-8")
    return 0


TARGETS = {
    "high": "high >= 0.90",
    "medium": "0.50 <= medium <= 0.90",
    "low": "low < 0.50",
    "no_change_any": "no-change any <= 0.05",
}
MEASURE = {
    "high": ("share of candidates labelled high that are the planted cause", ">= 90%"),
    "medium": ("share of candidates labelled medium that are the planted cause", "50% to 90%"),
    "low": ("share of candidates labelled low that are the planted cause", "< 50%"),
    "no_change_any": ("share of no-change datasets with any candidate above low", "<= 5%"),
}


def render(report: dict) -> str:
    """The held-out page from its JSON (`render` rebuilds it without recomputing)."""
    lines = [
        f"# rca calibration: held-out seeds {report['seeds'][0]}-{report['seeds'][1]} per scenario",
        "",
        f"Cut-offs frozen {report.get('frozen', an.FROZEN)} from the dev seeds (`tuning.json`), unchanged here: "
        f"`{report['cutoffs']}`. Read once; a missed target is reported, not re-tuned.",
        "",
        "| Measure | Target | k / n | Rate | 95% CI (Clopper-Pearson) | Met |",
        "|---|---|---|---|---|---|",
    ]
    for key, (measure, target) in MEASURE.items():
        v = report["targets"][key]
        rate = "n/a" if v["rate"] is None else f"{v['rate']:.1%}"
        lines.append(
            f"| {measure} | {target} | {v['k']:,} / {v['n']:,} | {rate} | "
            f"{v['ci_low']:.1%}-{v['ci_high']:.1%} | {'yes' if v['met'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
