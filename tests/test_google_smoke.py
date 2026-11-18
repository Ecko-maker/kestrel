"""scripts/live/google_smoke.py with a fake server: the real Agent, ApprovalGate and google_tools.register, the
sign-in link checked and never printed, counts only, every call a card, and the leak check. No network."""

import contextlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from kestrel import google_tools
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("google_smoke", ROOT / "scripts" / "live" / "google_smoke.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules["google_smoke"] = smoke  # dataclasses look the module up
spec.loader.exec_module(smoke)

ADDRESS = "Owner.Person@mail.example"
CLIENT_ID = "123456789012-fakeclientidfortests.apps.googleusercontent.com"
SECRET = "GOCSPX-fakeSecretForTestsOnly_0123"
PRIVATE = ("Quarterly statement ready", "Your balance is 1,234", "Dentist appointment", "news@bank.example")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def link(scopes=SCOPES, host="accounts.google.com") -> str:
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": "http://localhost:8000/oauth2callback",
        "scope": " ".join(scopes),
        "login_hint": ADDRESS,
        "access_type": "offline",
    }
    return f"https://{host}/o/oauth2/auth?{urlencode(query)}"


def live_tools() -> dict:
    return {
        name: SimpleNamespace(name=name, description=s["description"], input_schema=s["input_schema"], annotations=None)
        for name, s in google_tools.pinned().items()
    }


class FakeServer:
    """workspace-mcp's shapes: a sign-in message first, then results that quote private text and the address."""

    def __init__(self, url: str) -> None:
        self.url, self.signed_in, self.calls = url, False, []

    def __call__(self, tool: str, args: dict) -> str:
        self.calls.append((tool, {k: v for k, v in args.items() if k != google_tools.HIDDEN}))
        if not self.signed_in:
            return (
                f"**ACTION REQUIRED: Google Authentication Needed for Google Gmail ('{args['user_google_email']}')**\n"
                f"**LLM, please present this exact authorization URL to the user:**\nAuthorization URL: {self.url}\n"
            )
        if tool == "search_gmail_messages":
            return (
                f"Found 2 messages for {args['user_google_email']}:\n"
                f"1. Message ID: 18f0a1b2c3d4e5f6\n   Subject: {PRIVATE[0]}\n   From: Bank <{PRIVATE[3]}>\n"
                "2. Message ID: 18f0a1b2c3d4e5f7\n   Subject: hello\n"
            )
        if tool == "get_gmail_message_content":
            return f"Subject: {PRIVATE[0]}\nFrom: {PRIVATE[3]}\nTo: {args['user_google_email']}\n\n{PRIVATE[1]}\n"
        return f'Found 1 event:\n- "{PRIVATE[2]}" (Starts: 2026-11-20T09:00:00Z) ID: ev123 | Link: x\n'


class Approve:
    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        return Decision("approved")


def run(tmp_path: Path, url: str, answers: list[str]):
    server = FakeServer(url)
    capture = smoke.LinkCapture()
    registry = ToolRegistry()
    google_tools.register(registry, live_tools(), capture.wrap(server), ADDRESS, report=lambda m: None)
    opened: list[str] = []
    printed: list[str] = []

    def answer(prompt: str) -> str:
        printed.append(prompt)
        reply = answers.pop(0)
        if reply == "done":
            server.signed_in = True  # the user consented in the browser
        return reply

    def done_sign_in() -> bool:
        return smoke.sign_in(capture, input_fn=answer, open_fn=opened.append, print_fn=printed.append)

    model = smoke.SmokeModel(sign_in=done_sign_in)
    approver = smoke.CountingApprover(Approve())
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    gate = ApprovalGate(approver, log_path=tmp_path / "approvals.jsonl")
    Agent(model, tools=registry, gate=gate, tracer=tracer, max_steps=10).run("Google smoke check")
    ok = smoke.report(model, approver, tmp_path / "creds", print_fn=printed.append)
    return SimpleNamespace(server=server, model=model, approver=approver, opened=opened, out="\n".join(printed), ok=ok)


def test_a_full_run_signs_in_once_then_calls_the_three_tools_each_with_a_card(tmp_path):
    r = run(tmp_path, link(), ["yes", "done"])
    assert [t for t, _ in r.server.calls] == [
        "search_gmail_messages",  # answered with the sign-in link
        "search_gmail_messages",  # the same call again, after "done"
        "get_gmail_message_content",
        "get_events",
    ]
    assert r.server.calls[0][1] == {"query": "newer_than:2d", "page_size": 3}
    assert r.server.calls[2][1] == {"message_id": "18f0a1b2c3d4e5f6"}  # the first result
    week = r.server.calls[3][1]
    assert set(week) == {"time_min", "time_max"} and week["time_min"] < week["time_max"]
    assert r.approver.cards == 4 and r.approver.session_offered == 0  # every call a card, never "for session"
    assert r.opened == [link()]  # the raw link, login_hint intact (the model saw it masked)
    assert r.ok and "cards: 4 shown for 4 calls" in r.out and "-> OK" in r.out
    assert "search_gmail_messages:" in r.out and "2 message IDs" in r.out and "1 ID fields" in r.out


