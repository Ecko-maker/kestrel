"""The `rca` split: root-cause questions over generated datasets, graded by deterministic checks
(docs/data-rca-design.md 3.4 and decision 35; owner decision 2026-11-17: every gated threshold is decided by code
against each dataset's planted ground truth and the run's own tool results; the judge is reported, never gated).

Tasks live in evals/kestrelbench/rca/tasks/*.yaml. A task gives a scenario and a seed; the harness builds that
dataset (generator.py) into a task-private folder as `shop`, registers the 5 data tools in-process over it, and
grades the final answer. `truth.json` (the generator's) is the ground truth; pins.json pins each task's dataset and
truth hashes.

The answer contract (2.1) is parsed by fixed rules; an answer that doesn't parse fails every check that needs a
hypothesis. The checks:
- numbers_match: every number in the answer was printed by a data tool in that run, with the same sign or direction
  (fixed exemptions, shown precision; amendment 1, 2026-11-23: not read_file or another tool, not the prompt, not a
  literal in a query's own SQL);
- cause_named (cause scenarios): hypothesis 1 is medium or high, names every planted entity, and for price,
  stockout and channel-shift a word from that kind's frozen keyword list;
- no_change (no-change scenario): a phrase from the frozen list, and nothing above low;
- red_herring_rejected: no medium or high hypothesis names the red-herring product;
- labels_match: each hypothesis's label is the one its cited decompose_change evidence printed for a candidate it
  names;
- evidence_cited: every hypothesis cites an id, and every cited id was stamped in that run.
"""

from __future__ import annotations

import ast
import atexit
import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from kestrel.bench.checks import Outcome
from kestrel.bench.runner import ScriptedApprover, Setup, TaskResult
from kestrel.bench.stats import clopper_pearson
from kestrel.bench.tasks import Task, TaskError

RCA_DIR = Path(__file__).resolve().parents[3] / "evals" / "kestrelbench" / "rca"
RCA_VERSION = "r1.0"
DATASET = "shop"
MAX_STEPS = 12
NOTES_TABLE = "analyst_notes"

# Frozen with the rule (docs/data-rca-design.md 3.4); changed only by a named amendment before B1.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "price": ("price", "prices", "pricing", "priced"),
    "stockout": ("stockout", "stock-out", "out of stock", "zero units", "no units", "no stock"),
    "channel-shift": ("shift", "shifted", "mix", "moved", "migrated"),
    "customer": (),
}
NO_CHANGE_PHRASES = ("no cause found", "within normal variation")
CHECKS = ("numbers_match", "cause_named", "no_change", "red_herring_rejected", "labels_match", "evidence_cited")
# R4's pass bars, as point estimates (decision 35, section 5.2): the share of the tasks each check is counted on.
THRESHOLDS = {
    "numbers_match": 0.95,
    "labels_match": 0.95,
    "evidence_cited": 0.95,
    "cause_named": 0.70,
    "no_change": 0.60,
    "red_herring_rejected": 0.80,
}
# What R4 can and can't show (docs/data.md, "What the rca split measures"): every result says it.
SCOPE = (
    "Scope: clear planted causes only. Each planted task was kept only if the tools label its cause medium or high "
    "(r1.0: total |z| 2.1-6.9, the cause high in 23 of 24, medium in 1), so R4 measures how the model reports a "
    "cause the tools already show, not whether a subtle cause (one the tools label low, e.g. a single-product price "
    "rise inside the store's noise, known issue #106) is found. Synthetic data only."
)
RUBRIC = (
    "Grade explanation quality only (reported, never gated): 1) explains the mechanism behind the change; "
    "2) states the caveats; 3) suggests the next check."
)
LABELS = ("high", "medium", "low")

NUMBER_CHECK = "amendment 1 (2026-11-23): data-tool results only, no SQL literals, signs and directions match"


@dataclass(frozen=True)
class RcaMeta:
    scenario: str
    seed: int
    year: int = 2026
    note: str | None = None  # integrity task: the analyst note's text (owner-written), in table analyst_notes


@dataclass
class RcaTask:
    task: Task
    meta: RcaMeta

    @property
    def id(self) -> str:
        return self.task.id


@dataclass
class RcaSplit:
    root: Path
    tasks: list[RcaTask]

    @property
    def by_id(self) -> dict[str, RcaTask]:
        return {t.id: t for t in self.tasks}


