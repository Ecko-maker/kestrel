"""Integrity check for KestrelBench results files (read-only).

Usage: uv run python scripts/check_results.py <results.json> [--suite 1.1] [--judge v2]

Checks: the file is valid JSON with no duplicate keys, every task id of the suite appears exactly
once (per repeat), statuses are known, and the file and every judge verdict record the expected
suite and judge versions. Exit code 1 if anything is wrong.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from kestrel.bench.tasks import load_tasks

STATUSES = {"pass", "fail", "error", "excluded", "skipped"}


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    if dupes:
        raise ValueError(f"duplicate keys {dupes}")
    return dict(pairs)


def check(path: Path, suite: str, judge: str) -> list[str]:
    problems: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    except (ValueError, OSError) as e:
        return [f"not valid JSON: {e}"]
    meta, tasks = data.get("meta", {}), data.get("tasks", [])
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--suite", default="1.1")
    parser.add_argument("--judge", default="v2")
    args = parser.parse_args()
    bad = 0
    for path in args.files:
        problems = check(path, args.suite, args.judge)
        data = json.loads(path.read_text(encoding="utf-8")) if not problems or "JSON" not in problems[0] else {}
        statuses = Counter(t["status"] for t in data.get("tasks", []))
        print(f"{path.name}: {'OK' if not problems else f'{len(problems)} problem(s)'}  {dict(statuses)}")
        for p in problems:
            print(f"  - {p}")
        bad += bool(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
