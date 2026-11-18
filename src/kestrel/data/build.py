"""Build a dataset: CSV or Parquet files into `<name>.duckdb` in the data folder (`kestrel-data build`).

The only place a normal (unlocked) DuckDB connection is used, and never as a tool: the owner runs it. Each file
becomes one table named after the file (lower-case, other characters as `_`). An existing dataset is replaced only
with --replace. The tools then open the file locked (engine.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from kestrel.data import engine as en


def table_name(path: Path) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", path.stem.lower()) or "t"
    return name if not name[0].isdigit() else f"t_{name}"


def build(folder: Path, name: str, sources: list[Path], replace: bool = False) -> Path:
    import duckdb

    if not en.NAME.fullmatch(name):
        raise en.DataError(f"dataset name must match {en.NAME.pattern}, not {name!r}")
    if not sources:
        raise en.DataError("give at least one CSV or Parquet file")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}{en.SUFFIX}"
    if target.exists() and not replace:
        raise en.DataError(f"{target.name} already exists; use --replace to rebuild it")
    tmp = folder / f".{name}.building{en.SUFFIX}"
    tmp.unlink(missing_ok=True)
    seen: set[str] = set()
    con = duckdb.connect(str(tmp))
    try:
        for src in sources:
            src = Path(src).resolve()
            if not src.is_file():
                raise en.DataError(f"no such file: {src}")
            table = table_name(src)
            if table in seen:
                raise en.DataError(f"two files would both become table {table!r}")
            seen.add(table)
            reader = "read_parquet" if src.suffix.lower() == ".parquet" else "read_csv"
            options = "" if reader == "read_parquet" else ", header = true, all_varchar = false"
            con.execute(f"CREATE TABLE {en.quote_ident(table)} AS SELECT * FROM {reader}(?{options})", [str(src)])
    finally:
        con.close()
    target.unlink(missing_ok=True)
    tmp.replace(target)
    return target
