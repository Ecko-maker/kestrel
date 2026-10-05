"""Judge calibration: how often does the LLM judge agree with a careful human?

You label stored answers yourself (`kestrel bench label`): pass or fail against the task's expected
behavior, without seeing the judge's verdict. Then `kestrel bench calibrate` compares your labels
with the judge's verdicts on the same answers (the judge "passes" an answer at 0.5 or more, the same
rule the benchmark uses).

Raw agreement is easy to inflate (a judge that always says pass agrees a lot when most answers are
good), so Cohen's kappa is reported too: agreement beyond what chance would give. Roughly, 0.6-0.8
is substantial and above 0.8 almost perfect.

To avoid tuning the judge to its own test, rubric tasks are split once, by a fixed seed, into a dev
half and a held-out half (stratified by category). Improve the judge prompt while looking at dev
only; the agreement you report comes from held-out. All labels of one task fall in the same half,
so the held-out half stays unseen even if you label several runs of a task.
"""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel.bench.judge import Judge, Verdict
from kestrel.bench.runner import MAX_RESULT_CHARS_IN_LOG
from kestrel.bench.stats import Estimate, bootstrap_ci, split_half, stable_order
from kestrel.bench.tasks import Task

REPO = Path(__file__).resolve().parents[3]
LABELS = REPO / "evals" / "labels" / "human.jsonl"  # written only by `kestrel bench label`, i.e. by a person
VERDICT_CACHE = REPO / "evals" / "results" / "judge-cache.jsonl"  # re-graded verdicts, so retries cost nothing
SPLITS = ("dev", "heldout")


def answer_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def judge_passes(score: float | None) -> bool | None:
    return None if score is None else score >= 0.5


def rel(path: Path) -> str:
    """A results path as stored in labels: relative to the repo when inside it."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


# --- the split ---------------------------------------------------------------------------


def splits(tasks: list[Task]) -> dict[str, str]:
    """Rubric task id -> "dev" or "heldout". Fixed by the seed and the task ids alone, so it never
    changes as labels come in."""
    return split_half([t for t in tasks if t.rubric], category=lambda t: t.category, key=lambda t: t.id)


# --- what the human (and the judge) see -------------------------------------------------


def tool_log_for(result: dict[str, Any], db: Path | None = None) -> tuple[list[str], str]:
    """The tool steps in the judge's format, cut to the current judge's length. Rebuilt from the bench
    traces when they exist (they keep 2,000 characters per result, so a log stored by an older judge
    with a shorter cut is never reused for a newer one), else the log stored in the results (since
    2026-10-04), else only the call list. Returns (lines, where they came from)."""
    if db is not None and db.exists() and result.get("trace_ids"):
        lines = _tool_log_from_traces(db, result["trace_ids"])
        if lines is not None:
            return lines, "rebuilt from traces"
    if result.get("tool_log"):
        return list(result["tool_log"]), "stored"
    return [f"- {c} [result not recorded]" for c in result.get("tool_calls", [])], "calls only"


def _tool_log_from_traces(db: Path, trace_ids: list[str]) -> list[str] | None:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        lines: list[str] = []
        for trace_id in trace_ids:
            spans = con.execute(
                "SELECT span_id, parent_id, name, error, attributes FROM spans WHERE trace_id = ? ORDER BY end_time",
                (trace_id,),
            ).fetchall()
            if not spans:
                return None
            decisions = {
                parent: json.loads(attrs or "{}").get("kestrel.approval.decision")
                for _, parent, name, _, attrs in spans
                if name == "approval"
            }
            for span_id, _, name, error, attrs in spans:
                if name != "tool_call":
                    continue
                a = json.loads(attrs or "{}")
                ran = a.get("kestrel.tool.ran")
                state = "ran" if ran else f"not run ({decisions.get(span_id) or 'refused'})"
                result = a.get("gen_ai.tool.call.result") or error or ""
                args = a.get("gen_ai.tool.call.arguments") or "{}"
                lines.append(f"- {a.get('gen_ai.tool.name')}({args}) [{state}] -> {result[:MAX_RESULT_CHARS_IN_LOG]}")
        return lines
    finally:
        con.close()


# --- labels ------------------------------------------------------------------------------


def load_labels(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or LABELS  # looked up at call time, so tests can point it elsewhere
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_label(entry: dict[str, Any], path: Path | None = None) -> None:
    path = path or LABELS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def make_label(result: dict[str, Any], results_file: Path, choice: str, note: str, split: str) -> dict[str, Any]:
    return {
        "task_id": result["id"],
        "category": result["category"],
        "repeat": result.get("repeat", 1),
        "answer_sha": answer_sha(result["answer"]),
        "results_file": rel(results_file),
        "label": {"p": "pass", "f": "fail", "s": "skip"}[choice],
        "note": note,
        "split": split,
        "labeled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "kestrel bench label (interactive)",
    }


def label_queue(data: dict[str, Any], tasks: list[Task], labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Judged answers still to label, stratified: one per category in turn, so stopping at any point
    leaves a balanced set; within a category the split's order alternates dev and held-out."""
    by_id = {t.id: t for t in tasks}
    done = {(lab["task_id"], lab["answer_sha"]) for lab in labels}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in data["tasks"]:
        task = by_id.get(r["id"])
        if (
            task is not None
            and task.rubric
            and r.get("judge")
            and r["judge"].get("score") is not None
            and (r["id"], answer_sha(r["answer"])) not in done
        ):
            groups[r["category"]].append(r)
    ordered = [stable_order(g, key=lambda r: f"{r['id']}#{r.get('repeat', 1)}") for _, g in sorted(groups.items())]
    queue = []
    for i in range(max((len(g) for g in ordered), default=0)):
        queue += [g[i] for g in ordered if i < len(g)]
    return queue


