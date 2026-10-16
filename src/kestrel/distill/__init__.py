"""Phase 4 distillation: turn the teacher's good runs into training data for a small student model.

The distill task set (distill/tasks, distill/workspace) is run with `kestrel bench run --split distill`.
It lives outside evals/kestrelbench on purpose: everything under that folder is KestrelBench, and
`kestrel distill build` treats its prompts as contamination.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DISTILL_DIR = ROOT / "distill"  # tasks/ and workspace/
DISTILL_DB = Path("logs") / "distill.db"  # distill-run traces, kept apart from bench.db and your chats
# Bump on any change to a distill task or its workspace. Recorded in results meta as split_version.
DISTILL_VERSION = "d1.1"
