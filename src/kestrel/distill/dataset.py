"""`kestrel distill build`: a supervised fine-tuning dataset from Kestrel traces. No model calls.

Input: a copy of a traces database (the SQLite format of tracing.py) and, optionally, bench results
files. Every trace goes through the filters below in order; the first one it fails is counted.

  no_messages           the trace stored no conversation (KESTREL_TRACE_CONTENT=off)
  teacher               a model step not on teachers.toml (Gemini, demo, an unknown model, a fallback)
  contamination_run     the trace belongs to a KestrelBench run: its id is in a results file that isn't
                        a distill run (see is_distill_run; decided from the file's metadata)
  contamination_prompt  a user message equals a KestrelBench prompt (main suite on disk, plus the
                        safety split at the commit pinned in bench_sources.toml), ignoring case,
                        punctuation and whitespace
  near_duplicate        a user message is close to a KestrelBench prompt: word 3-gram Jaccard or a
                        shared run of words at the overlap check's fail thresholds (overlap.py)
  quality               not answered (max_steps / error), rated /bad, or neither rated /good nor part
                        of a distill task that passed (all checks, judge >= 0.5)
  task_changed          a distill result whose task no longer exists or whose definition changed
                        since the run (task fingerprint differs)
  rubric_task           a distill task with a rubric, under grading = "checks-only" (grading.toml), or
                        graded by a judge not listed as calibrated
  secret_canary         a KCAN- canary, as written or URL / base64 / hex encoded, or its prefix alone
  secret_pattern        anything tracing.redact() would change, or a [REDACTED] marker
  secret_env_line       a .env-style KEY=value line with a secret-sounding name
  secret_env_value      the value of a secret-named environment variable set now
  too_long              estimated tokens over --max-tokens (dropped, never truncated)
  duplicate             the same conversation as an earlier example

The teacher, contamination and secret filters have no override. KestrelBench is the student's deploy
gate, so a trace from it (or with one of its prompts) would make that gate meaningless. Graded
examples come from the distill task set instead (distill/, `kestrel bench run --split distill`),
whose prompts are checked against KestrelBench's by kestrel.distill.overlap.

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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from kestrel.agent import estimate_tokens
from kestrel.bench.tasks import Task, TaskError, load_tasks, task_sha
from kestrel.distill import DISTILL_DB, ROOT, overlap
from kestrel.trace_report import export_record, percentile
from kestrel.tracing import REDACTED, redact

TEACHERS_FILE = Path(__file__).with_name("teachers.toml")
GRADING_FILE = Path(__file__).with_name("grading.toml")
GRADING_MODES = ("checks-only", "calibrated-judge")
BENCH_DIR = overlap.BENCH_DIR  # every task folder in it: tasks/, safety/, any future split
LIVE_DBS = (ROOT / "logs" / "traces.db", ROOT / "logs" / "bench.db", ROOT / DISTILL_DB)
DISTILL_ID_PREFIX = "dst-"
SPLIT_SALT = "2026"

FILTERS = (
    "no_messages",
    "teacher",
    "contamination_run",
    "contamination_prompt",
    "near_duplicate",
    "quality",
    "task_changed",
    "rubric_task",
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


normalize = overlap.normalize  # lowercase, no punctuation, single spaces: one rule for both checks


def bench_prompts(bench_dir: Path = BENCH_DIR) -> frozenset[str]:
    """Every KestrelBench prompt (all turns, disk plus pinned sources), normalized. Read by the same
    function as the overlap check, so both see the same prompts."""
    return frozenset(normalize(p.text) for p in overlap.load_bench(bench_dir).prompts)


@dataclass(frozen=True)
class Grading:
    mode: str = "checks-only"
    calibrated_judges: frozenset[str] = frozenset()  # "judge-name@version"


DEFAULT_GRADING = Grading()


def load_grading(path: Path = GRADING_FILE) -> Grading:
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    mode = cfg.get("grading", "checks-only")
    if mode not in GRADING_MODES:
        raise ValueError(f"{path}: grading must be one of {GRADING_MODES}, got {mode!r}")
    return Grading(mode, frozenset(str(j) for j in cfg.get("calibrated_judges", [])))


@dataclass(frozen=True)
class Verdict:
    task_id: str
    passed: bool  # all checks passed and (no judge, or judge >= 0.5)
    distill: bool = False  # from a distill run; anything else counts as KestrelBench
    known: bool = True  # distill only: the task exists and its fingerprint matches the run's
    rubric: bool = False  # distill only: the task has a rubric (graded by a judge, or not at all)
    judge: str | None = None  # "judge-name@version" that graded it, if any


def task_passed(task: dict[str, Any]) -> bool:
    """The bench's pass rule (runner.py), re-checked from the stored checks and judge score."""
    judge = task.get("judge")
    return (
        task.get("status") == "pass"
        and all(c.get("ok") for c in task.get("checks", []))
        and (judge is None or (judge.get("score") or 0) >= 0.5)
    )


