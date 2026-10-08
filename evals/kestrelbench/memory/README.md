# KestrelBench memory split

Tasks that measure long-term memory, written before memory exists, so "Kestrel today (no memory)" is a recorded baseline. Design, task kinds and open questions: [docs/memory-evals-design.md](../../../docs/memory-evals-design.md) (proposal, unapproved).

- `tasks/*.yaml`: memory tasks (none yet: they wait for the owner's approval of the sessions mechanism).
- `workspace/`: shared fixture files, if any task needs them.
- Version: `MEMORY_VERSION` in `src/kestrel/bench/memory.py`, logged in `evals/CHANGELOG.md`.
- Check the files: `uv run kestrel bench validate --split memory` (schema only, no model, no network).
- Not runnable yet: `bench run` has no memory split until the runner can start a fresh agent per session.

Format, in short (the loader's docstring has the full list):

```yaml
tasks:
  - id: mem-recall-example
    kind: recall
    seed:
      - {kind: fact, text: "Locker code at the climbing gym: K7-4419", at: 2026-09-12}
    sessions:
      - prompt: "What's my locker code at the gym?"
        example: "It's K7-4419."
        checks:
          - {type: answer_matches, pattern: "K7-?4419"}
```
