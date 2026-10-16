"""`kestrel distill build`: a supervised fine-tuning dataset from Kestrel traces. No model calls.

Input: a copy of a traces database (the SQLite format of tracing.py) and, optionally, bench results
files. Every trace goes through the filters below in order; the first one it fails is counted.

  no_messages           the trace stored no conversation (KESTREL_TRACE_CONTENT=off)
  teacher               a model step not on teachers.toml (Gemini, demo, an unknown model, a fallback)
  contamination_run     the trace belongs to a KestrelBench run (its id is in a results file)
  contamination_prompt  a user message equals a KestrelBench prompt (main suite, safety split, any
                        split), ignoring case and whitespace
  quality               not answered (max_steps / error), rated /bad, or neither rated /good nor part
                        of a bench task that passed (all checks, judge >= 0.5)
  secret_canary         a KCAN- canary, as written or URL / base64 / hex encoded, or its prefix alone
  secret_pattern        anything tracing.redact() would change, or a [REDACTED] marker
  secret_env_line       a .env-style KEY=value line with a secret-sounding name
  secret_env_value      the value of a secret-named environment variable set now
  too_long              estimated tokens over --max-tokens (dropped, never truncated)
  duplicate             the same conversation as an earlier example

The teacher, contamination and secret filters have no override. KestrelBench is the student's deploy
gate, so a trace from it (or with one of its prompts) would make that gate meaningless. Bench traces
are therefore always dropped, and the "bench task passed" half of the quality rule only matters for
graded sources that aren't KestrelBench runs; today, only chat traces rated /good get through.

Output in --out: train.jsonl and val.jsonl in the shape of `kestrel export` (trace_report.export_record),
split by session so a conversation never lands on both sides, and manifest.json with the inputs, the
settings, the drops per filter and the stats. Two builds of the same inputs give identical files.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import yaml

from kestrel.agent import estimate_tokens
from kestrel.trace_report import export_record, percentile
from kestrel.tracing import REDACTED, redact

ROOT = Path(__file__).resolve().parents[3]
TEACHERS_FILE = Path(__file__).with_name("teachers.toml")
BENCH_DIR = ROOT / "evals" / "kestrelbench"  # every task folder in it: tasks/, safety/, any future split
LIVE_DBS = (ROOT / "logs" / "traces.db", ROOT / "logs" / "bench.db")
SPLIT_SALT = "2026"

FILTERS = (
    "no_messages",
    "teacher",
    "contamination_run",
    "contamination_prompt",
    "quality",
    "secret_canary",
    "secret_pattern",
    "secret_env_line",
    "secret_env_value",
    "too_long",
    "duplicate",
)
REJECTED = frozenset({"rejected", "forbidden", "refused"})  # approval decisions where the call didn't run

# Key decision 3: no training on Claude, GPT or Gemini outputs; the demo provider is not a model.
_CLOSED_PROVIDERS = frozenset({"gemini", "google", "anthropic", "claude", "openai", "demo"})
_CLOSED_MODEL = re.compile(r"^(?:[\w.-]+/)?(?:gemini|claude|gpt-(?!oss)|o\d|demo)", re.IGNORECASE)

_CANARY = re.compile(r"KCAN-[0-9a-f]{12}|\bkcan", re.IGNORECASE)  # the prefix alone catches split canaries
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/_-]{12,}={0,2}")
_HEX_TOKEN = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_SECRET_NAME = r"[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9_]*"
_ENV_LINE = re.compile(rf"(?im)^\s*(?:export\s+)?{_SECRET_NAME}\s*=\s*\S+")
_SECRET_ENV_NAME = re.compile(r"(?i)(key|token|secret|password)")  # the rule tracing.py redacts by


# --- Inputs -------------------------------------------------------------------


def is_closed(provider: str, model: str) -> bool:
    return provider.lower() in _CLOSED_PROVIDERS or bool(_CLOSED_MODEL.match(model))


def load_teachers(path: Path = TEACHERS_FILE) -> frozenset[tuple[str, str]]:
    with path.open("rb") as f:
        entries = tomllib.load(f).get("teacher", [])
    teachers = frozenset((str(e["provider"]), str(e["model"])) for e in entries)
    if not teachers:
        raise ValueError(f"{path}: no [[teacher]] entries")
    if closed := sorted(t for t in teachers if is_closed(*t)):
        raise ValueError(f"{path}: {closed} can't be a teacher: open-weight models only (key decision 3)")
    return teachers


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def bench_prompts(bench_dir: Path = BENCH_DIR) -> frozenset[str]:
    """Every user prompt of every KestrelBench task file (all turns), normalized. Fixture files in
    workspace/ are data the tasks read, not prompts."""

    def collect(node: Any) -> list[str]:
        if isinstance(node, dict):
            found = []
            for key, value in node.items():
                if key == "prompt":
                    found += [value] if isinstance(value, str) else [v for v in value or [] if isinstance(v, str)]
                else:
                    found += collect(value)
            return found
        if isinstance(node, list):
            return [p for item in node for p in collect(item)]
        return []

    prompts: set[str] = set()
    for path in sorted(bench_dir.rglob("*.y*ml")):
        if "workspace" in path.relative_to(bench_dir).parts:
            continue
        prompts.update(normalize(p) for p in collect(yaml.safe_load(path.read_text(encoding="utf-8"))))
    return frozenset(prompts)


@dataclass(frozen=True)
class Verdict:
    task_id: str
    passed: bool  # all checks passed and (no judge, or judge >= 0.5)


def task_passed(task: dict[str, Any]) -> bool:
    """The bench's pass rule (runner.py), re-checked from the stored checks and judge score."""
    judge = task.get("judge")
    return (
        task.get("status") == "pass"
        and all(c.get("ok") for c in task.get("checks", []))
        and (judge is None or (judge.get("score") or 0) >= 0.5)
    )


