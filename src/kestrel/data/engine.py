"""DuckDB, locked down: the only way the data tools touch a dataset (docs/data-rca-design.md 1.3).

Every dataset is opened with all three settings the design measured as necessary:
- `read_only=True`: no CREATE, INSERT, UPDATE or DELETE on the database;
- `enable_external_access=false`: no file or network access at all (read_csv, read_text, glob, COPY ... TO,
  ATTACH, INSTALL, LOAD, http URLs), so DuckDB reads only the attached `.duckdb` file;
- `lock_configuration=true`, after `memory_limit` (1 GB) and `threads` (2), so no SET can undo the above.
Extension autoloading and autoinstalling are off too. `read_only=True` alone is not a lock-down: it still reads
any file, writes with COPY and installs extensions (measured 2026-11-12). tests/test_data_engine.py tries each
escape against a real connection.

Datasets are named, never given as paths: `^[a-z0-9_-]{1,40}$`, a `<name>.duckdb` file directly inside the data
folder, not a link or junction.

Results are capped below the registry's 8,000-character cut, so a table is never cut mid-row: 50 rows, 200
characters per cell (cut with an ellipsis), 4,000 characters per result; every cap that applies is stated. Cells are
printed escaped on one line, and every table line starts with "| ", so cell text can't forge a line of its own
(an evidence id, a heading).
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

NAME = re.compile(r"^[a-z0-9_-]{1,40}$")
SUFFIX = ".duckdb"
MAX_ROWS = 50
MAX_CELL = 200
MAX_RESULT = 4_000
QUERY_TIMEOUT = 10.0  # seconds, below the registry's 20 s tool timeout, so the tool's own error comes first
MEMORY_LIMIT = "1GB"
THREADS = 2
ELLIPSIS = "…"

LOCKED_CONFIG: dict[str, Any] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "memory_limit": MEMORY_LIMIT,
    "threads": THREADS,
    "lock_configuration": True,  # last: everything above is fixed from here on
}


class DataError(Exception):
    """A refusal or failure the model reads (no path, no secret: names and reasons only)."""


def default_folder() -> Path:
    return Path(os.getenv("KESTREL_DATA_DIR", "datasets")).resolve()


def dataset_path(folder: Path, name: str) -> Path:
    """The `.duckdb` file for a dataset name, or DataError. Names only: a path, `..`, a link or a junction is
    refused before DuckDB sees anything."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise DataError(f"dataset must be a name like 'sales_2026' (a-z, 0-9, _ and -, at most 40), not {name!r}")
    root = folder.resolve()
    path = root / f"{name}{SUFFIX}"
    if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
        raise DataError(f"dataset {name!r} is a link; only plain files in the data folder are opened")
    if not path.is_file() or path.resolve().parent != root:
        raise DataError(f"no dataset named {name!r}. Available: {', '.join(available(folder)) or 'none'}")
    return path


def available(folder: Path) -> list[str]:
    root = folder.resolve()
    if not root.is_dir():
        return []
    return sorted(
        p.stem for p in root.glob(f"*{SUFFIX}") if p.is_file() and not p.is_symlink() and NAME.fullmatch(p.stem)
    )


def connect(path: Path) -> Any:
    """A locked connection (see the module docstring). The caller closes it."""
    import duckdb

    return duckdb.connect(str(path), read_only=True, config=dict(LOCKED_CONFIG))


def open_dataset(folder: Path, name: str) -> Any:
    return connect(dataset_path(folder, name))


def check_select(con: Any, sql: str) -> None:
    """Exactly one statement, of type SELECT (WITH ... SELECT, FROM ..., VALUES, DESCRIBE and SHOW parse as
    SELECT). Anything else is refused before the engine runs it: one execute() runs every statement it is given."""
    if not isinstance(sql, str) or not sql.strip():
        raise DataError("sql must be one SELECT statement")
    try:
        statements = con.extract_statements(sql)
    except Exception as e:  # a parser error: the model can correct it
        raise DataError(f"could not parse the SQL: {_one_line(str(e))[:300]}") from None
    if len(statements) != 1:
        raise DataError(f"exactly one statement is allowed, got {len(statements)}")
    kind = str(statements[0].type).rsplit(".", 1)[-1]
    if kind != "SELECT":
        raise DataError(f"only SELECT is allowed (read only), got {kind}")


def execute(con: Any, sql: str, params: Sequence[Any] | None = None, timeout: float = QUERY_TIMEOUT) -> Any:
    """Run one statement with a timeout: con.interrupt() from a timer thread stops it."""
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql, list(params) if params else [])
    except Exception as e:
        name = type(e).__name__
        if "Interrupt" in name:
            raise DataError(f"the query took longer than {timeout:g} s and was stopped") from None
        raise DataError(f"{name}: {_one_line(str(e))[:300]}") from None
    finally:
        timer.cancel()


def run_select(con: Any, sql: str) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """(columns, up to MAX_ROWS rows, more rows exist)."""
    check_select(con, sql)
    cur = execute(con, sql)
    columns = [d[0] for d in cur.description or []]
    rows = cur.fetchmany(MAX_ROWS + 1)
    return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# --- formatting --------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def cell(value: Any) -> str:
    """A value as one escaped line, at most MAX_CELL characters."""
    if value is None:
        text = "NULL"
    elif isinstance(value, float):
        text = fmt_number(value)
    elif isinstance(value, Decimal):
        text = fmt_number(float(value))
    elif isinstance(value, datetime):
        text = value.isoformat(sep=" ")
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    text = _one_line(text).replace("|", "\\|")
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + ELLIPSIS


def fmt_number(value: float, decimals: int = 2) -> str:
    """Fixed decimals with thousands separators, exactly as an answer may quote it."""
    if value != value:  # NaN
        return "n/a"
    if abs(value) == float("inf"):
        return "inf" if value > 0 else "-inf"
    return f"{value:,.{decimals}f}"


def fmt_int(value: float) -> str:
    return f"{round(value):,}"


def fmt_pct(fraction: float, decimals: int = 1) -> str:
    if fraction != fraction or abs(fraction) == float("inf"):
        return "n/a"
    return f"{fraction * 100:.{decimals}f}%"


def fmt_z(z: float | None) -> str:
    return "n/a" if z is None or z != z else f"{z:.1f}"


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """Markdown-style lines; every line starts with '| '."""
    lines = ["| " + " | ".join(cell(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return lines


def finish(lines: list[str]) -> str:
    """Join, keeping whole lines under MAX_RESULT characters; say so when lines were left out."""
    out: list[str] = []
    size = 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > MAX_RESULT - 120:
            out.append(f"[result cut at {MAX_RESULT:,} characters: {len(lines) - i} more line(s) not shown]")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)
