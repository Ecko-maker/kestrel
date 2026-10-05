"""KestrelBench statistics and judge calibration: confidence intervals, paired comparisons, repeats,
the dev/held-out split, labelling, and re-grading stored answers. Offline, fixed seeds."""

import hashlib
import json
import re
import sys
from collections import Counter

import pytest

from kestrel.bench import calibrate as cal
from kestrel.bench import cli as bench_cli
from kestrel.bench import stats
from kestrel.bench.judge import JUDGE_PROMPT, JUDGE_VERSION, Judge, build_request
from kestrel.bench.report import markdown, summarize
from kestrel.bench.runner import run_suite, run_task
from kestrel.bench.tasks import SUITE_VERSION, Task, load_tasks, task_sha
from kestrel.llm import LLMError
from kestrel.tracing import Tracer


def say(text):
    return {"role": "assistant", "content": text}


def call(name, **args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0) if self.replies else say("done")


def task(**kw):
    return Task(**{"id": "t", "category": "c", "prompts": ("go",), "checks": (), **kw})


def row(task_id, status, category="c", repeat=1, **kw):
    """One stored run, as in a results file."""
    return {
        "id": task_id,
        "category": category,
        "status": status,
        "score": 1.0 if status == "pass" else 0.0,
        "repeat": repeat,
        "checks": [],
        "answer": "ok",
        "tokens": 0,
        "latency_ms": 0.0,
        **kw,
    }


def results_file(rows, judge="groq/m", version="v1"):
    return {"meta": {"provider": "groq", "model": "m", "judge": judge, "judge_version": version}, "tasks": rows}


@pytest.fixture
def base(tmp_path):
    ws = tmp_path / "base"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello\n", encoding="utf-8")
    return ws


# --- confidence intervals -----------------------------------------------------------------


def test_interval_shrinks_as_tasks_grow():
    small = stats.bootstrap_ci([1.0] * 7 + [0.0] * 3)
    medium = stats.bootstrap_ci([1.0] * 70 + [0.0] * 30)
    large = stats.bootstrap_ci([1.0] * 700 + [0.0] * 300, resamples=2_000)
    assert small and medium and large
    assert small.value == medium.value == large.value == 0.7
    widths = [e.high - e.low for e in (small, medium, large)]
    assert widths[0] > widths[1] > widths[2] > 0
    assert all(e.low <= 0.7 <= e.high for e in (small, medium, large))


def test_bootstrap_is_reproducible_and_formatted():
    values = [1.0] * 71 + [0.0] * 29
    a = stats.bootstrap_ci(values)
    assert a == stats.bootstrap_ci(values)  # fixed seed: same data, same interval
    assert a is not None and re.fullmatch(r"71% \(95% CI \d\d–\d\d%, n=100\)", a.fmt())
    assert stats.bootstrap_ci([]) is None


def test_small_categories_are_flagged():
    rows = [row(f"big{i}", "pass", "big") for i in range(12)]
    rows += [row(f"small{i}", "pass" if i else "fail", "small") for i in range(4)]
    cis = stats.category_cis(rows)
    assert cis["small"]["too_few"] and not cis["big"]["too_few"]
    md = markdown(results_file(rows))
    small_line = md.split("| small")[1].splitlines()[0]
    big_line = md.split("| big")[1].splitlines()[0]
    assert "too few tasks to compare" in small_line and "too few" not in big_line
    assert "†" in big_line and "rule of three" in md  # all 12 passed: the zero-width interval is marked


def test_repeats_count_tasks_not_runs_and_find_flaky_tasks():
    rows = [
        row("a", "pass", repeat=1),
        row("a", "fail", repeat=2),  # flaky
        row("b", "pass", repeat=1),
        row("b", "pass", repeat=2),
        row("c", "error", repeat=1),  # not graded
        row("c", "fail", repeat=2),
    ]
    s = summarize(rows)
    assert s["pass_rate_ci"]["n"] == 3 and s["runs"] == 6  # tasks are resampled, not runs
    assert s["pass_rate"] == round((0.5 + 1 + 0) / 3, 3)
    assert s["repeats"]["flaky"] == ["a"] and s["repeats"]["pass_rates"] == {1: 1.0, 2: round(1 / 3, 4)}
    assert "1 tasks changed outcome between repeats: a" in markdown(results_file(rows))