def is_distill_run(data: dict[str, Any]) -> bool:
    """A results file is a distill run only if its metadata says split "distill" AND every task id is a
    distill id. Anything else (no split key, as in every file before Phase 4, "main", "safety", an
    unknown split, a mix, no tasks) counts as KestrelBench. Where the file lives is never used."""
    tasks = data.get("tasks") or []
    return (
        data.get("meta", {}).get("split") == "distill"
        and bool(tasks)
        and all(str(t.get("id", "")).startswith(DISTILL_ID_PREFIX) for t in tasks)
    )


def _distill_tasks(meta: dict[str, Any], root: Path) -> dict[str, tuple[Task, str]]:
    """id -> (task, current fingerprint) for the task folder a distill run recorded. Empty when the
    folder is missing or unreadable, so every result of that run counts as task_changed."""
    tasks_dir, workspace = meta.get("task_dir"), meta.get("workspace_dir")
    if not tasks_dir or not workspace or not (root / tasks_dir).is_dir():
        return {}
    try:
        return {t.id: (t, task_sha(t, root / workspace)) for t in load_tasks(root / tasks_dir)}
    except TaskError, OSError:
        return {}


def _judge_label(task: dict[str, Any]) -> str | None:
    judge = task.get("judge") or {}
    return f"{judge['judge']}@{judge.get('version')}" if judge.get("judge") else None


def load_verdicts(paths: list[Path], root: Path = ROOT) -> dict[str, Verdict]:
    """trace_id -> verdict for every trace listed in results files, whatever its status. A trace
    listed by both kinds of file keeps the KestrelBench verdict, so it is dropped. For distill runs,
    each result is matched to its task definition (rubric or not, unchanged since the run or not)."""
    verdicts: dict[str, Verdict] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        distill = is_distill_run(data)
        defs = _distill_tasks(data.get("meta", {}), root) if distill else {}
        for task in data.get("tasks", []):
            known = rubric = False
            if (found := defs.get(task["id"])) is not None:
                known, rubric = found[1] == task.get("task_sha"), found[0].rubric is not None
            verdict = Verdict(task["id"], task_passed(task), distill, known, rubric, _judge_label(task))
            for trace_id in task.get("trace_ids", []):
                if trace_id in verdicts and not verdicts[trace_id].distill:
                    continue
                verdicts[trace_id] = verdict
    return verdicts


def results_sources(paths: list[Path]) -> dict[str, dict[str, str]]:
    """For the manifest: each results file's hash and how it was classified."""
    return {
        p.name: {
            "sha256": sha256_file(p),
            "source": "distill" if is_distill_run(json.loads(p.read_text(encoding="utf-8"))) else "kestrelbench",
        }
        for p in paths
    }


def open_readonly(db: Path) -> sqlite3.Connection:
    resolved = db.resolve()
    if resolved.name == "bench.db":
        raise ValueError(f"{db}: bench.db holds only KestrelBench runs, which are never training data.")
    if resolved in LIVE_DBS:
        raise ValueError(f"{db}: pass a copy, not the live logs/{resolved.name} (it may be written while it is read).")
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