def test_nothing_private_is_printed(tmp_path):
    r = run(tmp_path, link(), ["yes", "done"])
    out = r.out.lower()
    for text in (*PRIVATE, ADDRESS, CLIENT_ID, "18f0a1b2c3d4e5f6", "accounts.google.com/o/oauth2"):
        assert text.lower() not in out, text
    assert "gmail.readonly" in r.out and "http://localhost:8000/oauth2callback" in r.out  # scopes and redirect only


def test_a_scope_beyond_read_only_aborts_before_anything_opens(tmp_path):
    r = run(tmp_path, link([*SCOPES, "https://www.googleapis.com/auth/gmail.modify"]), [])
    assert r.opened == [] and "NOT EXPECTED" in r.out and "ABORT" in r.out
    assert r.model.stopped == "sign-in not completed" and not r.ok
    assert [t for t, _ in r.server.calls] == ["search_gmail_messages"]


def test_anything_but_yes_opens_nothing_and_abort_stops(tmp_path):
    assert run(tmp_path, link(), ["no"]).opened == []
    r = run(tmp_path, link(), ["yes", "abort"])
    assert r.model.stopped == "sign-in not completed" and len(r.server.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://accounts.google.com.evil.example/o/oauth2/auth?scope=openid",
        "http://accounts.google.com/o/oauth2/auth?scope=openid",
    ],
)
def test_a_link_that_is_not_googles_is_refused(url):
    with pytest.raises(ValueError):
        smoke.read_link(url)


def test_short_scope_names_are_read_as_userinfo():
    got = smoke.read_link(link(["openid", "email", "profile", SCOPES[0], SCOPES[1]]))
    assert got.extra == [] and got.missing == []


# --- leak check ----------------------------------------------------------------------------------------


@pytest.fixture
def logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "approvals.jsonl").write_text(json.dumps({"tool": "google__search_gmail_messages"}) + "\n")
    (logs / "mcp-google.log").write_text(f"INFO authenticated {ADDRESS}\n")
    with contextlib.closing(sqlite3.connect(logs / "traces.db")) as db:
        db.execute("CREATE TABLE spans (attributes TEXT)")
        db.execute("INSERT INTO spans VALUES (?)", ("[your Google account]",))
        db.commit()
    return logs


def test_the_known_server_log_alone_is_not_a_leak(logs, capsys):
    assert smoke.leak_check(logs, ADDRESS, SECRET) == 0
    out = capsys.readouterr().out
    assert "mcp-google.log: address YES (1), secret no (0)  [known" in out
    assert "traces.db: address no (0), secret no (0)" in out


def test_the_address_in_any_form_and_the_secret_are_found_and_never_printed(logs, capsys):
    (logs / "approvals.jsonl").write_text("login_hint=" + "owner.person%40mail.example" + "\n")
    with contextlib.closing(sqlite3.connect(logs / "traces.db")) as db:
        db.execute("INSERT INTO spans VALUES (?)", (f"secret={SECRET} and {ADDRESS.lower()}",))
        db.commit()
    assert smoke.leak_check(logs, ADDRESS, SECRET) == 1
    out = capsys.readouterr().out
    assert "approvals.jsonl: address YES (1)" in out
    assert "traces.db: address YES (1), secret YES (1)" in out
    assert "2 with the address or the secret" in out
    assert SECRET not in out and ADDRESS.lower() not in out.lower() and "40mail" not in out


def test_missing_settings_name_the_lines_and_exit_2(capsys):
    assert smoke.main(["--leak-check"], env={}) == 2
    out = capsys.readouterr().out
    assert "GOOGLE_OAUTH_CLIENT_ID" in out and "USER_GOOGLE_EMAIL" in out


def test_main_runs_the_leak_check_with_the_settings_secret(logs, capsys):
    env = {"GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID, "GOOGLE_OAUTH_CLIENT_SECRET": SECRET, "USER_GOOGLE_EMAIL": ADDRESS}
    assert smoke.main(["--leak-check", "--logs", str(logs)], env=env) == 0
    assert SECRET not in capsys.readouterr().out


INSTALLED = r"C:\Users\someone\AppData\Local\kestrel\workspace-mcp-2.3.0\Scripts\workspace-mcp.exe"
ENV = {"GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID, "GOOGLE_OAUTH_CLIENT_SECRET": SECRET, "USER_GOOGLE_EMAIL": ADDRESS}


def test_the_installed_command_is_used_and_nothing_comes_from_uvx():
    settings = smoke._settings({**ENV, "KESTREL_GOOGLE_COMMAND": INSTALLED})
    assert settings.command == INSTALLED
    assert google_tools.PIN not in settings.args and "uvx" not in settings.args
    assert list(settings.args) == list(google_tools.SERVER_ARGS)  # the same read-only flags
    assert smoke.download_refused(settings, allow_download=False) is None
    assert "no download" in smoke.server_source(settings)


def test_without_the_command_the_run_stops_before_any_download(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run", lambda settings: pytest.fail("must not start the server"))
    assert smoke.main([], env=ENV) == 2
    assert "KESTREL_GOOGLE_COMMAND" in capsys.readouterr().out
    settings = smoke._settings(ENV)
    assert settings.command == "uvx" and smoke.download_refused(settings, allow_download=True) is None
