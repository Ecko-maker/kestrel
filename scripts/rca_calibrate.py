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
    parser.add_argument("command", choices=["tune", "report"])
    parser.add_argument("--seeds", type=int, help="use only the first N seeds of the split (a quick look; not saved)")
    args = parser.parse_args()
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
    lines = [
        f"# rca calibration, held-out seeds {seeds[0]}-{seeds[-1]} per scenario (frozen {an.FROZEN})",
        "",
        f"Cut-offs: `{asdict(an.CUTOFFS)}`",
        "",
        "| Target | k / n | Rate | 95% CI (Clopper-Pearson) | Met |",
        "|---|---|---|---|---|",
    ]
    met = cal.met(t)
    names = {
        "high": "high >= 0.90",
        "medium": "0.50 <= medium <= 0.90",
        "low": "low < 0.50",
        "no_change_any": "no-change any <= 0.05",
    }
    report = {"split": split, "seeds": [seeds[0], seeds[-1]], "cutoffs": asdict(an.CUTOFFS), "targets": {}}
    for key, label in names.items():
        k, n = t[key]["k"], t[key]["n"]
        low, high = cal.clopper_pearson(k, n)
        rate = k / n if n else None
        report["targets"][key] = {
            "k": k,
            "n": n,
            "rate": rate,
            "ci_low": low,
            "ci_high": high,
            "target": label,
            "met": met[label],
        }
        shown = "n/a" if rate is None else f"{rate:.1%}"
        lines.append(
            f"| P(planted cause | {key}) {label} | {k} / {n} | {shown} | {low:.1%}-{high:.1%} | "
            f"{'yes' if met[label] else 'no'} |"
            if key != "no_change_any"
            else f"| P(any high or medium | no-change) {label.split()[-2:]} | {k} / {n} | {shown} | "
            f"{low:.1%}-{high:.1%} | {'yes' if met[label] else 'no'} |"
        )
    text = "\n".join(lines) + "\n"
    print(text)
    if not args.seeds:
        (OUT / "heldout.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (OUT / "heldout.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
