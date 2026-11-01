# Option c teacher selection (known issue #72), pinned

**Evidence only (design decision 29):** #72 ended in option a (Groq + gpt-oss-120b), so this selection's
winner is not the teacher.

The evidence for #72's pre-registered option c selection, copied byte for byte from the Kaggle compare
run's output (`kestrel-compare-5ec57a709a43.zip`, unpacked in `..\kestrel-runs\evals\results\teacher-selection-5ec57a709a43\compare\`).
These files must never change: `tests/test_kaggle_collect.py` checks their SHA-256 values. If it fails, restore the file
from git instead of updating the hash.

**Run:** 2026-10-31, 20:17-20:29 UTC, Kaggle `collect.ipynb` version 2 in compare mode, GPU T4 x2, Ollama 0.40.13,
snapshot commit `5ec57a7`, `--judge none`, no Groq. Both candidates ran #72's 20 tasks once each.

| File | SHA-256 | From |
|---|---|---|
| `selection.md` | `4e67a92713f1e590df4defef058ed59eea2e90094a03b974f6fe616aeb6c2e3f` | `compare/selection.md` |
| `selection.json` | `5f34824d6d35a1587c55f54c9684d5354e5c84531c7918bc8e911f09ed0c67cf` | `compare/selection.json` |
| `manifest-gpt-oss-20b.json` | `1f3707a150ec928a932c42e001ccc1e4b9269dea5883f50fcf0710415b3191d3` | `compare/gpt-oss-20b/manifest.json` |
| `manifest-kestrel-teacher-qwen3-30b-a3b.json` | `01da2c433e6869d27af2790d278cfcbf0df87516bb6a34c56b0bd9949756b135` | `compare/kestrel-teacher-qwen3-30b-a3b/manifest.json` |

**Not in git:** each candidate's `results.json`, `results.md` and `logs/distill.db` (traces of the 20 distill tasks).
They stay in the runs folder with the other results files (docs/STATUS.md, "Data outside git").

**Result:** Qwen3-30B-A3B-Instruct-2507 (Q4, served as `kestrel-teacher-qwen3-30b-a3b`). The table and how
#72's rule was applied are in `docs/known-issues.md` #72. It is the teacher only if option c is chosen on
2026-11-04.
