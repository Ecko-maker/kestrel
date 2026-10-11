"""SqliteMemory (src/kestrel/memory.py, design decision 17): FTS5 + sqlite-vec in one file, fused by
RRF. No model and no network: a hashing fake stands in for the embedder, and "Ollama is down" is a
fake that raises (plus one real OllamaEmbedder pointed at a closed local port)."""

import hashlib
import math
import re
import sqlite3
from pathlib import Path

import pytest

from kestrel import memory as mem
from kestrel import tools
from kestrel.bench.memory import Backend, Record
from kestrel.bench.runner import agent_fingerprint
from kestrel.memory import EmbedderUnavailable, EmbeddingMismatch, MemoryStoreError, OllamaEmbedder, SqliteMemory
from kestrel.tools import ToolRegistry

SEED = (
    Record("fact", "Locker code at the climbing gym: K7-4419", "2026-09-12"),
    Record(
        "episode",
        "Drafted the venue email to Priya Raman. Venue chosen: Larchmont Hall; deposit EUR 450.",
        "2026-10-01",
    ),
    Record("fact", "Studio door code: 7713", "2026-09-01"),
    Record(
        "preference", "Distances: always give kilometres only, rounded to whole numbers; never miles.", "2026-09-05"
    ),
)


class HashEmbedder:
    """Bag of words hashed into `dim` buckets, normalised: texts sharing words are close. Offline."""

    def __init__(self, model: str = "fake-embed", dim: int = 32):
        self.model, self.dim = model, dim
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for word in re.findall(r"\w+", text.lower().split(": ", 1)[-1]):  # drop the nomic prefix
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class DownEmbedder(HashEmbedder):
    """Ollama unreachable until `up` is set."""

    up = False

    def embed(self, texts):
        if not self.up:
            raise EmbedderUnavailable("nomic-embed-text on Ollama: APIConnectionError: Connection error.")
        return super().embed(texts)


def store(tmp_path: Path | None = None, embedder=None, **kw) -> SqliteMemory:
    path = ":memory:" if tmp_path is None else tmp_path / "mem.db"
    return SqliteMemory(path, embedder or HashEmbedder(), **kw)


def rows(m: SqliteMemory, table: str) -> int:
    return m.conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_seed_then_search_finds_the_record_in_both_indexes():
    m = store()
    m.load_seed(SEED)
    result = m.search("What's my locker code at the climbing gym?")
    assert result.mode == "hybrid" and result.note is None
    top = result.hits[0]
    assert "K7-4419" in top.text and top.kind == "fact" and top.at == "2026-09-12" and top.source == "user"
    assert top.keyword_rank == 1 and top.vector_rank is not None  # found by both retrievers, fused
    assert rows(m, "memories") == rows(m, "memories_fts") == rows(m, "memories_vec") == 4
    assert m.search("K7-4419", mode="keyword").hits[0].text == top.text  # an exact code, keyword alone


def test_delete_is_hard_and_reaches_every_index():
    m = store()
    m.load_seed(SEED)
    assert m.delete(r"7713") == 1
    assert not any("7713" in h.text for h in m.search("studio door code").hits)
    assert not any("7713" in h.text for h in m.search("7713", mode="keyword").hits)
    assert m.contains("7713") is False
    assert rows(m, "memories") == rows(m, "memories_fts") == rows(m, "memories_vec") == 3
    assert m.delete(r"no such thing") == 0


def test_contains_uses_the_bench_checks_regex_flags():
    m = store()
    m.load_seed(SEED)
    assert m.contains(r"larchmont") is True  # case-insensitive, like the checks
    assert m.contains(r"K7[- ]?4419") is True
    assert m.contains(r"P-207") is False


def test_a_sqlite_without_fts5_fails_loudly(monkeypatch):
    monkeypatch.setattr(mem, "FTS_MODULE", "fts5_missing")
    with pytest.raises(MemoryStoreError, match="no FTS5"):
        store()


