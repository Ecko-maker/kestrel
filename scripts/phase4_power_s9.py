"""Power of the Phase 4 quality claim under rule A's routing (section 9's proposal; analysis only, no model calls).

Section 8.3's simulation (scripts/phase4_power.py, frozen, not changed) asked what happens if the routed system
behaves like the teacher, minus some scenario. Section 8 has now measured the student, so this one asks: what if the
router's taint rule gives the teacher's result on tainted runs and the student's on the rest?

From the pinned files only, so anyone can reproduce it:
- teacher: main-after-v21-x3.json, check-pass counts out of 3;
- student: routed-main-r5.json (section 8's router: student first, teacher on 3.6% of task runs);
- taint: a run is tainted if its tool log shows read_file or list_files ran and returned data
  (permissions.returned_local_data), 201 of 500 runs, the same count as the traces give.

Per task, taint share w = tainted runs / 5. Routed pass probability, one draw per simulated run:
    q = w x q_tainted + (1 - w) x q_untainted
- q_untainted: the Beta posterior of that task's section 8 runs that didn't taint; with none, its posterior over all 5;
- q_tainted depends on the scenario:
    teacher    : the teacher's posterior (the fix hands those runs over entirely: the optimistic end);
    half       : the mean of the teacher's and the student's posterior on those runs (the student still wrote the
                 steps up to the read, and may have read the wrong file, so the teacher can't always recover);
    student    : the student's own posterior on those runs (the fix changes nothing about quality: the pessimistic end).

Statistic as section 8.2: D = routed - 0.95 x teacher per task; shown when the percentile-bootstrap 2.5% bound of
mean(D) is > 0; FAIL on quality when the 97.5% bound is < 0. The teacher side is the real file, fixed. Student rates
were measured under section 8's two samplings and section 9 fixes one (docs/phase4-plan.md 8.7), so the student part
is an assumption, not a measurement of 0.7 / 0.8.

    uv run --no-sync python scripts/phase4_power_s9.py
"""

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kestrel.permissions import returned_local_data  # noqa: E402

AFTER = ROOT / "evals" / "after-runs"
INPUTS = {
    "teacher": (AFTER / "main-after-v21-x3.json", "8948b434fdcf"),
    "routed": (AFTER / "routed-main-r5.json", "02af56169f68"),
}
SIMS = INNER = 2000
SEED = 2026
RATIO, CONTEXT_RATIO = 0.95, 0.90
REPS = (3, 5, 8)
PRIOR = (1.48, 0.12)  # section 8.3's fit on the after-run x3, Beta(1.48, 0.12)
SCENARIOS = ("teacher", "half", "student")
LOCAL_READ = re.compile(r"^- (read_file|list_files)\(.*?\) \[ran\] -> (.*)$", re.DOTALL)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def tainted(row: dict) -> bool:
    return any((m := LOCAL_READ.match(line)) and returned_local_data(m.group(2)) for line in row.get("tool_log") or [])


def checks_pass(row: dict) -> bool:
    return row["status"] in ("pass", "fail") and all(c["ok"] for c in row["checks"])


def load() -> dict:
    """Per task, in sorted order: teacher passes of 3; section 8's runs split by taint."""
    for path, expected in INPUTS.values():
        if sha(path) != expected:
            sys.exit(f"{path.name}: sha256 {sha(path)}, expected {expected}")
    teacher: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in json.loads(INPUTS["teacher"][0].read_text(encoding="utf-8"))["tasks"]:
        teacher[r["id"]][0] += checks_pass(r)
        teacher[r["id"]][1] += 1
    split: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"t": [0, 0], "u": [0, 0]})
    runs = hit = 0
    for r in json.loads(INPUTS["routed"][0].read_text(encoding="utf-8"))["tasks"]:
        side = "t" if tainted(r) else "u"
        split[r["id"]][side][0] += checks_pass(r)
        split[r["id"]][side][1] += 1
        runs += 1
        hit += side == "t"
    ids = sorted(teacher)
    return {
        "ids": ids,
        "runs": runs,
        "tainted_runs": hit,
        "tk": np.array([teacher[i][0] for i in ids], float),
        "tn": np.array([teacher[i][1] for i in ids], float),
        "kt": np.array([split[i]["t"][0] for i in ids], float),
        "nt": np.array([split[i]["t"][1] for i in ids], float),
        "ku": np.array([split[i]["u"][0] for i in ids], float),
        "nu": np.array([split[i]["u"][1] for i in ids], float),
    }


