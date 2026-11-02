# Option a collection d1.2, groq + openai/gpt-oss-120b, pinned

**The training data's provenance** (design decision 29): the collection run on Groq and the `kestrel distill
build` that made the training set from it. Two files, which must never change: `tests/test_distill_collection_a.py`
checks their SHA-256 values. If it fails, restore the file from git instead of updating the hash.

**Collection:** 2026-11-01 and 2026-11-02 (finished 2026-11-02 14:33 UTC), in the runs folder at `5b2a242`,
`kestrel bench run --split distill --judge none --provider groq --model openai/gpt-oss-120b`, agent
`35fac5ba6277`, under the 11-01/11-02 approval line (up to 320k). 122/122 distill tasks once each:
**104 passed, 18 failed, 0 errored, 0 skipped**, **206,378 billable**. The rate-limit audit
(`scripts/audit_rate_limits.py`) flagged 104 graded tasks, all for retried calls that then answered (plus
`max_steps` on 3 failed tasks); no call gave up, no tool timed out, no trace was missing.

**Build:** in the runs folder at `5b2a242` (clean), on a copy of the run's `distill.db`, with `--val-frac 0.3`.
131 traces (129 named by the results, 2 earlier attempts named by none), **89 kept: 71 train, 18 val**, from 82
tasks (65 train, 17 val). Dropped: `quality` 20 (the 18 failed tasks' traces and the 2 unnamed ones),
`rubric_task` 22 (the passed rubric tasks, `grading = "checks-only"`); every other filter 0, the Harmony-leak
and KestrelBench-overlap filters included. `--val-frac` was 0.2 first, which put only 8 examples in val; 0.3 was
then chosen from the session hashes alone (no model had seen the data) to land in the 15-20 asked for.

**Split by task:** each task ran in one session and no session holds two tasks, so the build's session-hash
split puts all traces of a task on the same side. The test rebuilds the 71 / 18 counts from the collection
manifest's sessions and checks that no task is on both sides.

| File | SHA-256 | From |
|---|---|---|
| `collect-manifest-groq-5b2a242.json` | `967f8bd5989e578716d17a401e0d6e099eb95224d9e968fc29ce32642fee9480` | `scripts/collection_manifest.py` on the results file and a copy of `logs\distill.db` |
| `build-manifest-d1.2-groq-gpt-oss-120b.json` | `e39dd6616f6e5a248c2e5ecf04d61212211a92bb8e89137bc4775604866b93dc` | `data\distill\d1.2-groq-gpt-oss-120b\manifest.json`, byte for byte |

**Not in git:** the results file (`evals\results\distill-d1.2-groq-gpt-oss-120b.json`), the traces
(`logs\distill.db`) and the dataset (`data\distill\d1.2-groq-gpt-oss-120b\`), all in the runs folder and backed up
in `C:\kestrel-backup\2026-11-02\` (`MANIFEST.txt` with SHA-256 per file). Both manifests record the hashes of the
results file and the database, so any copy can be checked against them.
