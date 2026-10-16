"""Check that no distill task prompt copies or paraphrases a KestrelBench prompt (no model calls).

    uv run python scripts/distill_overlap.py [--fail-at 0.5] [--review-at 0.3] [--bench-dir DIR]

Exit 1 on an exact match or a word 3-gram Jaccard at or above --fail-at. The logic and its tests
live in src/kestrel/distill/overlap.py.
"""

import sys

from kestrel.distill.overlap import main

if __name__ == "__main__":
    sys.exit(main())
