"""Did the Telegram bot token end up in Kestrel's local files? Run it after a phone test.

    uv run python scripts/live/telegram_leak_check.py              # searches logs/
    uv run python scripts/live/telegram_leak_check.py --logs DIR

No network, no model calls, no quota. The token is read the way `kestrel telegram` reads it
(`kestrel.telegram.settings.load_settings`, after loading .env into memory) and is never printed:
the output is only yes/no per file, and how many times each was found.

Every file under the folder is searched as raw bytes, so SQLite databases (traces.db and its -wal
journal), approvals.jsonl, MCP logs and the lock file are all covered, whatever their format. Two
things are searched for:
- the whole token (`<bot id>:<secret>`);
- the secret alone (the part after the colon), in case something split or re-encoded the rest.
The bot id before the colon is public (it's in the bot's own profile), so it isn't searched alone.

Exit code 0 = found nowhere, 1 = found somewhere, 2 = settings missing (the message names the .env lines).
"""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

CHUNK = 1 << 20  # read files 1 MB at a time, with an overlap so a match across chunks isn't missed


def count(path: Path, needle: bytes) -> int:
    """How many times `needle` occurs in the file's bytes (non-overlapping)."""
    found, tail = 0, b""
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            data = tail + block
            found += data.count(needle)
            # keep the last len(needle)-1 bytes: enough to complete a match, too short to be counted twice
            tail = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    return found


def scan(logs: Path, token: str) -> list[tuple[str, int, int]]:
    """(file, times the whole token was found, times the secret part was found) for every file."""
    whole = token.encode("utf-8")
    secret = token.split(":", 1)[1].encode("utf-8")
    rows = []
    for path in sorted(p for p in logs.rglob("*") if p.is_file()):
        try:
            rows.append((path.relative_to(logs).as_posix(), count(path, whole), count(path, secret)))
        except OSError as e:  # e.g. a database held open by a running Kestrel on Windows
            rows.append((f"{path.relative_to(logs).as_posix()} (unreadable: {type(e).__name__})", -1, -1))
    return rows


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--logs", type=Path, default=Path("logs"), help="folder to search (default logs/)")
    args = parser.parse_args(argv)

    from kestrel.telegram.settings import TelegramConfigError, load_settings

    if env is None:
        from dotenv import load_dotenv

        load_dotenv()  # into this process's memory only, as `kestrel telegram` does
        env = os.environ
    try:
        token = load_settings(env).token
    except TelegramConfigError as e:
        print(e)
        return 2
    if not args.logs.is_dir():
        print(f"{args.logs} doesn't exist: nothing to search.")
        return 0

    rows = scan(args.logs, token)
    leaked = 0
    for name, whole, secret in rows:
        if whole < 0:
            print(f"{name}: could not be read")
            continue
        hit = whole > 0 or secret > 0
        leaked += hit
        print(f"{name}: token {'YES' if whole else 'no'} ({whole}), secret part {'YES' if secret else 'no'} ({secret})")
    unreadable = sum(1 for _, whole, _ in rows if whole < 0)
    print(
        f"\n{len(rows)} file(s) searched, {leaked} with the token or its secret part"
        + (f", {unreadable} unreadable (stop Kestrel and run again)" if unreadable else "")
        + "."
    )
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