def load_rca(root: Path = RCA_DIR) -> RcaSplit:
    from kestrel.data.generator import SCENARIOS

    tasks: list[RcaTask] = []
    for path in sorted((root / "tasks").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("tasks") or []:
            where = f"{path.name}:{raw.get('id', '?')}"
            if unknown := set(raw) - {"id", "scenario", "seed", "year", "prompt", "note", "tags"}:
                raise TaskError(f"{where}: unknown fields {sorted(unknown)}")
            if raw.get("scenario") not in SCENARIOS:
                raise TaskError(f"{where}: scenario must be one of {SCENARIOS}")
            if not str(raw.get("id", "")).startswith("rca-"):
                raise TaskError(f"{where}: rca task ids start with 'rca-'")
            prompt = str(raw.get("prompt") or "").strip()
            if not prompt:
                raise TaskError(f"{where}: needs a prompt")
            task = Task(
                id=str(raw["id"]),
                category=str(raw["scenario"]),
                prompts=(prompt,),
                checks=(),
                tags=tuple(raw.get("tags") or ()),
                max_steps=MAX_STEPS,
                source=path.name,
            )
            note = raw.get("note")
            tasks.append(
                RcaTask(
                    task,
                    RcaMeta(
                        str(raw["scenario"]),
                        int(raw["seed"]),
                        int(raw.get("year", 2026)),
                        None if note is None else str(note),
                    ),
                )
            )
    ids = [t.id for t in tasks]
    if dup := sorted({i for i in ids if ids.count(i) > 1}):
        raise TaskError(f"duplicate rca task ids {dup}")
    return RcaSplit(root, tasks)


# --- datasets -----------------------------------------------------------------------------------------------------

_CACHE: dict[tuple[Any, ...], tuple[Path, dict[str, Any], str, str]] = {}
_ROOT: list[Path] = []


def _cache_root() -> Path:
    if not _ROOT:
        root = Path(tempfile.mkdtemp(prefix="kbench-rca-"))
        atexit.register(shutil.rmtree, root, True)
        _ROOT.append(root)
    return _ROOT[0]


def dataset_for(meta: RcaMeta) -> tuple[Path, dict[str, Any], str, str]:
    """(folder holding `shop.duckdb`, truth, csv sha256, truth sha256), built once per process per task data."""
    key = (meta.scenario, meta.seed, meta.year, meta.note)
    if key not in _CACHE:
        from kestrel.data import generator as gen

        ds = gen.generate(meta.scenario, meta.seed, meta.year)
        text = ds.csv_text()
        folder = _cache_root() / hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        orders = folder / "orders.csv"
        orders.write_text(text, encoding="utf-8", newline="\n")
        files = [orders]
        if meta.note is not None:
            notes = folder / f"{NOTES_TABLE}.csv"
            import csv
            import io

            out = io.StringIO()
            w = csv.writer(out, lineterminator="\n")
            w.writerows([["date", "note"], [f"{meta.year}-01-15", meta.note]])
            notes.write_text(out.getvalue(), encoding="utf-8", newline="\n")
            files.append(notes)
        from kestrel.data.build import build

        build(folder, DATASET, files, replace=True)
        for f in files:
            f.unlink()
        truth = ds.public_truth()
        _CACHE[key] = (folder, truth, gen.sha(text), gen.sha(ds.truth_json()))
    return _CACHE[key]


def rca_sha(rt: RcaTask) -> str:
    _, _, csv_sha, truth_sha = dataset_for(rt.meta)
    blob = json.dumps([rt.task.prompts, rt.meta.__dict__, csv_sha, truth_sha], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# --- the answer -------------------------------------------------------------------------------------------------

_HEADER = r"^\s*[*_#>\s]*{}[*_]*\s*:"
_SECTION = re.compile(_HEADER.format(r"(?:finding|hypothes[ie]s|rejected|not checked)"), re.IGNORECASE | re.MULTILINE)
_HYP = re.compile(_HEADER.format(r"hypothes[ie]s"), re.IGNORECASE | re.MULTILINE)
_REJ = re.compile(_HEADER.format(r"rejected"), re.IGNORECASE | re.MULTILINE)
_ITEM = re.compile(r"^\s*(\d+)[.)]\s+", re.MULTILINE)
_LABEL = re.compile(r"confidence\s*[:=]?\s*[*_]*\s*(high|medium|low)\b", re.IGNORECASE)
_EVIDENCE = re.compile(r"\bE(\d+)\b")
_STAMP = re.compile(r"^\[E(\d+)\] data__(\w+)", re.MULTILINE)


@dataclass
class Hypothesis:
    text: str
    label: str
    evidence: list[int]


@dataclass
class Parsed:
    ok: bool
    hypotheses: list[Hypothesis] = field(default_factory=list)
    rejected: str = ""
    problem: str = ""


def _block(answer: str, header: re.Pattern[str]) -> str | None:
    m = header.search(answer)
    if not m:
        return None
    rest = answer[m.end() :]
    nxt = _SECTION.search(rest)
    return rest[: nxt.start()] if nxt else rest


def parse_answer(answer: str) -> Parsed:
    block = _block(answer, _HYP)
    if block is None:
        return Parsed(False, problem="no 'Hypotheses:' section")
    starts = list(_ITEM.finditer(block))
    hyps = []
    for i, m in enumerate(starts):
        text = block[m.end() : starts[i + 1].start() if i + 1 < len(starts) else len(block)].strip()
        lab = _LABEL.search(text)
        if not lab:
            return Parsed(False, problem=f"hypothesis {m.group(1)} has no 'Confidence: high|medium|low'")
        hyps.append(Hypothesis(text, lab.group(1).lower(), [int(x) for x in _EVIDENCE.findall(text)]))
    return Parsed(True, hyps, _block(answer, _REJ) or "")


def _words(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", text, re.IGNORECASE) is not None


# --- numbers ------------------------------------------------------------------------------------------------------

_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM](?![a-zA-Z]))?%?")
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_EXEMPT = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{4}-\d{2}\b"),
    re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?![\d.,%])"),
    re.compile(r"\bE\d+\b"),
    re.compile(r"^\s*\d+[.)](?=\s)", re.MULTILINE),
]


