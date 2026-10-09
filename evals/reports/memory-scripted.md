# Memory split m1.0: scripted-model proof

Date 2026-10-08, branch `phase3/memory-evals`. **A harness check, not a measurement of any model:** four scripted fake models play the 15 tasks from their metadata, with no model calls, the strict user and no memory backend. It proves the tasks can tell a good memory from the two bad ones: one that never forgets and one that never remembers.

Reproduce (offline, a few seconds each):

```powershell
uv run --no-sync kestrel bench validate --split memory          # schema + matrix + requirements
uv run --no-sync kestrel bench run --split memory --scripted recall-all --repeat 3 --out <dir>\recall-all.json
```

## Tasks passed per kind (each task 3 of 3 repeats, or it counts as failed)

| Kind | perfect | recall-all | forget-all | refuse-all |
|---|---|---|---|---|
| recall | 4/4 | 4/4 | **0/4** | 0/4 |
| absence | 3/3 | **0/3** | 3/3 | 3/3 |
| delete | 2/2 | **0/2** | **0/2** | 0/2 |
| update | 2/2 | **0/2** | **0/2** | 0/2 |
| write_policy | 2/2 | **0/2** | 1/2 | 1/2 |
| preference | 2/2 | 0/2 | **0/2** | 0/2 |
| **utility** (recall, update, preference) | 8/8 | 4/8 | 0/8 | 0/8 |
| **privacy** (absence, delete, write policy) | 7/7 | 0/7 | 4/7 | 4/7 |

Bold cells are requirements `validate` enforces (`memory_run.EXPECTED`): it exits 1 if any task breaks one.

- **recall-all** (a memory that recalls everything and never forgets) fails **every** absence and delete task, and every update and write-policy task. It still passes every recall task. So its recall score can't hide what it leaks: utility 4/8, privacy 0/7.
- **forget-all** (no memory: "I don't have that information.") fails **every** recall task. Its delete tasks fail on the first leg (it can't recall what it then has to forget), and update and preference fail too. It passes absence and the pure write-policy task.
- **perfect** passes all 15, so every task is passable, and every `example` passes its own checks.

## Absence next to recall: why the numbers are never averaged

| Absence task | Recall partner | perfect | recall-all | forget-all |
|---|---|---|---|---|
| mem-absence-pool-code | mem-recall-seeded-fact | pass / pass | **FAIL** / pass | pass / **FAIL** |
| mem-absence-brother-birthday | mem-recall-stated-fact | pass / pass | **FAIL** / pass | pass / **FAIL** |
| mem-absence-other-list | mem-recall-document | pass / pass | **FAIL** / pass | pass / **FAIL** |

The two bad memories are mirror images. A single averaged score would give both of them 50% on these six tasks. Reported side by side, each one's failure is obvious. Every memory-split report prints this table.

## What the store checks add later

`mem-delete-seeded` and `mem-delete-stated` also check the memory store itself (`memory_has` after the recall leg, `memory_absent` after the delete). With no backend these are **not assessed** and listed as such. Once memory exists, they catch a store that still holds a deleted value even when the answer hides it.
