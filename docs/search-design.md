# Search for long-term memory: design

Status: **approved 2026-10-10** by the owner, with the decisions in section 6 (design decision 17). Proposed on branch `phase3/search-spike`; built on `phase3/memory` as `SqliteMemory` (`src/kestrel/memory.py`). This is step 5 of `docs/phase3-plan.md` (hybrid search).

## 1. Decision

| Choice | Decision |
|---|---|
| Keyword search | SQLite **FTS5** (BM25 ranking), built into Python's `sqlite3` |
| Vector search | **sqlite-vec** `vec0` virtual table, KNN by distance, **float32** |
| Embeddings | local Ollama **`nomic-embed-text`**, 768 dimensions, with `search_query:` / `search_document:` prefixes |
| Hybrid | **reciprocal rank fusion** (RRF, k = 60) of the keyword and vector rankings |
| Storage | one SQLite file, `memory/kestrel-memory.db` (git-ignored, `KESTREL_MEMORY_DB`), never under `workspace/`: one `memories` table with a `kind` column, its FTS5 index and its vectors side by side, plus a `meta` row naming the embedding model and dimension |
| Documents | chunked at about 400 tokens with about 50 overlapping; each chunk points to its parent document |

Why: it costs $0, runs on Windows, needs no server, and Kestrel already uses SQLite for traces (`logs/traces.db`, `logs/bench.db`). Keyword and vector search then share one file, one connection and one transaction, so a delete removes a memory from both indexes at once.

## 2. Evidence

**sqlite-vec on Windows: verified** (2026-10-10, `scripts/spikes/vec_feasibility.py`, commit `80e61ce`).
- Environment: `sqlite-vec` 0.1.9 (wheel ships `vec0.dll`), Python 3.14.4 AMD64, SQLite 3.50.4, uv-managed `.venv`.
- `enable_load_extension(True)` is available, `sqlite_vec.load()` succeeds and `vec_version()` returns `v0.1.9`. The script locks extension loading again right after.
- A `vec0(embedding float[3])` table took 4 inserts, and a k=2 KNN query returned the expected neighbours in order (`[(1, 0.05), (4, 0.1118)]`, asserted).
- Rerun: `uv run --no-sync python scripts/spikes/vec_feasibility.py`.

**Embedding model: measured.** `ollama show nomic-embed-text`: `nomic-bert` architecture, 137M parameters, embedding length **768**, context 2048 (num_ctx 8192), F16, Apache-2.0. Per text, through `SqliteMemory`: median **43 ms**, p90 85 ms (28 calls); the first call, which loads the model, took 2.1 s (`evals/reports/search-comparison.md`).

**FTS5: available locally.** A one-line check in the same environment created an `fts5` table, inserted a row and matched it with `bm25()` ranking. It is not part of the spike script. The memory backend's first test should create an FTS5 table, so a missing FTS5 fails loudly in CI on both Windows and Ubuntu.

## 3. Postgres + pgvector: rejected

