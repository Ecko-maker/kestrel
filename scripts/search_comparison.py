"""Keyword vs vector vs hybrid (RRF) search on the memory split's own seeds and queries.

    uv run --no-sync python scripts/search_comparison.py

Local only: SqliteMemory with the local Ollama nomic-embed-text, no cloud call. Every unique seed
record in the split goes into one store (so each query has distractors). The queries are the
session prompts whose answer is a seeded record; a retriever scores a hit when that record is
rank 1. Absence prompts (nothing correct is stored) are listed separately: they show what each
retriever offers instead. Tasks whose record would come from the agent's own memory_save are
left out, since that wording isn't known before the agent writes it.
Writes evals/reports/search-comparison.md.
"""

import platform
import re
import statistics
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from kestrel.bench.memory import MEMORY_VERSION, load_memory
from kestrel.memory import CANDIDATES, EMBED_MODEL, RRF_K, OllamaEmbedder, SqliteMemory

OUT = Path(__file__).resolve().parents[1] / "evals" / "reports" / "search-comparison.md"
MODES = ("keyword", "vector", "hybrid")
# The seeded record each query must find (a regex over the record's text), per (task, session).
TARGETS = {
    ("mem-recall-seeded-fact", 1): r"climbing gym: K7-4419",
    ("mem-recall-episode", 1): r"Larchmont",
    ("mem-recall-document", 1): r"Lofoten",
    ("mem-delete-seeded", 1): r"Studio door code",
    ("mem-update-seeded", 1): r"Dentist appointment",  # either record; "newest first" is reported apart
    ("mem-update-stated", 2): r"parking spot: P-114",  # the only seeded parking record
    ("mem-pref-seeded", 1): r"kilometres",
}
NEWEST = {("mem-update-seeded", 1): r"moved to 21 October"}


