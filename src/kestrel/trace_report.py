"""Reading traces back: list, tree view, stats, and JSONL export for training data."""

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

Out = Callable[[str], None]
DIM, RED, GREEN, RESET = "\033[2m", "\033[31m", "\033[32m", "\033[0m"


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "-"
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    if x == 0:
        return "$0"
    return f"${x:.5f}" if x < 0.01 else f"${x:.4f}"


def fmt_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M")


def short(text: str | None, n: int) -> str:
    if text is None:
        return "(content off)"
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: the value at or below which p% of values fall."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


# --- Queries (shared by the CLI and the web API) ------------------------------


def _trace_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("messages", None)  # the full conversation is only needed for export
    d["providers"] = json.loads(d.get("providers") or "[]")
    d["tokens"] = (d["input_tokens"] or 0) + (d["output_tokens"] or 0)
    d["latency_ms"] = (d["duration_ms"] or 0) - (d["wait_ms"] or 0)  # excludes waiting for approvals
    return d


def list_traces(conn: sqlite3.Connection, limit: int = 20, query: str | None = None) -> list[dict]:
    """Newest first. `query` matches the request, the answer, or the start of the trace id."""
    sql, params = "SELECT * FROM traces", []
    if query:
        like = f"%{query}%"
        sql += " WHERE user_message LIKE ? OR final_answer LIKE ? OR trace_id LIKE ?"
        params += [like, like, query.lower() + "%"]
    sql += " ORDER BY start_time DESC LIMIT ?"
    return [_trace_dict(r) for r in conn.execute(sql, [*params, limit])]


def get_trace(conn: sqlite3.Connection, trace_id: str) -> dict | None:
    """One trace with its spans (attributes parsed), spans in start order."""
    row = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
    if row is None:
        return None
    trace = _trace_dict(row)
    trace["spans"] = [
        dict(s) | {"attributes": json.loads(s["attributes"] or "{}")}
        for s in conn.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time", (trace_id,))
    ]
    return trace