# --- comparing two runs -------------------------------------------------------------------


def test_identical_runs_differ_by_zero(tmp_path, monkeypatch, capsys):
    rows = [row(f"t{i}", "pass" if i % 3 else "fail", task_sha="s") for i in range(30)]
    c = stats.compare(results_file(rows), results_file([dict(r) for r in rows]))
    assert c.diff == stats.Estimate(0.0, 0.0, 0.0, 30)
    assert c.better_in_b == c.worse_in_b == c.only_in_one == c.warnings == []
    assert c.within(0.05) is True

    path = tmp_path / "r.json"
    path.write_text(json.dumps(results_file(rows)), encoding="utf-8")
    args = type("Args", (), {"a": path, "b": path, "margin": 0.05})()
    assert bench_cli.cmd_compare(args) == 0
    out = capsys.readouterr().out
    assert "+0 points (95% CI +0 to +0, n=30)" in out and "0 better in B, 0 worse in B" in out


def test_paired_comparison_counts_flips_and_leaves_out_mismatches():
    a = [row(f"t{i}", "pass", task_sha="s") for i in range(40)]
    b = [dict(r) for r in a]
    b[0]["status"] = b[1]["status"] = "fail"  # worse in B
    b[2]["task_sha"] = "changed"  # the task was edited between runs: not comparable
    b.append(row("extra", "pass", task_sha="s"))  # only in B
    a[3]["status"] = "error"  # not graded in A
    c = stats.compare(results_file(a), results_file(b, version="v2"))
    assert c.worse_in_b == ["t0", "t1"] and c.better_in_b == []
    assert c.changed_tasks == ["t2"] and set(c.only_in_one) == {"extra", "t3"}
    assert c.diff is not None and c.diff.n == 38 and c.diff.value == round(-2 / 38, 4) and c.diff.high <= 0
    assert any("different judges" in w for w in c.warnings)


def test_non_inferiority_needs_the_whole_interval_inside_the_margin():
    a = [row(f"t{i}", "pass", task_sha="s") for i in range(200)]
    slightly_worse = [dict(r, status="fail" if i < 2 else "pass") for i, r in enumerate(a)]
    much_worse = [dict(r, status="fail" if i < 30 else "pass") for i, r in enumerate(a)]
    kw = {"resamples": 2_000}
    assert stats.compare(results_file(a), results_file(slightly_worse), **kw).within(0.05) is True
    assert stats.compare(results_file(a), results_file(much_worse), **kw).within(0.05) is False


# --- sampling and the dev/held-out split --------------------------------------------------


def test_stratified_sample_is_proportional_and_stable():
    tasks = load_tasks()

    def pick(**kw):
        return stats.stratified_sample(tasks, 20, lambda t: t.category, lambda t: t.id, **kw)

    sample = pick()
    counts = Counter(t.category for t in sample)
    assert len(sample) == 20 and len(counts) == 10  # every category represented
    assert counts["safety"] == 3 and counts["time"] == 1  # 16/100 and 6/100 of 20
    assert sample == pick() and sample != pick(seed=1)


def test_dev_heldout_split_is_stable_and_stratified():
    tasks = load_tasks()
    rubric = [t for t in tasks if t.rubric]
    split = cal.splits(tasks)
    assert set(split) == {t.id for t in rubric}
    for category in {t.category for t in rubric}:
        sides = Counter(split[t.id] for t in rubric if t.category == category)
        assert abs(sides["dev"] - sides["heldout"]) <= 1
    extra = Task(id="zz-new", category="files", prompts=("x",), checks=(), rubric="r")
    moved = cal.splits([*tasks, extra])
    assert all(moved[t.id] == split[t.id] for t in rubric if t.category != "files")  # other categories unaffected


# --- versions: judge prompt, task definitions, tool log -----------------------------------


