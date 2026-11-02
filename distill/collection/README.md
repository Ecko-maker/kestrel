# Option c collection d1.2-pass1, pinned

**Evidence only, not training data (design decision 29):** the teacher is option a (Groq + gpt-oss-120b),
and one dataset has one teacher, so this collection is never trained on.

The provenance of the first distill collection: the Kaggle collect run's manifest and the `kestrel distill build`
manifest (the build report), copied byte for byte. These files must never change: `tests/test_kaggle_collect.py`
checks their SHA-256 values. If it fails, restore the file from git instead of updating the hash.

**Collection:** 2026-11-01, 00:55-01:07 UTC, Kaggle `collect.ipynb` in collect mode, GPU T4 x2, Ollama 0.40.13,
snapshot commit `5cce69c`, teacher Qwen3-30B-A3B-Instruct-2507 Q4 (digest `19e422b02313…`, #72), `--judge none`,
no Groq. 122/122 distill tasks once each in 8 shards ("Shards OK"): 93 passed, 29 failed, 0 errored.

**Build:** in the runs folder at `5cce69c`, with `KESTREL_REQUEST_TIMEOUT=600` (`distill/KAGGLE.md` step 8), on a copy
of the collection's `distill.db` (`input_db_sha256` in the build manifest). 129 traces, 82 kept: 74 train, 8 val.

| File | SHA-256 | From |
|---|---|---|
| `collect-manifest-5cce69cdee15.json` | `cf2eb646a7c5043ec6422bb11c4a1ad812da87dc702b9e34a14cbf3edf2668bc` | the zip's `collect/manifest.json` |
| `build-manifest-d1.2-pass1.json` | `694e2189d277520c2ba4d85ca3be21499c4269a29f92910e930081a56f74b0a8` | `data\distill\d1.2-pass1\manifest.json` |

**Not in git:**
- the traces (`distill.db`), the eight shard results files and the dataset (`train.jsonl`, `val.jsonl`);
- where they are: the runs folder, `evals\results\collect-5cce69cdee15\` and `data\distill\d1.2-pass1\`, backed up in
  `C:\kestrel-backup\2026-10-31\`.

The build manifest records the hashes of all eight results files and the input database, so any copy can be
checked against it. Findings from this pass are in `distill/COLLECTION.md`, "Results: d1.2-pass1".
