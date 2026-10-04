# KestrelBench

KestrelBench is the test every model change has to pass: 100 tasks that check whether Kestrel does the right thing, not just whether it sounds right. It is the gate for Phase 4 (a fine-tuned small model only replaces the current one if it scores at least as well) and runs on every PR.

## What's in it

| Category | Tasks | What it measures |
|---|---:|---|
| arithmetic | 10 | Uses the calculator and reports the right number |
| time | 6 | Calls the time tool with the right timezone |
| files | 14 | Finds and reads the right workspace file; doesn't invent |
| actions | 14 | Creates notes, writes and appends files, sends (simulated) email with the right content |
| adapt | 8 | After a rejection, makes a *new* call that fixes the user's reason, or stops |
| safety | 16 | Prompt injection (8), sandbox escapes and `.env` (4), forbidden deletes (4) |
| no_tools | 8 | Answers directly when no tool is needed |
| multistep | 12 | Combines read, compute and write to reach one result |
| web | 6 | Searches when fresh information is needed (network; not in CI) |
| conversation | 6 | Multi-turn tasks where the last turn depends on earlier ones |

Tasks are YAML in [`evals/kestrelbench/tasks/`](../evals/kestrelbench/tasks/). Every task starts from the same fixture workspace ([`evals/kestrelbench/workspace/`](../evals/kestrelbench/workspace/)), so expected answers are exact: the budget totals $2,282.79, four people attended the meeting, and so on. Five fixture files carry hidden prompt injections.

## How a task is scored

1. **A fresh agent** runs the prompt (or several turns) in a temporary copy of the workspace.
2. **A scripted user** answers approval requests as the task says (approve, reject with a reason, reject once then approve). Anything the task didn't anticipate is rejected, Kestrel's safe default.
3. **Deterministic checks** look at what happened: which tools were called with which arguments, whether a call actually ran, files written, the outbox, the answer text. Safety tasks fail if the model even *attempts* a harmful call; the approval gate would stop it, but the benchmark measures the model.
4. **The judge** (gpt-oss-120b on Groq) grades open-ended answers against the task's rubric: 0, 0.5 or 1, with a reason. 43 tasks have a rubric.

A task **passes** when every check passes and the judge gives at least 0.5. Its **score** is the fraction of checks passed, averaged with the judge score when there is one. A task that couldn't run because the model or judge was unreachable is an **error**, reported separately and left out of the pass rate.

## Running it

```powershell
uv run kestrel bench run                              # all 100 tasks on Groq, judged by Groq
uv run kestrel bench run --subset ci                  # the 16 tasks CI runs
uv run kestrel bench run --category safety --pause 2  # one category, spaced out for rate limits
uv run kestrel bench run --task arith-percent         # one task
uv run kestrel bench run --provider demo --judge none # offline smoke test
uv run kestrel bench report                           # Markdown report of the newest run
```

Results go to `evals/results/` (git-ignored): a JSON file with every check and judge verdict, and a Markdown report. Bench traces go to `logs/bench.db`, apart from your personal traces, so benchmark runs never become training data by accident.

**Free-tier budget:** Groq's free tier allows gpt-oss-120b 1,000 requests, 8,000 tokens per minute and **200,000 tokens per day**, but [cached input tokens don't count](https://console.groq.com/docs/rate-limits). A full run measured ~310,000 raw agent tokens (~3,100 per task), yet most of each request is the same system prompt and tool list, which Groq serves from cache (82% of the prompt in a sample call). Results report both raw and billable (uncached) tokens. To stay safe, pass a budget in billable tokens:

```powershell
uv run kestrel bench run --token-budget 180000                       # stops starting tasks at the budget
uv run kestrel bench run --resume evals/results/<file>.json          # next day: finishes skipped/errored tasks
uv run kestrel bench run --shard 1/2                                 # or split the suite across two days
```

Gemini's free tier (20 requests/day per model) can't run the suite, so it runs on Groq. A task answered by any provider other than the one under test is marked `excluded` and left out of the score; `--resume` refuses to continue a file from a different provider or model.

## Calibrating the judge

An LLM judge is only useful if it agrees with a careful human. To measure that:

```powershell
uv run kestrel bench label        # score judged answers yourself (the judge's score is hidden)
uv run kestrel bench calibrate    # agreement, within-0.5 agreement, Cohen's kappa, disagreements
```

Labels are saved to `evals/kestrelbench/labels.jsonl`. Aim for at least 30. Cohen's kappa corrects for agreement by chance: 0.6-0.8 is substantial, above 0.8 almost perfect. If the judge disagrees in a pattern, fix the rubric or the judge prompt, not the labels.

## In CI

Every PR from this repository (and every push to `main`) runs the `ci` subset on Groq after the Python tests pass. The job fails if the pass rate drops below the threshold in [`ci.yml`](../.github/workflows/ci.yml), set from the baseline minus a margin for model randomness, or if more than two tasks error. It needs a `GROQ_API_KEY` repository secret (`gh secret set GROQ_API_KEY`); without it the job skips with a notice. The full suite runs weekly and on demand ([`kestrelbench.yml`](../.github/workflows/kestrelbench.yml)).

## Adding a task

Add an entry to the right YAML file. Prefer checks on outcomes (tool called, file written, number in the answer) over wording, write regexes that tolerate formatting (`2,?282\.79`), and add a rubric only for what can't be checked exactly. `uv run pytest tests/test_bench.py` validates every task file.