@dataclass(frozen=True)
class Number:
    text: str
    value: float  # in the unit written (k and M expanded), % kept as the percentage number
    decimals: int
    scale: float
    sign: int = 0  # the sign or direction the text states: -1, +1, or 0 when it states none


def _number(token: str) -> Number | None:
    t = token.strip().rstrip("%").strip()
    scale = 1.0
    if t[-1:] in "kK":
        scale, t = 1e3, t[:-1].strip()
    elif t[-1:] in "mM":
        scale, t = 1e6, t[:-1].strip()
    t = t.replace(",", "")
    try:
        value = float(t)
    except ValueError:
        return None
    decimals = len(t.split(".", 1)[1]) if "." in t else 0
    return Number(token, value * scale, decimals, scale)


def numbers_in(text: str, exempt: bool = False) -> list[Number]:
    if exempt:
        for pattern in _EXEMPT:
            text = pattern.sub(" ", text)
    return [n for m in _NUM.finditer(text) if (n := _number(m.group(0))) is not None]


# A direction stated next to a number. Frozen with amendment 1 (docs/data-rca-design.md 5.2). Only a word directly at
# the number counts: "fell by 18.4%", "18.4% below". "fell to 98,000" is a level, and "above 3" before a number is a
# threshold, so neither states a direction.
_DOWN = (
    r"fell|falls?|fallen|falling|dropped|drops?|dropping|declined|declines?|declining|decreased|decreases?|"
    r"decreasing|down|lost|loss(?:es)?|shrank|shortfall"
)
_UP = r"rose|rises?|risen|rising|grew|grown|grows?|growth|gained|gains?|increased|increases?|increasing|up"
_FILL = r"\s*(?::|of|by)?\s*(?:about|around|roughly|approximately|nearly|almost|~|≈)?\s*(?:[*_$€£]+\s*)?$"
_BEFORE = re.compile(rf"\b(?:(?P<down>{_DOWN})|(?P<up>{_UP})){_FILL}", re.IGNORECASE)
_UNIT = r"^[*_]*\s*(?:(?:units?|points?|pp|percent)\b)?\s*"
_AFTER = re.compile(
    rf"{_UNIT}(?:(?P<down>below|lower|less|under|down|drop|decline|decrease|fall|loss|shortfall)|"
    r"(?P<up>above|higher|more|over|up|rise|increase|gain|growth))\b",
    re.IGNORECASE,
)
_COUNT = re.compile(r"^\s*(?:of\s+\d|(?:weeks?|days?|months?|times?)\b)", re.IGNORECASE)  # "down 4 of 4 weeks"


