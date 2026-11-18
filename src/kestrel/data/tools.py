"""The five read-only data tools (docs/data-rca-design.md section 1), as plain functions over a data folder.

The same `DataTools` object answers both the `kestrel-data` MCP server (the shipped opt-in) and the bench, which
registers it in-process (`register_local`), so the bench measures the code that ships. Registration is `register()`
either way: capabilities `reads_local` declared in code (safe tier, no card); the registry adds `reads_untrusted`,
so every result is wrapped as untrusted data, and a call that returns data taints the conversation.

Every successful result starts with an evidence id, `[E1]`, `[E2]`, ... in call order, stamped here, before any
data. Numbers are printed exactly as an answer may quote them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from kestrel.data import analysis as an
from kestrel.data import engine as en
from kestrel.data.engine import DataError, fmt_number, fmt_pct, fmt_z

SERVER = "data"
NAMES = ("describe_dataset", "query", "compare_periods", "detect_anomalies", "decompose_change")
PVM_BY = "price_volume_mix"
TOP = 15
TOP_VALUES = 20
ANOMALY_Z = 3.0
ANOMALY_BASE_DAYS = 28
ANOMALY_STEP = 7
NUMERIC = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
)
DATE_TYPES = ("DATE", "TIMESTAMP")

PERIOD_HELP = "a month 'YYYY-MM', a day 'YYYY-MM-DD', or a range 'YYYY-MM-DD..YYYY-MM-DD' (both ends included)"
ANSWER_CONTRACT = (
    "When you answer why a metric changed, use this format: 'Finding:' (the change, with its evidence id); "
    "'Hypotheses:' a numbered list, each with 'Confidence: high|medium|low' (exactly the label this tool printed for "
    "that candidate, never your own) and 'Evidence: E<n>' (ids of results you got); 'Rejected:' candidates you ruled "
    "out; 'Not checked:' what the data can't show. Quote only numbers the tools printed. If the tool says the total "
    "change is within normal variation, say 'no cause found' and list nothing above low."
)

_DATASET = {"type": "string", "description": "the dataset's name (not a path)"}
_METRIC = {"type": "string", "description": "a measure column, e.g. revenue"}
SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "describe_dataset": (
        "Describe a local dataset (read only): its tables, columns and types, row counts, date range, the dimension "
        "columns with their top values, and the measure columns. Start here.",
        {"type": "object", "properties": {"dataset": _DATASET}, "required": ["dataset"], "additionalProperties": False},
    ),
    "query": (
        "Run one read-only SELECT on a local dataset (DuckDB SQL). Returns at most 50 rows. Nothing can be written, "
        "and no file or network is reachable.",
        {
            "type": "object",
            "properties": {"dataset": _DATASET, "sql": {"type": "string", "description": "one SELECT statement"}},
            "required": ["dataset", "sql"],
            "additionalProperties": False,
        },
    ),
    "compare_periods": (
        "Compare a metric between two periods of a local dataset (read only): both totals, the change, the seasonal "
        "expected value for period_b, the gap from it and its z-score; with `by`, the same per value of a dimension "
        f"(top {TOP} by absolute gap). Periods: {PERIOD_HELP}.",
        {
            "type": "object",
            "properties": {
                "dataset": _DATASET,
                "metric": _METRIC,
                "period_a": {"type": "string", "description": "the base period"},
                "period_b": {"type": "string", "description": "the period to explain"},
                "by": {"type": "string", "description": "optional: a dimension column"},
            },
            "required": ["dataset", "metric", "period_a", "period_b"],
            "additionalProperties": False,
        },
    ),
    "detect_anomalies": (
        "Find days or weeks in a period of a local dataset (read only) whose metric is far from its seasonal "
        f"expected value (robust |z| >= {ANOMALY_Z:g}), optionally per value of a dimension. Periods: {PERIOD_HELP}.",
        {
            "type": "object",
            "properties": {
                "dataset": _DATASET,
                "metric": _METRIC,
                "period": {"type": "string"},
                "by": {"type": "string", "description": "optional: a dimension column"},
                "grain": {"type": "string", "enum": ["day", "week"], "description": "default day"},
            },
            "required": ["dataset", "metric", "period"],
            "additionalProperties": False,
        },
    ),
    "decompose_change": (
        "Split a metric's gap from its seasonal expected value between two periods of a local dataset (read only) "
        f"into candidates: the values of a dimension, or `{PVM_BY}` (price, volume and mix, for revenue). Each "
        "candidate gets its contribution, share of the gap, effect size, consistency and a confidence label computed "
        f"by a fixed rule; contributions sum to the total gap. Periods: {PERIOD_HELP}. {ANSWER_CONTRACT}",
        {
            "type": "object",
            "properties": {
                "dataset": _DATASET,
                "metric": _METRIC,
                "period_a": {"type": "string", "description": "the base period"},
                "period_b": {"type": "string", "description": "the period to explain"},
                "by": {"type": "string", "description": f"a dimension column, or {PVM_BY}"},
            },
            "required": ["dataset", "metric", "period_a", "period_b", "by"],
            "additionalProperties": False,
        },
    ),
}


def kestrel_name(tool: str) -> str:
    return f"{SERVER}__{tool}"


# --- the dataset's shape ------------------------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    type: str

    @property
    def kind(self) -> str:
        base = self.type.split("(")[0].upper()
        if base.startswith(DATE_TYPES):
            return "date"
        if base in NUMERIC:
            return "measure"
        return "dimension"


def _tables(con: Any) -> dict[str, list[Column]]:
    rows = en.execute(
        con,
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position",
    ).fetchall()
    out: dict[str, list[Column]] = {}
    for table, column, kind in rows:
        out.setdefault(str(table), []).append(Column(str(column), str(kind)))
    return out


@dataclass
class Frame:
    table: str
    date: str
    columns: list[Column]

    def kinds(self, kind: str) -> list[str]:
        return [c.name for c in self.columns if c.kind == kind]


def _frame(con: Any, metric: str) -> Frame:
    tables = _tables(con)
    for table, cols in tables.items():
        names = {c.name: c for c in cols}
        dates = [c.name for c in cols if c.kind == "date"]
        if dates and metric in names and names[metric].kind == "measure":
            return Frame(table, dates[0], cols)
    measures = sorted({c.name for cols in tables.values() for c in cols if c.kind == "measure"})
    raise DataError(f"no table has a date column and a measure named {metric!r}. Measures: {', '.join(measures)}")


def _q(name: str) -> str:
    return en.quote_ident(name)


def _day_range(con: Any, fr: Frame) -> tuple[date, date]:
    lo, hi = en.execute(
        con, f"SELECT min(CAST({_q(fr.date)} AS DATE)), max(CAST({_q(fr.date)} AS DATE)) FROM {_q(fr.table)}"
    ).fetchone()
    if lo is None:
        raise DataError(f"table {fr.table!r} has no dates")
    return lo, hi


def _series(con: Any, fr: Frame, value_sql: str, day0: date, n: int, by: str | None = None) -> dict[str, an.Series]:
    """Daily sums of value_sql, per value of `by` (or one series under the key ''), over n days from day0."""
    import numpy as np

    group = f", CAST({_q(by)} AS VARCHAR)" if by else ""
    sql = (
        f"SELECT date_diff('day', DATE '{day0.isoformat()}', CAST({_q(fr.date)} AS DATE)) AS d{group}, "
        f"SUM({value_sql}) FROM {_q(fr.table)} GROUP BY ALL"
    )
    rows = en.execute(con, sql).fetchall()
    arrays: dict[str, Any] = {}
    for row in rows:
        d, v = int(row[0]), row[-1]
        key = ("NULL" if row[1] is None else str(row[1])) if by else ""
        arr = arrays.setdefault(key, np.zeros(n))
        if 0 <= d < n and v is not None:
            arr[d] += float(v)
    weekday0 = day0.weekday()
    return {k: an.Series(v, weekday0) for k, v in sorted(arrays.items())}


def parse_period(text: str, day0: date, last: date) -> tuple[date, date]:
    if not isinstance(text, str) or not text.strip():
        raise DataError(f"a period is {PERIOD_HELP}")
    t = text.strip()
    try:
        if ".." in t:
            a, b = (date.fromisoformat(x.strip()) for x in t.split("..", 1))
        elif len(t) == 7:
            a = date.fromisoformat(t + "-01")
            nxt = date(a.year + (a.month == 12), a.month % 12 + 1, 1)
            b = nxt - timedelta(days=1)
        else:
            a = b = date.fromisoformat(t)
    except ValueError:
        raise DataError(f"can't read the period {text!r}: use {PERIOD_HELP}") from None
    if b < a:
        raise DataError(f"period {text!r} ends before it starts")
    if a < day0 or b > last:
        raise DataError(f"period {text!r} is outside the data ({day0} to {last})")
    return a, b


def _label_period(a: date, b: date) -> str:
    return a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}"


# --- the tools ----------------------------------------------------------------------------------------------------


class DataTools:
    """The tools over one data folder. Evidence ids count up per instance (one MCP server session, or one bench
    task)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._next = 1
        self._lock = threading.Lock()

    def _stamp(self, tool: str, lines: list[str]) -> str:
        with self._lock:
            n = self._next
            self._next += 1
        return en.finish([f"[E{n}] {kestrel_name(tool)}", *lines])

    def call(self, tool: str, args: dict[str, Any]) -> str:
        if tool not in NAMES:
            raise DataError(f"unknown data tool {tool!r}")
        con = en.open_dataset(self.folder, str(args.get("dataset", "")))
        try:
            fn: Callable[..., list[str]] = getattr(self, f"_{tool}")
            lines = fn(con, **{k: v for k, v in args.items() if k != "dataset"})
        finally:
            con.close()
        return self._stamp(tool, lines)

    # describe_dataset ------------------------------------------------------------------------------------------

    def _describe_dataset(self, con: Any) -> list[str]:
        lines = [f"Dataset tables ({len(_tables(con))}):"]
        tables = _tables(con)
        rows = []
        for table, cols in tables.items():
            count = en.execute(con, f"SELECT count(*) FROM {_q(table)}").fetchone()[0]
            dates = [c.name for c in cols if c.kind == "date"]
            span = "no date column"
            if dates:
                lo, hi = en.execute(
                    con, f"SELECT min(CAST({_q(dates[0])} AS DATE)), max(CAST({_q(dates[0])} AS DATE)) FROM {_q(table)}"
                ).fetchone()
                span = f"{lo} to {hi} ({dates[0]})"
            rows.append((table, f"{count:,}", span))
        lines += en.table(["table", "rows", "date range"], rows)
        for table, cols in tables.items():
            lines.append(f"Columns of {en.cell(table)}:")
            col_rows = []
            for c in cols:
                distinct = ""
                if c.kind == "dimension":
                    distinct = (
                        f"{en.execute(con, f'SELECT count(DISTINCT {_q(c.name)}) FROM {_q(table)}').fetchone()[0]:,}"
                    )
                col_rows.append((c.name, c.type, c.kind, distinct))
            lines += en.table(["column", "type", "kind", "distinct values"], col_rows)
            for c in cols:
                if c.kind != "dimension":
                    continue
                top = en.execute(
                    con,
                    f"SELECT CAST({_q(c.name)} AS VARCHAR) AS v, count(*) AS n FROM {_q(table)} "
                    f"GROUP BY ALL ORDER BY n DESC, v LIMIT {TOP_VALUES}",
                ).fetchall()
                lines.append(f"Top values of {en.cell(table)}.{en.cell(c.name)} (rows):")
                lines += en.table(["value", "rows"], [(v, f"{n:,}") for v, n in top])
        return lines

    # query -----------------------------------------------------------------------------------------------------

    def _query(self, con: Any, sql: str) -> list[str]:
        columns, rows, more = en.run_select(con, sql)
        lines = [f"{len(rows)} row(s)" + (f" shown (the first {en.MAX_ROWS}); more rows not shown" if more else "")]
        return lines + en.table(columns, rows)

    # shared -----------------------------------------------------------------------------------------------------

    def _setup(self, con: Any, metric: str, by: str | None) -> tuple[Frame, date, date, int]:
        fr = _frame(con, str(metric))
        if by is not None and by != PVM_BY and by not in fr.kinds("dimension"):
            raise DataError(f"`by` must be a dimension column of {fr.table!r}: {', '.join(fr.kinds('dimension'))}")
        day0, last = _day_range(con, fr)
        return fr, day0, last, (last - day0).days + 1

    # compare_periods ---------------------------------------------------------------------------------------------

    def _compare_periods(self, con: Any, metric: str, period_a: str, period_b: str, by: str | None = None) -> list[str]:
        if by == PVM_BY:
            raise DataError(f"{PVM_BY} is for decompose_change; give compare_periods a dimension column")
        fr, day0, last, n = self._setup(con, metric, by)
        a, b = parse_period(period_a, day0, last), parse_period(period_b, day0, last)
        wa, wb = an.window(day0, *a), an.window(day0, *b)
        total_s = _series(con, fr, _q(metric), day0, n)[""] if n else None
        assert total_s is not None
        tot = an.total_measures(total_s, wa, wb)
        est = tot.estimate
        change = est.actual - est.base
        lines = [
            f"{metric}: period_a {_label_period(*a)}, period_b {_label_period(*b)}",
            f"Total: period_a {fmt_number(est.base)}; period_b {fmt_number(est.actual)}; change {fmt_number(change)} "
            f"({fmt_pct(change / est.base) if est.base else 'n/a'})",
            f"Seasonal expected value for period_b: {fmt_number(est.expected)} (method: {est.method}; ratio "
            f"{est.ratio:.4f})",
            _gap_line(tot),
        ]
        if by:
            groups = _series(con, fr, _q(metric), day0, n, by)
            cands = an.dimension_candidates(groups, by, wa, wb, tot)
            shown, _, rest = an.top(cands, TOP)
            rows = []
            for cand in shown:
                value = cand.name.split(" = ", 1)[1]
                s = groups[value]
                e = an.estimate(s, wa, wb)
                rows.append(
                    (
                        value,
                        fmt_number(e.base),
                        fmt_number(e.actual),
                        fmt_number(e.expected),
                        fmt_number(e.gap),
                        fmt_pct(e.gap / e.expected) if e.expected else "n/a",
                        fmt_z(cand.z),
                    )
                )
            other = tot.gap - sum(c.contribution for c in shown)
            lines.append(f"By {by} (top {len(shown)} of {len(cands)} by absolute gap):")
            lines += en.table([by, "period_a", "period_b", "expected", "gap", "gap %", "z"], rows)
            if rest:
                lines.append(f"Other ({rest} values, and the per-value estimates' remainder): gap {fmt_number(other)}")
        return lines

    # detect_anomalies --------------------------------------------------------------------------------------------

    def _detect_anomalies(
        self, con: Any, metric: str, period: str, by: str | None = None, grain: str = "day"
    ) -> list[str]:
        if by == PVM_BY:
            raise DataError(f"{PVM_BY} is for decompose_change")
        if grain not in ("day", "week"):
            raise DataError("grain must be 'day' or 'week'")
        fr, day0, last, n = self._setup(con, metric, by)
        p = parse_period(period, day0, last)
        wp = an.window(day0, *p)
        size = 1 if grain == "day" else 7
        points = [an.Window(s, min(s + size - 1, wp.end)) for s in range(wp.start, wp.end + 1, size)]
        groups = _series(con, fr, _q(metric), day0, n, by) if by else _series(con, fr, _q(metric), day0, n)
        found = []
        unknown = 0
        for key, s in groups.items():
            for pt in points:
                base = an.Window(pt.start - ANOMALY_BASE_DAYS, pt.start - 1)
                if base.start < 0:
                    unknown += 1
                    continue
                est = an.estimate(s, base, pt)
                sigma = an.robust_sigma(an.backtest(s, base, pt, est.method, step=ANOMALY_STEP))
                if sigma is None:
                    unknown += 1
                    continue
                z = est.gap / sigma
                if abs(z) >= ANOMALY_Z:
                    found.append((key, pt, est, z))
        found.sort(key=lambda x: (-abs(x[3]), x[1].start, x[0]))
        lines = [
            f"{metric}, {_label_period(*p)}, by {grain}"
            + (f" and {by}" if by else "")
            + f": {len(found)} point(s) with |z| >= {ANOMALY_Z:g}"
        ]
        if unknown:
            lines.append(f"{unknown} point(s) had too little history for a noise estimate and were not tested")
        rows = []
        for key, pt, est, z in found[:TOP]:
            when = (day0 + timedelta(days=pt.start)).isoformat()
            if pt.days > 1:
                when += f" to {(day0 + timedelta(days=pt.end)).isoformat()}"
            rows.append(
                ((key,) if by else ())
                + (when, fmt_number(est.actual), fmt_number(est.expected), fmt_number(est.gap), fmt_z(z))
            )
        if rows:
            lines += en.table(([by] if by else []) + [grain, "value", "expected", "gap", "z"], rows)
            if len(found) > TOP:
                lines.append(f"{len(found) - TOP} more point(s) not shown")
        return lines

    # decompose_change --------------------------------------------------------------------------------------------

    def _decompose_change(self, con: Any, metric: str, period_a: str, period_b: str, by: str) -> list[str]:
        fr, day0, last, n = self._setup(con, metric, by)
        a, b = parse_period(period_a, day0, last), parse_period(period_b, day0, last)
        wa, wb = an.window(day0, *a), an.window(day0, *b)
        total_s = _series(con, fr, _q(metric), day0, n)[""]
        tot = an.total_measures(total_s, wa, wb)
        if by == PVM_BY:
            cols = {c.name for c in fr.columns}
            if not {"units", "product"} <= cols or not ({"price", "revenue"} & cols):
                raise DataError(f"{PVM_BY} needs columns product, units and price (or revenue)")
            rev = _q("revenue") if "revenue" in cols else f"{_q('units')} * {_q('price')}"
            if metric != "revenue" and rev != _q(metric):
                raise DataError(f"{PVM_BY} explains revenue; use metric 'revenue'")
            units = _series(con, fr, _q("units"), day0, n, "product")
            revenue = _series(con, fr, rev, day0, n, "product")
            keys = sorted(units)
            cands = an.pvm_candidates([units[k] for k in keys], [revenue[k] for k in keys], total_s, wa, wb, tot)
            rest, rest_n = 0.0, 0
        else:
            groups = _series(con, fr, _q(metric), day0, n, by)
            cands = an.dimension_candidates(groups, by, wa, wb, tot)
            cands, _, rest_n = an.top(cands, TOP)
            rest = tot.gap - sum(c.contribution for c in cands)
        an.finish(cands, tot)
        cands = an.order(cands)
        unit = "weeks" if cands and cands[0].subs and wb.days // 7 >= an.MIN_WEEKS else "days"
        lines = [
            f"{metric}, period_a {_label_period(*a)} vs period_b {_label_period(*b)}, by {by}",
            f"Seasonal expected value for period_b: {fmt_number(tot.estimate.expected)} (method: "
            f"{tot.estimate.method}); actual {fmt_number(tot.estimate.actual)}",
            _gap_line(tot),
        ]
        rows = [
            (
                c.name,
                fmt_number(c.contribution),
                fmt_pct(c.share),
                fmt_z(c.z),
                f"{c.same} of {c.subs} {unit}",
                c.label,
                f"{c.score:.3f}",
            )
            for c in cands
        ]
        lines += en.table(
            ["candidate", "contribution", "share of gap", "effect size z", "consistent", "confidence", "score"], rows
        )
        if rest_n or abs(rest) >= 0.005:  # the per-value estimates don't add up to the total's by themselves
            lines.append(
                f"other ({rest_n} values not shown, and the per-value estimates' remainder): contribution "
                f"{fmt_number(rest)}"
            )
        cut = an.CUTOFFS
        lines.append(
            "Contributions sum to the total gap. Confidence is a fixed rule, never an opinion: high = |total z| >= "
            f"{cut.z_total:g}, share >= {fmt_pct(cut.high_share, 0)}, |z| >= {cut.high_z:g}, consistent >= "
            f"{fmt_pct(cut.high_consistency, 0)}; medium = share >= {fmt_pct(cut.medium_share, 0)}, |z| >= "
            f"{cut.medium_z:g}, consistent >= {fmt_pct(cut.medium_consistency, 0)}; otherwise low. Score orders "
            "candidates within a label only."
        )
        return lines


