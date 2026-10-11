# Memory split smoke run 2: with memory guidance (SMOKE TEST)

> **SMOKE TEST, not a result.** One repeat on a weak local model (`qwen2.5:7b` on CPU). It shows whether the system-prompt memory guidance (design decision 18) changes how a real model uses memory. Pass counts are not KestrelBench numbers and must not be quoted as Kestrel's memory performance. The graded before/after runs are gpt-oss-120b on Groq.

Run 2026-10-11 on `phase3/memory` with `MEMORY_GUIDANCE` added (uncommitted at run time; committed with this report). Memory split m1.0, strict user, 1 repeat, judge none.
- **Agent and embeddings:** Ollama `qwen2.5:7b` for the agent, Ollama `nomic-embed-text` for embeddings.
- **Ollama only:** `KESTREL_PROVIDERS=ollama`, both cloud keys empty in the process.
- **Two runs:** `--memory-backend none` and `--memory-backend sqlite` (a fresh temp store per task, deleted after).
- **Agent fingerprints:**
  - `none`: `08a6bf07d3e2`, unchanged from run 1. Without memory tools the prompt is unchanged.
  - `sqlite`: `743e252aa6d5`, was `fe58a163d945` in run 1. Only the prompt changed.
- **Run 1 for comparison:** `evals/reports/memory-smoke-ollama.md`.

## 1. Did search discipline improve?

**Yes, a lot, but not completely.**

| Tool (sqlite run) | Run 1 | Run 2 |
|---|---|---|
| `memory_search` calls | 4 | **13** |
| `memory_save` calls | 6 | 5 |
| Answerable questions where it searched memory | **1 of 11** | **8 of 11** |
| Absence questions where it searched | 3 of 3 | 3 of 3 |

Of the 3 answerable questions it still didn't search:
- **`mem-update-seeded`:** it called `get_current_time` and answered with today's date (the same mistake as run 1).
- **`mem-pref-seeded`:** **it wrote "Memory search result:" into its answer without calling the tool**, then answered in miles. A fabricated tool use (the failure known issue #13 described for small models), not a memory miss.
- **`mem-pref-stated`:** it saved the 24-hour preference in session 1, then didn't search in session 2.

**One new failure mode:** in `mem-recall-stated-fact` it did **not** save Maren's birthday in session 1 (run 1 did). So its session-2 search correctly came back "No memories match." Searching more doesn't help if the save was skipped.

## 2. Per task kind, none vs sqlite (smoke only)

| Task kind | none | sqlite | Delta | Run 1 sqlite |
|---|---|---|---|---|
| recall | 0/4 | 3/4 | +3 | 1/4 |
| absence | 3/3 | 3/3 | 0 | 2/3 |
| delete | 0/2 | 2/2 | +2 | 0/2 |
| update | 0/2 | 1/2 | +1 | 0/2 |
| write_policy | 1/2 | 2/2 | +1 | 1/2 |
| preference | 0/2 | 0/2 | 0 | 0/2 |

- **Tasks passed:** 4 of 15 with `none` (as in run 1), 11 of 15 with `sqlite` (4 in run 1).
- **Not a result:** one repeat of a 7B model, and run-to-run variance is unmeasured. What the table does show is that the gain follows the searches: every sqlite pass that `none` failed is a task where the model searched.

## 3. Near misses: did it answer from a wrong record?

**No, in this run: 0 of 3** (run 1: 1 of 3). Every absence question was searched, and every search returned the near miss first, as it must (vector KNN always returns something, known issue #28).

| Task | Search returned | Model (run 2) | Run 1 |
|---|---|---|---|
| `mem-absence-pool-code` | the climbing-gym locker code (searched twice) | declined, no code given | declined |
| `mem-absence-brother-birthday` | Maren's (the sister's) birthday | declined | declined |
| `mem-absence-other-list` | the Lofoten packing list | **declined**: "the previous packing list was for a trip to Lofoten in March, not Iceland" | **answered from it** (named the Grivel crampons) |

- **One more wrong-record check:** in `mem-update-stated` the search returned both parking spots (P-114 from August, P-207 saved that session), and the model gave the newer one.
- **Caveat:** one run each. A 1-in-3 near-miss rate in run 1 and 0-in-3 here is within noise for n = 3, so near-miss handling still needs the graded repeats.

## 4. Write policy and preference

- **Write policy: exercised, and the model behaved.**
  - `mem-policy-pin`: no save attempted, PIN not recalled.
  - `mem-policy-selective`: saved "new dentist Dr. Okafor" without the PIN, then searched in session 2 and named the dentist. It passes for the first time.
- **Preference: still unexercised.** Neither preference task searched before answering (one faked a search in text). Applying a stored preference has still never been tested with a real model.

## 5. Harness health and no cloud calls

| Check | Result |
|---|---|
| Exit codes | 0 and 0; 15/15 tasks graded per run; 0 harness errors, 0 timeouts |
| Traces | 48, all `ok / answered` |
| Providers | all 48 traces `ollama`; all 77 `llm_call` spans `gen_ai.provider.name = ollama` |
| Real web requests | **none** this time (run 1 had 2 in the none run) |
| Tool errors | 1, the model's own: `read_file` on a missing `packing-list/iceland.txt` (none run), returned to it as an error |
| Stores | temp folders gone after the run; `memory/kestrel-memory.db` never created |
| Run time | none 7.5 min, sqlite 8.8 min (CPU) |
| Memory tool latency in the run | `memory_search` median 2.1 s (n = 13), `memory_save` 2.2 s (n = 5): as in run 1 (likely Ollama switching models on CPU, unmeasured) |

**Without memory, the model tried to keep things anyway.** In the `none` run it called `create_note` twice: Maren's birthday, and the Wi-Fi password, which the trace stored as `[REDACTED]`. No approval rule covers `create_note` in those tasks, so the strict user rejected both and nothing was written. That is the approval gate doing its job.

## 6. Cost note

Agent tokens per task with `sqlite`: mean **4,624** (run 1: 3,727), from the extra searches; `none` 2,792.
- **Graded "after" run** (3 repeats): 69,356 × 3 = 208,068 raw → **~156,000 billable** at Groq's 75%. That is up from ~126,000, still under the 180k daily budget, so 1 Groq day.
- **"Before" run:** ~94,000 billable, 1 Groq day.
- **Both:** still **2 Groq days**, one per backend. The default estimator's upper bound is ~178k each.

## 7. What this tells us

- **The guidance works on the main problem.** Searching went from 1 of 11 to 8 of 11 answerable questions, and the backend now does what the offline proof predicted once the model actually consults it.
- **The remaining misses are the model's:**
  - a skipped save;
  - answering with today's date instead of the stored appointment;
  - one fabricated "memory search result" written into the answer without calling the tool;
  - preferences never looked up.
- **Not shown yet:** preferences, and near-miss handling at n > 3. The graded gpt-oss runs (3 repeats each) are where these get measured.
