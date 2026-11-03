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


def test_judge_tokens_count_by_the_runs_trace_time(tmp_path: Path) -> None:
    """11-03: the judge's calls aren't traced, so the traces alone gave 45,034 for a run whose summary said
    49,953. Judge tokens in a results file count when the run's trace started in the window."""
    now = datetime.now(UTC)
    starts = {"t-new": (now - timedelta(hours=1)).timestamp(), "t-old": (now - timedelta(hours=30)).timestamp()}
    rows = [
        {"id": "a", "judge_tokens": 300, "trace_ids": ["t-new"]},
        {"id": "b", "judge_tokens": 200, "trace_ids": ["t-old"]},  # outside the window
        {"id": "c", "judge_tokens": 0, "trace_ids": ["t-new"]},  # not judged
        {"id": "d", "judge_tokens": 100, "trace_ids": []},  # no trace: can't be placed
    ]
    groq = tmp_path / "run.json"
    groq.write_text(json.dumps({"meta": {"judge": "groq/openai/gpt-oss-120b"}, "tasks": rows}), encoding="utf-8")
    assert quota.judge_billable(groq, starts, now - timedelta(hours=24)) == (1, 300)
    local = tmp_path / "local.json"  # a judge that isn't on Groq costs no Groq quota
    local.write_text(json.dumps({"meta": {"judge": "ollama/x"}, "tasks": rows}), encoding="utf-8")
    assert quota.judge_billable(local, starts, now - timedelta(hours=24)) == (0, 0)
    none = tmp_path / "none.json"
    none.write_text(json.dumps({"meta": {"judge": None}, "tasks": rows}), encoding="utf-8")
    assert quota.judge_billable(none, starts, now - timedelta(hours=24)) == (0, 0)


GRADED_LOG = (
    "2026-11-03T00:58:12.1Z Pass rate 88% (95% CI 69%-100%, n=16 tasks); 14/16 graded runs passed; "
    "0 errored, 0 excluded, 0 skipped; mean score 0.891; 39,572 tokens (31,892 billable)"
)
NOT_RUN_LOG = "2026-11-03T18:50:02.0Z 3 changed file(s). KestrelBench not run: no agent-affecting changes"


def job(conclusion: str | None, status: str = "completed") -> dict:
    return {"databaseId": 1, "name": "KestrelBench (CI subset on Groq)", "status": status, "conclusion": conclusion}


def test_a_long_not_run_job_used_nothing() -> None:
    """11-03: two "not run" jobs took 32-33 s; the old duration rule made the quota check "Not final" for 24 h."""
    assert quota.classify_job("completed", job("success"), NOT_RUN_LOG) == ("not run", 0, "KestrelBench not run")
    assert quota.classify_job("completed", job("skipped"), None)[0] == "not run"  # no step ran, no log


def test_a_graded_job_is_counted() -> None:
    assert quota.classify_job("completed", job("success"), GRADED_LOG) == ("counted", 31_892, "graded")
    assert quota.classify_job("completed", job("failure"), GRADED_LOG)[:2] == ("counted", 31_892)  # gate failed


def test_a_running_job_is_unknown() -> None:
    assert quota.classify_job("in_progress", job(None, "in_progress"), None) == ("unknown", 0, "still running")
    assert quota.classify_job("in_progress", job("success"), NOT_RUN_LOG)[0] == "unknown"  # the run isn't done


def test_a_cancelled_job_is_unknown_unless_its_log_says_not_run() -> None:
    assert quota.classify_job("completed", job("cancelled"), None)[0] == "unknown"  # no log left
    assert quota.classify_job("completed", job("cancelled"), "Run uv sync\nKestrelBench: 7 tasks graded...")[0] == (
        "unknown"
    )
    assert quota.classify_job("completed", job("cancelled"), NOT_RUN_LOG)[0] == "not run"


def test_the_margin_arithmetic() -> None:
    assert quota.free(135_442) == 49_558  # 200,000 - 135,442 - 15,000 (11-03 13:00 UTC)
    assert quota.free(0) == 185_000
