"""Make text non-clickable before it goes to Telegram (docs/notifications-design.md, section 5.2).

Telegram apps make bare URLs, domains, email addresses, @usernames, /commands and phone numbers
tappable even in plain text. A tappable `/stop` in a reminder would send that command as you, so
every field placed in a notifier message passes through `defang()` first:

- URLs (any scheme://, www., mailto: / tel: / tg: / sms:) -> `[link removed]`;
- domains and email domains: dots as `[.]` (`example[.]org`);
- `/` before a letter or digit -> U+2215 DIVISION SLASH;
- `@` before a name (in an email address too) -> U+FF20 FULLWIDTH COMMERCIAL AT;
- digit runs that look like phone numbers (7+ digits with spaces, dashes, dots or brackets) -> the
  digits in groups of 2 (`+1 555 123 4567` -> `15 55 12 34 56 7`); dates (2026-11-20, 20.11.2026)
  are left alone;
- control and format characters (bidirectional overrides, zero-width) removed; line breaks become
  spaces, so a field can't start a line of its own.

Stored text is the owner's original; only what is shown is defanged. Whether Telegram still finds an
entity is checked at runtime on each delivered reminder (`tappable_entities`), as the design asks.
"""

import re
import unicodedata

LINK_REMOVED = "[link removed]"
DIVISION_SLASH = "∕"
FULLWIDTH_AT = "＠"
TAPPABLE = {"url", "text_link", "email", "mention", "text_mention", "bot_command", "phone_number"}

URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S*|\bwww\.\S+|\b(?:mailto|tel|tg|sms|callto):\S+")
DOMAIN = re.compile(r"(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}\b")
SLASH = re.compile(r"/(?=[^\W_])")
AT = re.compile(r"@(?=\w)")
PHONE = re.compile(r"(?<![\w+])\+?\(?\d[\d \t().\-]{5,}\d(?!\w)")
DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")


def _clean(text: str) -> str:
    text = re.sub(r"[\r\n\t\v\f  ]+", " ", text)
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf"))


def _phone(m: re.Match[str]) -> str:
    found = m.group(0)
    digits = re.sub(r"\D", "", found)
    if len(digits) < 7 or DATE.fullmatch(found.strip()):
        return found
    return " ".join(digits[i : i + 2] for i in range(0, len(digits), 2))


def defang(text: str) -> str:
    text = _clean(text)
    text = URL.sub(LINK_REMOVED, text)
    text = DOMAIN.sub(lambda m: m.group(0).replace(".", "[.]"), text)
    text = SLASH.sub(DIVISION_SLASH, text)
    text = AT.sub(FULLWIDTH_AT, text)
    return PHONE.sub(_phone, text)


def tappable_entities(message: object) -> list[str]:
    """The tappable entity types Telegram found in a message it accepted (python-telegram-bot's
    Message.entities); empty for a fake or a message without entities."""
    found = {str(e.type) for e in getattr(message, "entities", None) or ()}
    return sorted(found & TAPPABLE)
