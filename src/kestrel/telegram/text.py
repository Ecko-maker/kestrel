"""Text for Telegram: approval cards (design section 1) and long answers split into messages (section 2).

All plain text: nothing is sent with a parse_mode, so model, file or email text can't inject formatting.
"""

from telegram.constants import MessageLimit

from kestrel.approval import Notice

MAX_MESSAGE = MessageLimit.MAX_TEXT_LENGTH  # 4,096 characters
MAX_PARTS = 4  # an answer longer than this many messages is cut
CARD_LIMIT = 3_800  # a longer card sends its full preview as a .txt document first
PREVIEW_HEAD = 1_500  # characters of the preview kept on a card whose preview went as a file


def card_text(tool_name: str, preview: str, notice: Notice | None) -> tuple[str, str | None]:
    """(the card's text, the full preview to send as a file first, or None).

    Order: the tool, recipients the user never typed (first, marked), why the card appeared, where
    local text sits in the call, its decoded forms, then the preview."""
    lines = [f"Approval needed: {tool_name}"]
    if notice:
        lines += [f"⚠ {warning}" for warning in notice.recipient_warnings]
        lines += [f"Why: {reason}" for reason in notice.reasons]
        for h in notice.highlights:
            before, match, after = h.parts()
            lines.append(f"Local text ({h.chars} characters from {h.source}): {before}[{match}]{after}")
        lines += [f"Decoded: {d}" for d in notice.decoded]
    head = "\n".join(lines)
    full = f"{head}\n\n{preview}"
    if len(full) <= CARD_LIMIT:
        return full, None
    shown = f"\n\nFull preview in the file above ({len(preview):,} characters). It starts:\n{preview[:PREVIEW_HEAD]}"
    room = CARD_LIMIT - len(shown)  # a very long notice is cut too, so the card always fits one message
    return (head if len(head) <= room else head[: room - 1] + "…") + shown, preview


def split_message(
    text: str, trace_id: str | None = None, limit: int = MAX_MESSAGE, parts: int = MAX_PARTS
) -> list[str]:
    """Split at paragraphs, then lines, then anywhere, into at most `parts` messages of `limit`
    characters. Anything beyond that is cut, with a note saying where the whole answer is."""
    if not text:
        return []
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if len(chunks) <= parts:
        return chunks
    kept = chunks[: parts - 1]
    where = f"`kestrel trace {trace_id[:8]}`" if trace_id else "the trace"
    shown = sum(len(c) for c in kept)

    def note(cut_chars: int) -> str:
        return f"\n\n(answer cut: {cut_chars:,} more characters; {where} has all of it)"

    last = chunks[parts - 1]
    room = limit - len(note(len(text)))  # the note's own length, at its longest
    last = last[:room]
    return [*kept, last + note(len(text) - shown - len(last))]