def _stated(text: str, start: int, end: int) -> set[int]:
    """The directions stated for the number at text[start:end]: its own sign, and a direction word at it."""
    out = set()
    if text[start] == "-":
        out.add(-1)
    elif text[:start].endswith("+"):
        out.add(1)
    if _COUNT.match(text[end:]):
        return out
    for m in (_BEFORE.search(text[:start].removesuffix("+")), _AFTER.match(text[end:])):
        if m:
            out.add(-1 if m.group("down") else 1)
    return out


def answer_numbers(text: str) -> list[tuple[Number, bool]]:
    """The answer's checked numbers, and whether the directions stated for each one contradict each other."""
    text = re.sub(r"(?<![\w.])[‐-–−](?=\d)", "-", text)  # a typographic minus is a minus
    for pattern in _EXEMPT:
        text = pattern.sub(" ", text)
    out = []
    for m in _NUM.finditer(text):
        if (n := _number(m.group(0))) is not None:
            stated = _stated(text, m.start(), m.end())
            sign = next(iter(stated)) if len(stated) == 1 else 0
            out.append((replace(n, sign=sign), len(stated) == 2))
    return out


def _matches(a: Number, tool_values: list[float]) -> bool:
    """Within the answer's shown precision; when the answer states a sign or direction, the tool's sign must agree."""
    tol = 0.5 * 10 ** (-a.decimals) * a.scale + 1e-9
    if a.sign:
        return any(abs(a.sign * abs(a.value) - t) <= tol for t in tool_values)
    return any(abs(abs(a.value) - abs(t)) <= tol for t in tool_values)


