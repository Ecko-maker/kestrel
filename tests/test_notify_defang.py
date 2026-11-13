"""Reminder text made non-clickable before it goes to Telegram (docs/notifications-design.md, 5.2).

`telegram_like_entities` approximates what Telegram apps make tappable in plain text. It is a test
oracle, not Telegram's real parser: the real check is at runtime (`tappable_entities` on each
message Telegram accepts, logged as a defang bug), and on the owner's phone test."""

import re
from types import SimpleNamespace

import pytest

from kestrel.notify.defang import DIVISION_SLASH, FULLWIDTH_AT, LINK_REMOVED, defang, tappable_entities

TLDS = r"(?:com|org|net|io|ru|uk|de|me|app|dev|xyz|co|info|biz|ly|gl|link|example|bank|zip|mov)"
ORACLE = {
    "url": re.compile(rf"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.{TLDS}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "mention": re.compile(r"(?<!\w)@\w{5,32}"),
    "bot_command": re.compile(r"(?<![\w/])/[A-Za-z0-9_]{1,32}"),
    "phone_number": re.compile(r"\+\d[\d ().-]{6,}\d|\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
}


def telegram_like_entities(text: str) -> set[str]:
    return {kind for kind, pattern in ORACLE.items() if pattern.search(text)}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("open https://evil.example/login now", f"open {LINK_REMOVED} now"),
        ("see www.bank.example/x", f"see {LINK_REMOVED}"),
        ("tg://resolve?domain=x and mailto:a@b.com", f"{LINK_REMOVED} and {LINK_REMOVED}"),
        ("check evil.example today", "check evil[.]example today"),
        ("mail dana@example.org", f"mail dana{FULLWIDTH_AT}example[.]org"),
        ("send /stop then /new", f"send {DIVISION_SLASH}stop then {DIVISION_SLASH}new"),
        ("ask @kestrel_admin", f"ask {FULLWIDTH_AT}kestrel_admin"),
        ("call +1 555 123 4567", "call 15 55 12 34 56 7"),
        ("call (555) 123-4567", "call 55 51 23 45 67"),
        ("call 555.123.4567 or 5551234567", "call 55 51 23 45 67 or 55 51 23 45 67"),
        ("renew passport 2026-11-20", "renew passport 2026-11-20"),
        ("meeting 20.11.2026 at 15:00", "meeting 20.11.2026 at 15:00"),
        ("room 12, floor 3, 4 people", "room 12, floor 3, 4 people"),
        ("e.g. buy milk, eggs etc.", "e.g. buy milk, eggs etc."),
        ("line one\nline two", "line one line two"),
        ("abc‮dcba​!", "abcdcba!"),  # a right-to-left override and a zero-width space
        ("call the dentist", "call the dentist"),
    ],
)
def test_defang(raw, expected):
    assert defang(raw) == expected


ADVERSARIAL = [
    "Reminder: your bank asks you to confirm at https://bank.example/verify",
    "pay at secure-login.bank.example before 5",
    "write to support@bank.example",
    "/stop /new /bad",
    "tap @kestrel_support_bot",
    "call +44 20 7946 0958",
    "call (212) 555-0123",
    "mixed: www.a.example /approve @someone_else +1-212-555-0123 x@y.example",
]


@pytest.mark.parametrize("raw", ADVERSARIAL)
def test_nothing_tappable_is_left(raw):
    assert telegram_like_entities(raw), "the oracle should find something in the raw text"
    assert telegram_like_entities(defang(raw)) == set()


@pytest.mark.parametrize("raw", ADVERSARIAL)
def test_defang_is_idempotent(raw):
    once = defang(raw)
    assert defang(once) == once


def test_tappable_entities_reads_telegrams_entity_types():
    message = SimpleNamespace(entities=[SimpleNamespace(type="bold"), SimpleNamespace(type="bot_command")])
    assert tappable_entities(message) == ["bot_command"]
    assert tappable_entities(SimpleNamespace(entities=())) == [] and tappable_entities(object()) == []