# --- comparing labels with verdicts ------------------------------------------------------


@dataclass
class Pair:
    task_id: str
    category: str
    human: bool  # True = pass
    judge: bool
    judge_score: float
    judge_reason: str
    note: str = ""


@dataclass
class Calibration:
    n: int
    agreement: Estimate | None  # with a bootstrap interval over labelled answers
    kappa: float | None  # None when undefined (no variation in the labels)
    confusion: dict[str, int]  # both_pass, both_fail, too_lenient (judge pass, you fail), too_strict
    by_category: dict[str, tuple[int, int]]  # category -> (agreed, labelled)
    disagreements: list[Pair]


def cohens_kappa(pairs: list[tuple[Hashable, Hashable]]) -> float | None:
    """Agreement beyond chance: (observed - expected) / (1 - expected)."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(h == j for h, j in pairs) / n
    human, judge = Counter(h for h, _ in pairs), Counter(j for _, j in pairs)
    expected = sum(human[c] * judge[c] for c in set(human) | set(judge)) / (n * n)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 3)


def calibrate(pairs: list[Pair]) -> Calibration:
    by_cat: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_cat[p.category].append(p)
    return Calibration(
        n=len(pairs),
        agreement=bootstrap_ci([float(p.human == p.judge) for p in pairs]),
        kappa=cohens_kappa([(p.human, p.judge) for p in pairs]),
        confusion={
            "both_pass": sum(p.human and p.judge for p in pairs),
            "both_fail": sum(not p.human and not p.judge for p in pairs),
            "too_lenient": sum(p.judge and not p.human for p in pairs),
            "too_strict": sum(p.human and not p.judge for p in pairs),
        },
        by_category={c: (sum(p.human == p.judge for p in ps), len(ps)) for c, ps in sorted(by_cat.items())},
        disagreements=[p for p in pairs if p.human != p.judge],
    )


def labelled(labels: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    """Pass/fail labels in one half. A later label of the same answer replaces an earlier one."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for lab in labels:
        latest[(lab["task_id"], lab["answer_sha"])] = lab
    return [lab for lab in latest.values() if lab["label"] in ("pass", "fail") and lab.get("split") == split]


def find_result(label: dict[str, Any], cache: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    """The stored run a label refers to: same results file, same task, same answer."""
    name = label["results_file"]
    if name not in cache:
        path = Path(name) if Path(name).is_absolute() else REPO / name
        cache[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    data = cache[name]
    if data is None:
        return None
    return next(
        (r for r in data["tasks"] if r["id"] == label["task_id"] and answer_sha(r["answer"]) == label["answer_sha"]),
        None,
    )


def stored_pairs(labels: list[dict[str, Any]]) -> tuple[list[Pair], list[str], set[str]]:
    """Labels vs. the verdicts stored with the answers. Returns (pairs, problems, judge versions seen)."""
    files: dict[str, dict[str, Any] | None] = {}
    pairs, problems, judges = [], [], set()
    for lab in labels:
        r = find_result(lab, files)
        if r is None or not r.get("judge") or r["judge"].get("score") is None:
            problems.append(f"{lab['task_id']}: answer or verdict not found in {lab['results_file']}")
            continue
        j = r["judge"]
        judges.add(f"{j.get('judge')}@{j.get('version') or 'v1'}")
        pairs.append(_pair(lab, j["score"], j.get("reason", "")))
    return pairs, problems, judges


def regraded_pairs(
    labels: list[dict[str, Any]], tasks: list[Task], judge: Judge, db: Path | None, cache_path: Path | None = None
) -> tuple[list[Pair], list[str], int]:
    """Labels vs. a (new or candidate) judge grading the STORED answers again. The agent is never
    re-run. Verdicts are cached per (judge, version, task, answer). Returns (pairs, problems, new calls)."""
    cache_path = cache_path or VERDICT_CACHE
    by_id = {t.id: t for t in tasks}
    cached = {
        (c["judge"], c["version"], c["task_id"], c["answer_sha"]): c for c in load_labels(cache_path)
    }  # same JSONL format
    files: dict[str, dict[str, Any] | None] = {}
    pairs, problems, calls = [], [], 0
    for lab in labels:
        key = (judge.name, judge.version, lab["task_id"], lab["answer_sha"])
        if key not in cached:
            r, task = find_result(lab, files), by_id.get(lab["task_id"])
            if r is None or task is None or not task.rubric:
                problems.append(f"{lab['task_id']}: stored answer or rubric not found")
                continue
            log, _ = tool_log_for(r, db)
            verdict: Verdict = judge.grade(list(task.prompts), task.rubric, log, r["answer"])
            calls += 1
            if verdict.score is None:
                problems.append(f"{lab['task_id']}: {verdict.reason}")
                continue
            entry = {
                "judge": judge.name,
                "version": judge.version,
                "task_id": lab["task_id"],
                "answer_sha": lab["answer_sha"],
                "score": verdict.score,
                "reason": verdict.reason,
            }
            append_label(entry, cache_path)
            cached[key] = entry
        c = cached[key]
        pairs.append(_pair(lab, c["score"], c["reason"]))
    return pairs, problems, calls


def _pair(label: dict[str, Any], score: float, reason: str) -> Pair:
    return Pair(
        task_id=label["task_id"],
        category=label["category"],
        human=label["label"] == "pass",
        judge=bool(judge_passes(score)),
        judge_score=score,
        judge_reason=reason,
        note=label.get("note", ""),
    )
