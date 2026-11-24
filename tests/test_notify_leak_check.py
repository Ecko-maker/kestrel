"""scripts/live/notify_leak_check.py with fake values: finds each in every file and prints only counts."""

import contextlib
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kestrel.notify.google_source import CALENDAR_SCOPE, STATES_FILE

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notify_leak_check", ROOT / "scripts" / "live" / "notify_leak_check.py")
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

VALUES = {
    "TELEGRAM_BOT_TOKEN": "987654321:FAKEbotpartFORtests",
    "GOOGLE_OAUTH_CLIENT_SECRET": "FAKEclientsecretFORtests",
    "GOOGLE_OAUTH_CLIENT_ID": "FAKEclientid.apps.example.com",
    "USER_GOOGLE_EMAIL": "someone@example.com",
}
TOKEN = {"token": "FAKEaccesstoken", "refresh_token": "FAKErefreshtoken", "scopes": [CALENDAR_SCOPE]}
NEVER_PRINTED = ("FAKE", "someone", "987654321", "KCAL", "ya29", "https", "o/oauth2", "example.com")


@pytest.fixture
def setup(tmp_path):
    root, creds = tmp_path / "repo", tmp_path / "creds"
    logs = root / "logs"
    logs.mkdir(parents=True)
    creds.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("logs/\n")
    (creds / "one.json").write_text(json.dumps(TOKEN))
    (creds / STATES_FILE).write_text("{}")
    for name in check.FILES:
        (logs / name).write_text("clean\n")
    (logs / "notify.db").unlink()
    with contextlib.closing(sqlite3.connect(logs / "notify.db")) as db:
        db.execute("CREATE TABLE deliveries (kind TEXT, status TEXT)")
        db.execute("INSERT INTO deliveries VALUES ('calendar', 'sent')")
        db.commit()
    return root, creds, {**VALUES, "KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR": str(creds)}


def run(setup, capsys):
    root, _, env = setup
    code = check.main(root, env)
    out = capsys.readouterr().out
    assert not [v for v in NEVER_PRINTED if v in out]
    return code, out


def test_clean_files_exit_zero(setup, capsys):
    code, out = run(setup, capsys)
    assert code == 0
    assert "token files in the notifier's folder: 1" in out and "deliveries: calendar sent 1" in out
    assert "problems: 0, not checked: 0" in out


@pytest.mark.parametrize("name", check.FILES)
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_each_value_is_found_in_each_file(setup, capsys, monkeypatch, name, encoding):
    git = subprocess.CompletedProcess([], 0, stdout="")
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **k: git)  # slow on Windows, tested below
    info = [VALUES["GOOGLE_OAUTH_CLIENT_ID"], VALUES["USER_GOOGLE_EMAIL"], "SOMEONE@example.com", "KCAL-1"]
    hard = [VALUES["TELEGRAM_BOT_TOKEN"], VALUES["GOOGLE_OAUTH_CLIENT_SECRET"], "FAKEbotpartFORtests"]
    hard += ["FAKEaccesstoken", "FAKErefreshtoken", "ya29.x"]
    for value in info + hard:
        (setup[0] / "logs" / name).write_bytes(f"a {value} b".encode(encoding))  # over notify.db too: raw bytes
        code, out = run(setup, capsys)
        allowed = name == check.SERVER_LOG and value in info
        assert code == (0 if allowed else 1), value
        assert ("(information)" in out) == allowed, value


def test_a_value_only_in_the_journal_is_found(setup, capsys):
    (setup[0] / "logs" / "traces.db-wal").write_text(VALUES["GOOGLE_OAUTH_CLIENT_SECRET"])
    assert run(setup, capsys)[0] == 1


def test_sign_in_links_count_everywhere_but_the_server_log(setup, capsys):
    link = "https://accounts.google.com/o/oauth2/auth?x"
    (setup[0] / "logs" / check.SERVER_LOG).write_text(link)
    assert run(setup, capsys)[0] == 0
    (setup[0] / "logs" / check.TEST_LOG).write_text(link)
    assert run(setup, capsys)[0] == 1


def test_a_wider_scope_a_second_token_a_git_change_or_a_defang_bug_is_a_problem(setup, capsys):
    root, creds, _ = setup
    for path, text in (
        (creds / "one.json", json.dumps({**TOKEN, "scopes": ["https://example.com/scope"]})),
        (creds / "two.json", json.dumps(TOKEN)),
        (root / "new.txt", "x"),
        (root / "logs" / check.TEST_LOG, "defang bug"),
    ):
        before = path.read_text() if path.exists() else None
        path.write_text(text)
        assert run(setup, capsys)[0] == 1
        if before is None:
            path.unlink()
        else:
            path.write_text(before)
    assert run(setup, capsys)[0] == 0


def test_what_could_not_be_checked_is_never_clean(setup, capsys):
    root, creds, env = setup
    for name in VALUES:
        assert run((root, creds, {**env, name: ""}), capsys)[0] == 2
    (root / "logs" / check.TEST_LOG).unlink()
    assert run(setup, capsys)[0] == 2
    (root / "logs" / check.TEST_LOG).write_text("clean")
    for bad in ("not json", "[]", json.dumps({"scopes": []})):
        (creds / "one.json").write_text(bad)
        assert run(setup, capsys)[0] == 2
    (creds / "one.json").unlink()
    assert run(setup, capsys)[0] == 2
