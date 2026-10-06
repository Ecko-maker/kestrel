"""Decide whether CI must run the KestrelBench subset for this event (stdlib only, no model calls).

Needed when the pull request has the `run-evals` label, or the change touches a path that can change
how the agent behaves or how it is graded (AGENT_PATHS). Otherwise the job writes "KestrelBench not
run" to the step summary and passes. The rule and the alternatives are in docs/design-decisions.md.

CI (.github/workflows/ci.yml) lists the changed files with plain git and passes the event in the
environment: EVENT, REF, LABELED ("true"/"false"), AUTHOR (the PR author's login).

Try it locally on any file list (no network, no keys):
    "docs/known-issues.md" | Out-File -Encoding utf8 changed.txt
    $env:EVENT = "pull_request"; uv run python scripts/ci/evals_needed.py --changed changed.txt
"""

import argparse
import os
import sys
from pathlib import Path

LABEL = "run-evals"
DEPENDABOT = "dependabot[bot]"

# Folder prefixes: a change anywhere below one counts. Dependency files (uv.lock, pyproject.toml) and
# .python-version are left out on purpose: CI pins the Python version, and dependency-only changes
# are covered by the manual full run (Dependabot PRs get no Actions secrets, so they couldn't run).
AGENT_PATHS = {
    "src/kestrel/": "agent loop, system prompt (agent.py), tools, model layer, and the bench's runner/checks/judge",
    "evals/kestrelbench/tasks/": "task prompts and checks of the main suite (the CI subset is drawn from it)",
    "evals/kestrelbench/workspace/": "fixture files the tasks read",
}


def agent_paths_hit(changed: list[str]) -> list[str]:
    return sorted({p for f in changed for p in AGENT_PATHS if f.startswith(p)})


def decide(changed: list[str] | None, *, event: str, ref: str, labeled: bool, author: str) -> tuple[bool, str]:
    """(needed, reason). `changed` is None when the changed files couldn't be listed."""
    if event == "pull_request":
        if labeled:
            return True, f"the pull request has the `{LABEL}` label"
        if author == DEPENDABOT:
            return False, "dependency update by Dependabot (no Actions secrets); the manual full run covers it"
    elif not (event == "push" and ref == "refs/heads/main"):
        return False, "branch push: evals run on the pull request"
    if changed is None:
        return True, "the changed files could not be listed, so evals run to be safe"
    hits = agent_paths_hit(changed)
    if hits:
        return True, "agent-affecting changes in " + ", ".join(hits)
    return False, "no agent-affecting changes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--changed", type=Path, help="file with one changed path per line; missing = unknown")
    args = parser.parse_args()
    changed = None
    if args.changed and args.changed.is_file():
        lines = args.changed.read_text(encoding="utf-8-sig").splitlines()
        changed = [line.strip() for line in lines if line.strip()]
    needed, reason = decide(
        changed,
        event=os.getenv("EVENT", ""),
        ref=os.getenv("REF", ""),
        labeled=os.getenv("LABELED", "false") == "true",
        author=os.getenv("AUTHOR", ""),
    )
    line = f"KestrelBench {'needed' if needed else 'not run'}: {reason}"
    print(f"{len(changed) if changed is not None else 'unknown'} changed file(s). {line}")
    if out := os.getenv("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"needed={'true' if needed else 'false'}\n")
    if not needed and (summary := os.getenv("GITHUB_STEP_SUMMARY")):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
