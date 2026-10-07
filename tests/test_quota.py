"""Wait-for-quota mode: daily limits are waited out, not recorded; per-minute limits behave as before.
Fake LLMs and fake clocks only: no network, no keys, no real sleeping."""

import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest

from kestrel.bench import cli as bench_cli
from kestrel.bench import quota as quota_module
from kestrel.bench.quota import DEFAULT_WAIT, MARGIN, QuotaWait, daily_limit_wait
from kestrel.bench.runner import run_suite
from kestrel.bench.tasks import Task
from kestrel.llm import DAILY_LIMIT, LLM, LLMError

REQUEST = httpx.Request("POST", "http://test/chat/completions")


def groq_429(limit: str, headers: dict | None = None, try_again: str = "1h2m3.5s") -> openai.RateLimitError:
    """Shape of a Groq free-tier 429: which limit it hit is only in the message."""
    body = {
        "error": {
            "message": f"Rate limit reached for model `openai/gpt-oss-120b` ... on {limit}: Limit 200000, "
            f"Used 199800, Requested 2400. Please try again in {try_again}.",
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
    response = httpx.Response(429, request=REQUEST, headers=headers or {})
    return openai.RateLimitError("HTTP 429", response=response, body=body)


@pytest.fixture(autouse=True)
def dummy_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")  # the client is replaced: nothing is sent


def fake_llm(outcomes) -> tuple[LLM, list[float]]:
    llm = LLM("groq")
    pending = list(outcomes)

    def create(**kwargs):
        outcome = pending.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    waits: list[float] = []
    llm.sleep = waits.append
    return llm, waits


# --- the LLM layer labels daily limits --------------------------------------------------------


@pytest.mark.parametrize("limit", ["tokens per day (TPD)", "requests per day (RPD)"])
def test_a_daily_limit_is_labelled_and_carries_the_servers_wait(limit):
    llm, waits = fake_llm([groq_429(limit, {"retry-after": "3600"})])
    with pytest.raises(LLMError) as e:
        llm.chat([])
    assert str(e.value) == f"groq: {DAILY_LIMIT}, server asks to wait 3600s" and waits == []


def test_without_a_header_the_wait_comes_from_try_again_in():
    llm, _ = fake_llm([groq_429("tokens per day (TPD)")])  # 1h2m3.5s, no Retry-After
    with pytest.raises(LLMError, match=r"daily limit \(429\), server asks to wait 3724s"):
        llm.chat([])


def test_per_minute_limits_keep_todays_label_and_behaviour():
    tpm = groq_429("tokens per minute (TPM)", {"retry-after": "6"}, try_again="5.2s")
    llm, waits = fake_llm([tpm] * 20)
    with pytest.raises(LLMError, match=r"groq: rate limited \(429\), gave up after 8 tries"):
        llm.chat([])
    assert waits == [6.0] * 7  # unchanged: test_llm.py pins the same numbers


# --- reading the label --------------------------------------------------------------------------


def test_daily_limit_wait_reads_the_servers_hint_or_falls_back_to_15_minutes():
    assert daily_limit_wait(f"Sorry, ... groq: {DAILY_LIMIT}, server asks to wait 1200s") == 1200 + MARGIN
    assert daily_limit_wait(f"groq: {DAILY_LIMIT}, gave up after 8 tries") == DEFAULT_WAIT == 900
    assert daily_limit_wait("groq: rate limited (429), server asks to wait 1200s") is None  # per-minute
    assert daily_limit_wait(None) is None and daily_limit_wait("judge error: timeout") is None


# --- the runner -----------------------------------------------------------------------------------


class DailyLimited:
    """A fake LLM: raises a daily-limit error on the listed calls (1-based), answers otherwise."""

    def __init__(self, limited: set[int], message: str = f"{DAILY_LIMIT}, server asks to wait 1200s"):
        self.limited, self.message, self.calls = limited, message, 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls in self.limited:
            raise LLMError("groq", self.message)
        return {"role": "assistant", "content": "done"}


def clock():
    start = datetime(2026, 10, 7, 9, 0, tzinfo=timezone(timedelta(hours=-4)))
    return lambda: start


@pytest.fixture
def base(tmp_path):
    ws = tmp_path / "base"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello\n", encoding="utf-8")
    return ws


def tasks(n):
    return [Task(id=f"t{i}", category="c", prompts=("go",), checks=()) for i in range(n)]


def test_daily_limits_are_waited_out_and_each_run_is_recorded_once(base):
    lines, slept, checkpoints = [], [], []
    quota = QuotaWait(
        total=4,
        log=lines.append,
        sleep=slept.append,
        now=clock(),
        checkpoint=lambda done: checkpoints.append(len(done)),
    )
    llm = DailyLimited({2, 3, 4, 5, 6})  # t0 fine; then 5 daily-limit errors in a row; then fine
    results = run_suite(tasks(4), llm, base_workspace=base, stop_after_errors=3, quota=quota, repeats=[1, 1, 2, 2])
    assert [(r.id, r.repeat, r.status) for r in results] == [
        ("t0", 1, "pass"),
        ("t1", 1, "pass"),
        ("t2", 2, "pass"),
        ("t3", 2, "pass"),
    ]  # no error recorded, no stop after 3 errors in a row, no task twice
    assert slept == [1230.0] * 5 and quota.waited == 6150 and checkpoints == [1] * 5
    assert lines[0] == (
        "[quota] daily limit at t1 #1: 1 done, 3 remaining; waiting 20 min, next attempt 2026-10-07 09:20 UTC-04:00"
    )


def test_per_minute_errors_still_count_toward_the_stop_with_quota_on(base):
    quota = QuotaWait(total=5, log=lambda line: None, sleep=lambda s: None)
    llm = DailyLimited(set(range(1, 99)), message="rate limited (429), gave up after 8 tries")
    results = run_suite(tasks(5), llm, base_workspace=base, stop_after_errors=3, quota=quota)
    assert [r.status for r in results] == ["error", "error", "error", "skipped", "skipped"] and quota.waited == 0


def test_waiting_stops_at_the_cap_and_the_error_is_recorded(base):
    lines: list[str] = []
    quota = QuotaWait(total=1, log=lines.append, sleep=lambda s: None, max_total_wait=3000)
    results = run_suite(tasks(1), DailyLimited(set(range(1, 99))), base_workspace=base, quota=quota)
    assert results[0].status == "error" and DAILY_LIMIT in (results[0].error or "")
    assert quota.waited == 2460 and lines[-1].startswith("[quota] gave up waiting after 0.7 h")


def test_without_the_flag_a_daily_limit_is_an_error_as_before(base):
    results = run_suite(tasks(4), DailyLimited({2, 3, 4, 5}), base_workspace=base, stop_after_errors=3)
    assert [r.status for r in results] == ["pass", "error", "error", "error"]


# --- the CLI: checkpoint while waiting, resume keeps finished runs --------------------------------


def run_kestrel(monkeypatch, *argv):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def test_cli_wait_for_quota_checkpoints_while_waiting_and_resume_keeps_finished_runs(tmp_path, monkeypatch, capsys):
    import kestrel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setenv("KESTREL_MCP", "off")
    llm = DailyLimited({2})  # the second run hits the daily limit once
    llm.llms = [SimpleNamespace(model="openai/gpt-oss-120b")]
    monkeypatch.setattr(bench_cli, "build_llm", lambda providers, model=None: (llm, []))
    out = tmp_path / "r.json"
    seen_while_waiting = []

    def fake_sleep(seconds):  # the process "sleeps": the results file must already be resumable
        data = json.loads(out.read_text(encoding="utf-8"))
        seen_while_waiting.append([(t["id"], t["repeat"], t["status"]) for t in data["tasks"]])

    monkeypatch.setattr(quota_module.time, "sleep", fake_sleep)
    ids = "time-weekday-newyork,arith-percent"
    argv = ["run", "--tasks", ids, "--repeat", "2", "--judge", "none", "--wait-for-quota", "--out", str(out)]
    assert run_kestrel(monkeypatch, *argv) == 0
    printed = capsys.readouterr().out
    assert "[quota] daily limit at " in printed and "1 done, 3 remaining; waiting 20 min" in printed

    [checkpoint] = seen_while_waiting
    assert [s for *_, s in checkpoint].count("skipped") == 3 and checkpoint[0][2] in ("pass", "fail")
    final = json.loads(out.read_text(encoding="utf-8"))["tasks"]
    keys = [(t["id"], t["repeat"]) for t in final]
    assert len(keys) == len(set(keys)) == 4 and all(t["status"] in ("pass", "fail") for t in final)

    # resuming the mid-wait checkpoint runs only the 3 unfinished runs, never the finished one again
    out.with_name("cp.json").write_text(
        json.dumps(
            {
                **json.loads(out.read_text(encoding="utf-8")),
                "tasks": [
                    {**t, "status": "skipped", "error": bench_cli.WAITING} if i else t for i, t in enumerate(final)
                ],
            }
        ),
        encoding="utf-8",
    )
    before = llm.calls
    assert (
        run_kestrel(
            monkeypatch,
            "run",
            "--tasks",
            ids,
            "--repeat",
            "2",
            "--judge",
            "none",
            "--resume",
            str(out.with_name("cp.json")),
        )
        == 0
    )
    resumed = json.loads(out.with_name("cp.json").read_text(encoding="utf-8"))["tasks"]
    assert resumed[0] == final[0] and llm.calls - before >= 3
    assert len({(t["id"], t["repeat"]) for t in resumed}) == 4
