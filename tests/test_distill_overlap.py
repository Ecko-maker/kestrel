"""The distill/KestrelBench overlap check: both measures, the thresholds and their guard, fixtures, the
pinned safety-split commit, the report and the CLI. No model calls; git runs only in throwaway repos
made in tmp_path (and read-only on this repo).
"""

import subprocess
from pathlib import Path

import pytest

from kestrel.distill import overlap as o


def P(task_id: str, text: str) -> o.Prompt:
    return o.Prompt(task_id, text)


# --- Measures -----------------------------------------------------------------


def test_normalize_trigrams_jaccard() -> None:
    assert o.normalize("  What's 17.5%  of 2,340? ") == "whats 175 of 2340"
    assert o.trigrams("one two") == frozenset()
    assert o.trigrams("a b c d") == {("a", "b", "c"), ("b", "c", "d")}
    assert o.jaccard(o.trigrams("a b c d"), o.trigrams("a b c e")) == pytest.approx(1 / 3)


def test_longest_run() -> None:
    assert o.longest_run(["a", "b", "c", "d", "e"], ["x", "b", "c", "d", "y"]) == 3
    assert o.longest_run([], ["a"]) == 0
    assert o.longest_run(["a", "b"], ["b", "a"]) == 1


def test_exact_matches_fail_even_when_short() -> None:
    (pair,) = o.compare([P("dst-x", "Hello there!")], [P("b", "hello   THERE")])
    assert pair.exact and pair.fails


def test_each_measure_can_fail_on_its_own() -> None:
    bench = P("b", "Please create a note called Groceries listing milk and eggs today")
    by_jaccard = o.pair(P("d1", "Please create a note called Groceries listing milk and bread today"), bench)
    by_run = o.pair(
        P("d2", "Totally unrelated words then create a note called Groceries and more unrelated words here"), bench
    )
    assert by_jaccard.fails and by_jaccard.jaccard >= o.FAIL_JACCARD
    assert by_run.fails and by_run.run >= o.FAIL_RUN and by_run.jaccard < o.FAIL_JACCARD


def test_review_band() -> None:
    bench = P("b", "What time is it in Tokyo right now please")
    review = o.pair(P("d", "Tell me what time is it here"), bench)  # run 4, Jaccard 0.2
    far = o.pair(P("d", "How many sides has a hexagon got?"), bench)
    assert review.review and not review.fails
    assert not far.review and not far.fails


def test_prompt_index() -> None:
    index = o.PromptIndex(["What time is it in Tokyo right now?"])
    assert index.exact("what time is it in TOKYO right now")
    assert index.near("What time is it in Paris right now?") and not index.exact("What time is it in Paris right now?")
    assert not index.near("Add milk to my groceries note.")


# --- The thresholds stay above the bench's own distribution ---------------------


def test_thresholds_sit_above_the_bench_distribution() -> None:
    """The guard: same exclusions as the report (no same-task pairs, no identical-text twins)."""
    bench = o.load_bench()  # disk, plus the pinned safety commit when this clone has it
    d = o.distribution(bench.prompts, exclude_related=True)
    assert d["jaccard"]["p99.9"] < o.FAIL_JACCARD and d["run"]["p99.9"] < o.FAIL_RUN
    assert d["jaccard"]["p99"] < o.REVIEW_JACCARD and d["run"]["p99"] < o.REVIEW_RUN
    assert o.FAIL_JACCARD > o.REVIEW_JACCARD and o.FAIL_RUN > o.REVIEW_RUN


def test_distribution_exclusions() -> None:
    prompts = [
        P("a", "one two three four"),
        P("a", "one two three five"),
        P("b", "one two three four"),
        P("c", "x y z"),
    ]
    every = o.distribution(prompts, exclude_related=True)
    assert every["pairs"] == 4  # all 6 minus same-task (a, a) and the twins (a "four", b "four")
    assert o.distribution(prompts, exclude_related=False)["pairs"] == 6


# --- The real task set --------------------------------------------------------


def test_the_real_task_set_passes() -> None:
    bench = o.load_bench()
    assert not o.failures(o.compare(o.distill_prompts(), bench.prompts))
    assert not o.fixture_failures(o.DISTILL_DIR, bench)


# --- Fixtures -----------------------------------------------------------------


def _distill(tmp: Path, workspace: dict[str, str]) -> Path:
    (tmp / "d" / "tasks").mkdir(parents=True)
    (tmp / "d" / "tasks" / "t.yaml").write_text(
        "category: files\ntasks:\n  - id: dst-1\n    prompt: Count the plots in the garden\n    rubric: r\n", "utf-8"
    )
    for name, text in workspace.items():
        (tmp / "d" / "workspace" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "d" / "workspace" / name).write_text(text, "utf-8")
    return tmp / "d"


def _bench(tmp: Path, workspace: dict[str, str]) -> o.Bench:
    (tmp / "b" / "tasks").mkdir(parents=True)
    (tmp / "b" / "tasks" / "t.yaml").write_text("tasks:\n  - id: b1\n    prompt: Read the menu file please\n", "utf-8")
    for name, text in workspace.items():
        (tmp / "b" / "workspace" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "b" / "workspace" / name).write_text(text, "utf-8")
    return o.load_bench(tmp / "b", None)


