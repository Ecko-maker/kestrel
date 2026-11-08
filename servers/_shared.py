"""What both servers share: read-only annotations, name and path checks, untrusted-data wrapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from kestrel.tools import UNTRUSTED_NOTE

REPO = Path(__file__).resolve().parents[1]
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MAX_LIMIT = 100  # rows a list tool returns at most
MAX_TEXT = 4_000  # characters of one untrusted field


def refuse(message: str) -> ToolError:
    """A readable tool error for the client (the server keeps running)."""
    return ToolError(message)


def plain_name(name: str, what: str) -> str:
    """A bare file name: no folder parts, no drive, nothing hidden (so `../x`, `C:\\x`, `/x` and `.env` fail)."""
    if (
        not name
        or len(name) > 200
        or name in (".", "..")
        or name.startswith(".")
        or any(c in name for c in ("/", "\\", ":", "\0"))
        or Path(name).is_absolute()
    ):
        raise refuse(f"{what} must be a plain file name from the list, not a path: {name!r}")
    return name


def inside(path: Path, folder: Path, what: str) -> Path:
    """`path` with every link followed, refused unless it is still inside `folder`."""
    real, root = path.resolve(), folder.resolve()
    if not real.is_relative_to(root):
        raise refuse(f"{what} resolves outside its allowed folder: refused")
    return real


def short(text: str, n: int = MAX_TEXT) -> str:
    return text if len(text) <= n else text[:n] + f"... [{len(text) - n} more characters]"


def untrusted(source: str, value: Any) -> str:
    """Content the server didn't write (model answers, tool results, task fixtures, your prompts), as Kestrel's
    own tools return it: inside <untrusted_data>, with any closing tag inside it defused."""
    text = value if isinstance(value, str) else json.dumps(value, indent=1, ensure_ascii=False)
    text = text.replace("</untrusted_data", "<\\/untrusted_data").replace("<untrusted_data", "<\\untrusted_data")
    return f'<untrusted_data source="{source}">\n{text}\n</untrusted_data>\n{UNTRUSTED_NOTE}'


def reply(meta: Any, *blocks: str) -> str:
    """Metadata the server computed (JSON), then any untrusted blocks."""
    return "\n".join([json.dumps(meta, indent=1, ensure_ascii=False), *blocks])
