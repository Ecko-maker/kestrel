"""scripts/groq_quota.py: the parts that need no `gh` and no network."""

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("groq_quota", ROOT / "scripts" / "groq_quota.py")
assert spec is not None and spec.loader is not None
quota = importlib.util.module_from_spec(spec)
sys.modules["groq_quota"] = quota
spec.loader.exec_module(quota)


def test_billable_from_a_kestrelbench_job_log() -> None:
    graded = (
        "2026-11-03T00:58:12.1Z Pass rate 88% (95% CI 69%-100%, n=16 tasks); 14/16 graded runs passed; "
        "0 errored, 0 excluded, 0 skipped; mean score 0.891; 39,572 tokens (31,892 billable)"
    )
    assert quota.billable_from_log(graded) == 31_892
    assert quota.billable_from_log("KestrelBench not run: no agent-affecting changes") is None


def test_local_billable_counts_recent_groq_calls_only(tmp_path: Path) -> None:
    db = tmp_path / "bench.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE spans (name TEXT, start_time REAL, attributes TEXT)")
    now = datetime.now(UTC)

    def span(provider: str, age_h: float, tokens_in: int, cached: int, tokens_out: int) -> None:
        attrs = {
            "gen_ai.provider.name": provider,
            "gen_ai.usage.input_tokens": tokens_in,
            "kestrel.usage.cached_input_tokens": cached,
            "gen_ai.usage.output_tokens": tokens_out,
        }
        con.execute(
            "INSERT INTO spans VALUES ('llm_call', ?, ?)",
            ((now - timedelta(hours=age_h)).timestamp(), json.dumps(attrs)),
        )

    span("groq", 1, 1000, 300, 100)  # 800 billable
    span("groq", 23, 500, 0, 50)  # 550
    span("groq", 25, 9999, 0, 9999)  # outside the 24 h window
    span("ollama", 1, 9999, 0, 9999)  # local, free
    con.commit()
    con.close()
    assert quota.local_billable(db, now - timedelta(hours=24)) == (2, 1350)


def test_the_margin_arithmetic() -> None:
    assert quota.free(135_442) == 49_558  # 200,000 - 135,442 - 15,000 (11-03 13:00 UTC)
    assert quota.free(0) == 185_000
