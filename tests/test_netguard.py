"""The private-network block for fetch__fetch (netguard). No network: a fake resolver stands in for DNS."""

import json

import pytest
from test_approval import FakeApprover, ScriptedLLM, call, calls, say

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.bench.runner import agent_fingerprint
from kestrel.bench.stubs import FETCH_DESCRIPTION, FETCH_SCHEMA
from kestrel.netguard import BLOCKED, FETCH_TOOL, blocked_call, check_url, no_dns
from kestrel.permissions import NETWORK_ONLY
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer

PUBLIC = "93.184.216.34"


def resolver(table: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError("no such name")
        return table[host]

    return resolve


NO_NAMES = resolver({})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",  # loopback
        "http://127.8.9.10:8080/admin",
        "http://10.0.0.1/",  # RFC 1918
        "http://172.16.5.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # link-local: the cloud metadata address
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0:11434/api/tags",  # unspecified
        "http://224.0.0.1/",  # multicast
        "http://[::1]/",  # IPv6 loopback
        "http://[fc00::1]/",  # unique-local
        "http://[fd12:3456::1]/",
        "http://[fe80::1]/",  # link-local
        "http://[::]/",  # unspecified
        "http://[ff02::1]/",  # multicast
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped: judged by the IPv4 inside
        "http://[::ffff:169.254.169.254]/",
        "http://localhost:8765/",
        "http://app.localhost/",
    ],
)
def test_every_private_address_class_is_blocked(url):
    reason = check_url(url, NO_NAMES)
    assert reason is not None and reason.startswith(BLOCKED)


@pytest.mark.parametrize("url", [f"http://{PUBLIC}/", "https://[2606:2800:220:1:248:1893:25c8:1946]/"])
def test_a_public_address_is_allowed(url):
    assert check_url(url, NO_NAMES) is None


def test_a_hostname_that_resolves_to_a_private_address_is_blocked():
    resolve = resolver({"intranet.example.com": ["10.1.2.3"]})
    assert (
        check_url("https://intranet.example.com/wiki", resolve)
        == f"{BLOCKED} (intranet.example.com resolves to 10.1.2.3)"
    )


def test_a_mixed_public_and_private_dns_answer_is_blocked():
    resolve = resolver({"rebind.example.com": [PUBLIC, "192.168.0.10"]})
    assert check_url("https://rebind.example.com/", resolve).startswith(BLOCKED)


def test_a_name_resolving_to_public_addresses_is_allowed():
    resolve = resolver({"example.com": [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"]})
    assert check_url("https://example.com/page", resolve) is None


@pytest.mark.parametrize("answer", ["::1", "fe80::1%12", "::ffff:10.0.0.1"])
def test_ipv6_answers_are_checked_too(answer):
    resolve = resolver({"v6.example.com": [answer]})
    assert check_url("https://v6.example.com/", resolve).startswith(BLOCKED)


def test_odd_ipv4_spellings_are_caught_where_the_resolver_turns_them_into_addresses():
    """2130706433 and 0x7f.1 aren't literals to ipaddress; the system resolver reads them as 127.0.0.1."""
    resolve = resolver({"2130706433": ["127.0.0.1"], "0x7f.1": ["127.0.0.1"]})
    assert check_url("http://2130706433/", resolve).startswith(BLOCKED)
    assert check_url("http://0x7f.1/", resolve).startswith(BLOCKED)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "example.com/x"])
def test_other_schemes_are_blocked(url):
    assert check_url(url, NO_NAMES).startswith("blocked: only http and https")


def test_a_name_that_doesnt_resolve_is_left_to_the_fetch_itself():
    assert check_url("https://nothing-here.example/", NO_NAMES) is None


def test_only_fetch_is_checked():
    assert blocked_call("web_search", {"query": "http://127.0.0.1/"}, NO_NAMES) is None
    assert blocked_call(FETCH_TOOL, {"url": "http://127.0.0.1/"}, NO_NAMES).startswith(BLOCKED)


def test_the_bench_resolver_checks_literals_but_looks_nothing_up():
    assert no_dns("site.example") == []
    assert check_url("https://site.example/page", no_dns) is None
    assert check_url("http://127.0.0.1/", no_dns).startswith(BLOCKED)


# --- through the gate and the agent ---------------------------------------------------------------------


@pytest.fixture
def fetch_registry():
    registry = ToolRegistry()
    fetched: list[dict] = []

    def fetch(**args):
        fetched.append(args)
        return "page"

    registry.register_external(
        FETCH_TOOL, fetch, FETCH_DESCRIPTION, FETCH_SCHEMA, capabilities=NETWORK_ONLY, server="fetch"
    )
    registry.fetched = fetched  # type: ignore[attr-defined]
    return registry


def test_a_blocked_fetch_is_a_tool_error_logged_in_the_audit_log_and_the_trace_and_never_a_card(
    fetch_registry, tmp_path
):
    approver = FakeApprover(Decision("approved"))  # would approve anything it were asked
    log = tmp_path / "approvals.jsonl"
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    llm = ScriptedLLM(calls(call("c1", FETCH_TOOL, url="http://169.254.169.254/latest/meta-data/")), say("Done."))
    agent = Agent(llm, tools=fetch_registry, gate=ApprovalGate(approver, log_path=log, resolve=NO_NAMES), tracer=tracer)
    agent.run("Fetch the metadata page.")
    assert fetch_registry.fetched == [] and approver.requests == []  # didn't run, no card
    [result] = llm.tool_results(1)
    assert result.startswith(f"Error: {BLOCKED}")
    [entry] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert (entry["tool"], entry["decision"]) == (FETCH_TOOL, "blocked") and entry["reason"].startswith(BLOCKED)
    with tracer.connect() as conn:
        span = conn.execute("SELECT status, attributes FROM spans WHERE name = 'tool_call'").fetchone()
    attributes = json.loads(span["attributes"])
    assert span["status"] == "error" and attributes["kestrel.tool.blocked"].startswith(BLOCKED)
    assert attributes["kestrel.tool.ran"] is False


def test_a_fetch_that_would_need_a_card_is_still_blocked_before_any_card(fetch_registry, tmp_path):
    """After a local read, a fetch normally gets a card (tiers v2). A private address is refused first."""
    from kestrel.permissions import Session

    approver = FakeApprover(Decision("approved"))
    gate = ApprovalGate(approver, log_path=tmp_path / "a.jsonl", resolve=NO_NAMES)
    tool = fetch_registry.tools[FETCH_TOOL]
    session = Session()
    session.note_ran("read_file", frozenset({"reads_local"}), {"path": "notes.txt"}, "secret notes")
    assert session.tainted
    result = gate.check(tool, {"url": "http://10.0.0.5/"}, None, session)
    assert result.args is None and result.decision == "blocked" and approver.requests == []


def test_a_public_fetch_runs_as_before(fetch_registry, tmp_path):
    llm = ScriptedLLM(calls(call("c1", FETCH_TOOL, url=f"http://{PUBLIC}/")), say("Done."))
    gate = ApprovalGate(FakeApprover(), log_path=tmp_path / "a.jsonl", resolve=NO_NAMES)
    Agent(llm, tools=fetch_registry, gate=gate).run("Fetch it.")
    assert fetch_registry.fetched == [{"url": f"http://{PUBLIC}/"}]


def test_the_agent_fingerprint_is_unchanged():
    assert agent_fingerprint("groq", "openai/gpt-oss-120b")["sha"] == "35fac5ba6277"
