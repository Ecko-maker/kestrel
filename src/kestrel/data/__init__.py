"""kestrel-data: read-only root-cause analysis tools over local datasets (docs/data.md; the design and its frozen
decision rule: docs/data-rca-design.md, decision 35).

Off by default. The tools load only with `kestrel --tools data ...` or `KESTREL_TOOLS=data`, and the bench registers
them only with `kestrel bench run --tools data` (or `--split rca`). Without either, nothing here is imported by the
agent and the default agent is byte for byte what it was (fingerprint 8a8174d8f17d, pinned by tests/test_data_optin.py).

Needs the optional extra: `uv sync --extra data` (DuckDB 2.0.0 and numpy).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER = "data"


class DataConfigError(ValueError):
    """A missing extra or a bad setting; the message says what to do."""


def requested(cli: list[str] | None = None, env: Mapping[str, str] = os.environ) -> bool:
    """`--tools data` on the command line, or KESTREL_TOOLS=data."""
    from_env = [t.strip().lower() for t in env.get("KESTREL_TOOLS", "").replace(",", " ").split()]
    return "data" in [*(cli or []), *from_env]


def installed() -> bool:
    try:
        import duckdb  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class DataSettings:
    folder: Path
    command: str
    args: list[str]


def load_settings(env: Mapping[str, str] = os.environ, workspace: Path | None = None) -> DataSettings:
    """Check the opt-in can start, or raise DataConfigError. The data folder is KESTREL_DATA_DIR (default
    ./datasets); it must not be the workspace or inside it, so write_file can never change a dataset."""
    if not installed():
        raise DataConfigError("--tools data needs the optional extra: uv sync --extra data (DuckDB and numpy)")
    folder = Path(env.get("KESTREL_DATA_DIR", "").strip() or "datasets").resolve()
    if workspace is not None:
        ws = Path(workspace).resolve()
        if folder == ws or folder.is_relative_to(ws):
            raise DataConfigError("KESTREL_DATA_DIR is inside the workspace; datasets must live outside it")
    return DataSettings(folder, sys.executable, ["-m", "kestrel.data.server", "serve", "--folder", str(folder)])


def describe() -> dict[str, Any]:
    from kestrel.data.tools import describe as _describe

    return _describe()


def start(settings: DataSettings, registry: Any, on_problem: Callable[[str, str], None]) -> Any:
    """Start the kestrel-data server (its own MCPManager) and register its 5 tools. Returns the manager to close."""
    from mcp import StdioServerParameters

    from kestrel.data.tools import NAMES, register
    from kestrel.mcp_client import MCPManager

    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH")}
    params = StdioServerParameters(command=settings.command, args=settings.args, env=env)
    manager = MCPManager({SERVER: params}, on_problem=on_problem)
    manager.start()
    conn = manager.connections[SERVER]
    if conn.alive:
        listed = {t.name for t in conn.tools}
        if missing := [n for n in NAMES if n not in listed]:
            on_problem(SERVER, f"the server has no tool(s) {missing}; not registered")
            return manager
        register(registry, lambda tool, args: manager.call(SERVER, tool, args), available=conn.is_alive)
    return manager
