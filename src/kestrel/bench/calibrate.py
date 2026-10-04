"""Judge calibration: how often does the LLM judge agree with a human?

You label answers yourself (`kestrel bench label`), and we compare your scores with the judge's on
the same answers. Raw agreement is easy to inflate (a judge that always says 1 agrees a lot if most
answers are good), so we also report Cohen's kappa, which discounts agreement expected by chance:
about 0.6-0.8 is substantial, above 0.8 almost perfect.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

LABELS = Path(__file__).resolve().parents[3] / "evals" / "kestrelbench" / "labels.jsonl"
SCORES = (0.0, 0.5, 1.0)


@dataclass
class Calibration:
    n: int
    agreement: float  # exact match
    within_half: float  # off by at most 0.5
    kappa: float | None  # None when undefined (all labels identical)
    confusion: dict[tuple[float, float], int]  # (human, judge) -> count
    disagreements: list[dict]


def cohens_kappa(pairs: list[tuple[float, float]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(h == j for h, j in pairs) / n
    human, judge = Counter(h for h, _ in pairs), Counter(j for _, j in pairs)
    expected = sum(human[s] * judge[s] for s in SCORES) / (n * n)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 3)


def load_labels(path: Path = LABELS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calibrate(labels: list[dict]) -> Calibration:
    usable = [lab for lab in labels if lab.get("judge_score") is not None and lab.get("human_score") is not None]
    pairs = [(float(lab["human_score"]), float(lab["judge_score"])) for lab in usable]
    n = len(pairs)
    return Calibration(
        n=n,
        agreement=round(sum(h == j for h, j in pairs) / n, 3) if n else 0.0,
        within_half=round(sum(abs(h - j) <= 0.5 for h, j in pairs) / n, 3) if n else 0.0,
        kappa=cohens_kappa(pairs),
        confusion=dict(Counter(pairs)),
        disagreements=[lab for lab in usable if float(lab["human_score"]) != float(lab["judge_score"])],
    )
