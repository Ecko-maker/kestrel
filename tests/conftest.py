"""Shared test setup."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import pytest

from kestrel import approval
from kestrel.bench import calibrate

ROOT = Path(__file__).resolve().parents[1]
REAL_LOGS = ROOT / "logs"  # approvals.jsonl, traces.db, the Telegram lock: the owner's, never a test's


@pytest.fixture(autouse=True)
def _no_real_labels(tmp_path_factory, monkeypatch):
    """Human labels are created only by a person running `kestrel bench label`. No test may read or
    write the real evals/labels/human.jsonl (or the judge cache), whatever it forgets to patch."""
    scratch = tmp_path_factory.mktemp("labels")
    monkeypatch.setattr(calibrate, "LABELS", scratch / "human.jsonl")
    monkeypatch.setattr(calibrate, "VERDICT_CACHE", scratch / "judge-cache.jsonl")


@pytest.fixture(autouse=True)
def _no_real_audit_log(tmp_path_factory, monkeypatch):
    """An ApprovalGate built without a log_path writes to approval.AUDIT_LOG (read when the gate is
    built): in tests that is a scratch file, never the real logs/approvals.jsonl."""
    monkeypatch.setattr(approval, "AUDIT_LOG", tmp_path_factory.mktemp("audit") / "approvals.jsonl")


# --- the guard: no test may touch logs/ ------------------------------------------------------------

# Python's audit events for what reaches a file: open() (any mode), SQLite, and changes to the folder.
WATCHED = {"open", "sqlite3.connect", "os.mkdir", "os.remove", "os.rename", "os.replace", "os.rmdir"}
touched: list[str] = []  # filled by the hook while a test runs; checked after each test
_watching = False


def under_real_logs(target: object) -> bool:
    if isinstance(target, bytes):
        target = os.fsdecode(target)
    if not isinstance(target, str | os.PathLike):
        return False  # a file descriptor, or ":memory:"-style None
    target = os.fspath(target)
    if target.startswith("file:"):  # an SQLite URI: file:logs/traces.db?mode=ro, file:///C:/...
        target = url2pathname(urlsplit(target).path)
    path = os.path.abspath(target)  # relative paths against the current folder, as open() does
    return os.path.normcase(path).startswith(os.path.normcase(str(REAL_LOGS)) + os.sep) or (
        os.path.normcase(path) == os.path.normcase(str(REAL_LOGS))
    )


def audit_hook(event: str, args: tuple) -> None:
    if _watching and event in WATCHED and args:
        for target in args[:2] if event in ("os.rename", "os.replace") else args[:1]:
            if under_real_logs(target):
                touched.append(f"{event} {os.fspath(target)!s}")


sys.addaudithook(audit_hook)  # in-process only: a Kestrel running elsewhere can't fail a test


@pytest.fixture(autouse=True)
def _no_test_touches_real_logs():
    """Fails the test that opened, created or removed anything under the repo's logs/ (read or write)."""
    global _watching
    touched.clear()
    _watching = True
    try:
        yield
    finally:
        _watching = False
    if touched:
        found = sorted(set(touched))
        touched.clear()
        pytest.fail("this test touched the real logs/ folder: " + "; ".join(found), pytrace=False)
