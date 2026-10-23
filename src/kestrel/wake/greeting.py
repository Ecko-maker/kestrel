"""The one thing the wake word says. Fixed text, built locally: no model is ever asked."""

MAX_NAME_CHARS = 40


def clean_name(raw: str | None) -> str | None:
    """KESTREL_USER_NAME, made safe to speak and show: printable characters only, single spaces,
    at most 40 characters. Empty means no name."""
    if not raw:
        return None
    printable = "".join(c if c.isprintable() else " " for c in raw)
    name = " ".join(printable.split())[:MAX_NAME_CHARS].strip()
    return name or None


def greeting(name: str | None) -> str:
    name = clean_name(name)
    return f"I'm here, {name}. What do you need?" if name else "I'm here. What do you need?"
