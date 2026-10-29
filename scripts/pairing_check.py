"""Before an after-run: will its results pair with the before side's? No model calls.

Checks the commit you run it on (the after candidate) against the before side's results files:
- SAFETY_VERSION and every task's fingerprint (task_sha) are identical, for every task in the files;
- what differs is only the agent fingerprint and the answer-URL policy (KESTREL_IMAGE_POLICY).
Then it builds an after-shaped copy of each before file (same rows, the candidate's fingerprint and
policy in its meta) and pairs it with `compare_safety`, the function `kestrel bench compare` uses, so
a refusal or a dropped task shows up now rather than after two days of quota.

    uv run python scripts/pairing_check.py ..\\kestrel-runs\\evals\\results\\safety-before-strict-x3.json
        [more before files] [--write-sim DIR]
"""

import argparse
import copy
import json
import sys
from pathlib import Path

from kestrel.answer_policy import AnswerPolicy
from kestrel.bench.runner import agent_fingerprint
from kestrel.bench.safety import SAFETY_VERSION, SafetyMeta, compare_safety, load_safety, safety_sha
from kestrel.bench.stubs import build_registry

MAIN_BEFORE = "cc5c16377662"  # main suite agent fingerprint before tiers v2 (CLAUDE.md, D4)
AFTER_POLICY = AnswerPolicy(images_only_allowlisted=True)  # KESTREL_IMAGE_POLICY=allowlist, nothing else set


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before", nargs="+", type=Path)
    parser.add_argument("--write-sim", type=Path, help="also save the after-shaped copies here (for `bench compare`)")
    args = parser.parse_args()

    split = load_safety()
    candidate = {st.id: safety_sha(st, split.workspace) for st in split.tasks}
    problems: list[str] = []
    print(f"candidate: SAFETY_VERSION {SAFETY_VERSION}, {len(candidate)} tasks")
    main_after = agent_fingerprint("groq", "openai/gpt-oss-120b")["sha"]
    print(f"main-suite agent fingerprint: {MAIN_BEFORE} -> {main_after}")

    for path in args.before:
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["meta"]
        print(f"\n== {path.name}: {meta.get('user')}, repeat {meta.get('repeat', 1)}, kinds {meta.get('kinds')}")
        if meta.get("split_version") != SAFETY_VERSION:
            problems.append(f"{path.name}: SAFETY_VERSION {meta.get('split_version')} != {SAFETY_VERSION}")
        ids = sorted({r["id"] for r in data["tasks"]})
        unknown = [i for i in ids if i not in candidate]
        graded = [r for r in data["tasks"] if r["status"] in ("pass", "fail")]
        changed = sorted({r["id"] for r in graded if r["id"] in candidate and r.get("task_sha") != candidate[r["id"]]})
        ungraded = sorted({r["id"] for r in data["tasks"]} - {r["id"] for r in graded})
        print(
            f"tasks: {len(ids)}; graded fingerprints identical to the candidate's: "
            f"{len({r['id'] for r in graded}) - len(changed)}/{len({r['id'] for r in graded})}"
            + (f"; not graded (errored, not paired): {', '.join(ungraded)}" if ungraded else "")
        )
        problems += [f"{path.name}: {i} not in the candidate split" for i in unknown]
        problems += [f"{path.name}: {i} task_sha differs" for i in changed]

        after_agent = agent_fingerprint(meta["provider"], meta["model"], build_registry(SafetyMeta(), []))
        before_agent = meta.get("agent", {})
        differ = sorted(k for k in set(before_agent) | set(after_agent) if before_agent.get(k) != after_agent.get(k))
        print(
            f"safety-run agent fingerprint: {before_agent.get('sha')} -> {after_agent['sha']} "
            f"(differs in: {', '.join(differ)})"
        )
        if set(differ) - {"tools", "sha"}:
            problems.append(f"{path.name}: agent fingerprint differs beyond the tool list: {differ}")
        print(f"answer policy: {meta.get('answer_policy')} -> {AFTER_POLICY.as_dict()}")

        sim = copy.deepcopy(data)
        sim["meta"]["agent"], sim["meta"]["answer_policy"] = after_agent, AFTER_POLICY.as_dict()
        for r in sim["tasks"]:
            if r["status"] in ("pass", "fail"):
                r["task_sha"] = candidate.get(r["id"], r.get("task_sha"))
        try:
            c = compare_safety(data, sim)
        except ValueError as e:
            problems.append(f"{path.name}: compare refuses: {e}")
            continue
        compared = {i for level in c.attacks.values() for i in level} | set(c.benign)
        probes = sorted({r["id"] for r in data["tasks"] if (r.get("safety") or {}).get("probe")})
        attacks = len({i for level in c.attacks.values() for i in level})
        print(
            f"compare pairs {len(compared) + len(probes)} of {len(ids)} task(s): {attacks} attacks at "
            f"{', '.join(c.attacks)}, {len(c.benign)} benign, {len(probes)} policy probe(s) (reported apart); "
            f"warnings: {len(c.warnings)}"
        )
        for w in c.warnings:
            print(f"  warning: {w}")
        if args.write_sim:
            args.write_sim.mkdir(parents=True, exist_ok=True)
            (args.write_sim / f"sim-after-{path.name}").write_text(json.dumps(sim, indent=1), encoding="utf-8")

    print("\nRESULT:", "OK, pairs" if not problems else "PROBLEMS")
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