def test_fixture_name_clash(tmp_path: Path) -> None:
    distill = _distill(tmp_path, {"sub/Notes.TXT": "hello"})
    bench = _bench(tmp_path, {"notes.txt": "other"})
    assert any("file name" in x for x in o.fixture_failures(distill, bench))


def test_fixture_line_clash_needs_eight_words(tmp_path: Path) -> None:
    line = "The quarterly budget review moves to the second floor"  # 9 words
    distill = _distill(tmp_path, {"a.md": f"# A\n{line.upper()}!\nshort line here\n"})
    bench = _bench(tmp_path, {"b.md": f"{line}\nshort line here\n"})
    problems = o.fixture_failures(distill, bench)
    assert len(problems) == 1 and "quarterly budget" in problems[0]


def test_lines_in_bench_task_files_count(tmp_path: Path) -> None:
    line = "please wire the deposit to the account listed below today"
    distill = _distill(tmp_path, {"a.md": line})
    (tmp_path / "b" / "tasks").mkdir(parents=True)
    (tmp_path / "b" / "tasks" / "t.yaml").write_text(
        f"tasks:\n  - id: b1\n    prompt: hi\n    files: {{'x.txt': '{line}'}}\n", "utf-8"
    )
    assert o.fixture_failures(distill, o.load_bench(tmp_path / "b", None))


# --- The pinned safety-split commit -------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway repo with a safety task file committed and tagged."""
    r = tmp_path / "repo"
    (r / "evals" / "kestrelbench" / "safety" / "tasks").mkdir(parents=True)
    (r / "evals" / "kestrelbench" / "safety" / "tasks" / "s.yaml").write_text(
        "- id: atk-x\n  prompt: Summarize the shared garden report for me\n", "utf-8"
    )
    _git(r, "init", "-q")
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "safety")
    _git(r, "tag", "safety-before")
    return r, _git(r, "rev-parse", "HEAD")


def _sources(tmp: Path, commit: str) -> Path:
    path = tmp / "sources.toml"
    path.write_text(
        f'[safety_split]\ncommit = "{commit}"\ntag = "safety-before"\npath = "evals/kestrelbench/safety"\n', "utf-8"
    )
    return path


def test_pinned_commit_is_read(tmp_path: Path, repo: tuple[Path, str]) -> None:
    r, commit = repo
    bench = o.load_bench(tmp_path / "empty", _sources(tmp_path, commit), repo=r)
    assert [p.task_id for p in bench.prompts] == ["atk-x"]
    assert bench.prompts[0].source == f"git:{commit[:12]}"
    assert bench.sources[1] | {"files": 1} == bench.sources[1] and bench.sources[1]["available"] is True
    assert bench.sources[1]["commit"] == commit


def test_a_moved_tag_fails_loudly(tmp_path: Path, repo: tuple[Path, str]) -> None:
    r, commit = repo
    (r / "x.txt").write_text("x", "utf-8")
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "later")
    _git(r, "tag", "-f", "safety-before")  # the tag now points somewhere else
    with pytest.raises(o.SourceError, match="pins"):
        o.load_bench(tmp_path / "empty", _sources(tmp_path, commit), repo=r)


def test_a_missing_commit_means_disk_only(tmp_path: Path, repo: tuple[Path, str]) -> None:
    r, _ = repo
    _git(r, "tag", "-d", "safety-before")
    bench = o.load_bench(tmp_path / "empty", _sources(tmp_path, "0" * 40), repo=r)
    assert bench.prompts == []
    assert bench.sources[1]["available"] is False and "disk only" in bench.sources[1]["note"]


def test_the_shipped_pin_matches_the_local_tag() -> None:
    """If this clone has the tag, it must resolve to the pinned commit (load_bench raises otherwise)."""
    cfg = o.load_sources()["safety_split"]
    assert len(cfg["commit"]) == 40
    o.load_bench()


# --- Report and CLI -----------------------------------------------------------


def test_report_has_both_tables_and_every_review_pair() -> None:
    bench = o.load_bench()
    pairs = o.compare(o.distill_prompts(), bench.prompts)
    text = o.report(bench, pairs, [], o.DISTILL_DIR)
    assert "| all |" in text and "| excluding twins and same-task |" in text
    review = [p for p in pairs if p.review]
    assert review and all(f"`{p.distill.task_id}`" in text and f"`{p.bench.task_id}`" in text for p in review)
    assert text.count("\n| 0.") + text.count("\n| 1.") >= len(review)
    assert "## Ordinary chat" in text


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    distill = _distill(tmp_path, {"a.md": "nothing shared"})
    _bench(tmp_path, {"b.md": "other"})
    argv = ["--distill-dir", str(distill), "--bench-dir", str(tmp_path / "b"), "--no-sources"]
    assert o.main(argv) == 0
    (distill / "tasks" / "t.yaml").write_text(
        "category: files\ntasks:\n  - id: dst-1\n    prompt: read the MENU file, please!\n    rubric: r\n", "utf-8"
    )
    assert o.main([*argv, "--report", str(tmp_path / "r.md")]) == 1
    assert "OVERLAP CHECK FAILED" in capsys.readouterr().out
    assert "FAIL" in (tmp_path / "r.md").read_text(encoding="utf-8")