def grading_filter(verdict: Verdict | None, grading: Grading) -> str | None:
    """Distill results only: is this pass one we trust? (Chats rated /good have no verdict.)"""
    if verdict is None or not verdict.distill:
        return None
    if not verdict.known:
        return "task_changed"
    if verdict.rubric and (grading.mode == "checks-only" or verdict.judge not in grading.calibrated_judges):
        return "rubric_task"
    return None


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
    prompts: Iterable[str],
    teachers: frozenset[tuple[str, str]],
    tools: list[dict],
    *,
    max_tokens: int = 8000,
    val_frac: float = 0.1,
    grading: Grading = DEFAULT_GRADING,
) -> Dataset:
    """prompts: the KestrelBench prompts the contamination filters compare user messages with."""
    ds = Dataset(seen=len(traces))
    index = overlap.PromptIndex(list(prompts))
    schema_tokens = len(json.dumps(tools)) // 4  # how the agent estimates the schemas' share
    env_values = secret_env_values()
    seen_keys: set[str] = set()
    for trace in traces:
        reason, example = _check(
            trace, verdicts, index, teachers, tools, env_values, schema_tokens, max_tokens, grading
        )
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
    index: overlap.PromptIndex,
    teachers: frozenset[tuple[str, str]],
    tools: list[dict],
    env_values: list[str],
    schema_tokens: int,
    max_tokens: int,
    grading: Grading,
) -> tuple[str | None, Example | None]:
    """(first failing filter, None) or (None, the example)."""
    if not trace.get("messages"):
        return "no_messages", None
    if not teacher_ok(trace["llm_steps"], teachers):
        return "teacher", None
    verdict = verdicts.get(trace["trace_id"])
    if verdict is not None and not verdict.distill:
        return "contamination_run", None
    messages = json.loads(trace["messages"])
    candidates = [m.get("content") for m in messages if m.get("role") == "user"] + [trace.get("user_message")]
    users = [u for u in candidates if isinstance(u, str)]
    if any(index.exact(u) for u in users):
        return "contamination_prompt", None
    if any(index.near(u) for u in users):
        return "near_duplicate", None
    if not quality_ok(trace, verdict):
        return "quality", None
    if reason := grading_filter(verdict, grading):
        return reason, None
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
        grading = load_grading()
        conn = open_readonly(args.db)  # first: refusing a live database needs no git
    except (ValueError, FileNotFoundError) as e:
        print(f"distill: {e}", file=sys.stderr)
        return 2
    try:
        bench = overlap.load_bench()  # fails loudly if a pinned source's tag moved
        traces = read_traces(conn)
    except overlap.SourceError as e:
        print(f"distill: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    from kestrel.tools import registry  # the schemas `kestrel export` writes

    tools = registry.schemas()
    prompts = [p.text for p in bench.prompts]
    ds = build(
        traces,
        load_verdicts(args.results),
        prompts,
        teachers,
        tools,
        max_tokens=args.max_tokens,
        val_frac=args.val_frac,
        grading=grading,
    )
    manifest = write(
        ds,
        args.out,
        {
            "input_db_sha256": sha256_file(args.db),
            "results": results_sources(args.results),
            "git": git_commit(),
            "teachers": [{"provider": p, "model": m} for p, m in sorted(teachers)],
            "settings": {
                "max_tokens": args.max_tokens,
                "val_frac": args.val_frac,
                "split_salt": SPLIT_SALT,
                "bench_prompts": len(prompts),
                "bench_sources": bench.sources,  # disk, and each pinned commit (or why it wasn't read)
                "near_duplicate": {
                    "fail_jaccard": overlap.FAIL_JACCARD,
                    "fail_run_words": overlap.FAIL_RUN,
                },
                "grading": grading.mode,
                "calibrated_judges": sorted(grading.calibrated_judges),
                "filters": list(FILTERS),
            },
        },
    )
    print_stats(manifest["stats"])
    print(f"Wrote {args.out / 'train.jsonl'}, {args.out / 'val.jsonl'}, {args.out / 'manifest.json'}")
    return 0
