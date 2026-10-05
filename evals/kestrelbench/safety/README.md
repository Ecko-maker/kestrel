# KestrelBench safety split

Attacks hidden in data, plus benign look-alikes. The task format, the checks and how to run it are in
[docs/kestrelbench.md, "Safety split"](../../../docs/kestrelbench.md#safety-split).

- `tasks/*.yaml`: the tasks (none yet: the owner writes them).
- `workspace/`: private data every task starts from; plant canaries (`KCAN-` + 12 hex digits) here.
- `pages/`: long fixture pages, referenced from a task as `{file: pages/...}`.

Never name a folder `data` or a file `.env*` here: `.gitignore` would hide it (a test checks).
Any change here bumps `SAFETY_VERSION` in `src/kestrel/bench/safety.py` and gets a line in
`evals/CHANGELOG.md`. Neutral placeholder tasks that exercise the harness live in `tests/fixtures/safety/`.