def timeseries(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    """Per-request points for charts, oldest first."""
    rows = conn.execute("SELECT * FROM traces ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall()
    return [
        {k: t[k] for k in ("trace_id", "start_time", "latency_ms", "tokens", "list_price_usd", "status", "rating")}
        for t in map(_trace_dict, reversed(rows))
    ]


# --- kestrel traces -----------------------------------------------------------


def print_traces(conn: sqlite3.Connection, limit: int = 20, out: Out = print) -> None:
    rows = list_traces(conn, limit)
    if not rows:
        out("No traces yet. Chat with Kestrel first: uv run kestrel")
        return
    out(f"{'TIME':<12} {'ID':<8}  {'REQUEST':<42} {'STEPS':>5} {'TOKENS':>7} {'LATENCY':>8}  RATING")
    for r in rows:
        tokens, latency = r["tokens"], r["latency_ms"]
        rating = r["rating"] or ""
        if r["status"] == "error":
            rating = f"{rating} (error)".strip()
        out(
            f"{fmt_time(r['start_time']):<12} {r['trace_id'][:8]:<8}  {short(r['user_message'], 42):<42} "
            f"{r['steps'] or 0:>5} {tokens:>7,} {fmt_ms(latency):>8}  {rating}"
        )
    out(f"{DIM}Latency excludes time spent waiting for your approvals. Details: kestrel trace <id>{RESET}")


# --- kestrel trace <id> -------------------------------------------------------


def _label(span: dict, attrs: dict) -> str:
    name, dur = span["name"], fmt_ms(span["duration_ms"])
    if name == "agent_run":
        tokens = attrs.get("gen_ai.usage.input_tokens", 0) + attrs.get("gen_ai.usage.output_tokens", 0)
        return (
            f"agent_run {dur}  [{attrs.get('kestrel.stop_reason')}, {attrs.get('kestrel.steps')} steps, "
            f"{tokens:,} tokens, list price {fmt_usd(attrs.get('kestrel.list_price_usd'))}]"
        )
    if name == "llm_call":
        est = ", estimated" if attrs.get("kestrel.usage.estimated") else ""
        text = (
            f"llm_call {attrs.get('gen_ai.provider.name')} {dur} "
            f"({attrs.get('gen_ai.usage.input_tokens', '?')} in / "
            f"{attrs.get('gen_ai.usage.output_tokens', '?')} out{est})"
        )
        if calls := attrs.get("kestrel.tool_calls"):
            text += f" -> {', '.join(calls)}"
        elif span["status"] == "ok":
            text += " -> answer"
        if attrs.get("kestrel.retries"):
            text += f" [{attrs['kestrel.retries']} retries]"
        if attrs.get("kestrel.fallback"):
            text += f" [fallback: {' > '.join(attrs.get('kestrel.providers_tried', []))}]"
        return text
    if name == "tool_call":
        took = fmt_ms(attrs["kestrel.tool.exec_ms"]) if "kestrel.tool.exec_ms" in attrs else dur
        tags = attrs.get("kestrel.tool.risk", "?")
        if attrs.get("kestrel.tool.external"):
            tags += f", mcp:{attrs.get('kestrel.tool.server')}"
        text = f"tool_call {attrs.get('gen_ai.tool.name')} [{tags}] {took}"
        if attrs.get("kestrel.tool.ran") is False:
            text += " (not run)"
        return text
    if name == "approval":
        reason = attrs.get("kestrel.approval.reason")
        return f"approval {attrs.get('kestrel.approval.decision')} {dur} (your time)" + (
            f": {reason!r}" if reason else ""
        )
    return f"{name} {dur}"


def print_trace(conn: sqlite3.Connection, trace_id: str, out: Out = print) -> None:
    trace = get_trace(conn, trace_id)
    if trace is None:
        out(f"No trace {trace_id}")
        return
    children: dict[str | None, list[dict]] = defaultdict(list)
    for s in trace["spans"]:
        children[s["parent_id"]].append(s)

    out(
        f"Trace {trace_id}  {datetime.fromtimestamp(trace['start_time']):%Y-%m-%d %H:%M:%S}"
        + (
            f"  rated {trace['rating']}" + (f": {trace['rating_note']!r}" if trace["rating_note"] else "")
            if trace["rating"]
            else ""
        )
    )
    out(f"  you > {short(trace['user_message'], 100)}")
    out(f"  kestrel > {short(trace['final_answer'], 100)}\n")

    def render(span: dict, prefix: str, connector: str) -> None:
        attrs = span["attributes"]
        line = connector + _label(span, attrs)
        if span["status"] == "error":
            line += f"  {RED}ERROR: {short(span['error'], 80)}{RESET}"
        out(prefix + line)
        kids = children.get(span["span_id"], [])
        child_prefix = prefix + ("" if not connector else ("    " if connector.startswith("└") else "│   "))
        for i, kid in enumerate(kids):
            render(kid, child_prefix, "└─ " if i == len(kids) - 1 else "├─ ")

    for root in children.get(None, []):
        render(root, "", "")


# --- kestrel stats ------------------------------------------------------------


def compute_stats(conn: sqlite3.Connection) -> dict:
    traces = conn.execute("SELECT * FROM traces").fetchall()
    spans = conn.execute("SELECT name, duration_ms, status, attributes FROM spans").fetchall()
    n = len(traces)
    if n == 0:
        return {"traces": 0}

    llm_latency: dict[str, list[float]] = defaultdict(list)
    tools, approvals = Counter[str](), Counter[str]()
    tool_errors = 0
    for s in spans:
        attrs = json.loads(s["attributes"] or "{}")
        if s["name"] == "llm_call" and s["status"] == "ok":
            llm_latency[attrs.get("gen_ai.provider.name") or "unknown"].append(s["duration_ms"] or 0)
        elif s["name"] == "tool_call":
            tools[attrs.get("gen_ai.tool.name") or "?"] += 1
            tool_errors += s["status"] == "error"
        elif s["name"] == "approval":
            approvals[attrs.get("kestrel.approval.decision") or "?"] += 1

    latency = [(t["duration_ms"] or 0) - (t["wait_ms"] or 0) for t in traces]
    tokens = [(t["input_tokens"] or 0) + (t["output_tokens"] or 0) for t in traces]
    list_prices = [t["list_price_usd"] for t in traces if t["list_price_usd"] is not None]
    rated = [t["rating"] for t in traces if t["rating"]]
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    providers = Counter(p for t in traces for p in json.loads(t["providers"] or "[]"))
    per_conversation: dict[str, float] = defaultdict(float)
    for t in traces:
        if t["session_id"] and t["list_price_usd"] is not None:
            per_conversation[t["session_id"]] += t["list_price_usd"]
    return {
        "requests_today": sum(t["start_time"] >= midnight for t in traces),
        "providers": dict(providers.most_common()),
        "list_price_usd_per_conversation": sum(per_conversation.values()) / len(per_conversation)
        if per_conversation
        else None,
        "traces": n,
        "first": min(t["start_time"] for t in traces),
        "last": max(t["start_time"] for t in traces),
        "latency_p50_ms": percentile(latency, 50),
        "latency_p95_ms": percentile(latency, 95),
        "llm_latency_ms": {p: (percentile(v, 50), percentile(v, 95), len(v)) for p, v in sorted(llm_latency.items())},
        "tokens_total": sum(tokens),
        "tokens_per_request": sum(tokens) / n,
        "tokens_estimated_share": sum(t["tokens_estimated"] or 0 for t in traces) / n,
        "steps_per_request": sum(t["steps"] or 0 for t in traces) / n,
        "cost_usd_total": sum(t["cost_usd"] or 0 for t in traces),
        "list_price_usd_total": sum(list_prices) if list_prices else None,
        "list_price_usd_per_request": sum(list_prices) / len(list_prices) if list_prices else None,
        "list_price_coverage": len(list_prices) / n,
        "tool_calls": dict(tools.most_common()),
        "tool_error_rate": tool_errors / sum(tools.values()) if tools else 0.0,
        "approvals": dict(approvals.most_common()),
        "error_rate": sum(t["status"] == "error" for t in traces) / n,
        "fallback_rate": sum(t["fallback"] or 0 for t in traces) / n,
        "rated": len(rated),
        "good_share": rated.count("good") / len(rated) if rated else None,
    }


def print_stats(conn: sqlite3.Connection, out: Out = print) -> None:
    s = compute_stats(conn)
    if not s["traces"]:
        out("No traces yet. Chat with Kestrel first: uv run kestrel")
        return

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    out(f"Traces: {s['traces']}  ({fmt_time(s['first'])} to {fmt_time(s['last'])})\n")
    out(
        f"Latency per request   p50 {fmt_ms(s['latency_p50_ms'])}   p95 {fmt_ms(s['latency_p95_ms'])}"
        f"   {DIM}(excludes your approval time){RESET}"
    )
    for provider, (p50, p95, count) in s["llm_latency_ms"].items():
        out(f"  llm_call {provider:<10} p50 {fmt_ms(p50)}   p95 {fmt_ms(p95)}   ({count} calls)")
    out(
        f"Tokens per request    {s['tokens_per_request']:,.0f}   (total {s['tokens_total']:,}"
        + (f", {pct(s['tokens_estimated_share'])} of requests estimated" if s["tokens_estimated_share"] else "")
        + ")"
    )
    out(f"Steps per request     {s['steps_per_request']:.1f}")
    out(
        f"Cost                  {fmt_usd(s['cost_usd_total'])} actual   "
        f"list price {fmt_usd(s['list_price_usd_per_request'])}/request, {fmt_usd(s['list_price_usd_total'])} total"
        + (
            f"   {DIM}({pct(s['list_price_coverage'])} of requests have a list price){RESET}"
            if s["list_price_coverage"] < 1
            else ""
        )
    )
    tools = ", ".join(f"{k} {v}" for k, v in s["tool_calls"].items()) or "none"
    out(f"Tool calls            {tools}   (error rate {pct(s['tool_error_rate'])})")
    if s["approvals"]:
        out(f"Approvals             {', '.join(f'{k} {v}' for k, v in s['approvals'].items())}")
    out(f"Error rate            {pct(s['error_rate'])}")
    out(f"Fallback rate         {pct(s['fallback_rate'])}")
    out(f"Rated good            {pct(s['good_share'])}   ({s['rated']} of {s['traces']} rated)")


# --- kestrel export -----------------------------------------------------------


def weight_current_turn(messages: list[dict]) -> list[dict]:
    """A trace stores the whole conversation, but its rating is for the last turn only.
    Earlier assistant messages stay as context with weight 0 (the OpenAI fine-tuning
    convention for "don't learn from this"), so a good example never teaches an
    earlier answer that may have been rated bad."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return [
        m | {"weight": 0 if i < last_user else 1} if m.get("role") == "assistant" else m for i, m in enumerate(messages)
    ]


def export(conn: sqlite3.Connection, rated: str, out_path: Path, tools: list[dict]) -> tuple[int, int]:
    """Write traces as chat-format JSONL: {"messages": [...], "tools": [...], "metadata": {...}}.
    Returns (written, skipped). Skipped: no stored messages (content off), errored runs, demo mode."""
    query = "SELECT * FROM traces"
    params: tuple = ()
    if rated in ("good", "bad"):
        query += " WHERE rating = ?"
        params = (rated,)
    query += " ORDER BY start_time"
    written = skipped = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for t in conn.execute(query, params):
            if not t["messages"] or t["status"] == "error" or "demo" in json.loads(t["providers"] or "[]"):
                skipped += 1  # no stored text, a failed run, or scripted demo replies (never training data)
                continue
            record = {
                "messages": weight_current_turn(json.loads(t["messages"])),
                "tools": tools,
                "metadata": {
                    "trace_id": t["trace_id"],
                    "time": datetime.fromtimestamp(t["start_time"]).isoformat(timespec="seconds"),
                    "rating": t["rating"],
                    "rating_note": t["rating_note"],
                    "providers": json.loads(t["providers"] or "[]"),
                    "steps": t["steps"],
                    "stop_reason": t["stop_reason"],
                },
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    return written, skipped
