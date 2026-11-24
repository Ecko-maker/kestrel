"""Did the calendar phone test leave a secret, the address or an event title in Kestrel's local files? Counts only.

    uv run --no-sync python scripts/live/notify_leak_check.py      # from the kestrel folder, after the test

No network, no model calls, no quota. It prints counts and fixed labels, never a value: the bot token, the Google
client id/secret, the address and the notifier's own Google tokens are read into memory only, to be searched for
(UTF-8 and UTF-16, as PowerShell's Tee-Object writes it). The test's events carry KCAL in their titles.

`mcp-google-notifier.log` is the calendar server's own log, which is known to hold the address and may hold sign-in
links and titles: there only the secrets and tokens count as a leak, the rest is printed for information.

Exit code 0 = clean, 1 = something found (or a wrong token folder, scope or git change), 2 = it couldn't check
everything (a value to search for or a file is missing or unreadable). Only the current Google tokens are known; an
access token from before a refresh is caught by its shape (`ya29.`).
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

SECRETS = ("TELEGRAM_BOT_TOKEN", "GOOGLE_OAUTH_CLIENT_SECRET")
NAMES = (*SECRETS, "GOOGLE_OAUTH_CLIENT_ID", "USER_GOOGLE_EMAIL")
TEST_LOG = "telegram-test.txt"
SERVER_LOG = "mcp-google-notifier.log"
FILES = (TEST_LOG, "notify.db", "traces.db", "approvals.jsonl", SERVER_LOG)
SHAPES = {"test titles": "KCAL", "sign-in links": "accounts.google.com/o/oauth2", "access-token shapes": "ya29."}
INFO_IN_SERVER_LOG = {"GOOGLE_OAUTH_CLIENT_ID", "USER_GOOGLE_EMAIL", "test titles", "sign-in links"}


def count(data: bytes, value: str) -> int:
    return sum(len(re.findall(re.escape(value.encode(enc)), data, re.IGNORECASE)) for enc in ("utf-8", "utf-16-le"))


def read(path: Path) -> bytes:
    """The file's bytes, with SQLite's journal when there is one."""
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def main(root: Path | None = None, env: Mapping[str, str] | None = None) -> int:
    root = root or Path.cwd()
    if env is None:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")  # into this process's memory only, as `kestrel` does
        env = os.environ
    from kestrel.notify.google_source import ALLOWED_SCOPES, STATES_FILE, default_credentials_dir

    problems = unchecked = 0
    needles = {name: env.get(name, "").strip() for name in NAMES}
    for name, value in needles.items():
        if not value:
            print(f"{name}: not set, can't be searched for")
            unchecked += 1
    needles["bot secret part"] = needles["TELEGRAM_BOT_TOKEN"].partition(":")[2]

    folder = Path(env.get("KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR", "").strip() or default_credentials_dir())
    tokens = [p for p in folder.glob("*.json") if p.name != STATES_FILE]
    scopes: set[str] = set()
    for i, path in enumerate(tokens, 1):
        try:
            data = json.loads(path.read_text("utf-8"))
            scopes |= set(data.get("scopes") or [])
            for key in ("token", "refresh_token"):
                if data.get(key):
                    needles[f"google {key} {i}"] = data[key]
                else:
                    print(f"token file {i}: no {key}")
                    unchecked += 1
        except OSError, ValueError, AttributeError, TypeError:
            print(f"token file {i}: unreadable")
            unchecked += 1
    extra = len(scopes - ALLOWED_SCOPES)
    print(f"token files in the notifier's folder: {len(tokens)} (expect 1)")
    print(f"scopes beyond the allowed ones: {extra} (expect 0)")
    problems += (len(tokens) > 1) + (extra > 0)
    unchecked += not tokens

    needles = {n: v for n, v in needles.items() if v} | SHAPES
    for name in FILES:
        path = root / "logs" / name
        try:
            raw = read(path)
        except OSError as e:
            print(f"{name}: {'missing' if isinstance(e, FileNotFoundError) else 'unreadable'}")
            unchecked += 1
            continue
        if not raw:
            print(f"{name}: empty")
            unchecked += name == TEST_LOG
            continue
        parts = []
        for label, value in needles.items():
            n = count(raw, value)
            info = name == SERVER_LOG and label in INFO_IN_SERVER_LOG
            problems += n > 0 and not info
            parts.append(f"{label} {n}" + (" (information)" if n and info else ""))
        print(f"{name}: " + ", ".join(parts))
        if name == TEST_LOG:
            bugs = count(raw, "defang bug")
            print(f"'defang bug' lines: {bugs} (expect 0)")
            problems += bugs > 0

    db = root / "logs" / "notify.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                rows = con.execute("SELECT kind, status, COUNT(*) FROM deliveries GROUP BY kind, status").fetchall()
            finally:
                con.close()
            for kind, status, n in rows:
                print(f"deliveries: {kind} {status} {n}")
        except sqlite3.Error:
            print("deliveries: unreadable")
            unchecked += 1

    git = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False)
    if git.returncode:
        print("git status: not available")
        unchecked += 1
    else:
        changed = len(git.stdout.splitlines())
        print(f"files git sees as changed or new: {changed} (expect 0)")
        problems += changed > 0

    print(f"\nproblems: {problems}, not checked: {unchecked}")
    return 1 if problems else 2 if unchecked else 0


if __name__ == "__main__":
    sys.exit(main())