def _gap_line(tot: an.Total) -> str:
    est = tot.estimate
    pct = fmt_pct(tot.gap / est.expected) if est.expected else "n/a"
    if tot.sigma is None:
        return (
            f"Gap from expected: {fmt_number(tot.gap)} ({pct} of expected); z n/a: too little history for a "
            f"noise estimate ({tot.pairs} earlier window pair(s), at least {an.MIN_PAIRS} needed), so every "
            "confidence is low"
        )
    note = "" if tot.significant else "; total change within normal variation"
    return (
        f"Gap from expected: {fmt_number(tot.gap)} ({pct} of expected); z {fmt_z(tot.z)} (noise from "
        f"{tot.pairs} earlier window pairs){note}"
    )


# --- registration -------------------------------------------------------------------------------------------------


def describe() -> dict[str, Any]:
    """For the agent fingerprint of an opt-in run: what the data tools are and how they're declared."""
    import hashlib
    import json

    blob = json.dumps({k: list(v) for k, v in SCHEMAS.items()}, sort_keys=True)
    return {
        "tools": [kestrel_name(t) for t in NAMES],
        "capabilities": ["reads_local"],
        "cutoffs": an.CUTOFFS.__dict__,
        "caps": {"rows": en.MAX_ROWS, "cell": en.MAX_CELL, "result": en.MAX_RESULT},
        "schemas": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12],
    }


def register(
    registry: Any,
    call: Callable[[str, dict[str, Any]], str],
    available: Callable[[], bool] | None = None,
) -> list[Any]:
    """The 5 tools, as one MCP server's tools: declared reads_local (safe tier, tainting; the registry adds
    reads_untrusted, so every result is wrapped as untrusted data)."""
    from kestrel.tools import ExternalToolError

    added = []
    for name in NAMES:
        description, schema = SCHEMAS[name]

        def run(_name: str = name, **args: Any) -> str:
            try:
                return call(_name, args)
            except DataError as e:  # the same shape as an MCP tool error: wrapped as untrusted by the registry
                raise ExternalToolError(str(e)) from None

        added.append(
            registry.register_external(
                kestrel_name(name),
                run,
                f"[MCP server '{SERVER}'] {description}",
                schema,
                capabilities={"reads_local"},
                server=SERVER,
                annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                available=available,
            )
        )
    return added


def register_local(registry: Any, folder: Path) -> DataTools:
    """In-process (the bench): the same functions the server runs, over `folder`."""
    tools = DataTools(folder)
    register(registry, tools.call)
    return tools