class TimedEmbedder(OllamaEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.times: list[float] = []

    def embed(self, texts):
        start = time.perf_counter()
        out = super().embed(texts)
        self.times.append((time.perf_counter() - start) / len(texts))
        return out


def main() -> None:
    split = load_memory()
    embedder = TimedEmbedder()
    warm = time.perf_counter()
    embedder.embed(["warm-up"])  # loads the model; not counted
    load_s = time.perf_counter() - warm
    embedder.times.clear()

    store = SqliteMemory(":memory:", embedder)
    seeds = list(dict.fromkeys(r for mt in split.tasks for r in mt.meta.seed))
    store.load_seed(seeds)

    rows, absence = [], []
    for mt in split.tasks:
        for n, session in enumerate(mt.meta.conversations(), 1):
            query = session.prompts[-1]
            target = TARGETS.get((mt.id, n))
            if mt.meta.kind == "absence" and session.checks:  # the asking session, not a setup one
                tops = {m: (store.search(query, mode=m).hits or [None])[0] for m in MODES}
                absence.append((mt.id, query, {m: h.text if h else "(nothing)" for m, h in tops.items()}))
            if target is None:
                continue
            hits = {m: store.search(query, mode=m).hits for m in MODES}
            ok = {m: bool(h) and re.search(target, h[0].text) is not None for m, h in hits.items()}
            newest = NEWEST.get((mt.id, n))
            fresh = None
            if newest:
                fresh = {m: bool(h) and re.search(newest, h[0].text) is not None for m, h in hits.items()}
            kinds = {r.kind for r in seeds if re.search(target, r.text)}
            rows.append((mt.id, mt.meta.kind, "/".join(sorted(kinds)), query, ok, fresh, hits))

    lat = sorted(embedder.times)
    write(seeds, rows, absence, lat, load_s)


def pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "–"


def write(seeds, rows, absence, lat, load_s) -> None:
    n = len(rows)
    total = {m: sum(r[4][m] for r in rows) for m in MODES}
    L = [
        "# Memory search: keyword vs vector vs hybrid (RRF)",
        "",
        f"Measured {date.today().isoformat()} by `scripts/search_comparison.py` on the memory split {MEMORY_VERSION}'s "
        f"own seeds and queries. Local only: `SqliteMemory` with Ollama `{EMBED_MODEL}` "
        f"({platform.system()} {platform.machine()}), no cloud call. Hybrid = RRF (k = {RRF_K}) of the top "
        f"{CANDIDATES} of each retriever.",
        "",
        f"**Corpus:** the {len(seeds)} unique seed records of the split, all in one store, so every query has "
        f"{len(seeds) - 1} distractors. **Queries:** the {n} session prompts whose answer is a seeded record. "
        "A hit = that record at rank 1.",
        "",
        "> **Small sample.** "
        f"{n} queries over {len(seeds)} records can show a retriever missing a query type; it can't rank close "
        "retrievers. A one-query difference is noise. Absence tasks and tasks whose record comes from the "
        "agent's own save are reported separately or left out (see the end).",
        "",
        "## Rank-1 hit rate",
        "",
        "| Retriever | Rank-1 hits |",
        "|---|---|",
        *[f"| {m} | {pct(total[m], n)} |" for m in MODES],
        "",
        "## Per task kind",
        "",
        "| Task kind | Queries | " + " | ".join(MODES) + " |",
        "|---|---|" + "---|" * len(MODES),
    ]
    by_kind: dict[str, list] = defaultdict(list)
    for r in rows:
        by_kind[r[1]].append(r)
    for kind, rs in sorted(by_kind.items()):
        L.append(f"| {kind} | {len(rs)} | " + " | ".join(pct(sum(r[4][m] for r in rs), len(rs)) for m in MODES) + " |")
    L += [
        "",
        "## Per query",
        "",
        "✓ = the right record at rank 1; otherwise the record that came first. Ranks in brackets: (keyword, vector) "
        "rank of the right record in the hybrid list's candidates.",
        "",
        "| Task | Kind | Record kind | Query | " + " | ".join(MODES) + " |",
        "|---|---|---|---|" + "---|" * len(MODES),
    ]
    for task_id, kind, rkind, query, ok, _fresh, hits in rows:
        cells = []
        for m in MODES:
            if ok[m]:
                h = hits[m][0]
                cells.append(f"✓ ({h.keyword_rank or '–'}, {h.vector_rank or '–'})" if m == "hybrid" else "✓")
            else:
                cells.append("✗ " + (short(hits[m][0].text) if hits[m] else "(nothing)"))
        L.append(f"| `{task_id}` | {kind} | {rkind} | {query} | " + " | ".join(cells) + " |")
    fresh_rows = [r for r in rows if r[5] is not None]
    if fresh_rows:
        L += ["", "**Latest wins** (the newer of two records at rank 1; retrieval alone has no notion of time):", ""]
        for task_id, *_rest, fresh, _hits in [(r[0], r[5], r[6]) for r in fresh_rows]:
            L.append(
                f"- `{task_id}`: " + ", ".join(f"{m} {'newest first' if fresh[m] else 'older first'}" for m in MODES)
            )
        L.append(
            "- So ranking can't be trusted to put the current value first. `memory_search` returns the top 5 with "
            "each record's date, so both records reach the agent, and picking the newer one is the agent's job "
            "(that is what the update tasks grade)."
        )
    L += [
        "",
        "## Absence prompts (nothing correct is stored)",
        "",
        "What each retriever puts first. Vector KNN always returns its nearest records, so the agent, not the "
        "retriever, must decide that nothing matches.",
        "",
        "| Task | Query | " + " | ".join(MODES) + " |",
        "|---|---|" + "---|" * len(MODES),
    ]
    for task_id, query, tops in absence:
        L.append(f"| `{task_id}` | {query} | " + " | ".join(short(tops[m]) for m in MODES) + " |")
    med = statistics.median(lat) * 1000
    p90 = lat[min(len(lat) - 1, int(0.9 * len(lat)))] * 1000
    L += [
        "",
        f"## Embedding latency (`{EMBED_MODEL}`, local)",
        "",
        f"- **Per text:** median {med:.0f} ms, p90 {p90:.0f} ms, over {len(lat)} calls (seed records stored one "
        "call each, plus every vector and hybrid query).",
        f"- **First call** (loads the model into memory): {load_s:.1f} s, not counted above.",
        "",
        "## Verdict",
        "",
        verdict(total, n),
        "",
        "## Left out",
        "",
        "- Tasks whose record would be the agent's own `memory_save` text (`mem-recall-stated-fact`, "
        "`mem-delete-stated`, `mem-pref-stated`, `mem-policy-*`): that wording doesn't exist until the agent "
        "writes it.",
        "- `mem-delete-seeded` session 2 and the update tasks' stale record: those test the store and the agent, not "
        "ranking.",
    ]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(rows)} queries).")


def verdict(total: dict[str, int], n: int) -> str:
    h, k, v = total["hybrid"], total["keyword"], total["vector"]
    if h > k and h > v:
        return f"Hybrid beats both singles here ({h}/{n} vs keyword {k}/{n}, vector {v}/{n}): keep RRF."
    if h >= max(k, v):
        best = "keyword" if k >= v else "vector"
        return (
            f"**Hybrid does not beat both singles.** It ties the best one ({h}/{n}; keyword {k}/{n}, vector {v}/{n}): "
            f"on this sample it is no better than {best} alone, and there is no measured gain from fusion.\n\n"
            "**Recommendation: keep hybrid RRF, for reasons this sample doesn't measure, and re-measure.** "
            "(1) The keyword index is needed anyway: it is memory's only search while Ollama is down. "
            "(2) Exact tokens (codes like K7-4419) are where keyword search is expected to win once the store holds "
            "thousands of similar records; with 8 records, vector search finds them too. Re-run this script when real "
            "memories exist or the split grows; if vector alone still matches hybrid there, drop the keyword side "
            "from ranking and keep it only as the fallback."
        )
    return (
        f"Hybrid does NOT beat both singles: hybrid {h}/{n}, keyword {k}/{n}, vector {v}/{n}. Recommendation: "
        "look at the misses above before keeping RRF; a weighted fusion or a single retriever may be better."
    )


def short(text: str, n: int = 48) -> str:
    text = text.replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


if __name__ == "__main__":
    main()
