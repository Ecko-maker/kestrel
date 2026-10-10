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

**Embedding model: installed, not yet measured.** `ollama show nomic-embed-text`: `nomic-bert` architecture, 137M parameters, embedding length **768**, context 2048 (num_ctx 8192), F16, Apache-2.0. **Per-embed latency was not measured** in this spike; it is measured with the comparison in section 5.

**FTS5: available locally.** A one-line check in the same environment created an `fts5` table, inserted a row and matched it with `bm25()` ranking. It is not part of the spike script. The memory backend's first test should create an FTS5 table, so a missing FTS5 fails loudly in CI on both Windows and Ubuntu.

## 3. Postgres + pgvector: rejected

- **It needs a running server.** Postgres must be installed and run as a service (or in Docker, which this laptop doesn't have), then kept running, upgraded and backed up. On Windows that means an installer, a service account and a port. That breaks the "no extra server to run" constraint and makes Kestrel harder to install than `uv sync`.
- **pgvector is a separate extension.** It must be built or installed to match the Postgres version, and on Windows that is a manual build or a third-party binary.
- **No gain at this scale.** A personal agent's memory is thousands of records, not millions. Exact KNN in `vec0` is fine at that size, and pgvector's approximate indexes (HNSW, IVFFlat) solve a problem Kestrel doesn't have.
- **A second store.** Kestrel's traces are already in SQLite. One SQLite file per concern keeps backup and deletion simple (copy or delete a file).
- **Revisit if:** memory becomes multi-user or shared across devices, which needs a server anyway.

## 4. How it plugs into the memory backend

The memory evals define the contract (`Backend` protocol in `src/kestrel/bench/memory.py`; tools in `docs/memory-evals-design.md` section 2). A future `SqliteMemory` backend would implement it like this:

| `Backend` method | With FTS5 + sqlite-vec |
|---|---|
| `load_seed(records)` | Insert each `Record` (kind, text, at, source) into a `memories` table, its text into the FTS5 index and its embedding into `vec0`, all under the same rowid, in one transaction. |
| `register(registry)` | Adds `memory_search(query)` (keyword top-N + vector top-N, fused by RRF, returned as `<untrusted_data>`) and `memory_save(kind, text)` (confirm tier). Never a delete tool. |
| `delete(pattern)` | The user's delete: find matching rowids, then delete them from `memories`, FTS5 and `vec0` in one transaction. A hard delete, so recall in later sessions can't find it. Returns the count. |
| `contains(pattern)` | A regex scan over `memories.text`, for the split's `memory_has` / `memory_absent` checks. |

**RRF in one line:** each record scores `sum over retrievers of 1 / (k + rank)`, with `k = 60` (Cormack, Clarke & Büttcher, 2009). It needs only ranks, not scores, so BM25 and vector distances never have to be put on one scale. That's why RRF over a weighted sum: a weight would have to be tuned, and it would drift if the embedding model changed.

**If Ollama is down:** `memory_save` stores the record and its FTS5 entry and leaves the vector missing (backfilled later). `memory_search` falls back to keyword only and says so in its trace. Memory keeps working without the embedder, and no cloud fallback is ever used.

## 5. Deferred, not skipped: the keyword vs vector vs hybrid comparison

The empirical comparison (which retriever finds the right record at rank 1, keyword vs vector vs RRF, plus embed latency) **runs while building the memory backend**, against the memory split's own queries and seeds, not against a throwaway synthetic corpus. Why:
- **Representative queries.** The memory tasks were written to test what Kestrel's memory must do (facts, episodes, latest-wins, absence). A result on them predicts real behavior; a hand-made corpus mostly measures how it was written.
- **No throwaway data.** The seed records already exist, are reviewed and are versioned (`MEMORY_VERSION`). A second corpus would need its own review and would drift from them.
- **The decision doesn't wait on it.** Every option in section 1 is local and free. If the comparison shows vector or keyword alone is as good, the backend drops the other retriever without any change to storage.

## 6. Owner decisions (2026-10-10)

The open questions of the proposal, answered:
1. **Embedding storage:** float32 (3,072 bytes per vector), plus a `meta` row with the model and dimension. Opening the store with another model or dimension is refused until a re-embed rebuilds every vector, so vectors from two models never mix.
2. **Chunking:** documents are split into chunks of about 400 tokens with about 50 overlapping. Each chunk row points to its parent document, so a delete removes every chunk.
3. **One table:** one `memories` table with a `kind` column. One search covers facts, episodes, documents and preferences, and RRF ranks across kinds.
4. **Where the file lives:** `memory/kestrel-memory.db`, git-ignored, overridable with `KESTREL_MEMORY_DB`, and refused under `workspace/`, so `read_file` can't bypass memory's own tools and tier.
5. **Prefixes on:** `search_document:` for stored text, `search_query:` for queries (the `nomic-embed-text` model card's convention).