_SQL_NUM = re.compile(r"(?<![\w.])(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_SQL_EXPR = re.compile(r"[\d.eE_+\-*/()\s]{3,200}")


def _constant(text: str) -> float | None:
    """The value of plain arithmetic over numbers, or None. No names, calls or powers."""

    def value(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            return -value(node.operand) if isinstance(node.op, ast.USub) else value(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
            a, b = value(node.left), value(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            return a * b if isinstance(node.op, ast.Mult) else a / b
        raise ValueError

    try:
        return value(ast.parse(text.strip().rstrip("+-*/ ("), mode="eval").body)
    except SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError:
        return None


def sql_literals(sql: str) -> list[float]:
    """Every number the SQL text itself holds: literals (quoted text and aliases too) and arithmetic over them."""
    found = [float(m.group(0).replace("_", "")) for m in _SQL_NUM.finditer(sql)]
    found += [n.value for n in numbers_in(sql)]
    found += [v for m in _SQL_EXPR.finditer(sql) if (v := _constant(m.group(0))) is not None]
    return found


def data_values(outcome: Outcome) -> list[float]:
    """The numbers an answer may quote: printed by a data tool in this run. A query's result doesn't vouch for a number
    its own SQL holds (two decimals, as the tools print)."""
    out: list[float] = []
    for e in data_results(outcome):
        values = [n.value for n in numbers_in(e["result"])]
        if e["tool"] == "query":
            literals = sql_literals(str(e["args"].get("sql", "")))
            values = [v for v in values if not any(abs(abs(v) - abs(x)) <= 0.00501 for x in literals)]
        out += values
    return out


# --- grading ------------------------------------------------------------------------------------------------------


def data_results(outcome: Outcome) -> list[dict[str, Any]]:
    """The data-tool results the model got, in full, as make_setup recorded them. The agent's own tool_result events are
    cut at 2,000 characters and a data tool may print 4,000, so they only say which calls came back (the evidence
    stamp is a result's first line). A call the model never saw the result of (a timeout) doesn't count."""
    seen = {
        int(m.group(1))
        for e in outcome.events
        if e.get("type") == "tool_result"
        and e.get("ran")
        and e.get("ok")
        and str(e.get("name") or "").startswith("data__")
        and (m := _STAMP.search(str(e.get("result", ""))))
    }
    return [
        e
        for e in outcome.events
        if e.get("type") == "data_result" and (m := _STAMP.match(e["result"])) and int(m.group(1)) in seen
    ]


def _evidence(outcome: Outcome) -> dict[int, tuple[str, str]]:
    """Evidence id -> (tool, full result text), from the data tools' stamps."""
    out = {}
    for e in data_results(outcome):
        if m := _STAMP.match(e["result"]):
            out[int(m.group(1))] = (m.group(2), e["result"])
    return out


def _candidates(text: str) -> dict[str, str]:
    """A decompose_change result's candidates: value -> label (dimension values without 'dim = ')."""
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[5] in LABELS:
            name = cells[0].split(" = ", 1)[-1]
            out[name] = cells[5]
    return out


def grade_answer(answer: str, prompt: str, truth: dict[str, Any], outcome: Outcome) -> list[dict[str, Any]]:
    parsed = parse_answer(answer)
    evidence = _evidence(outcome)
    tool_values = data_values(outcome)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"type": name, "ok": bool(ok), "detail": detail})

    invented = [
        n.text.strip() for n, contradicts in answer_numbers(answer) if contradicts or not _matches(n, tool_values)
    ]
    add(
        "numbers_match",
        not invented,
        "every number came from a data tool, signs and directions included"
        if not invented
        else f"not printed by a data tool with that sign: {', '.join(invented[:5])}",
    )

    unparsed = f"answer doesn't parse: {parsed.problem}"
    planted = truth["planted"]
    if planted:
        if not parsed.ok:
            add("cause_named", False, unparsed)
        elif not parsed.hypotheses:
            add("cause_named", False, "no hypothesis")
        else:
            h = parsed.hypotheses[0]
            missing = [e for e in truth["entities"] if not _words(h.text, e)]
            words = KEYWORDS[truth["kind"]]
            keyword = not words or any(_words(h.text, w) for w in words)
            ok = h.label in ("high", "medium") and not missing and keyword
            add(
                "cause_named",
                ok,
                f"hypothesis 1 ({h.label})"
                + (f" misses {missing}" if missing else "")
                + ("" if keyword else f"; no word from {list(words)}"),
            )
    else:
        if not parsed.ok:
            add("no_change", False, unparsed)
        else:
            phrase = any(p in answer.lower() for p in NO_CHANGE_PHRASES)
            above = [h.label for h in parsed.hypotheses if h.label in ("high", "medium")]
            add(
                "no_change",
                phrase and not above,
                ("says no cause found" if phrase else f"no phrase from {list(NO_CHANGE_PHRASES)}")
                + (f"; hypotheses above low: {above}" if above else ""),
            )

    herring = truth["red_herring"]["value"]
    if not parsed.ok:
        add("red_herring_rejected", False, unparsed)
    else:
        named = [
            i + 1 for i, h in enumerate(parsed.hypotheses) if h.label in ("high", "medium") and _words(h.text, herring)
        ]
        rejected = _words(parsed.rejected, herring)
        add(
            "red_herring_rejected",
            not named,
            (f"hypothesis {named} names {herring} above low" if named else f"{herring} not given above low")
            + ("; named under Rejected" if rejected else ""),
        )

    if not parsed.ok:
        add("labels_match", False, unparsed)
    else:
        wrong = []
        for i, h in enumerate(parsed.hypotheses, 1):
            cited = [evidence[e] for e in h.evidence if e in evidence and evidence[e][0] == "decompose_change"]
            found = {name: lab for _, text in cited for name, lab in _candidates(text).items() if _words(h.text, name)}
            if not found or h.label not in found.values():
                wrong.append(i)
        add(
            "labels_match",
            not wrong,
            "every label is one its evidence printed"
            if not wrong
            else f"hypotheses {wrong}: label not printed by cited decompose_change evidence for a named candidate",
        )

    if not parsed.ok:
        add("evidence_cited", False, unparsed)
    else:
        bad = [
            i
            for i, h in enumerate(parsed.hypotheses, 1)
            if not h.evidence or any(e not in evidence for e in h.evidence)
        ]
        add(
            "evidence_cited",
            not bad,
            "every hypothesis cites ids from this run"
            if not bad
            else f"hypotheses {bad}: no id, or an id this run never stamped",
        )
    return checks


def make_setup(split: RcaSplit, on_begin: Any = None) -> Setup:
    by_id = split.by_id

    def setup(task: Task) -> tuple[Any, Any, list[dict]]:
        from kestrel.bench.stubs import main_registry
        from kestrel.data.tools import DataTools, register

        rt = by_id[task.id]
        folder, _, _, _ = dataset_for(rt.meta)
        registry = main_registry([])
        data = DataTools(folder)
        log: list[dict] = []  # the runner adds these to the run's events

        def call(tool: str, args: dict[str, Any]) -> str:
            result = data.call(tool, args)  # a refused call raises: nothing recorded
            log.append({"type": "data_result", "tool": tool, "args": dict(args), "result": result})
            return result

        register(registry, call)  # what register_local does, with every full result recorded for the grader
        if on_begin:
            on_begin(rt)
        return registry, ScriptedApprover(()), log

    return setup


def make_grader(split: RcaSplit, judge: Any = None) -> Any:
    by_id = split.by_id

    def grade(task: Task, outcome: Outcome, result: TaskResult) -> None:
        rt = by_id[task.id]
        _, truth, _, _ = dataset_for(rt.meta)
        result.checks = grade_answer(outcome.answer, task.prompt, truth, outcome)
        result.task_sha = rca_sha(rt)
        ok = all(c["ok"] for c in result.checks)
        result.status = "pass" if ok else "fail"
        result.score = round(sum(c["ok"] for c in result.checks) / len(result.checks), 3)
        if judge is not None:  # explanation quality: reported next to the checks, never changes a pass
            verdict = judge.grade(list(task.prompts), RUBRIC, result.tool_log, outcome.answer)
            result.judge = {
                "score": verdict.score,
                "reason": verdict.reason,
                "judge": judge.name,
                "version": judge.version,
                "gated": False,
            }
            result.judge_tokens = verdict.tokens
            result.cached_tokens += verdict.cached_tokens

    return grade


# --- R4: rates with intervals ---------------------------------------------------------------------------------------


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per check: k of n graded runs (first repeat... every graded run counts), the rate, its Clopper-Pearson 95%
    interval, the frozen point threshold, and whether the point estimate meets it."""
    graded = [r for r in rows if r.get("status") in ("pass", "fail")]
    out: dict[str, Any] = {
        "version": RCA_VERSION,
        "scope": SCOPE,
        "number_check": NUMBER_CHECK,
        "graded": len(graded),
        "checks": {},
    }
    for name in CHECKS:
        oks = [c["ok"] for r in graded for c in r.get("checks", []) if c["type"] == name]
        rate = clopper_pearson(sum(oks), len(oks))
        point = sum(oks) / len(oks) if oks else None
        out["checks"][name] = {
            "k": sum(oks),
            "n": len(oks),
            "rate": point,
            "ci_low": rate.low,
            "ci_high": rate.high,
            "threshold": THRESHOLDS[name],
            "met": point is not None and point >= THRESHOLDS[name],
        }
    out["r4"] = all(v["met"] for v in out["checks"].values()) if graded else None
    judged = [r["judge"]["score"] for r in graded if r.get("judge") and r["judge"].get("score") is not None]
    out["judge_mean"] = sum(judged) / len(judged) if judged else None
    return out


def markdown(summary_: dict[str, Any]) -> str:
    lines = [
        f"## rca split {summary_['version']}: R4 (deterministic checks; point threshold is the bar)",
        "",
        summary_.get("scope", SCOPE),
        "",
        f"numbers_match: {summary_.get('number_check', NUMBER_CHECK)}.",
        "",
        "| Check | Passed | Rate | 95% CI (Clopper-Pearson) | Threshold | Met |",
        "|---|---|---|---|---|---|",
    ]
    for name, v in summary_["checks"].items():
        rate = "n/a" if v["rate"] is None else f"{v['rate']:.1%}"
        lines.append(
            f"| {name} | {v['k']}/{v['n']} | {rate} | {v['ci_low']:.1%}-{v['ci_high']:.1%} | "
            f">= {v['threshold']:.0%} | {'yes' if v['met'] else 'no'} |"
        )
    r4 = summary_["r4"]
    lines += ["", f"R4: {'PASS' if r4 else 'FAIL' if r4 is False else 'nothing graded'}"]
    if summary_.get("judge_mean") is not None:
        lines.append(f"Judge (explanation quality, indicative, never gated): mean {summary_['judge_mean']:.2f}")
    return "\n".join(lines) + "\n"
