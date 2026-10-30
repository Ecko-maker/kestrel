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


def greeting(name: str | None, heard: str | None = None) -> str:
    """The reply to a wake. heard: a greeting said before "Kestrel" ("hello", "good morning"),
    echoed back: "Hello, Robin. I'm here, what do you need?"; otherwise the plain greeting."""
    name = clean_name(name)
    if heard:
        opener = heard.strip().capitalize()
        return f"{opener}, {name}. I'm here, what do you need?" if name else f"{opener}. I'm here, what do you need?"
    return f"I'm here, {name}. What do you need?" if name else "I'm here. What do you need?"
