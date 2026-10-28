"""Integrity check for KestrelBench results files (read-only).

Usage: uv run python scripts/check_results.py <results.json>... [--suite 1.1] [--judge v2] [--safety s1.0] [--partial]

Checks: the file is valid JSON with no duplicate keys, every task id of the suite appears exactly
once (per repeat), statuses are known, and the file and every judge verdict record the expected
suite and judge versions. Exit code 1 if anything is wrong.

Safety split files (meta.split == "safety") are checked against the safety split instead: its version,
no judge, every (task, repeat, user) at most once, only users and repeats the meta names, every task
of the chosen kinds present for each user and repeat (unfiltered runs only: --tasks, --sample, --subset,
--category and --shard runs are partial by design; so are --task runs, which the meta doesn't record: pass
--partial for those), each row's safety kind matching its task, and one
task fingerprint per task within the file (graded rows only: an errored row never reaches the safety
grader, so it keeps the main suite's kind of fingerprint; known issue #55).
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from kestrel.bench.safety import SAFETY_VERSION, load_safety
from kestrel.bench.stats import GRADED
from kestrel.bench.tasks import load_tasks

STATUSES = {"pass", "fail", "error", "excluded", "skipped"}


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    if dupes:
        raise ValueError(f"duplicate keys {dupes}")
    return dict(pairs)


def check(path: Path, suite: str, judge: str, safety: str = SAFETY_VERSION, partial: bool = False) -> list[str]:
    problems: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    except (ValueError, OSError) as e:
        return [f"not valid JSON: {e}"]
    meta, tasks = data.get("meta", {}), data.get("tasks", [])
    if meta.get("split") == "safety":
        return check_safety(meta, tasks, safety, partial)
    if meta.get("suite_version") != suite:
        problems.append(f"meta.suite_version is {meta.get('suite_version')!r}, expected {suite!r}")
    if (meta.get("judge_version") or "v1") != judge:
        problems.append(f"meta.judge_version is {meta.get('judge_version')!r}, expected {judge!r}")
    runs = Counter((t["id"], t.get("repeat", 1)) for t in tasks)
    problems += [f"{tid} (repeat {k}) appears {n} times" for (tid, k), n in runs.items() if n > 1]
    suite_ids = {t.id for t in load_tasks()}
    found = {tid for tid, _ in runs}
    problems += [f"missing task {tid}" for tid in sorted(suite_ids - found)]
    problems += [f"unknown task {tid}" for tid in sorted(found - suite_ids)]
    for t in tasks:
        if t["status"] not in STATUSES:
            problems.append(f"{t['id']}: unknown status {t['status']!r}")
        verdict = t.get("judge")
        if verdict and (verdict.get("version") or "v1") != judge:
            problems.append(f"{t['id']}: verdict from judge {verdict.get('version') or 'v1'}, expected {judge}")
    return problems


def check_safety(meta: dict[str, Any], tasks: list[dict[str, Any]], version: str, partial: bool = False) -> list[str]:
    problems: list[str] = []
    if meta.get("split_version") != version:
        problems.append(f"meta.split_version is {meta.get('split_version')!r}, expected {version!r}")
    if meta.get("judge") or meta.get("judge_version"):
        problems.append(f"meta.judge is {meta.get('judge')!r}: the safety split is graded by its guards, not a judge")
    users = meta.get("users") or [meta.get("user")]
    repeats = range(1, (meta.get("repeat") or 1) + 1)
    kinds = meta.get("kinds") or "all"
    split = {st.id: st.meta.kind for st in load_safety().tasks}
    chosen = {tid for tid, kind in split.items() if kinds in ("all", kind)}

    runs = Counter((t["id"], t.get("repeat", 1), t.get("user")) for t in tasks)
    problems += [f"{tid} (repeat {k}, user {u}) appears {n} times" for (tid, k, u), n in runs.items() if n > 1]
    problems += [f"unknown task {tid}" for tid in sorted({t["id"] for t in tasks} - set(split))]
    for t in tasks:
        where = f"{t['id']} (repeat {t.get('repeat', 1)}, user {t.get('user')})"
        if t["status"] not in STATUSES:
            problems.append(f"{where}: unknown status {t['status']!r}")
        if t.get("user") not in users:
            problems.append(f"{where}: user not in meta.users {users}")
        if t.get("repeat", 1) not in repeats:
            problems.append(f"{where}: repeat outside 1..{repeats[-1]}")
        kind = (t.get("safety") or {}).get("kind")
        if t["id"] in split and kind is not None and kind != split[t["id"]]:
            problems.append(f"{where}: safety kind {kind!r}, the task is {split[t['id']]!r}")
        if t.get("judge"):
            problems.append(f"{where}: has a judge verdict")

    filtered = [k for k in ("tasks_filter", "sample", "shard", "category") if meta.get(k)]
    if meta.get("subset") not in (None, "all"):
        filtered.append("subset")
    if not filtered and not partial:
        found = set(runs)
        problems += [
            f"missing {tid} (repeat {k}, user {u})"
            for u in users
            for k in repeats
            for tid in sorted(chosen)
            if (tid, k, u) not in found
        ]

    shas: dict[str, set[str]] = {}
    for t in tasks:
        if t["status"] in GRADED and t.get("task_sha"):
            shas.setdefault(t["id"], set()).add(t["task_sha"])
    problems += [f"{tid}: {len(s)} task fingerprints in one file {sorted(s)}" for tid, s in shas.items() if len(s) > 1]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--suite", default="1.1")
    parser.add_argument("--judge", default="v2")
    parser.add_argument("--safety", default=SAFETY_VERSION, help="expected safety split version")
    parser.add_argument("--partial", action="store_true", help="safety files: skip the coverage check (--task runs)")
    args = parser.parse_args()
    bad = 0
    for path in args.files:
        problems = check(path, args.suite, args.judge, args.safety, args.partial)
        data = json.loads(path.read_text(encoding="utf-8")) if not problems or "JSON" not in problems[0] else {}
        statuses = Counter(t["status"] for t in data.get("tasks", []))
        print(f"{path.name}: {'OK' if not problems else f'{len(problems)} problem(s)'}  {dict(statuses)}")
        for p in problems:
            print(f"  - {p}")
        bad += bool(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
