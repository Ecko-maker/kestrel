"""scripts/live/telegram_leak_check.py with a fake token: finds it in any file format, never prints it."""

import contextlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("telegram")  # the settings code lives in the optional `telegram` extra

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "telegram_leak_check", ROOT / "scripts" / "live" / "telegram_leak_check.py"
)
assert spec and spec.loader
leak_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leak_check)

SECRET = "AAH4fakeSecretPartForTests_only-0123456"  # 39 characters, the shape of a real secret
TOKEN = f"987654321:{SECRET}"  # not a real bot
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_ALLOWED_USER_ID": "42"}


@pytest.fixture
def logs(tmp_path):
    logs = tmp_path / "logs"
    (logs / "sub").mkdir(parents=True)
    (logs / "approvals.jsonl").write_text(json.dumps({"tool": "x", "args": {"note": "[REDACTED]"}}) + "\n")
    (logs / "mcp-fetch.log").write_text(f"GET https://api.telegram.org/bot{TOKEN}/getUpdates\nagain {TOKEN}\n")
    (logs / "sub" / "secret-only.txt").write_text(f"something re-encoded the id but kept {SECRET}")
    with contextlib.closing(sqlite3.connect(logs / "traces.db")) as db:
        db.execute("CREATE TABLE spans (attributes TEXT)")
        db.execute("INSERT INTO spans VALUES (?)", (json.dumps({"kestrel.user_message": f"my token is {TOKEN}"}),))
        db.commit()
    return logs


def test_finds_the_token_and_its_secret_part_in_every_format_and_never_prints_them(logs, capsys):
    code = leak_check.main(["--logs", str(logs)], env=ENV)
    out = capsys.readouterr().out
    assert code == 1
    lines = {line.split(":")[0]: line for line in out.splitlines() if ": token" in line}
    assert lines["approvals.jsonl"].endswith("token no (0), secret part no (0)")
    assert lines["mcp-fetch.log"].endswith("token YES (2), secret part YES (2)")
    assert lines["sub/secret-only.txt"].endswith("token no (0), secret part YES (1)")
    assert "YES" in lines["traces.db"]  # inside SQLite's own file format
    assert "4 file(s) searched, 3 with the token or its secret part." in out
    assert SECRET not in out and SECRET[:8] not in out and "987654321" not in out


def test_clean_logs_exit_zero(tmp_path, capsys):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "traces.db").write_bytes(b"SQLite format 3\x00 nothing here [REDACTED]")
    assert leak_check.main(["--logs", str(logs)], env=ENV) == 0
    assert "1 file(s) searched, 0 with" in capsys.readouterr().out


def test_a_match_across_a_chunk_boundary_is_found_once(tmp_path, monkeypatch):
    monkeypatch.setattr(leak_check, "CHUNK", 16)
    path = tmp_path / "f.bin"
    path.write_bytes(b"x" * 13 + TOKEN.encode() + b"y" * 40 + TOKEN.encode())
    assert leak_check.count(path, TOKEN.encode()) == 2


def test_missing_settings_name_the_env_lines_and_exit_two(tmp_path, capsys):
    assert leak_check.main(["--logs", str(tmp_path)], env={}) == 2
    assert "TELEGRAM_BOT_TOKEN=" in capsys.readouterr().out
