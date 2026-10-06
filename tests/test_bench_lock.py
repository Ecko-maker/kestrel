"""One process per results file: the lock taken by `kestrel bench run` and `rejudge`."""

import json
import os
import socket
import subprocess
import sys

import pytest

from kestrel.bench import lock


def dead_pid() -> int:
    """The PID of a process that has already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def write_lock(results, pid, host=None):
    lock.lock_path(results).write_text(
        json.dumps({"pid": pid, "host": host or socket.gethostname(), "command": "bench run", "started": "t"}),
        encoding="utf-8",
    )


def test_pid_alive():
    assert lock.pid_alive(os.getpid())
    assert not lock.pid_alive(dead_pid())
    assert not lock.pid_alive(-1)


def test_second_process_is_refused_and_lock_is_released(tmp_path):
    results = tmp_path / "r.json"
    with lock.results_lock(results, None, results):  # duplicates and None are ignored
        held = json.loads(lock.lock_path(results.resolve()).read_text(encoding="utf-8"))
        assert held["pid"] == os.getpid()
        with pytest.raises(lock.LockError, match="is in use by PID"), lock.results_lock(results):
            pass
    assert not lock.lock_path(results).exists()  # released on exit


def test_lock_is_released_after_a_crash_and_partial_locks_are_rolled_back(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    with pytest.raises(RuntimeError), lock.results_lock(a):
        raise RuntimeError("the run crashed")
    assert not lock.lock_path(a).exists()
    write_lock(b, os.getpid())  # b is busy
    with pytest.raises(lock.LockError), lock.results_lock(a, b):
        pass
    assert not lock.lock_path(a).exists()  # a's lock was given back when b failed


def test_stale_lock_needs_unlock_which_checks_the_pid(tmp_path):
    results = tmp_path / "r.json"
    write_lock(results, dead_pid())
    with pytest.raises(lock.LockError, match=r"stale lock.*kestrel bench unlock"), lock.results_lock(results):
        pass
    assert "Removed the stale lock" in lock.unlock(results)
    with lock.results_lock(results):  # free again
        pass
    assert lock.unlock(results) == "r.json is not locked."


def test_unlock_never_removes_a_live_or_foreign_lock(tmp_path):
    results = tmp_path / "r.json"
    write_lock(results, os.getpid())
    with pytest.raises(lock.LockError, match="still running"):
        lock.unlock(results)
    write_lock(results, dead_pid(), host="another-machine")
    with pytest.raises(lock.LockError, match="another machine"):
        lock.unlock(results)
    assert lock.lock_path(results).exists()


@pytest.fixture
def offline(tmp_path, monkeypatch):
    import kestrel
    from kestrel.demo import DemoLLM

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setattr(DemoLLM.__init__, "__defaults__", (0.0,))
    return tmp_path


def kestrel_bench(monkeypatch, *argv):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", *argv])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


def test_cli_run_and_rejudge_refuse_a_locked_results_file(offline, monkeypatch):
    out = offline / "r.json"
    run = ["run", "--provider", "demo", "--judge", "none", "--task", "arith-percent", "--out", str(out)]
    assert kestrel_bench(monkeypatch, *run) == 0
    assert not lock.lock_path(out).exists()  # the finished run gave its lock back

    write_lock(out.resolve(), os.getpid())  # as if another run were still going
    assert "is in use by" in str(kestrel_bench(monkeypatch, *run))
    assert "is in use by" in str(kestrel_bench(monkeypatch, "rejudge", str(out)))

    write_lock(out.resolve(), dead_pid())  # its process crashed
    assert "kestrel bench unlock" in str(kestrel_bench(monkeypatch, *run))
    assert kestrel_bench(monkeypatch, "unlock", str(out)) == 0
    assert kestrel_bench(monkeypatch, *run) == 0