def load_verdicts(paths: list[Path]) -> dict[str, Verdict]:
    """trace_id -> verdict for every trace listed in bench results files, whatever its status."""
    verdicts: dict[str, Verdict] = {}
    for path in paths:
        for task in json.loads(path.read_text(encoding="utf-8")).get("tasks", []):
            for trace_id in task.get("trace_ids", []):
                verdicts[trace_id] = Verdict(task["id"], task_passed(task))
    return verdicts


def open_readonly(db: Path) -> sqlite3.Connection:
    resolved = db.resolve()
    if resolved in LIVE_DBS or resolved.name == "bench.db":
        raise ValueError(
            f"{db}: pass a copy, not a live database ({'logs/' + resolved.name}). bench.db holds only "
            "KestrelBench runs, which are never training data."
            if resolved.name == "bench.db"
            else f"{db}: pass a copy, not the live logs/traces.db (it may be written while it is read)."
        )
    if not resolved.is_file():
        raise FileNotFoundError(f"no traces database at {db}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_traces(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every trace, oldest first, with each model step's (provider, models) and its approval decisions."""
    steps: dict[str, list[tuple[str, frozenset[str]]]] = {}
    decisions: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT trace_id, name, attributes FROM spans WHERE name IN ('llm_call', 'approval') "
        "ORDER BY start_time, span_id"
    ):
        a = json.loads(row["attributes"] or "{}")
        if row["name"] == "llm_call":
            models = frozenset(m for m in (a.get("gen_ai.request.model"), a.get("gen_ai.response.model")) if m)
            steps.setdefault(row["trace_id"], []).append((a.get("gen_ai.provider.name") or "", models))
        else:
            decisions.setdefault(row["trace_id"], []).append(a.get("kestrel.approval.decision") or "")
    traces = []
    for row in conn.execute("SELECT * FROM traces ORDER BY start_time, trace_id"):
        t = dict(row)
        t["llm_steps"] = steps.get(t["trace_id"], [])
        t["approvals"] = decisions.get(t["trace_id"], [])
        traces.append(t)
    return traces


# --- Filters ------------------------------------------------------------------


def teacher_ok(steps: list[tuple[str, frozenset[str]]], teachers: frozenset[tuple[str, str]]) -> bool:
    """Every step answered by a teacher. A step with no recorded model, or no steps at all, fails."""
    return bool(steps) and all(models and all((p, m) in teachers for m in models) for p, models in steps)


def quality_ok(trace: dict[str, Any], verdict: Verdict | None) -> bool:
    if trace.get("stop_reason") != "answered" or trace.get("status") == "error" or trace.get("rating") == "bad":
        return False
    return trace.get("rating") == "good" or (verdict is not None and verdict.passed)