def test_the_meta_row_refuses_another_model_or_dimension_until_a_reembed(tmp_path):
    m = store(tmp_path, HashEmbedder("model-a", 32))
    m.load_seed(SEED)
    m.close()
    for other in (HashEmbedder("model-b", 32), HashEmbedder("model-a", 16)):
        with pytest.raises(EmbeddingMismatch, match="reembed=True"):
            store(tmp_path, other)
    m = store(tmp_path, HashEmbedder("model-b", 16), reembed=True)
    assert dict(m.conn.execute("select key, value from meta")) == {"embed_model": "model-b", "embed_dim": "16"}
    assert m.pending() == [] and rows(m, "memories_vec") == 4  # every vector rebuilt in the new dimension
    assert "K7-4419" in m.search("climbing gym locker").hits[0].text
    m.close()
    store(tmp_path, HashEmbedder("model-b", 16)).close()  # and it opens normally from now on


def test_with_ollama_down_saves_still_land_and_search_says_it_is_keyword_only():
    embedder = DownEmbedder()
    m = store(embedder=embedder)
    registry = ToolRegistry()
    m.register(registry)
    saved = registry.execute("memory_save", {"kind": "fact", "text": "Office parking spot: P-207"}, approved=True)
    assert saved.startswith("Saved. It is keyword-searchable now")
    assert rows(m, "memories") == rows(m, "memories_fts") == 1 and rows(m, "memories_vec") == 0
    assert len(m.pending()) == 1

    out = registry.execute("memory_search", {"query": "parking spot"})
    # The tool result is what the agent records in the trace (gen_ai.tool.call.result): it says so.
    assert "Note: keyword search only (the local embedding model is unavailable)" in out
    assert "P-207" in out
    assert m.last is not None and m.last.mode == "keyword-only (fallback)"
    assert "APIConnectionError" in (m.last.note or "")
    with pytest.raises(EmbedderUnavailable):
        m.search("parking", mode="vector")  # vector-only has no fallback: it says it can't

    embedder.up = True  # Ollama is back: the next search backfills the missing vector first
    result = m.search("parking spot")
    assert result.mode == "hybrid" and m.pending() == [] and rows(m, "memories_vec") == 1


def test_a_real_ollama_embedder_on_a_closed_port_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens there; never leaves the machine
    with pytest.raises(EmbedderUnavailable, match="nomic-embed-text"):
        OllamaEmbedder(timeout=2).embed(["hello"])


def test_tools_search_is_untrusted_save_needs_approval_and_there_is_no_delete():
    m = store()
    m.load_seed(SEED)
    registry = ToolRegistry()
    m.register(registry)
    assert set(registry.tools) == {"memory_search", "memory_save"}
    assert registry.tools["memory_search"].risk == "safe" and registry.tools["memory_search"].untrusted_output
    assert registry.tools["memory_save"].risk == "confirm"
    out = registry.execute("memory_search", {"query": "Priya venue"})
    assert out.startswith('<untrusted_data source="memory_search">') and "Larchmont" in out
    assert "needs the user's approval" in registry.execute("memory_save", {"kind": "fact", "text": "x"})
    assert rows(m, "memories") == 4  # the unapproved save did nothing
    bad = registry.execute("memory_save", {"kind": "secret", "text": "x"}, approved=True)
    assert bad.startswith("Error: ValueError: kind must be one of")
    # Only stopwords: keyword search finds nothing, but vector KNN always returns its nearest records,
    # so "nothing stored" is the agent's call, not the retriever's (search-design open question).
    assert m.search("the of and", mode="keyword").hits == []
    hits = m.search("the of and").hits
    assert hits and all(h.keyword_rank is None and h.vector_rank is not None for h in hits)
    empty = ToolRegistry()
    store().register(empty)
    assert "No memories match." in empty.execute("memory_search", {"query": "anything"})  # an empty store
    preview = registry.tools["memory_save"].preview
    assert preview is not None and "Larchmont" in preview({"kind": "fact", "text": "Larchmont"})


