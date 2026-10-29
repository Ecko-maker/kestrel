import sys
from pathlib import Path

# evals/analysis holds scripts, not a package: make compare_before_after importable from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
