"""Prompt overlap between the distill task set and KestrelBench. No model calls.

The student is trained on distill tasks and gated on KestrelBench, so a distill prompt that copies or
paraphrases a bench prompt would leak the exam into the training data. Every distill prompt (every
turn) is compared with every KestrelBench prompt from every task folder under evals/kestrelbench/
(main suite, safety split, any later split):
- both are normalized: lowercase, punctuation removed, whitespace collapsed;
- an exact match fails;
- word 3-gram Jaccard similarity >= FAIL_AT fails, >= REVIEW_AT is listed for a human to look at.
Prompts shorter than three words have no 3-grams, so only the exact match applies to them.

    uv run python scripts/distill_overlap.py            # exit 1 on any failure
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kestrel.bench.tasks import load_tasks
from kestrel.distill import DISTILL_DIR, ROOT

BENCH_DIR = ROOT / "evals" / "kestrelbench"
FAIL_AT = 0.5
REVIEW_AT = 0.3


@dataclass(frozen=True)
class Prompt:
    task_id: str
    text: str


@dataclass(frozen=True)
class Pair:
    distill: Prompt
    bench: Prompt
    score: float  # 3-gram Jaccard, 1.0 for an exact match
    exact: bool


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def trigrams(text: str) -> frozenset[tuple[str, ...]]:
    words = normalize(text).split()
    return frozenset(tuple(words[i : i + 3]) for i in range(len(words) - 2))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def bench_prompts(bench_dir: Path = BENCH_DIR) -> list[Prompt]:
    """Every user prompt (all turns) of every KestrelBench task file, in any task format. Fixture
    files under workspace/ are data the tasks read, not prompts."""

    def collect(node: Any, task_id: str) -> list[Prompt]:
        if isinstance(node, dict):
            task_id = str(node.get("id", task_id))
            found = []
            for key, value in node.items():
                if key == "prompt":
                    texts = [value] if isinstance(value, str) else [v for v in value or [] if isinstance(v, str)]
                    found += [Prompt(task_id, t) for t in texts]
                else:
                    found += collect(value, task_id)
            return found
        if isinstance(node, list):
            return [p for item in node for p in collect(item, task_id)]
        return []

    prompts: list[Prompt] = []
    for path in sorted(bench_dir.rglob("*.y*ml")):
        if "workspace" in path.relative_to(bench_dir).parts:
            continue
        prompts += collect(yaml.safe_load(path.read_text(encoding="utf-8")), path.stem)
    return prompts


def distill_prompts(tasks_dir: Path = DISTILL_DIR / "tasks") -> list[Prompt]:
    return [Prompt(t.id, text) for t in load_tasks(tasks_dir) for text in t.prompts]


def compare(distill: list[Prompt], bench: list[Prompt]) -> list[Pair]:
    """Every pair, highest score first."""
    bench_grams = [(b, normalize(b.text), trigrams(b.text)) for b in bench]
    pairs = []
    for d in distill:
        norm, grams = normalize(d.text), trigrams(d.text)
        for b, b_norm, b_grams in bench_grams:
            exact = norm == b_norm
            pairs.append(Pair(d, b, 1.0 if exact else jaccard(grams, b_grams), exact))
    return sorted(pairs, key=lambda p: (-p.score, p.distill.task_id, p.bench.task_id))


def failures(pairs: list[Pair], fail_at: float = FAIL_AT) -> list[Pair]:
    return [p for p in pairs if p.exact or p.score >= fail_at]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--distill-dir", type=Path, default=DISTILL_DIR / "tasks", help="distill task folder")
    parser.add_argument("--bench-dir", type=Path, default=BENCH_DIR, help="KestrelBench folder (all splits)")
    parser.add_argument("--fail-at", type=float, default=FAIL_AT, help=f"3-gram Jaccard that fails (default {FAIL_AT})")
    parser.add_argument("--review-at", type=float, default=REVIEW_AT, help=f"listed for review (default {REVIEW_AT})")
    parser.add_argument("--top", type=int, default=5, help="also show the N closest pairs (default 5)")
    args = parser.parse_args(argv)

    distill, bench = distill_prompts(args.distill_dir), bench_prompts(args.bench_dir)
    pairs = compare(distill, bench)
    failed = failures(pairs, args.fail_at)
    review = [p for p in pairs if p not in failed and p.score >= args.review_at]
    print(f"{len(distill)} distill prompts x {len(bench)} KestrelBench prompts = {len(pairs):,} pairs")

    def show(p: Pair) -> None:
        tag = "EXACT" if p.exact else f"{p.score:.2f}"
        print(f"  {tag:>5}  {p.distill.task_id}: {p.distill.text!r}")
        print(f"         {p.bench.task_id}: {p.bench.text!r}")

    print(f"Fail (exact, or Jaccard >= {args.fail_at}): {len(failed)}")
    for p in failed:
        show(p)
    print(f"Review (Jaccard >= {args.review_at}): {len(review)}")
    for p in review:
        show(p)
    print(f"Closest {args.top}:")
    for p in pairs[: args.top]:
        show(p)
    print("OVERLAP CHECK FAILED" if failed else "Overlap check passed.")
    return 1 if failed else 0
