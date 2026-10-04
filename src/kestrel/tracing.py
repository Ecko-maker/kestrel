"""A small tracer: every user request becomes a trace of nested, timed spans in SQLite.

    trace  = one user request (one Agent.run), identified by trace_id
    span   = one timed piece of work inside it: agent_run (the root), llm_call,
             tool_call, approval. Each knows its parent, so they form a tree.

Attribute names follow the OpenTelemetry GenAI conventions where they fit
(gen_ai.provider.name, gen_ai.usage.input_tokens, ...), and Kestrel-specific ones
use a kestrel.* prefix, so traces can be exported to OpenTelemetry/Langfuse later
without touching the code that records them.

Privacy: every value passes through redact() before it is stored, and with
KESTREL_TRACE_CONTENT=off message text, tool arguments and results are not stored
at all, only timings, token counts and outcomes.
"""

import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kestrel.pricing import Prices, cost, load_prices

DEFAULT_DB = Path("logs") / "traces.db"
MAX_ATTR_CHARS = 2_000  # long strings (tool results, answers) are cut in span attributes

# Attributes that hold message text or user data; dropped when content recording is off.
CONTENT_ATTRS = frozenset(
    {
        "kestrel.user_message",
        "kestrel.final_answer",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "kestrel.approval.reason",
        "kestrel.approval.args",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id       TEXT PRIMARY KEY,
    start_time     REAL NOT NULL,
    duration_ms    REAL,
    wait_ms        REAL,       -- time spent waiting on the user's approvals
    status         TEXT,
    stop_reason    TEXT,
    steps          INTEGER,
    user_message   TEXT,
    final_answer   TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    tokens_estimated INTEGER,  -- 1 if any count was estimated instead of reported
    cost_usd       REAL,
    list_price_usd REAL,
    providers      TEXT,       -- JSON list, in order of first use
    fallback       INTEGER,    -- 1 if any llm_call switched provider
    messages       TEXT,       -- JSON: the conversation as of this request (for export)
    rating         TEXT,       -- "good" / "bad" from /good, /bad
    rating_note    TEXT,
    rated_at       REAL,
    session_id     TEXT        -- one conversation (an Agent instance)
);
CREATE TABLE IF NOT EXISTS spans (
    span_id      TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL REFERENCES traces(trace_id),
    parent_id    TEXT,
    name         TEXT NOT NULL,
    start_time   REAL NOT NULL,
    end_time     REAL,
    duration_ms  REAL,
    status       TEXT,
    error        TEXT,
    attributes   TEXT          -- JSON object
);
CREATE INDEX IF NOT EXISTS spans_by_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS traces_by_time ON traces(start_time);
"""

# --- Redaction ----------------------------------------------------------------

_KEY_PATTERN = re.compile(
    r"""
      AIza[0-9A-Za-z_\-]{20,}                 # Google API keys
    | AQ\.[0-9A-Za-z_\-.]{20,}                # Google (new-style) keys
    | gsk_[0-9A-Za-z]{20,}                    # Groq
    | xai-[0-9A-Za-z]{20,}                    # xAI
    | sk-(?:proj-|ant-)?[0-9A-Za-z_\-]{20,}   # OpenAI / Anthropic
    | gh[pousr]_[0-9A-Za-z]{30,}              # GitHub
    | hf_[0-9A-Za-z]{30,}                     # Hugging Face
    | (?i:bearer)\s+[0-9A-Za-z_\-.=]{20,}     # Authorization headers
    """,
    re.VERBOSE,
)
# NAME=value / "name": "value" where the name says it's a secret
_ASSIGNMENT = re.compile(
    r"""(?i)\b([\w-]*(?:api[_-]?key|secret|token|password)[\w-]*["']?\s*[:=]\s*["']?)([^\s"',}]{8,})"""
)
_SECRET_ENV_NAME = re.compile(r"(?i)(key|token|secret|password)")
REDACTED = "[REDACTED]"


def _secret_env_values() -> list[str]:
    """Actual values of secret-looking environment variables (e.g. keys loaded from .env)."""
    return [v for k, v in os.environ.items() if _SECRET_ENV_NAME.search(k) and len(v) >= 8]


def redact(text: str) -> str:
    for value in _secret_env_values():
        text = text.replace(value, REDACTED)
    text = _KEY_PATTERN.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda m: m.group(1) + REDACTED, text)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


# --- Spans and the tracer -----------------------------------------------------


@dataclass
class Span:
    tracer: Tracer
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_time: float = field(default_factory=time.time)
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None
    end_time: float | None = None
    _t0: float = field(default_factory=time.perf_counter)
    duration_ms: float | None = None

    def set(self, key: str, value: Any) -> Span:
        self.tracer._set(self, key, value)
        return self

    def fail(self, error: BaseException | str) -> Span:
        self.status = "error"
        if isinstance(error, BaseException):
            detail = f"{type(error).__name__}: {error}"
            kind = type(error).__name__
        else:
            detail, kind = str(error), "Error"
        self.error = redact(detail)[:MAX_ATTR_CHARS] if self.tracer.record_content else kind
        return self

    def end(self) -> None:
        if self.end_time is None:
            self.end_time = time.time()
            self.duration_ms = (time.perf_counter() - self._t0) * 1000


def _new_id(n_bytes: int) -> str:
    return secrets.token_hex(n_bytes)  # 16 bytes for traces, 8 for spans, as in OpenTelemetry


class Tracer:
    def __init__(
        self, db_path: str | Path | None = DEFAULT_DB, record_content: bool | None = None, prices: Prices | None = None
    ):
        """db_path=None keeps traces in memory only (used when tracing is off)."""
        self.db_path = Path(db_path) if db_path else None
        if record_content is None:
            record_content = os.getenv("KESTREL_TRACE_CONTENT", "on").strip().lower() not in ("off", "0", "false", "no")
        self.record_content = record_content
        self.prices = prices if prices is not None else _safe_load_prices()
        self._spans: dict[str, list[Span]] = {}
        self._lock = threading.Lock()
        self._warned = False

    # Recording ---------------------------------------------------------------

    def start_trace(self, name: str = "agent_run") -> Span:
        span = Span(self, _new_id(16), _new_id(8), None, name)
        with self._lock:
            self._spans[span.trace_id] = [span]
        return span

    def start_span(self, name: str, parent: Span) -> Span:
        span = Span(self, parent.trace_id, _new_id(8), parent.span_id, name)
        with self._lock:
            self._spans.setdefault(parent.trace_id, []).append(span)
        return span

    def _set(self, span: Span, key: str, value: Any) -> None:
        if not self.record_content and key in CONTENT_ATTRS:
            return
        value = redact_value(value)
        if isinstance(value, str) and len(value) > MAX_ATTR_CHARS:
            value = value[:MAX_ATTR_CHARS] + f"...[truncated, {len(value):,} chars]"
        span.attributes[key] = value

    def cost(self, provider: str | None, model: str | None, input_tokens: int, output_tokens: int):
        return cost(self.prices, provider, model, input_tokens, output_tokens)

    def finish_trace(self, root: Span, messages: list[dict] | None = None) -> None:
        """End the root span, roll up totals from its children, and save everything."""
        root.end()
        with self._lock:
            spans = self._spans.pop(root.trace_id, [root])
        for s in spans:
            s.end()  # anything left open (e.g. after an exception) still gets saved

        llm_spans = [s for s in spans if s.name == "llm_call"]
        a = root.attributes

        def total(key: str) -> float | None:
            """Sum of known costs; None if no call had a known price."""
            values = [v for s in llm_spans if (v := s.attributes.get(key)) is not None]
            return sum(values) if values else None

        input_tokens = int(sum(s.attributes.get("gen_ai.usage.input_tokens", 0) for s in llm_spans))
        output_tokens = int(sum(s.attributes.get("gen_ai.usage.output_tokens", 0) for s in llm_spans))
        providers: list[str] = []
        for s in llm_spans:
            if (p := s.attributes.get("gen_ai.provider.name")) and p not in providers:
                providers.append(p)
        row = {
            "trace_id": root.trace_id,
            "start_time": root.start_time,
            "duration_ms": root.duration_ms,
            "wait_ms": sum(s.duration_ms or 0 for s in spans if s.name == "approval"),
            "status": root.status,
            "stop_reason": a.get("kestrel.stop_reason"),
            "steps": a.get("kestrel.steps"),
            "user_message": a.get("kestrel.user_message"),
            "final_answer": a.get("kestrel.final_answer"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_estimated": int(any(s.attributes.get("kestrel.usage.estimated") for s in llm_spans)),
            "cost_usd": total("kestrel.cost_usd"),
            "list_price_usd": total("kestrel.list_price_usd"),
            "providers": json.dumps(providers),
            "fallback": int(any(s.attributes.get("kestrel.fallback") for s in llm_spans)),
            "session_id": a.get("kestrel.session_id"),
            "messages": json.dumps(redact_value(_clean_messages(messages)), ensure_ascii=False)
            if messages is not None and self.record_content
            else None,
        }
        root.set("gen_ai.usage.input_tokens", input_tokens)
        root.set("gen_ai.usage.output_tokens", output_tokens)
        root.set(
            "kestrel.usage.cached_input_tokens",
            int(sum(s.attributes.get("kestrel.usage.cached_input_tokens", 0) for s in llm_spans)),
        )
        root.set("kestrel.cost_usd", row["cost_usd"])
        root.set("kestrel.list_price_usd", row["list_price_usd"])
        self._save(row, spans)

    def _save(self, row: dict, spans: list[Span]) -> None:
        if self.db_path is None:
            return
        try:
            with self.connect() as conn:
                conn.execute(
                    f"INSERT INTO traces ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
                )
                conn.executemany(
                    "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            s.span_id,
                            s.trace_id,
                            s.parent_id,
                            s.name,
                            s.start_time,
                            s.end_time,
                            s.duration_ms,
                            s.status,
                            s.error,
                            json.dumps(s.attributes, ensure_ascii=False, default=str),
                        )
                        for s in spans
                    ],
                )
        except Exception as e:  # tracing must never break the agent
            if not self._warned:
                print(f"[trace] could not save trace: {type(e).__name__}: {e}", file=sys.stderr)
                self._warned = True

    # Reading -----------------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """`with tracer.connect() as conn:` commits on success, rolls back on error, and always
        closes. (sqlite3's own `with` only handles the transaction and leaves the connection open.)"""
        if self.db_path is None:
            raise RuntimeError("this tracer has no database")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(traces)")}
            if "session_id" not in columns:  # databases created before conversations were tracked
                conn.execute("ALTER TABLE traces ADD COLUMN session_id TEXT")
            with conn:  # the transaction
                yield conn
        finally:
            conn.close()

    def find_trace_id(self, prefix: str) -> str:
        """Full trace id from a prefix (like a short git hash). Raises LookupError."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT trace_id FROM traces WHERE trace_id LIKE ? LIMIT 2", (prefix.lower() + "%",)
            ).fetchall()
        if not rows:
            raise LookupError(f"no trace starts with '{prefix}'")
        if len(rows) > 1:
            raise LookupError(f"'{prefix}' matches several traces; use more characters")
        return rows[0]["trace_id"]

    def rate(self, trace_id: str, rating: str, note: str = "") -> None:
        if rating not in ("good", "bad"):
            raise ValueError("rating must be 'good' or 'bad'")
        note = redact(note) if self.record_content else ""
        with self.connect() as conn:
            updated = conn.execute(
                "UPDATE traces SET rating = ?, rating_note = ?, rated_at = ? WHERE trace_id = ?",
                (rating, note, time.time(), trace_id),
            ).rowcount
        if not updated:
            raise LookupError(f"trace {trace_id} not found")


def _clean_messages(messages: list[dict] | None) -> list[dict] | None:
    """Standard chat format only: drop provider extras such as Gemini thought signatures."""
    if messages is None:
        return None
    cleaned = []
    for m in messages:
        m = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id")}
        if m.get("tool_calls"):
            m["tool_calls"] = [{k: tc.get(k) for k in ("id", "type", "function")} for tc in m["tool_calls"]]
        cleaned.append(m)
    return cleaned


def _safe_load_prices() -> Prices:
    try:
        return load_prices()
    except Exception as e:
        print(f"[trace] could not load prices ({e}); costs will be unknown", file=sys.stderr)
        return {}