def draw(d: dict, scenario: str, rng: np.random.Generator) -> np.ndarray:
    """One draw of every task's routed pass probability under a scenario."""
    a, b = PRIOR
    teacher = rng.beta(a + d["tk"], b + d["tn"] - d["tk"])
    student_t = rng.beta(a + d["kt"], b + d["nt"] - d["kt"])
    student_u = rng.beta(a + d["ku"], b + d["nu"] - d["ku"])
    tainted_q = {"teacher": teacher, "half": (teacher + student_t) / 2, "student": student_t}[scenario]
    w = d["nt"] / (d["nt"] + d["nu"])
    return w * tainted_q + (1 - w) * student_u


def simulate(sims: int = SIMS, inner: int = INNER, reps: tuple[int, ...] = REPS) -> dict[tuple[str, int], dict]:
    d = load()
    teacher_real = d["tk"] / d["tn"]
    T = len(teacher_real)
    rng = np.random.default_rng(SEED)
    idx = np.random.default_rng(SEED).integers(0, T, size=(inner, T))
    out = {}
    for scenario in SCENARIOS:
        for R in reps:
            lo95, hi95, lo90, means, rates = [], [], [], [], []
            for _ in range(sims):
                routed = rng.binomial(R, draw(d, scenario, rng)) / R
                boot95 = (routed - RATIO * teacher_real)[idx].mean(axis=1)
                boot90 = (routed - CONTEXT_RATIO * teacher_real)[idx].mean(axis=1)
                lo95.append(np.quantile(boot95, 0.025))
                hi95.append(np.quantile(boot95, 0.975))
                lo90.append(np.quantile(boot90, 0.025))
                means.append(float((routed - RATIO * teacher_real).mean()))
                rates.append(float(routed.mean()))
            out[(scenario, R)] = {
                "shown": float(np.mean(np.array(lo95) > 0)),
                "fail": float(np.mean(np.array(hi95) < 0)),
                "not_shown": float(np.mean((np.array(lo95) <= 0) & (np.array(hi95) >= 0))),
                "shown_090": float(np.mean(np.array(lo90) > 0)),
                "mean_d": float(np.mean(means)),
                "mean_lower": float(np.mean(lo95)),
                "pass_rate": float(np.mean(rates)),
            }
    return out


def main() -> int:
    d = load()
    print(f"teacher check-pass {(d['tk'] / d['tn']).mean():.3f}; tainted runs {d['tainted_runs']} of {d['runs']}")
    print(f"section 8, pass rate on its tainted runs {d['kt'].sum() / d['nt'].sum():.3f} (n={int(d['nt'].sum())}), "
          f"untainted {d['ku'].sum() / d['nu'].sum():.3f} (n={int(d['nu'].sum())})")  # fmt: skip
    tainted_tasks = d["nt"] > 0
    rates = d["tk"] / d["tn"]
    print(
        f"teacher on the tasks that ever tainted {rates[tainted_tasks].mean():.3f} "
        f"(n={int(tainted_tasks.sum())} tasks); on the rest {rates[~tainted_tasks].mean():.3f}"
    )
    results = simulate()
    print(
        "\n| Tainted runs behave like | R | Pass rate | mean(D) | Mean lower bound | SHOWN | NOT SHOWN | FAIL | 0.90 |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for (scenario, R), c in results.items():
        print(
            f"| {scenario} | {R} | {c['pass_rate']:.1%} | {c['mean_d']:+.4f} | {c['mean_lower']:+.4f} | "
            f"{c['shown']:.0%} | {c['not_shown']:.0%} | {c['fail']:.0%} | {c['shown_090']:.0%} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