- **It needs a running server.** Postgres must be installed and run as a service (or in Docker, which this laptop doesn't have), then kept running, upgraded and backed up. On Windows that means an installer, a service account and a port. That breaks the "no extra server to run" constraint and makes Kestrel harder to install than `uv sync`.
- **pgvector is a separate extension.** It must be built or installed to match the Postgres version, and on Windows that is a manual build or a third-party binary.
- **No gain at this scale.** A personal agent's memory is thousands of records, not millions. Exact KNN in `vec0` is fine at that size, and pgvector's approximate indexes (HNSW, IVFFlat) solve a problem Kestrel doesn't have.
- **A second store.** Kestrel's traces are already in SQLite. One SQLite file per concern keeps backup and deletion simple (copy or delete a file).
- **Revisit if:** memory becomes multi-user or shared across devices, which needs a server anyway.

## 4. How it plugs into the memory backend

The memory evals define the contract (`Backend` protocol in `src/kestrel/bench/memory.py`; tools in `docs/memory-evals-design.md` section 2). `SqliteMemory` (`src/kestrel/memory.py`, tests in `tests/test_memory_store.py`) implements it like this:

| `Backend` method | With FTS5 + sqlite-vec |
|---|---|
| `load_seed(records)` | Insert each `Record` (kind, text, at, source) into a `memories` table, its text into the FTS5 index and its embedding into `vec0`, all under the same rowid, in one transaction. |
| `register(registry)` | Adds `memory_search(query)` (keyword top-N + vector top-N, fused by RRF, returned as `<untrusted_data>`) and `memory_save(kind, text)` (confirm tier). Never a delete tool. |
| `delete(pattern)` | The user's delete: find matching rowids, then delete them from `memories`, FTS5 and `vec0` in one transaction. A hard delete, so recall in later sessions can't find it. Returns the count. |
| `contains(pattern)` | A regex scan over `memories.text`, for the split's `memory_has` / `memory_absent` checks. |

**RRF in one line:** each record scores `sum over retrievers of 1 / (k + rank)`, with `k = 60` (Cormack, Clarke & Büttcher, 2009). It needs only ranks, not scores, so BM25 and vector distances never have to be put on one scale. That's why RRF over a weighted sum: a weight would have to be tuned, and it would drift if the embedding model changed.

**If Ollama is down:** `memory_save` stores the record and its FTS5 entry and leaves the vector missing (backfilled later). `memory_search` falls back to keyword only and says so at the top of its result, which the agent records in the trace (`gen_ai.tool.call.result`). Memory keeps working without the embedder, and no cloud fallback is ever used.

## 5. The comparison: keyword vs vector vs hybrid (done 2026-10-10)

Run on the memory split's own seeds and queries, as planned (not a synthetic corpus: the queries are the ones memory is graded on, and the seeds are already reviewed and versioned). Full table: `evals/reports/search-comparison.md`; rerun with `uv run --no-sync python scripts/search_comparison.py` (local Ollama only).

| Retriever | Rank-1 hits (7 queries, 8 records) |
|---|---|
| keyword (FTS5) | 6/7 |
| vector (sqlite-vec) | 7/7 |
| hybrid (RRF) | 7/7 |

- **Hybrid does not beat both singles.** It ties vector-only. Keyword misses one query, "How long is a marathon?", whose answer is the distances preference: no shared words, a paraphrase.
- **Kept anyway, for reasons this sample doesn't measure:** the keyword index is memory's only search while Ollama is down, and exact codes are expected to need keyword search once thousands of similar records exist. With 8 records, vector search finds them too.
- **Re-measure trigger:** if vector alone still matches hybrid on real memories or a larger split, drop keyword from ranking and keep it as the fallback only.
- **Two findings for the agent, not the retriever** (known issue #28):
  - Vector KNN always returns something, so absence questions get the near-miss record.
  - Ranking has no notion of time: keyword and hybrid put the stale dentist appointment first. Both records reach the agent with their dates.

## 6. Owner decisions (2026-10-10)

The open questions of the proposal, answered:
1. **Embedding storage:** float32 (3,072 bytes per vector), plus a `meta` row with the model and dimension. Opening the store with another model or dimension is refused until a re-embed rebuilds every vector, so vectors from two models never mix.
2. **Chunking:** documents are split into chunks of about 400 tokens with about 50 overlapping. Each chunk row points to its parent document, so a delete removes every chunk.
3. **One table:** one `memories` table with a `kind` column. One search covers facts, episodes, documents and preferences, and RRF ranks across kinds.
4. **Where the file lives:** `memory/kestrel-memory.db`, git-ignored, overridable with `KESTREL_MEMORY_DB`, and refused under `workspace/`, so `read_file` can't bypass memory's own tools and tier.
5. **Prefixes on:** `search_document:` for stored text, `search_query:` for queries (the `nomic-embed-text` model card's convention).

## 7. Still open (from the comparison)

- **A similarity cutoff for vector hits,** so an absence question can come back empty.
- **Whether to boost newer records** in ranking.

Both wait for the first real memory-split run (known issue #28).
