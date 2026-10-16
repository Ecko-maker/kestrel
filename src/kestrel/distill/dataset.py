"""Build a supervised fine-tuning (SFT) dataset from Kestrel traces (Phase 4, step 1). No model calls.

Input: a traces database (the SQLite format of tracing.py: logs/traces.db for chat, logs/bench.db for
KestrelBench) and, optionally, bench results files, which say which traces passed their grader:
each `tasks[]` entry lists its `trace_ids` with the task's status and score.

Every trace goes through these filters in order, and the first one it fails is counted:
  no_messages      the trace stored no conversation (KESTREL_TRACE_CONTENT=off)
  error_or_demo    the run errored, or the scripted demo provider answered (never training data)
  not_graded       no bench verdict for it (and not a chat trace rated /good with --include-rated)
  failed_grader    the bench graded it fail / error / excluded / skipped, or below --min-score
  closed_teacher   a closed model (Gemini, Claude, GPT) answered at least one step. Never trained on,
                   whatever the flags say: design decision 3 (the teacher is an open-weight model)
  not_teacher      some step was answered by a provider/model outside --provider / --model
  safety_split     a safety-split run (attack fixtures), unless --include-safety
  secret_canary    a planted KCAN- canary, in any form the bench's leak detector decodes
  secret_key       anything tracing.redact() would change (key patterns, NAME=secret, secret env
                   values loaded in this process), or a [REDACTED] marker left by the tracer
  pii              a real-looking email address (outside the reserved example domains), or a line
                   of --deny-file
  too_long         estimated tokens (messages + tool schemas) over --max-tokens
  duplicate        the same conversation as an earlier example (tool-call ids ignored)
The secret filters reject the whole example: a redacted copy would still teach the conversation
around the secret, and a canary that slipped past redaction would be memorized.

Output (in --out): train.jsonl and val.jsonl in the chat format of `kestrel export` ({"messages",
"tools", "metadata"}, earlier turns at weight 0), split by task so that repeats of one task never
land on both sides, and stats.json with the numbers printed at the end.

    uv run python -m kestrel.distill.dataset --db <copy of bench.db> --results <results.json> --out data/sft

Pass a copy of a traces database, not the live one: it is opened read-only, but a copy can't change
while it is read.
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from kestrel.agent import estimate_tokens
from kestrel.bench.canaries import CANARY, variants
from kestrel.trace_report import percentile, weight_current_turn
from kestrel.tracing import REDACTED, redact

# Filter names in the order they run (see the module docstring).
FILTERS = (
    "no_messages",
    "error_or_demo",
    "not_graded",
    "failed_grader",
    "closed_teacher",
    "not_teacher",
    "safety_split",
    "secret_canary",
    "secret_key",
    "pii",
    "too_long",
    "duplicate",
)
SECRET_FILTERS = ("secret_canary", "secret_key", "pii")

# Design decision 3: never train on outputs of Claude, GPT or Gemini. Matched on the provider name and
# on the model name (a closed model can be reached through another provider). gpt-oss is open-weight.
CLOSED_PROVIDERS = frozenset({"gemini", "google", "anthropic", "claude", "openai"})
_CLOSED_MODEL = re.compile(r"^(?:[\w.-]+/)?(?:gemini|claude|gpt-(?!oss)|o\d)", re.IGNORECASE)

# A canary's prefix starting a word: also catches a canary split into pieces or with its payload
# mangled, which the full KCAN-<12 hex> pattern misses.
_CANARY_PREFIX = re.compile(r"\bkcan", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# RFC 2606 / 6761 names: addresses there can't belong to anyone (the bench fixtures use them).
_RESERVED_TLDS = ("example", "test", "invalid", "localhost")
_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


@dataclass(frozen=True)
class Verdict:
    """What the bench grader said about the run a trace belongs to."""

    task_id: str
    status: str  # pass / fail / error / excluded / skipped
    score: float
    split: str  # main / safety
    source: str  # results file name


@dataclass(frozen=True)
class Config:
    providers: frozenset[str] = frozenset({"groq"})
    models: frozenset[str] = frozenset({"openai/gpt-oss-120b"})
    min_score: float = 0.0
    include_rated: bool = False  # chat traces rated /good count as passed
    include_safety: bool = False
    max_tokens: int = 16_000
    val_fraction: float = 0.1
    seed: str = "kestrel-distill-v1"
    deny: tuple[str, ...] = ()  # literal strings that drop an example (case-insensitive)


@dataclass
class Example:
    trace_id: str
    group: str  # what the train/val split keeps together: the task id, or the chat's first message
    messages: list[dict]
    metadata: dict[str, Any]
    tokens: int


@dataclass
class Dataset:
    train: list[Example] = field(default_factory=list)
    val: list[Example] = field(default_factory=list)
    seen: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    unknown_tools: Counter[str] = field(default_factory=Counter)  # called in an example, not in the schemas


# --- Reading ------------------------------------------------------------------


def load_verdicts(paths: list[Path]) -> dict[str, Verdict]:
    """trace_id -> verdict, from bench results files. A trace in two files keeps the later file's verdict."""
    verdicts: dict[str, Verdict] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        split = data.get("meta", {}).get("split", "main")
        for task in data.get("tasks", []):
            verdict = Verdict(task["id"], task["status"], float(task.get("score", 0.0)), split, path.name)
            for trace_id in task.get("trace_ids", []):
                verdicts[trace_id] = verdict
    return verdicts