def test_judge_changes_need_a_new_version():
    """What the judge sees: its prompt, the request layout, and how much of each tool result."""
    from kestrel.bench.runner import MAX_RESULT_CHARS_IN_LOG

    request = build_request(["q"], "rubric", ["- tool() [ran] -> x"], "answer")
    fingerprint = hashlib.sha256((json.dumps(request) + str(MAX_RESULT_CHARS_IN_LOG)).encode()).hexdigest()[:12]
    assert JUDGE_PROMPT in request[0]["content"]
    assert (JUDGE_VERSION, fingerprint) == ("v2", "53dfa9418d6f"), (
        "What the judge sees changed: bump JUDGE_VERSION in judge.py, log it in evals/CHANGELOG.md, then "
        f"update this test. New fingerprint: {fingerprint}"
    )


def test_results_record_judge_version_tool_log_and_task_version(base):
    t = task(rubric="Mentions Canberra.")
    judge = Judge(Scripted(say('{"score": 1, "reason": "ok"}')), name="j")
    r = run_task(t, Scripted(call("calculator", expression="6*7"), say("Canberra")), judge=judge, base_workspace=base)
    assert r.judge is not None and r.judge["version"] == JUDGE_VERSION
    assert len(r.tool_log) == 1 and r.tool_log[0].startswith("- calculator(") and "[ran] -> 42" in r.tool_log[0]
    assert r.task_sha == task_sha(t, base)
    assert task_sha(task(rubric="Mentions Sydney."), base) != r.task_sha
    from kestrel.bench import tasks as tasks_module

    (base / "notes.txt").write_text("changed\n", encoding="utf-8")
    tasks_module._workspace_sha.cache_clear()
    assert task_sha(t, base) != r.task_sha  # the fixture workspace is part of the task's version


def test_tool_log_is_rebuilt_from_traces_for_older_results(base, tmp_path):
    db = tmp_path / "bench.db"
    llm = Scripted(call("calculator", expression="6*7"), call("write_file", path="x.txt", content="hi"), say("done"))
    r = run_task(task(), llm, tracer=Tracer(db), base_workspace=base)
    assert any("not run (rejected)" in line for line in r.tool_log)  # the scripted user said no
    older = {**r.to_dict(), "tool_log": []}  # results written before tool logs were stored
    rebuilt, source = cal.tool_log_for(older, db)
    assert source == "rebuilt from traces" and rebuilt == r.tool_log
    assert cal.tool_log_for({**older, "trace_ids": []}, db)[1] == "calls only"


def test_suite_stops_after_errors_in_a_row(base):
    class Down:
        def chat(self, messages, tools=None):
            raise LLMError("groq", "rate limited (429)")

    tasks = [task(id=f"t{i}") for i in range(5)]
    results = run_suite(tasks, Down(), base_workspace=base, stop_after_errors=2, repeats=[1, 1, 2, 2, 2])
    assert [r.status for r in results] == ["error", "error", "skipped", "skipped", "skipped"]
    assert "errors in a row" in (results[2].error or "") and results[4].repeat == 2


# --- labels and calibration ---------------------------------------------------------------


def test_labels_latest_wins_and_skips_are_ignored():
    common = {"task_id": "a", "answer_sha": "x", "split": "dev", "category": "c"}
    labels = [
        {**common, "label": "pass"},
        {**common, "label": "fail"},  # relabelled: the later one counts
        {**common, "task_id": "b", "label": "skip"},
        {**common, "task_id": "c", "split": "heldout", "label": "pass"},
    ]
    assert [(lab["task_id"], lab["label"]) for lab in cal.labelled(labels, "dev")] == [("a", "fail")]
    assert [lab["task_id"] for lab in cal.labelled(labels, "heldout")] == ["c"]


