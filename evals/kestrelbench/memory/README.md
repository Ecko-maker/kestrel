# KestrelBench memory split

Tasks that measure long-term memory, written before memory exists, so "Kestrel today (no memory)" is a recorded baseline. Design and owner decisions: [docs/memory-evals-design.md](../../../docs/memory-evals-design.md). Scripted proof: [evals/reports/memory-scripted.md](../../reports/memory-scripted.md).

- `tasks/memory.yaml`: the 15 tasks (4 recall, 3 absence, 2 delete, 2 update, 2 write policy, 2 preference).
- `workspace/`: shared fixture files, if any task needs them.
- Version: `MEMORY_VERSION` in `src/kestrel/bench/memory.py`, logged in `evals/CHANGELOG.md`.
- Check the files: `uv run kestrel bench validate --split memory` (schema, then the four scripted models and their requirements; no model, no network).
- Run: `uv run kestrel bench run --split memory --repeat 3 [--scripted perfect|recall-all|forget-all|refuse-all] [--dry-run]`.

Format, in short (the loader's docstring has the full list):

```yaml
tasks:
  - id: mem-recall-example
    kind: recall
    proves: A fact stored in an earlier session is used in a new conversation.
    seed:
      - {kind: fact, text: "Locker code at the climbing gym: K7-4419", at: 2026-09-12}
    sessions:
      - prompt: "What's my locker code at the gym?"
        example: "It's K7-4419."
        checks:
          - {type: answer_matches, pattern: "K7-?4419"}
```
