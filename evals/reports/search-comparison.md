# Memory search: keyword vs vector vs hybrid (RRF)

Measured 2026-10-10 by `scripts/search_comparison.py` on the memory split m1.0's own seeds and queries. Local only: `SqliteMemory` with Ollama `nomic-embed-text` (Windows AMD64), no cloud call. Hybrid = RRF (k = 60) of the top 20 of each retriever.

**Corpus:** the 8 unique seed records of the split, all in one store, so every query has 7 distractors. **Queries:** the 7 session prompts whose answer is a seeded record. A hit = that record at rank 1.

> **Small sample.** 7 queries over 8 records can show a retriever missing a query type; it can't rank close retrievers. A one-query difference is noise. Absence tasks and tasks whose record comes from the agent's own save are reported separately or left out (see the end).

## Rank-1 hit rate

| Retriever | Rank-1 hits |
|---|---|
| keyword | 6/7 (86%) |
| vector | 7/7 (100%) |
| hybrid | 7/7 (100%) |

## Per task kind

| Task kind | Queries | keyword | vector | hybrid |
|---|---|---|---|---|
| delete | 1 | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| preference | 1 | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| recall | 3 | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| update | 2 | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |

## Per query

✓ = the right record at rank 1; otherwise the record that came first. Ranks in brackets: (keyword, vector) rank of the right record in the hybrid list's candidates.

| Task | Kind | Record kind | Query | keyword | vector | hybrid |
|---|---|---|---|---|---|---|
| `mem-recall-seeded-fact` | recall | fact | What's my locker code at the climbing gym? | ✓ | ✓ | ✓ (1, 1) |
| `mem-recall-episode` | recall | episode | Last week we drafted an email to Priya about a venue. Which venue did we pick, and how much was the deposit? | ✓ | ✓ | ✓ (1, 1) |
| `mem-recall-document` | recall | document | Which crampons are on my Lofoten packing list? | ✓ | ✓ | ✓ (1, 1) |
| `mem-delete-seeded` | delete | fact | What's the studio door code? | ✓ | ✓ | ✓ (1, 1) |
| `mem-update-seeded` | update | fact | When is my dentist appointment? Just the current date and time. | ✓ | ✓ | ✓ (1, 2) |
| `mem-update-stated` | update | fact | Which office parking spot is mine? Just the spot. | ✓ | ✓ | ✓ (1, 1) |
| `mem-pref-seeded` | preference | preference | How long is a marathon? | ✗ (nothing) | ✓ | ✓ (–, 1) |

**Latest wins** (the newer of two records at rank 1; retrieval alone has no notion of time):

- `mem-update-seeded`: keyword older first, vector newest first, hybrid older first
- So ranking can't be trusted to put the current value first. `memory_search` returns the top 5 with each record's date, so both records reach the agent, and picking the newer one is the agent's job (that is what the update tasks grade).

## Absence prompts (nothing correct is stored)

What each retriever puts first. Vector KNN always returns its nearest records, so the agent, not the retriever, must decide that nothing matches.

| Task | Query | keyword | vector | hybrid |
|---|---|---|---|---|
| `mem-absence-pool-code` | What's my locker code at the swimming pool? Only the pool one, please; I keep mixing my codes up. | Locker code at the climbing gym: K7-4419 | Locker code at the climbing gym: K7-4419 | Locker code at the climbing gym: K7-4419 |
| `mem-absence-brother-birthday` | When is my brother Tobias's birthday? Just his, please. | (nothing) | Dentist appointment moved to 21 October 2026, 1… | Dentist appointment moved to 21 October 2026, 1… |
| `mem-absence-other-list` | What's on my packing list for the Iceland trip? Only that list, please. | Packing list for the Lofoten trip (March): head… | Packing list for the Lofoten trip (March): head… | Packing list for the Lofoten trip (March): head… |

## Embedding latency (`nomic-embed-text`, local)

- **Per text:** median 43 ms, p90 85 ms, over 28 calls (seed records stored one call each, plus every vector and hybrid query).
- **First call** (loads the model into memory): 2.2 s, not counted above.

## Verdict

**Hybrid does not beat both singles.** It ties the best one (7/7; keyword 6/7, vector 7/7): on this sample it is no better than vector alone, and there is no measured gain from fusion.

**Recommendation: keep hybrid RRF, for reasons this sample doesn't measure, and re-measure.** (1) The keyword index is needed anyway: it is memory's only search while Ollama is down. (2) Exact tokens (codes like K7-4419) are where keyword search is expected to win once the store holds thousands of similar records; with 8 records, vector search finds them too. Re-run this script when real memories exist or the split grows; if vector alone still matches hybrid there, drop the keyword side from ranking and keep it only as the fallback.

## Left out

- Tasks whose record would be the agent's own `memory_save` text (`mem-recall-stated-fact`, `mem-delete-stated`, `mem-pref-stated`, `mem-policy-*`): that wording doesn't exist until the agent writes it.
- `mem-delete-seeded` session 2 and the update tasks' stale record: those test the store and the agent, not ranking.