def test_calibrate_against_stored_and_regraded_verdicts(tmp_path):
    t = Task(id="files-x", category="files", prompts=("Summarize",), checks=(), rubric="Faithful summary.")
    stored = {"score": 1.0, "reason": "fine", "judge": "groq/m", "version": "v1"}
    rows = [row("files-x", "pass", "files", answer="A summary.", judge=stored, tool_log=["- read_file({}) [ran] -> x"])]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(results_file(rows)), encoding="utf-8")
    label = cal.make_label(rows[0], path, "f", "misses the point", "dev")
    assert label["label"] == "fail" and label["note"] == "misses the point" and label["labeled_at"]
    assert label["results_file"] == str(path) and label["answer_sha"] == cal.answer_sha("A summary.")

    pairs, problems, judges = cal.stored_pairs([label])
    assert problems == [] and judges == {"groq/m@v1"}
    assert pairs[0].human is False and pairs[0].judge is True  # the stored judge was too lenient

    sent = []

    class Candidate(Scripted):
        def chat(self, messages, tools=None):
            sent.append(messages)
            return say('{"score": 0, "reason": "misses it"}')

    judge = Judge(Candidate(), name="groq/candidate")
    cache = tmp_path / "cache.jsonl"
    first, _, calls1 = cal.regraded_pairs([label], [t], judge, None, cache)
    again, _, calls2 = cal.regraded_pairs([label], [t], judge, None, cache)
    assert (calls1, calls2, len(sent)) == (1, 0, 1)  # the second time comes from the cache
    assert first[0].judge is False and again == first
    request = sent[0][1]["content"]
    assert "## Final answer\nA summary." in request and "read_file" in request  # the STORED answer and steps


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    import kestrel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setenv("KESTREL_MCP", "off")
    return tmp_path


def run_kestrel(monkeypatch, *argv):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


class FakeStdin:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


def test_label_command_hides_the_verdict_and_saves_labels(cli_env, monkeypatch, capsys):
    tasks = {t.id: t for t in load_tasks()}
    ids = [i for i, t in tasks.items() if t.rubric][:3]
    hidden = {"score": 0.0, "reason": "SECRET-VERDICT", "judge": "j", "version": "v1"}
    rows = [
        row(
            i,
            "fail",
            tasks[i].category,
            answer=f"answer {n}",
            judge=hidden,
            checks=[{"type": "x", "ok": False, "detail": "SECRET-CHECK"}],
            tool_log=["- read_file({}) [ran] -> data"],
        )
        for n, i in enumerate(ids)
    ]
    path = cli_env / "r.json"
    path.write_text(json.dumps(results_file(rows)), encoding="utf-8")
    labels = cli_env / "human.jsonl"
    monkeypatch.setattr(cal, "LABELS", labels)
    monkeypatch.setattr(sys, "stdin", FakeStdin(tty=True))
    keys = iter(["x", "p", "f made it up", "q"])  # an invalid key is asked again
    monkeypatch.setattr("builtins.input", lambda _: next(keys))

    assert run_kestrel(monkeypatch, "label", str(path)) == 0
    out = capsys.readouterr().out
    assert "SECRET-VERDICT" not in out and "SECRET-CHECK" not in out and "judge 0" not in out
    assert "read_file" in out and "Expected:" in out
    saved = cal.load_labels(labels)
    assert [s["label"] for s in saved] == ["pass", "fail"] and saved[1]["note"] == "made it up"
    assert all(s["results_file"] and s["labeled_at"] and s["split"] in cal.SPLITS for s in saved)
    remaining = cal.label_queue(json.loads(path.read_text(encoding="utf-8")), load_tasks(), saved)
    assert len(remaining) == 1  # resume: labelled answers aren't asked again


def test_label_command_refuses_without_a_terminal(cli_env, monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin(tty=False))
    assert "person at the keyboard" in str(run_kestrel(monkeypatch, "label", "whatever.json"))


def test_rejudge_grades_stored_answers_into_a_new_file(cli_env, monkeypatch):
    from kestrel.bench import runner

    t = next(t for t in load_tasks() if t.rubric)
    old = {"score": 0.0, "reason": "missed rejected call", "judge": "groq/m", "version": "v1"}
    checks = [{"type": "x", "ok": True, "detail": "", "why": ""}]
    rows = [row(t.id, "fail", t.category, judge=old, checks=checks, tool_log=["- t() [ran] -> x"])]
    path = cli_env / "r.json"
    path.write_text(json.dumps(results_file(rows)), encoding="utf-8")
    before = path.read_bytes()
    new_judge = Judge(Scripted(say('{"score": 1, "reason": "it did ask"}')), name="groq/new")
    monkeypatch.setattr(bench_cli, "_judge", lambda provider, model: new_judge)
    monkeypatch.setattr(runner, "Agent", None)  # Kestrel itself must never run

    assert run_kestrel(monkeypatch, "rejudge", str(path)) == 0
    assert path.read_bytes() == before  # the original results are untouched
    data = json.loads((cli_env / f"r.rejudged-{JUDGE_VERSION}.json").read_text(encoding="utf-8"))
    r = data["tasks"][0]
    assert r["status"] == "pass" and r["judge"]["judge"] == "groq/new" and r["judge"]["version"] == JUDGE_VERSION
    assert data["meta"]["rejudged_from"] == "r.json" and data["meta"]["judge"] == "groq/new"
    assert "never overwrites" in str(run_kestrel(monkeypatch, "rejudge", str(path), "--out", str(path)))


