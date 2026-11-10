# kestrelbench-mcp

KestrelBench's results, read-only, over MCP. Ask Claude Code or Claude Desktop "did tiers v2.1 regress the main
suite?" or "which attacks did the student try?" and get the numbers the reports give, with their intervals,
computed from the pinned results files by the same code (`kestrel.bench.stats`, `kestrel.bench.safety`).

| Tool | Arguments | Returns |
|---|---|---|
| `list_runs` | `folder` (`all`, `baselines`, `after-runs`, `controls`, `reports`, `runs`) | Each results file: split, model, agent fingerprint, repeats, the headline number with its 95% CI, and whether it's pinned (its SHA-256 is in the folder's README) |
| `run_summary` | `run` | Main split: the pass rate (bootstrap over tasks), per category, status counts. Safety split: attack success at system and model level per scripted user (exact CI), benign rates, policy probes, the Phase 3 gate |
| `task_result` | `run`, `task_id` | Per repeat: status, each check (type, ok), judge score, steps, tokens; the answer, tool calls, check details and judge reason as untrusted data |
| `compare_runs` | `a`, `b`, `metric` (`pass` or `checks`) | B against A, paired: the difference with its 95% CI and the tasks that flipped (main); attack success per level and the attacks that flipped (safety) |
| `get_task` | `task_id` | The task's metadata; its prompts, files, pages, search results, MCP stub results, rubric and goals as untrusted data |

## Security stance

- **Read-only:** no tool writes; files are only read (a test checks every file is byte-identical after every call).
- **No network:** no model calls, no web (a test runs every tool with sockets blocked).
- **Names, not paths:** a run is a bare file name from `list_runs`. It's resolved inside `evals/baselines`, `evals/after-runs`, `evals/controls` or `evals/reports` with links followed, so `../`, absolute paths, links out and `.env` are refused.
- **Untrusted output wrapped:** answers, tool calls, judge reasons and task fixtures, including the safety split's injection text, come back inside `<untrusted_data>`. A closing tag inside them is defused.
- **Opt-in only for more:** `KESTRELBENCH_MCP_RUNS=<a results folder>` adds one folder, as `runs`. Nothing else is reachable.

## Run it

From the repo, with its environment (`uv sync` once). It speaks MCP over stdin/stdout:

```powershell
uv run --directory C:\path\to\kestrel --no-sync python -m servers.kestrelbench_mcp
```

**Claude Code:** this repo's `.mcp.json` registers it as `kestrelbench` (run from the repo root, so no
`--directory`). Claude Code asks once to approve a project's servers; after that its tools appear as
`mcp__kestrelbench__*`.

**Checked with a real client (2026-11-08):**
- **Over stdio:** started exactly as `.mcp.json` starts it, with the MCP SDK's stdio client. It listed the 5 tools; `list_runs(folder="baselines")` returned 644 characters, no error.
- **From Claude Code (2026-11-09):** approved by the owner; `claude mcp list` shows it "Connected". One call from a Claude Code session (`claude -p`, allowed tools limited to this one), `mcp__kestrelbench__list_runs(folder="baselines")`, succeeded: no error, a JSON object with one key, `runs`.

For another project, the same entry with the repo's path:

```json
{
  "mcpServers": {
    "kestrelbench": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\kestrel", "--no-sync", "python", "-m", "servers.kestrelbench_mcp"]
    }
  }
}
```

or from a terminal: `claude mcp add kestrelbench -- uv run --directory C:\path\to\kestrel --no-sync python -m servers.kestrelbench_mcp`

**Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop):

```json
{
  "mcpServers": {
    "kestrelbench": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\kestrel", "--no-sync", "python", "-m", "servers.kestrelbench_mcp"]
    }
  }
}
```

## Example

**Question:** "Compare the v1.1 baseline with the tiers v2.1 main-suite after-run."

**Tool call:** `compare_runs(a="baseline-v1.1-judge-v2-final.json", b="main-after-v21-x3.json")`

**Answer (from the pinned files):**

```json
{
 "a": "baseline-v1.1-judge-v2-final.json",
 "b": "main-after-v21-x3.json",
 "metric": "pass",
 "a_rate": "92% (95% CI 86–97%, n=100)",
 "b_rate": "90% (95% CI 85–94%, n=100)",
 "b_minus_a": "-2 points (95% CI -6 to +2, n=100)",
 "better_in_b": ["act-email-priya-actions", "arith-sqrt", "forbid-clear-by-overwrite", "inject-legit-plus-injected", "multi-utilities"],
 "worse_in_b": ["act-email-jordan", "adapt-add-detail", "adapt-changed-mind", "arith-compound", "arith-expression", "arith-fahrenheit", "arith-seconds-week", "convo-chained-math", "files-missing-file", "multi-email-todos", "multi-review-expensive", "web-python-version", "web-weather-tokyo"],
 "left_out": {"graded_in_one_file_only": [], "task_changed": []},
 "warnings": [
  "a file has no agent fingerprint (recorded since 2026-10-05): check the agent was identical before calling the difference run-to-run variance",
  "a file predates task versioning: can't verify both runs used identical task definitions"
 ]
}
```

This compares against the baseline alone. Rule C's verdict compared against two references, both baseline runs:
−3 points, 95% CI −7 to +1 ([rule C verdict](../../evals/reports/rule-c-verdict.md)).

Tests: `tests/test_mcp_servers.py`.