def open_readonly(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise FileNotFoundError(f"no traces database at {db}")
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_traces(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every trace, oldest first, with the provider and model of each of its llm_call steps."""
    steps: dict[str, list[tuple[str, str]]] = {}
    for row in conn.execute("SELECT trace_id, attributes FROM spans WHERE name = 'llm_call' ORDER BY start_time"):
        a = json.loads(row["attributes"] or "{}")
        model = a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or ""
        steps.setdefault(row["trace_id"], []).append((a.get("gen_ai.provider.name") or "", model))
    traces = []
    for row in conn.execute("SELECT * FROM traces ORDER BY start_time"):
        t = dict(row)
        t["llm_steps"] = steps.get(t["trace_id"], [])
        traces.append(t)
    return traces


# --- Filters ------------------------------------------------------------------


def is_closed(provider: str, model: str) -> bool:
    return provider.lower() in CLOSED_PROVIDERS or bool(_CLOSED_MODEL.match(model))


def has_canary(text: str) -> bool:
    """A planted canary, as written or hidden in URL / base64 / hex encodings (bench/canaries.py)."""
    return any(CANARY.search(v) or _CANARY_PREFIX.search(v) for v in variants(text))


def has_secret(text: str) -> bool:
    return REDACTED in text or redact(text) != text


def real_emails(text: str) -> list[str]:
    """Email addresses outside the reserved example domains."""
    found = []
    for m in _EMAIL.finditer(text):
        domain = m.group(1).lower()
        if domain.rsplit(".", 1)[-1] in _RESERVED_TLDS:
            continue
        if any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS):
            continue
        found.append(m.group(0))
    return found


def secret_filter(text: str, deny: tuple[str, ...] = ()) -> str | None:
    """The secret filter an example's text fails, or None if it is clean."""
    if has_canary(text):
        return "secret_canary"
    if has_secret(text):
        return "secret_key"
    lowered = text.lower()
    if real_emails(text) or any(d.lower() in lowered for d in deny):
        return "pii"
    return None


def grader_filter(trace: dict[str, Any], verdict: Verdict | None, config: Config) -> str | None:
    if verdict is None:
        return None if config.include_rated and trace.get("rating") == "good" else "not_graded"
    if verdict.status != "pass" or verdict.score < config.min_score:
        return "failed_grader"
    return None


def teacher_filter(steps: list[tuple[str, str]], config: Config) -> str | None:
    if any(is_closed(p, m) for p, m in steps):
        return "closed_teacher"
    if not steps or any(p not in config.providers or m not in config.models for p, m in steps):
        return "not_teacher"
    return None


def dedupe_key(messages: list[dict]) -> str:
    """Hash of the conversation without tool-call ids (they differ on every run of the same answer)."""

    def strip(m: dict) -> dict:
        m = {k: v for k, v in m.items() if k not in ("tool_call_id", "weight")}
        if m.get("tool_calls"):
            m["tool_calls"] = [{k: v for k, v in tc.items() if k != "id"} for tc in m["tool_calls"]]
        return m

    blob = json.dumps([strip(m) for m in messages], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tools_called(messages: list[dict]) -> set[str]:
    return {
        tc.get("function", {}).get("name", "")
        for m in messages
        for tc in m.get("tool_calls") or []
        if tc.get("function", {}).get("name")
    }


# --- Building -----------------------------------------------------------------


def first_failure(
    trace: dict[str, Any], verdict: Verdict | None, config: Config, schema_tokens: int
) -> tuple[str | None, list[dict]]:
    """(name of the first filter the trace fails or None, its messages)."""
    if not trace.get("messages"):
        return "no_messages", []
    messages = json.loads(trace["messages"])
    providers = json.loads(trace.get("providers") or "[]")
    if trace.get("status") == "error" or "demo" in providers:
        return "error_or_demo", messages
    for reason in (grader_filter(trace, verdict, config), teacher_filter(trace["llm_steps"], config)):
        if reason:
            return reason, messages
    if verdict is not None and verdict.split == "safety" and not config.include_safety:
        return "safety_split", messages
    if reason := secret_filter(json.dumps(messages, ensure_ascii=False), config.deny):
        return reason, messages
    if estimate_tokens(messages) + schema_tokens > config.max_tokens:
        return "too_long", messages
    return None, messages


def split_groups(groups: list[str], val_fraction: float, seed: str) -> set[str]:
    """The groups that go to validation: a fixed, seeded shuffle, so a rebuild gives the same split.
    At least one group when there are two or more, so val is never empty by rounding."""
    if len(groups) < 2 or val_fraction <= 0:
        return set()
    ranked = sorted(groups, key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    n_val = min(len(groups) - 1, max(1, math.ceil(len(groups) * val_fraction)))
    return set(ranked[:n_val])


def build(traces: list[dict[str, Any]], verdicts: dict[str, Verdict], tools: list[dict], config: Config) -> Dataset:
    ds = Dataset(seen=len(traces))
    schema_tokens = len(json.dumps(tools)) // 4
    tool_names = {t.get("function", {}).get("name") for t in tools}
    seen_keys: set[str] = set()
    kept: list[Example] = []
    for trace in traces:
        verdict = verdicts.get(trace["trace_id"])
        reason, messages = first_failure(trace, verdict, config, schema_tokens)
        if reason is None:
            key = dedupe_key(messages)
            reason = "duplicate" if key in seen_keys else None
            seen_keys.add(key)
        if reason:
            ds.dropped[reason] += 1
            continue
        group = (
            verdict.task_id
            if verdict
            else "chat:" + hashlib.sha256((trace["user_message"] or "").encode()).hexdigest()[:12]
        )
        ds.unknown_tools.update(tools_called(messages) - tool_names)
        kept.append(
            Example(
                trace_id=trace["trace_id"],
                group=group,
                messages=weight_current_turn(messages),
                tokens=estimate_tokens(messages) + schema_tokens,
                metadata={
                    "trace_id": trace["trace_id"],
                    "task_id": verdict.task_id if verdict else None,
                    "source": verdict.source if verdict else "rated-good",
                    "score": verdict.score if verdict else None,
                    "time": datetime.fromtimestamp(trace["start_time"]).isoformat(timespec="seconds"),
                    "teacher": sorted({f"{p}/{m}" for p, m in trace["llm_steps"]}),
                    "steps": trace.get("steps"),
                },
            )
        )
    val_groups = split_groups(sorted({e.group for e in kept}), config.val_fraction, config.seed)
    for e in kept:
        (ds.val if e.group in val_groups else ds.train).append(e)
    return ds


# --- Output -------------------------------------------------------------------


def stats(ds: Dataset) -> dict[str, Any]:
    examples = ds.train + ds.val
    tokens = sorted(e.tokens for e in examples)
    per_task = Counter(e.metadata["task_id"] or "(chat)" for e in examples)
    return {
        "traces_seen": ds.seen,
        "examples": len(examples),
        "train": len(ds.train),
        "val": len(ds.val),
        "groups": {"train": len({e.group for e in ds.train}), "val": len({e.group for e in ds.val})},
        "dropped": {name: ds.dropped.get(name, 0) for name in FILTERS},
        "dropped_by_secret_scrub": sum(ds.dropped.get(name, 0) for name in SECRET_FILTERS),
        "tokens": {
            "total": sum(tokens),
            "min": tokens[0] if tokens else None,
            "p50": percentile([float(t) for t in tokens], 50),
            "p90": percentile([float(t) for t in tokens], 90),
            "max": tokens[-1] if tokens else None,
        },
        "per_task": dict(sorted(per_task.items())),
        "unknown_tools": dict(ds.unknown_tools),
    }


def write(ds: Dataset, tools: list[dict], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, examples in (("train", ds.train), ("val", ds.val)):
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for e in examples:
                record = {"messages": e.messages, "tools": tools, "metadata": e.metadata}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = stats(ds)
    (out_dir / "stats.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def print_stats(s: dict[str, Any], out: Any = print) -> None:
    out(f"Traces seen: {s['traces_seen']}  ->  examples: {s['examples']} (train {s['train']}, val {s['val']})")
    out(f"Groups (tasks / chats): train {s['groups']['train']}, val {s['groups']['val']}")
    t = s["tokens"]
    if s["examples"]:
        out(
            f"Tokens (estimated): total {t['total']:,}, min {t['min']:,}, "
            f"p50 {t['p50']:,.0f}, p90 {t['p90']:,.0f}, max {t['max']:,}"
        )
    out("Dropped (first failing filter):")
    for name, n in s["dropped"].items():
        out(f"  {name:<15}{n:>6}")
    out(f"  {'secret scrub':<15}{s['dropped_by_secret_scrub']:>6}  (secret_canary + secret_key + pii)")
    if s["per_task"]:
        out(f"Per task ({len(s['per_task'])}): " + ", ".join(f"{k} {v}" for k, v in s["per_task"].items()))
    if s["unknown_tools"]:
        out(f"WARNING: examples call tools missing from the schema list: {s['unknown_tools']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="traces database (a copy, not the live file)")
    parser.add_argument("--results", type=Path, nargs="*", default=[], help="bench results files with the verdicts")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument("--provider", nargs="+", default=sorted(Config.providers), help="teacher provider(s)")
    parser.add_argument("--model", nargs="+", default=sorted(Config.models), help="teacher model(s)")
    parser.add_argument(
        "--min-score", type=float, default=Config.min_score, help="a passed run must score at least this"
    )
    parser.add_argument("--include-rated", action="store_true", help="chat traces rated /good count as passed")
    parser.add_argument("--include-safety", action="store_true", help="keep safety-split runs (still scrubbed)")
    parser.add_argument("--max-tokens", type=int, default=Config.max_tokens)
    parser.add_argument("--val-fraction", type=float, default=Config.val_fraction)
    parser.add_argument("--seed", default=Config.seed)
    parser.add_argument("--deny-file", type=Path, help="strings that drop an example, one per line (e.g. real names)")
    args = parser.parse_args(argv)

    closed = [p for p in args.provider if is_closed(p, "")] + [m for m in args.model if is_closed("", m)]
    if closed:
        sys.exit(f"Refusing closed-model teacher(s) {closed}: the teacher must be open-weight (design decision 3).")
    deny = ()
    if args.deny_file:
        deny = tuple(line.strip() for line in args.deny_file.read_text(encoding="utf-8").splitlines() if line.strip())
    config = Config(
        providers=frozenset(args.provider),
        models=frozenset(args.model),
        min_score=args.min_score,
        include_rated=args.include_rated,
        include_safety=args.include_safety,
        max_tokens=args.max_tokens,
        val_fraction=args.val_fraction,
        seed=args.seed,
        deny=deny,
    )
    from kestrel.tools import registry  # the schemas `kestrel export` writes, so both outputs agree

    tools = registry.schemas()
    conn = open_readonly(args.db)
    try:
        traces = read_traces(conn)
    finally:
        conn.close()
    ds = build(traces, load_verdicts(args.results), tools, config)
    summary = write(ds, tools, args.out)
    print_stats(summary)
    print(f"Wrote {args.out / 'train.jsonl'}, {args.out / 'val.jsonl'}, {args.out / 'stats.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
