"""The guards in tests/conftest.py: no test writes the real audit log, and a test that touches logs/ fails.

On 2026-11-12 a router test (test_router_taint.py, an ApprovalGate built without a log_path) appended an
approved "search" call to the owner's logs/approvals.jsonl. These prove that can't happen again silently."""

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from kestrel import approval
from kestrel.approval import ApprovalGate

ROOT = Path(__file__).resolve().parents[1]
REAL_LOGS = ROOT / "logs"
PROBE = REAL_LOGS / "no-such-file-kestrel-guard-probe"  # never created: opened read-only, so the open fails


@pytest.fixture
def guard(request):
    """tests/conftest.py, as pytest loaded it: a plugin (tests/ is not a package, and another conftest.py exists)."""
    conftest = Path(__file__).with_name("conftest.py").resolve()
    plugins = request.config.pluginmanager.get_plugins()
    return next(m for m in plugins if Path(getattr(m, "__file__", None) or ".").resolve() == conftest)


def test_a_gate_without_a_log_path_writes_to_a_scratch_file_in_tests(guard):
    gate = ApprovalGate()
    assert gate.log_path == approval.AUDIT_LOG
    assert not guard.under_real_logs(gate.log_path)


def test_the_path_check_knows_the_real_logs_folder_however_it_is_written(guard, tmp_path, monkeypatch):
    under = guard.under_real_logs
    monkeypatch.chdir(ROOT)
    assert under(Path("logs") / "approvals.jsonl") and under(f"logs{os.sep}traces.db") and under(b"logs/x")
    assert under("logs")
    assert under(str(REAL_LOGS / "sub" / "x.log"))
    assert under("file:logs/traces.db?mode=ro") and under(f"file:{REAL_LOGS.as_posix()}/traces.db")
    assert not under(":memory:") and not under(f"file:{tmp_path.as_posix()}/x.db?mode=ro")
    assert not under(tmp_path / "logs" / "approvals.jsonl") and not under(ROOT / "logs-old") and not under(3)
    monkeypatch.chdir(tmp_path)
    assert not under(Path("logs") / "approvals.jsonl")  # relative to the current folder, as open() resolves it


def test_opening_anything_under_logs_is_caught_even_a_read_or_a_database(guard):
    """The hook sees real opens. Each probe fails (the file doesn't exist), so nothing is created; the
    entries are cleared at the end, or this test would fail by its own guard."""
    g = guard
    assert not PROBE.exists()
    with pytest.raises(FileNotFoundError):
        open(PROBE, encoding="utf-8")  # noqa: SIM115
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(f"file:{PROBE.as_posix()}?mode=ro", uri=True)
    caught = list(g.touched)
    g.touched.clear()
    assert not PROBE.exists()
    assert any(entry.startswith("open ") and PROBE.name in entry for entry in caught)
    assert any(entry.startswith("sqlite3.connect ") for entry in caught)


def test_a_path_relative_to_another_open_folder_is_not_the_real_logs(guard, monkeypatch):
    """Linux's shutil.rmtree removes a temporary folder's own logs/ subfolder by name, relative to the
    parent it holds open (CI, 2026-11-13: the bench's task folder). Those events aren't the repo's logs/."""
    monkeypatch.chdir(ROOT)
    hook, g = guard.audit_hook, guard
    flags = os.O_RDONLY | guard.O_DIRECTORY
    events = [("os.rmdir", ("logs", 7)), ("os.remove", ("logs/x", 7)), ("os.mkdir", ("logs", 0o777, 7))]
    events += [("os.rename", ("logs/a", "logs/b", 7, 7))]
    if guard.O_DIRECTORY:
        events.append(("open", ("logs", None, flags)))
    for event, args in events:
        hook(event, args)
    hook("os.rmdir", ("logs", -1))  # the same name with no dir_fd (CPython passes -1) *is* the real folder
    caught = list(g.touched)
    g.touched.clear()
    assert caught == ["os.rmdir logs"]


def test_removing_a_scratch_folder_that_has_its_own_logs_is_allowed(tmp_path, monkeypatch, guard):
    monkeypatch.chdir(ROOT)
    scratch = tmp_path / "task"
    (scratch / "logs").mkdir(parents=True)
    (scratch / "logs" / "approvals.jsonl").write_text("{}\n", encoding="utf-8")
    shutil.rmtree(scratch)
    assert not scratch.exists() and guard.touched == []