def test_long_documents_are_chunked_with_overlap_and_deleted_whole():
    words = [f"w{i}" for i in range(700)]
    words[650] = "Grivel-G12"
    m = store()
    doc = m.add("document", " ".join(words), "2026-09-28")
    chunks = m.conn.execute("select text from memories where parent_id = ? order by chunk", (doc,)).fetchall()
    assert len(chunks) == 3
    first, second = chunks[0][0].split(), chunks[1][0].split()
    assert len(first) == mem.CHUNK_WORDS and first[-mem.OVERLAP_WORDS :] == second[: mem.OVERLAP_WORDS]
    assert rows(m, "memories_fts") == rows(m, "memories_vec") == 3  # the chunks are indexed, not the parent
    [hit] = m.search("Grivel-G12", mode="keyword").hits
    assert hit.id == doc and "Grivel-G12" in hit.text and len(hit.text.split()) <= mem.CHUNK_WORDS
    assert len({h.id for h in m.search("w1 w2 w3 Grivel-G12").hits}) == len(m.search("w1 w2 w3 Grivel-G12").hits)
    assert m.delete("Grivel") == 1
    assert rows(m, "memories") == rows(m, "memories_fts") == rows(m, "memories_vec") == 0


def test_the_store_is_refused_under_the_workspace_and_defaults_outside_it(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path.resolve())
    with pytest.raises(MemoryStoreError, match="workspace"):
        SqliteMemory(tmp_path / "sub" / "mem.db", HashEmbedder())
    monkeypatch.delenv("KESTREL_MEMORY_DB", raising=False)
    assert mem.default_db_path() == mem.PROJECT_ROOT / "memory" / "kestrel-memory.db"
    monkeypatch.setenv("KESTREL_MEMORY_DB", str(tmp_path / "x.db"))
    assert mem.default_db_path() == tmp_path / "x.db"


def test_rrf_and_the_keyword_query_are_safe_and_ranked():
    assert mem.rrf([[1, 2], [2, 3]]) == pytest.approx({1: 1 / 61, 2: 1 / 62 + 1 / 61, 3: 1 / 62})
    assert (
        mem.fts_query('What\'s my "K7-4419" code? NEAR(x) OR *') == '"k7-4419" OR "code" OR "near"'
    )  # 1-letter terms dropped
    m = store()
    m.load_seed(SEED)
    assert m.search('") OR 1=1 --', mode="keyword").hits == []  # quotes and FTS5 syntax never get through


def test_record_kinds_match_the_memory_split():
    from kestrel.bench.memory import RECORD_KINDS

    assert mem.RECORD_KINDS == RECORD_KINDS


def test_it_is_a_backend_and_runs_the_sessions_harness_end_to_end(tmp_path):
    """The two-session task from test_memory_runner: seed, recall, the user's delete, then nothing."""
    from test_memory_runner import run, split_with

    backend: Backend = store()  # type-checks as the memory split's Backend
    assert backend.name == "sqlite"
    result, _, harness = run(split_with(tmp_path), lambda: store())
    assert result.status == "pass", result.checks
    assert all(c["assessed"] for c in result.checks)  # a real store: memory_has / memory_absent count
    assert "Studio door code: 7713" in result.tool_log[0]
    assert harness.log[0]["removed"] == 1


def test_memory_tools_never_join_the_global_registry_so_agent_fingerprints_hold():
    m = store()
    m.register(ToolRegistry())
    assert not {"memory_search", "memory_save"} & set(tools.registry.tools)
    assert agent_fingerprint("groq", "openai/gpt-oss-120b")["sha"] == "cc5c16377662"


def test_the_sqlite_build_supports_fts5_here():
    """CI runs on Windows and Ubuntu: a build without FTS5 fails here, not in production."""
    db = sqlite3.connect(":memory:")
    db.execute("create virtual table t using fts5(x)")
    db.close()