# --- repeats, reuse and the dry run through the CLI ---------------------------------------


@pytest.fixture
def demo(cli_env, monkeypatch):
    from kestrel.demo import DemoLLM

    monkeypatch.setattr(DemoLLM.__init__, "__defaults__", (0.0,))
    return cli_env


def test_repeat_runs_each_task_n_times_and_reuse_counts_as_repeat_one(demo, monkeypatch):
    common = ["run", "--provider", "demo", "--judge", "none", "--tasks", "arith-percent,files-contact-*"]
    out = demo / "r.json"
    run_kestrel(monkeypatch, *common, "--repeat", "2", "--out", str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    runs = sorted((t["id"], t["repeat"]) for t in data["tasks"])
    assert runs == [("arith-percent", 1), ("arith-percent", 2), ("files-contact-email", 1), ("files-contact-email", 2)]
    assert data["meta"]["repeat"] == 2 and data["summary"]["pass_rate_ci"]["n"] == 2
    assert data["meta"]["suite_version"] == SUITE_VERSION  # every results file records its suite version
    assert f"KestrelBench v{SUITE_VERSION}." in markdown(data)
    assert all(t["task_sha"] for t in data["tasks"])

    out3 = demo / "r3.json"
    run_kestrel(monkeypatch, *common, "--repeat", "3", "--reuse", str(out), "--out", str(out3))
    again = json.loads(out3.read_text(encoding="utf-8"))
    assert len(again["tasks"]) == 6
    first = [t for t in again["tasks"] if t["repeat"] == 1]
    assert first == [t for t in data["tasks"] if t["repeat"] == 1]  # reused as they were, not re-run


def test_dry_run_estimates_without_calling_a_model(demo, monkeypatch, capsys):
    assert run_kestrel(monkeypatch, "run", "--sample", "20", "--repeat", "3", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "20 tasks x 3 = 60 runs" in out and "billable" in out and "Groq free tier" in out
    assert out.count("would run") == 60
    assert not (demo / "logs").exists()  # nothing ran


def test_compare_warns_across_suite_versions():
    rows = [row(f"t{i}", "pass", task_sha="s") for i in range(10)]
    old, new = results_file(rows), results_file([dict(r) for r in rows])
    old["meta"]["suite_version"], new["meta"]["suite_version"] = "1.0", "1.1"
    assert any("different suite versions (1.0 vs 1.1)" in w for w in stats.compare(old, new).warnings)
    assert "suite version not recorded" in markdown(results_file(rows))  # files from before versioning


def test_resume_refuses_to_mix_judge_versions(demo, monkeypatch):
    out = demo / "r.json"
    data = results_file([row("arith-percent", "pass", "arithmetic")], judge="groq/openai/gpt-oss-120b", version="v1")
    data["meta"].update(provider="demo", model="demo")
    out.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    args = ["run", "--provider", "demo", "--model", "demo", "--judge", "groq", "--resume", str(out)]
    message = str(run_kestrel(monkeypatch, *args, "--tasks", "arith-percent"))
    assert "mix two judges" in message and "rejudge" in message


def test_rejudge_dry_run_estimates_without_calling_the_judge(cli_env, monkeypatch, capsys):
    t = next(t for t in load_tasks() if t.rubric)
    path = cli_env / "r.json"
    path.write_text(json.dumps(results_file([row(t.id, "pass", t.category, tool_log=["- x() [ran] -> y"])])), "utf-8")
    monkeypatch.setattr(bench_cli, "_judge", None)  # would fail if called
    assert run_kestrel(monkeypatch, "rejudge", str(path), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Re-grade 1 stored answers with judge v2" in out and "1 stored" in out and "Expected ~" in out
