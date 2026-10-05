"""One process per results file.

Two benchmark processes on the same results file spend the model quota twice, race on the file and
can leave a run half from one process and half from the other (it happened on 2026-10-05). So
`kestrel bench run` and `rejudge` take a lock next to every results file they read or write:
`<file>.lock`, created atomically, holding the PID, host, command and start time. A second process
refuses to start.

A lock left by a process that crashed is "stale". `kestrel bench unlock <file>` removes it, but only
after checking that the PID in it is really gone on this machine; it never removes a live lock.
"""

import json
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class LockError(RuntimeError):
    """Another process holds the lock, or a stale lock is in the way. The message says what to do."""


def lock_path(results: Path) -> Path:
    return results.with_name(results.name + ".lock")


def pid_alive(pid: int) -> bool:
    """Whether a process with this PID exists on this machine. Errs towards True when unsure."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists; anything else: no such process
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # signal 0: only checks (never used on Windows, where 0 means Ctrl+C)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_lock(results: Path) -> dict | None:
    try:
        return json.loads(lock_path(results).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError, OSError:
        return {"pid": -1, "host": "?", "command": "unreadable lock file", "started": "?"}


def describe(info: dict) -> str:
    return f"PID {info.get('pid')} on {info.get('host')} since {info.get('started')} ({info.get('command')})"


def _acquire(results: Path) -> Path:
    path = lock_path(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "command": " ".join(sys.argv[1:])[:300],
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # atomic: fails if the lock exists
    except FileExistsError:
        held = read_lock(results) or {}
        same_host = held.get("host") == socket.gethostname()
        if same_host and not pid_alive(int(held.get("pid", -1))):
            raise LockError(
                f"{results.name} has a stale lock from {describe(held)}: that process is no longer running. "
                f"Check nothing else is using the file, then clear it with: uv run kestrel bench unlock {results}"
            ) from None
        raise LockError(
            f"{results.name} is in use by {describe(held)}. Wait for it to finish (or stop it); "
            "running a second process on the same results file would spend the quota twice and race on the file."
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return path


@contextmanager
def results_lock(*files: Path | None) -> Iterator[None]:
    """Hold a lock on each results file (None entries and duplicates are ignored); release them all
    on exit, also on errors and Ctrl+C. If any lock can't be taken, the ones already taken are released."""
    held: list[Path] = []
    try:
        for results in dict.fromkeys(f.resolve() for f in files if f is not None):
            held.append(_acquire(results))
        yield
    finally:
        for path in held:
            path.unlink(missing_ok=True)


def unlock(results: Path) -> str:
    """Remove a stale lock. Refuses while the process that took it is still running here."""
    held = read_lock(results)
    if held is None:
        return f"{results.name} is not locked."
    if held.get("host") != socket.gethostname():
        raise LockError(f"The lock was taken on another machine ({describe(held)}); this one can't check that PID.")
    if pid_alive(int(held.get("pid", -1))):
        raise LockError(f"Not removed: {describe(held)} is still running. Stop it first.")
    lock_path(results).unlink(missing_ok=True)
    return f"Removed the stale lock on {results.name} ({describe(held)}, no longer running)."