def text_of(messages: list[dict]) -> str:
    """Every string in the example, one per line: system prompt, user turns, tool-call arguments,
    tool results and the answer. Built from the values, not json.dumps, so quotes and newlines in a
    secret aren't escaped out of recognition."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(messages)
    return "\n".join(parts)


def _decoded(text: str) -> list[str]:
    """The text, URL-decoded twice, plus whatever its base64 and hex tokens decode to."""
    once = unquote_plus(text)
    forms = [text, once, unquote_plus(once)]
    out = list(forms)
    for form in forms:
        for token in _B64_TOKEN.findall(form):
            for decode in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    out.append(decode(token + "=" * (-len(token) % 4)).decode("utf-8", errors="ignore"))
                except binascii.Error, ValueError:
                    continue
        for token in _HEX_TOKEN.findall(form):
            out.append(bytes.fromhex(token).decode("utf-8", errors="ignore"))
    return out


def secret_env_values() -> list[str]:
    """Values of secret-named environment variables, the same rule tracing.py redacts by. Only ever
    compared, never printed."""
    return [v for k, v in os.environ.items() if _SECRET_ENV_NAME.search(k) and len(v) >= 8]


def secret_filter(text: str, env_values: list[str]) -> str | None:
    if any(_CANARY.search(form) for form in _decoded(text)):
        return "secret_canary"
    if REDACTED in text or redact(text) != text:
        return "secret_pattern"
    if _ENV_LINE.search(text):
        return "secret_env_line"
    if any(v in text for v in env_values):
        return "secret_env_value"
    return None


def dedupe_key(messages: list[dict]) -> str:
    """Hash of the conversation with tool-call ids dropped (new on every run) and whitespace collapsed."""

    def norm(node: Any) -> Any:
        if isinstance(node, str):
            return " ".join(node.split())
        if isinstance(node, dict):
            return {k: norm(v) for k, v in node.items() if k not in ("id", "tool_call_id", "weight")}
        if isinstance(node, list):
            return [norm(v) for v in node]
        return node

    return hashlib.sha256(json.dumps(norm(messages), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def in_val(session: str, val_frac: float) -> bool:
    digest = hashlib.sha256(f"{SPLIT_SALT}:{session}".encode()).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000 < val_frac


# --- Build --------------------------------------------------------------------


@dataclass
class Example:
    record: dict[str, Any]
    session: str
    tokens: int
    tools_used: frozenset[str]
    approvals: list[str]


@dataclass
class Dataset:
    train: list[Example] = field(default_factory=list)
    val: list[Example] = field(default_factory=list)
    seen: int = 0
    dropped: Counter[str] = field(default_factory=Counter)


def build(
    traces: list[dict[str, Any]],
    verdicts: dict[str, Verdict],
    prompts: frozenset[str],
    teachers: frozenset[tuple[str, str]],
    tools: list[dict],
    *,
    max_tokens: int = 8000,
    val_frac: float = 0.1,
) -> Dataset:
    ds = Dataset(seen=len(traces))
    schema_tokens = len(json.dumps(tools)) // 4  # how the agent estimates the schemas' share
    env_values = secret_env_values()
    seen_keys: set[str] = set()
    for trace in traces:
        reason, example = _check(trace, verdicts, prompts, teachers, tools, env_values, schema_tokens, max_tokens)
        if example is not None:
            key = dedupe_key(example.record["messages"])
            reason = "duplicate" if key in seen_keys else None
            seen_keys.add(key)
        if reason or example is None:
            ds.dropped[reason or "no_messages"] += 1
            continue
        (ds.val if in_val(example.session, val_frac) else ds.train).append(example)
    return ds


def _check(
    trace: dict[str, Any],
    verdicts: dict[str, Verdict],
    prompts: frozenset[str],
    teachers: frozenset[tuple[str, str]],
    tools: list[dict],
    env_values: list[str],
    schema_tokens: int,
    max_tokens: int,
) -> tuple[str | None, Example | None]:
    """(first failing filter, None) or (None, the example)."""
    if not trace.get("messages"):
        return "no_messages", None
    if not teacher_ok(trace["llm_steps"], teachers):
        return "teacher", None
    verdict = verdicts.get(trace["trace_id"])
    if verdict is not None:
        return "contamination_run", None
    messages = json.loads(trace["messages"])
    users = [m.get("content") for m in messages if m.get("role") == "user"] + [trace.get("user_message")]
    if any(isinstance(u, str) and normalize(u) in prompts for u in users):
        return "contamination_prompt", None
    if not quality_ok(trace, verdict):
        return "quality", None
    if reason := secret_filter(text_of(messages), env_values):
        return reason, None
    tokens = estimate_tokens(messages) + schema_tokens
    if tokens > max_tokens:
        return "too_long", None
    record = export_record(trace, tools)
    record["metadata"]["session_id"] = trace.get("session_id")
    called = {
        tc.get("function", {}).get("name", "")
        for m in messages
        for tc in m.get("tool_calls") or []
        if tc.get("function", {}).get("name")
    }
    return None, Example(
        record, trace.get("session_id") or trace["trace_id"], tokens, frozenset(called), trace["approvals"]
    )


# --- Output -------------------------------------------------------------------


def stats(ds: Dataset) -> dict[str, Any]:
    examples = ds.train + ds.val
    tokens = [float(e.tokens) for e in examples]
    per_tool = Counter(name for e in examples for name in e.tools_used)
    return {
        "traces_seen": ds.seen,
        "kept": len(examples),
        "train": len(ds.train),
        "val": len(ds.val),
        "dropped": {name: ds.dropped.get(name, 0) for name in FILTERS},
        "sessions": {
            "total": len({e.session for e in examples}),
            "train": len({e.session for e in ds.train}),
            "val": len({e.session for e in ds.val}),
        },
        "tokens": {"p50": percentile(tokens, 50), "p90": percentile(tokens, 90), "max": max(tokens, default=None)},
        "examples_per_tool": dict(sorted(per_tool.items())),
        "with_approval": sum(1 for e in examples if e.approvals),
        "with_rejection": sum(1 for e in examples if REJECTED & set(e.approvals)),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit() -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
    except OSError, subprocess.CalledProcessError:
        return {"commit": None, "dirty": None}
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def write(ds: Dataset, out_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, examples in (("train", ds.train), ("val", ds.val)):
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for e in examples:
                f.write(json.dumps(e.record, ensure_ascii=False) + "\n")
    manifest = manifest | {"stats": stats(ds)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def print_stats(s: dict[str, Any], out: Any = print) -> None:
    out(f"Traces: {s['traces_seen']} -> kept {s['kept']} (train {s['train']}, val {s['val']})")
    out(f"Sessions: {s['sessions']['total']} (train {s['sessions']['train']}, val {s['sessions']['val']})")
    out("Dropped, in pipeline order:")
    for name, n in s["dropped"].items():
        out(f"  {name:<22}{n:>6}")
    t = s["tokens"]
    if s["kept"]:
        out(f"Tokens (estimated): p50 {t['p50']:,.0f}, p90 {t['p90']:,.0f}, max {t['max']:,.0f}")
        out("Examples per tool: " + (", ".join(f"{k} {v}" for k, v in s["examples_per_tool"].items()) or "none"))
    out(f"With an approval card: {s['with_approval']}, with a rejection: {s['with_rejection']}")


# --- CLI ----------------------------------------------------------------------


def add_parser(sub: Any) -> None:
    p = sub.add_parser("distill", help="build a fine-tuning dataset from teacher traces (Phase 4)")
    dsub = p.add_subparsers(dest="distill_command", required=True)
    b = dsub.add_parser("build", help="filter traces into train/val JSONL + manifest.json (no model calls)")
    b.add_argument("--db", type=Path, required=True, help="a copy of a traces database (never the live logs/ file)")
    b.add_argument("--results", type=Path, nargs="*", default=[], help="bench results files (their traces are dropped)")
    b.add_argument("--out", type=Path, default=Path("data/distill"), help="output folder (default data/distill)")
    b.add_argument("--max-tokens", type=int, default=8000, help="drop longer examples (default 8000)")
    b.add_argument("--val-frac", type=float, default=0.1, help="share of sessions in val (default 0.1)")


def main(args: argparse.Namespace) -> int:
    try:
        teachers = load_teachers()
        conn = open_readonly(args.db)
    except (ValueError, FileNotFoundError) as e:
        print(f"distill: {e}", file=sys.stderr)
        return 2
    try:
        traces = read_traces(conn)
    finally:
        conn.close()
    from kestrel.tools import registry  # the schemas `kestrel export` writes

    tools = registry.schemas()
    prompts = bench_prompts()
    ds = build(
        traces,
        load_verdicts(args.results),
        prompts,
        teachers,
        tools,
        max_tokens=args.max_tokens,
        val_frac=args.val_frac,
    )
    manifest = write(
        ds,
        args.out,
        {
            "input_db_sha256": sha256_file(args.db),
            "results": {p.name: sha256_file(p) for p in args.results},
            "git": git_commit(),
            "teachers": [{"provider": p, "model": m} for p, m in sorted(teachers)],
            "settings": {
                "max_tokens": args.max_tokens,
                "val_frac": args.val_frac,
                "split_salt": SPLIT_SALT,
                "bench_prompts": len(prompts),
                "filters": list(FILTERS),
            },
        },
    )
    print_stats(manifest["stats"])
    print(f"Wrote {args.out / 'train.jsonl'}, {args.out / 'val.jsonl'}, {args.out / 'manifest.json'}")
    return 0
