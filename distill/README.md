# Distill task set (Phase 4)

122 tasks in KestrelBench's task format, with their own fixture workspace (a community garden and bike
co-op), used only to collect **training data** for the student model. They are *not* KestrelBench:
the student is judged on KestrelBench, so it must never train on it. Design decisions 22-24 in
`docs/design-decisions.md`, plus 22 for the check conversions.

- `tasks/`: 10 files, one per category, in the main suite's proportions x1.2 (time has two extra
  check-only tasks since d1.2). Every task has the tag
  `distill` and an id starting with `dst-`. Over-weighted on three baseline failure modes: finding
  facts in the workspace (`beh-lookup`, 13), using the calculator (`beh-calc`, 14), calling the
  approval-gated tool instead of drafting and asking (`beh-gated`, 12). No injection or attack text.
- `workspace/`: the files the tasks read. Example domains only; no canaries or secrets (tested).
- Version: `d1.2` (`DISTILL_VERSION` in `src/kestrel/distill/__init__.py`, recorded in results meta).
- Overlap with KestrelBench: `uv run python scripts/distill_overlap.py` (report:
  `evals/reports/distill-overlap.md`). At `d1.2`: 0 fail, 38 pairs in the review band, 0 fixture problems.

## What counts as training data

`kestrel distill build` keeps a distill result only if it passed, its task is unchanged since the run,
and (under `grading = "checks-only"`, the default in `src/kestrel/distill/grading.toml`) the task is
graded entirely by deterministic checks. Tasks with a rubric are dropped (`rubric_task`) until a judge
is calibrated and listed in `calibrated_judges` (known issue #69).

**Under checks-only, 93 of the 122 tasks can become training data** (78 of 120 before d1.2):

| Category | Tasks | Check-only before (d1.1) | Check-only now (d1.2) |
|---|---|---|---|
| actions | 17 | 16 | 16 |
| multistep | 14 | 13 | 14 |
| files | 17 | 13 | 13 |
| arithmetic | 12 | 12 | 12 |
| safety | 19 | 2 | **10** |
| adapt | 10 | 8 | 8 |
| conversation | 7 | 7 | 7 |
| time | 9 | 0 | **6** |
| no_tools | 10 | 5 | 5 |
| web | 7 | 2 | 2 |
| **total** | **122** | **78** | **93** |

d1.2 converted 13 rubric tasks whose every rubric line has a deterministic check (4 time, 8 safety,
1 multistep) and added two check-only time tasks. Time answers are checked against the
`get_current_time` result itself (`answer_has_tool_time`: any common format, but the value must be
the tool's). The remaining 29 rubric tasks have a line no check can verify (a yes/no judgment, "does
not invent a listing", open-ended writing, live web facts); they come back when a calibrated judge
re-grades their stored answers. All three over-weighted behaviors are check-only (13 lookup, 14
calculator, 12 gated).

**Web stays at 2 of 7.** Web answers must agree with live search results, which no fixed check can
know. That's acceptable: the Phase 4 router can send questions that need the web to the teacher
instead of the student.

## Grading the rubric tasks later

The collection run uses `--judge none`: nothing is graded by the teacher itself, and no judge tokens
are spent. The answers of rubric tasks are still stored (with their tool log) in the results file, so
a calibrated judge can grade them later without re-running Kestrel:

```powershell
uv run --no-sync kestrel bench rejudge evals\results\distill-d1.1-pass1.json --judge groq --judge-model <calibrated model>
```

`rejudge` reads the distill task folder recorded in the file and `logs\distill.db`, and writes a new
file. Then add `"<judge>@<version>"` to `calibrated_judges`, set `grading = "calibrated-judge"`, and
rebuild with the rejudged file.

## Cost of one collection pass

**The dry run says ~133k billable tokens. That figure is too low.** It is 122 tasks x 3,300 raw tokens
+ 29 rubric tasks x 500 judge tokens = 417k raw, times `BILLABLE_SHARE = 0.32` = 133,472. (It counts
the judge tokens even with `--judge none`.) The 0.32 share is the
old, known-wrong estimate (fixed on the Phase 3 branch, `fix/estimator`; the estimator code is not
changed here). Measured on Groq gpt-oss-120b, 64-75% of tokens are billable.

**Corrected estimate: about 253k billable tokens (range ~242k-284k), about 2 Groq free-tier days:**

- Measured: a full main-suite run costs ~235k billable for 100 tasks, with the judge on 43 of them.
  The judge's share is ~27.5k (`kestrel bench rejudge --dry-run` on that run: 43 calls, ~27.5k tokens),
  which leaves ~2,075 billable tokens per task for the agent.
- The collection pass runs with `--judge none`: 122 x ~2,075 = **~253k**. From the raw side instead,
  3,103 raw tokens per task x 64-75% billable = 1,986-2,327 per task = **~242k-284k**.
- With the judge on it would be ~272k (29 rubric tasks x ~640; but the judge would be the teacher, #69).
- With `--token-budget 180000` the pass stops starting new tasks at about 180k on day one and finishes
  with `--resume` on day two (Groq's token limit is a rolling ~24 h window). Distill tasks are not
  measured yet; multistep and file tasks may run longer than the main suite's average.

## Comparing base and student

Serve both through the same Kestrel Modelfile: `python distill/export.py --base ... --create --verify` builds the
untrained base exactly like the student (same template, stops, sampling, `num_ctx`; only the GGUF differs).
Never compare against Ollama's packaged `qwen3:4b-instruct-2507`: its template garbles tool schemas (known
issue #73), which would handicap the base. Serve both with `KESTREL_MCP=off` (the tools they were trained and
compared on).

How to run it: `COLLECTION.md`. Training the student on the result: `TRAINING.md`; serving it: `EXPORT.md`.
