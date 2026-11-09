"""A run whose process vanishes keeps what finished (found by the routed main suite, 2026-11-04: its window went
away after 35 minutes, no Python code ran, and nothing had been saved). Offline: a scripted model in a child
process that dies with os._exit, as a closed console window kills it: no except, no finally, no atexit."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import kestrel
from kestrel.bench import cli as bench_cli
from kestrel.bench import runner

TASKS = "time-weekday-newyork,arith-percent"  # run in catalog order: arith-percent, then time-weekday-newyork
FIRST, SECOND = "arith-percent", "time-weekday-newyork"
CHILD = """
import os, sys
from types import SimpleNamespace
import kestrel
from kestrel.bench import cli as bench_cli

die_at = int(sys.argv[1])

class Dies:
    llms = [SimpleNamespace(model="openai/gpt-oss-120b")]
    calls = 0
    def chat(self, messages, tools=None):
        Dies.calls += 1
        if Dies.calls == die_at:
            os._exit(137)
        return {"role": "assistant", "content": "Done."}

bench_cli.build_llm = lambda providers, model=None: (Dies(), [])
kestrel.load_dotenv = lambda: None
sys.argv = ["kestrel", "bench", *sys.argv[2:]]
kestrel.main()
"""


class Answers:
    def __init__(self):
        self.llms = [SimpleNamespace(model="openai/gpt-oss-120b")]

    def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "Done."}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setenv("KESTREL_MCP", "off")
    monkeypatch.setattr(bench_cli, "build_llm", lambda providers, model=None: (Answers(), []))
    return tmp_path


def run_in_process(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def die_in_child(folder, call, out):
    args = ["run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--out", str(out)]
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(call), *args], cwd=folder, capture_output=True, text=True, check=False
    )
    assert done.returncode == 137, done.stderr[-2000:]


def statuses(out):
    return {t["id"]: t["status"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]}


def meta(out):
    return json.loads(out.read_text(encoding="utf-8"))["meta"]


def test_a_process_killed_before_any_run_finished_leaves_a_file_recording_the_attempt(folder, monkeypatch):
    out = folder / "r.json"
    die_in_child(folder, 1, out)
    assert set(statuses(out).values()) == {"skipped"}
    [attempt] = meta(out)["attempts"]
    assert attempt["started"] and attempt["pid"] and "git" in attempt
    assert run_in_process(monkeypatch, "unlock", str(out)) == 0  # the kill left a stale lock
    argv = ["run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--resume", str(out)]
    assert run_in_process(monkeypatch, *argv) == 0
    assert "skipped" not in statuses(out).values()
    assert len(meta(out)["attempts"]) == 2  # both attempts on record


def test_a_process_killed_after_a_run_finished_keeps_it_and_resume_runs_only_the_rest(folder, monkeypatch):
    out = folder / "r.json"
    die_in_child(folder, 2, out)
    saved = statuses(out)
    assert saved[SECOND] == "skipped" and saved[FIRST] in ("pass", "fail")
    assert not list(folder.glob("*.tmp"))
    real, ran = runner.run_task, []

    def counted(task, *args, **kwargs):
        ran.append(task.id)
        return real(task, *args, **kwargs)

    monkeypatch.setattr(runner, "run_task", counted)
    assert run_in_process(monkeypatch, "unlock", str(out)) == 0
    argv = ["run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--resume", str(out)]
    assert run_in_process(monkeypatch, *argv) == 0
    assert ran == [SECOND] and statuses(out)[FIRST] == saved[FIRST]


def test_the_same_command_again_refuses_to_overwrite_what_a_killed_run_saved(folder, monkeypatch, capsys):
    """Exactly what happened next on 11-04: the same line, run again by hand, over a file that now holds results."""
    out = folder / "r.json"
    die_in_child(folder, 2, out)
    assert run_in_process(monkeypatch, "unlock", str(out)) == 0
    before = out.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        ["kestrel", "bench", "run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--out", str(out)],
    )
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    assert "--resume" in str(e.value.code) and out.read_bytes() == before


def test_a_run_without_wait_for_quota_is_unchanged(folder, monkeypatch):
    """No file before the end, no attempts in its meta: CI runs and every pinned file's shape stay as they were."""
    out = folder / "r.json"
    seen = []
    real = runner.run_task

    def looks(task, *args, **kwargs):
        seen.append(out.exists())
        return real(task, *args, **kwargs)

    monkeypatch.setattr(runner, "run_task", looks)
    assert run_in_process(monkeypatch, "run", "--tasks", TASKS, "--judge", "none", "--out", str(out)) == 0
    assert seen == [False, False] and "attempts" not in meta(out)


def test_a_save_that_fails_mid_run_warns_and_the_run_goes_on(folder, monkeypatch, capsys):
    """A virus scanner holding the file for a moment must not stop a measurement; the next save writes it all."""
    real, calls = bench_cli.write_results, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:  # 1: the start of the run; 2: after the first finished run
            raise PermissionError("the file is held by another program")
        return real(*args, **kwargs)

    monkeypatch.setattr(bench_cli, "write_results", flaky)
    out = folder / "r.json"
    argv = ["run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--out", str(out)]
    assert run_in_process(monkeypatch, *argv) == 0
    assert "could not save" in capsys.readouterr().out
    assert "skipped" not in statuses(out).values()


def test_a_results_file_that_cant_be_written_at_the_start_stops_before_any_tokens(folder, monkeypatch):
    def no_disk(*args, **kwargs):
        raise PermissionError("no")

    monkeypatch.setattr(bench_cli, "write_results", no_disk)
    ran = []
    monkeypatch.setattr(runner, "run_task", lambda *a, **k: ran.append(1))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kestrel",
            "bench",
            "run",
            "--tasks",
            TASKS,
            "--judge",
            "none",
            "--wait-for-quota",
            "--out",
            str(folder / "r.json"),
        ],
    )
    with pytest.raises(PermissionError):
        kestrel.main()
    assert not ran


@pytest.mark.parametrize("stop", [RuntimeError("a bug in the bench"), KeyboardInterrupt()], ids=["crash", "ctrl-c"])
def test_a_stopped_run_says_so_on_a_line_of_its_own_and_keeps_what_finished(folder, monkeypatch, capsys, stop):
    """RUN STOPPED holds no outcome, so a filter that hides every outcome can always let it through."""
    real = runner.run_task

    def stop_on_second(task, *args, **kwargs):
        if task.id == SECOND:
            raise stop
        return real(task, *args, **kwargs)

    monkeypatch.setattr(runner, "run_task", stop_on_second)
    out = folder / "r.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["kestrel", "bench", "run", "--tasks", TASKS, "--judge", "none", "--wait-for-quota", "--out", str(out)],
    )
    with pytest.raises(type(stop)):
        kestrel.main()
    [line] = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("RUN STOPPED")]
    assert type(stop).__name__ in line and "--resume" in line
    assert statuses(out)[FIRST] in ("pass", "fail") and statuses(out)[SECOND] == "skipped"
    assert not (folder / "r.json.lock").exists()  # a stop Python sees still releases the lock
