# Memory split smoke run on local Ollama (SMOKE TEST)

> **SMOKE TEST, not a result.** One repeat on a weak local model (`qwen2.5:7b` on CPU). It checks that the harness runs and that a real model drives the memory tools. The pass counts below are not KestrelBench numbers and must not be quoted as Kestrel's memory performance. The graded "before" and "after" runs are gpt-oss-120b on Groq.

Run 2026-10-11 on `phase3/memory` at `d96f743`, memory split m1.0, strict user, 1 repeat, judge none.
- **Agent:** Ollama `qwen2.5:7b` (7.6B parameters; its chat template does send tool schemas, unlike `deepseek-r1:8b`, known issue #13).
- **Embeddings:** Ollama `nomic-embed-text`.
- **Ollama only:** `KESTREL_PROVIDERS=ollama`, both cloud keys empty in the process.
- **Two runs:** `--memory-backend none` (Kestrel today) and `--memory-backend sqlite` (a fresh temp store per task, deleted after the run).
- **Results files:** in the session scratchpad, not committed (results are git-ignored).

## 1. Did the model use memory?

**Partly: it saves well and almost never searches.**

| Tool (sqlite run) | Calls | Where |
|---|---|---|
| `memory_save` | 6 | every task where the user asked it to remember or update something, plus `mem-absence-brother-birthday`'s setup turn |
| `memory_search` | 4 | `mem-recall-episode`, and all 3 absence questions |

- **Saves are well-formed and respect the write policy.** All 6 are short third-person facts ("Maren's birthday is on 17 November.", "My new dentist is Dr. Okafor."). The PIN in `mem-policy-pin` and `mem-policy-selective` was never saved: the selective task saved the dentist and left the PIN out, as asked.
- **Search discipline is the gap.** Of the 11 graded questions whose answer was in memory (recall, delete session 1, update, preference, the selective task's session 2), the model searched memory in **1** (`mem-recall-episode`, which passed).
  - In the other 10 it answered "I don't have access to that information" without looking, including 5 questions about facts it had saved itself in the previous session.
  - Once it called `list_files` on a folder that doesn't exist (`lofoten/`) instead of searching memory.
  - The `memory_search` description says "Use it before saying you don't know something"; this model ignores that.
- So with this model the run exercises **saving, the write policy and absence**, and barely exercises recall. A pass-count difference between the two backends mostly measures whether the model happened to search.

## 2. Per task kind, none vs sqlite (smoke only)

| Task kind | none | sqlite | Delta | What drove it |
|---|---|---|---|---|
| recall | 0/4 | 1/4 | +1 | the one task where it searched (`mem-recall-episode`) |
| absence | 3/3 | 2/3 | −1 | `mem-absence-other-list` answered from a near miss (below) |
| delete | 0/2 | 0/2 | 0 | never searched in session 1, so the recall leg fails before the delete matters |
| update | 0/2 | 0/2 | 0 | never searched; `mem-update-seeded` answered with today's date from `get_current_time` |
| write_policy | 1/2 | 1/2 | 0 | PIN never stored or recalled (pass); `mem-policy-selective` saved Okafor correctly but didn't search for it next session |
| preference | 0/2 | 0/2 | 0 | seeded: never searched (said 42.195 km); stated: saved the 24-hour preference, then answered in AM/PM without searching |

Tasks passed: 4 of 15 under each backend. **Not a result:** this sample can't separate the backends, and the model mostly didn't use memory.

## 3. Absence: did it decline the near miss?

Search returned the near-miss record for all 3 absence questions, as expected (vector KNN always returns something, known issue #28).

| Task | Search returned | Model |
|---|---|---|
| `mem-absence-pool-code` | the climbing-gym locker code | **declined** ("no pool code saved"): pass |
| `mem-absence-brother-birthday` | Maren's birthday (the sister's) | **declined**: pass |
| `mem-absence-other-list` | the Lofoten packing list | **answered from it**, naming the Grivel crampons as if they were the Iceland list: **fail** |

So the risk known issue #28 predicted is real: 1 of 3 near misses was passed off as the answer. With `none` the model had nothing to misuse and passed all 3. That is why absence went down with memory. The distance floor (#28 note a) and the agent's own judgement are the two levers. The graded run will show how often gpt-oss does this.

## 4. Write policy and preference

- **Write policy: exercised, and the model behaved.**
  - `mem-policy-pin`: no save attempted, PIN not recalled.
  - `mem-policy-selective`: the save kept the dentist and dropped the PIN. Its only failure is recall (no search).
- **Preference: only half exercised.** The stated preference was saved, but neither preference task searched memory before answering, so applying a stored preference was never tested.

## 5. Harness health

| Check | Result |
|---|---|
| Exit codes | 0 and 0 |
| Results | 15/15 tasks graded per run; 0 harness errors, 0 timeouts, 0 crashes |
| Traces | 48 (24 sessions per run, one trace each), all `ok / answered` |
| Tool errors | 3, all from the model's own bad arguments: `list_files` on missing folders twice, `get_current_time` with zone `Berlin` instead of `Europe/Berlin` once. Each came back to the model as an error message, as designed. |
| Stores | a fresh temp store per task; the temp folders are gone after the run; `memory/kestrel-memory.db` was never created |
| Store checks | assessed under sqlite, "not assessed" under none, as designed |
| Run time | none 8.3 min, sqlite 9.0 min (CPU) |

**No cloud model calls.** All 48 traces list provider `ollama` only, and all 70 `llm_call` spans have `gen_ai.provider.name = ollama`.

**Two real web requests (none run).** As known issue #27 warns, the memory split keeps the built-in `web_search`. Without memory, qwen web-searched twice: "draft email to Priya about venue" and "crampons". Both were harmless DuckDuckGo requests, not model calls, and they cost no quota. The sqlite run made none.

**Memory tool latency in the run** (tool span, including the embedding): `memory_save` median 2.2 s, `memory_search` median 2.1 s (n = 6 and 4). In isolation, embedding took 43 ms (`search-comparison.md`). The likely cause, **not measured**, is Ollama switching between `qwen2.5:7b` and `nomic-embed-text` on CPU for every memory call. On Groq the chat model is remote, so this would not apply.

## 6. Cost of the graded runs (re-estimated from measured tokens)

Agent tokens per task, measured: **none** mean 2,766 (41,487 for 15 tasks); **sqlite** mean 3,727 (55,903). Memory tools cost about +35% tokens, from the extra tool round trips.

| Run (3 repeats, strict) | Raw tokens (measured × 3) | Billable at Groq's measured 75% | Requests | Groq days |
|---|---|---|---|---|
| before (`none`) | 124,461 | ~93,300 | ~96 | 1 |
| after (`sqlite`) | 167,709 | ~125,800 | ~114 | 1 |
| both | 292,170 | ~219,100 | ~210 | **2** (one per backend; together they exceed a 200k day) |

- **Upper bound:** the default estimator (main-suite mean per session, nothing measured) gives ~178,200 billable per backend. That still fits one Groq day each under `--token-budget 180000`, so the plan stays **2 Groq days either way**.
- **Caveats:** qwen's tokenizer and habits are not gpt-oss's. gpt-oss adds reasoning tokens and may call memory tools more, which costs more, so plan with the upper bound.
- **Estimator pitfall (new known issue #29):** `--estimate-from` on these Ollama files reports ~14k / ~11k billable. Ollama marks most input as cached through its own prompt reuse, and the estimator trusts a file's cached share. Groq's cache behaves differently. The figures above use raw tokens × 0.75 instead.

## 7. What this tells us

- **The plumbing works end to end with a real model:** saving through the approval gate, the temp stores, the store checks, a clean teardown, and local-only calls.
- **The weak link for recall is the agent deciding to search, not retrieval.** The one recall question it searched for got the right record first, and it passed. The other 3 searches were absence questions, where the top record is by design a near miss.
- **The near-miss risk is real** (1 of 3).
- **For the graded run:** watch the search rate and absence. If gpt-oss also skips search, the next change is the system prompt, not the backend. Telling the agent to check memory before saying it doesn't know changes the agent fingerprint, so it waits for the "before" measurement.
