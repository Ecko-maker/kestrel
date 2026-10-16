"""Overlap between the distill task set and KestrelBench. No model calls.

The student is trained on distill tasks and gated on KestrelBench, so a distill prompt or fixture that
copies a bench one would leak the exam into the training data. KestrelBench here means every task
folder under evals/kestrelbench/ on disk, plus the safety split read with git from the commit pinned
in bench_sources.toml (it isn't on this branch until Phase 3 merges).

Prompts (every turn) are normalized (lowercase, no punctuation, single spaces) and compared with two
measures: word 3-gram Jaccard similarity, and the longest run of words they share. A pair fails on an
exact match, Jaccard >= FAIL_JACCARD or a shared run >= FAIL_RUN words; it is listed for review at
REVIEW_JACCARD / REVIEW_RUN. The thresholds sit just above the bench's own p99.9 (fail) and p99
(review) over pairs of different bench tasks with different text; evals/reports/distill-overlap.md
has the distributions. Fixtures fail on a shared file name, or a shared content line of
MIN_LINE_WORDS+ words.

    uv run python scripts/distill_overlap.py [--report evals/reports/distill-overlap.md]
"""

import argparse
import functools
import itertools
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kestrel.bench.tasks import load_tasks
from kestrel.distill import DISTILL_DIR, ROOT

BENCH_DIR = ROOT / "evals" / "kestrelbench"
SOURCES_FILE = Path(__file__).with_name("bench_sources.toml")

FAIL_JACCARD = 0.23  # bench p99.9 = 0.222 (pairs of different tasks with different text)
REVIEW_JACCARD = 0.08  # bench p99 = 0.071
FAIL_RUN = 5  # words; bench p99.9 = 4
REVIEW_RUN = 4  # words; bench p99 = 3
MIN_LINE_WORDS = 8

# Ordinary requests someone might rate /good in a chat. Not tasks: the report shows how many of them
# the pipeline's prompt filters would drop, to make the filters' cost on everyday use visible.
CHAT_PROBES = (
    "What time is it in Tokyo?",
    "Add milk to my groceries note.",
    "What's 15% of 80?",
    "Email Sam that I'm running late.",
    "Summarize notes.txt for me.",
    "What's the weather like in Paris today?",
    "Search the web for the latest Python version.",
    "Create a note called 'Groceries' with milk and eggs.",
    "What's on my todo list?",
    "Translate 'good night' into French.",
    "How many days until Christmas?",
    "Read my meeting notes and list the action items.",
)


class SourceError(RuntimeError):
    """A pinned bench source doesn't match what git says (e.g. the tag moved)."""


@dataclass(frozen=True)
class Prompt:
    task_id: str
    text: str
    source: str = "disk"


@dataclass(frozen=True)
class Pair:
    distill: Prompt
    bench: Prompt
    jaccard: float  # 1.0 for an exact match
    run: int  # longest shared run of words
    exact: bool

    @property
    def fails(self) -> bool:
        return self.exact or self.jaccard >= FAIL_JACCARD or self.run >= FAIL_RUN

    @property
    def review(self) -> bool:
        return not self.fails and (self.jaccard >= REVIEW_JACCARD or self.run >= REVIEW_RUN)


@dataclass
class Bench:
    prompts: list[Prompt] = field(default_factory=list)
    fixture_names: set[str] = field(default_factory=set)  # base names of workspace files
    fixture_lines: set[str] = field(default_factory=set)  # normalized lines of MIN_LINE_WORDS+ words
    sources: list[dict[str, Any]] = field(default_factory=list)  # provenance, for reports and manifests


# --- Measures -----------------------------------------------------------------


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def trigrams(text: str) -> frozenset[tuple[str, ...]]:
    words = normalize(text).split()
    return frozenset(tuple(words[i : i + 3]) for i in range(len(words) - 2))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def longest_run(a: list[str], b: list[str]) -> int:
    """Length of the longest run of consecutive words both lists share."""
    best, prev = 0, [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def pair(d: Prompt, b: Prompt) -> Pair:
    nd, nb = normalize(d.text), normalize(b.text)
    exact = nd == nb
    return Pair(
        d, b, 1.0 if exact else jaccard(trigrams(d.text), trigrams(b.text)), longest_run(nd.split(), nb.split()), exact
    )


class PromptIndex:
    """Bench prompts prepared for the pipeline: exact and near-duplicate lookups of one message."""

    def __init__(self, texts: list[str]) -> None:
        self.exact_set = {normalize(t) for t in texts}
        self.items = [(normalize(t).split(), trigrams(t)) for t in texts]

    def exact(self, text: str) -> bool:
        return normalize(text) in self.exact_set

    def near(self, text: str) -> bool:
        words, grams = normalize(text).split(), trigrams(text)
        return any(
            jaccard(grams, b_grams) >= FAIL_JACCARD or longest_run(words, b_words) >= FAIL_RUN
            for b_words, b_grams in self.items
        )


# --- Reading KestrelBench -----------------------------------------------------


def _prompts_in(node: Any, task_id: str, source: str) -> list[Prompt]:
    """Every prompt (all turns) in a parsed task file, in any task format."""
    if isinstance(node, dict):
        task_id = str(node.get("id", task_id))
        found = []
        for key, value in node.items():
            if key == "prompt":
                texts = [value] if isinstance(value, str) else [v for v in value or [] if isinstance(v, str)]
                found += [Prompt(task_id, t, source) for t in texts]
            else:
                found += _prompts_in(value, task_id, source)
        return found
    if isinstance(node, list):
        return [p for item in node for p in _prompts_in(item, task_id, source)]
    return []


def _strings_in(node: Any, skip: frozenset[str] = frozenset({"prompt"})) -> list[str]:
    """Every string in a parsed task file except prompts: embedded files, pages, rubrics."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() if k not in skip for s in _strings_in(v, skip)]
    if isinstance(node, list):
        return [s for v in node for s in _strings_in(v, skip)]
    return []


def content_lines(text: str) -> set[str]:
    return {n for line in text.splitlines() if len((n := normalize(line)).split()) >= MIN_LINE_WORDS}


def bench_prompts(bench_dir: Path = BENCH_DIR) -> list[Prompt]:
    """Every prompt of every task file on disk under bench_dir. Fixture files under workspace/ are
    data the tasks read, not prompts."""
    prompts: list[Prompt] = []
    for path in sorted(bench_dir.rglob("*.y*ml")):
        if "workspace" in path.relative_to(bench_dir).parts:
            continue
        prompts += _prompts_in(yaml.safe_load(path.read_text(encoding="utf-8")), path.stem, "disk")
    return prompts


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=30)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def load_sources(path: Path = SOURCES_FILE) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _pinned_files(cfg: dict[str, Any], repo: Path) -> tuple[dict[str, str] | None, dict[str, Any]]:
    """The files of a pinned source, read from its commit with git, and their provenance."""
    commit, tag, path = cfg["commit"], cfg.get("tag"), cfg["path"]
    info: dict[str, Any] = {"source": f"git:{path}", "commit": commit, "tag": tag}
    resolved = _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}") if tag else None
    if resolved and resolved.strip() != commit:
        raise SourceError(
            f"tag {tag} now points to {resolved.strip()}, but bench_sources.toml pins {commit}. "
            "Check what changed, then update the pinned commit on purpose."
        )
    files = _files_at(repo, commit, path)
    if files is None:
        return None, info | {"available": False, "note": "commit not in this clone: files on disk only"}
    return dict(files), info | {"available": True, "files": len(files)}


@functools.cache
def _files_at(repo: Path, commit: str, path: str) -> tuple[tuple[str, str], ...] | None:
    """The task and page files under path at commit, or None if the commit isn't in the clone. Cached:
    a commit's content never changes (the tag check above still runs every time)."""
    if _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        return None
    names = (_git(repo, "ls-tree", "-r", "--name-only", commit, "--", path) or "").split()
    keep = [n for n in names if n.endswith((".yaml", ".yml", ".html"))]
    return tuple((n, _git(repo, "show", f"{commit}:{n}") or "") for n in keep)


def load_bench(bench_dir: Path = BENCH_DIR, sources_file: Path | None = SOURCES_FILE, repo: Path = ROOT) -> Bench:
    """KestrelBench prompts and fixtures: the files on disk, plus each pinned source in sources_file."""
    bench = Bench(prompts=bench_prompts(bench_dir))
    bench.sources.append({"source": "disk", "path": _rel(bench_dir)})  # repo-relative: no local paths in manifests
    workspace = bench_dir / "workspace"
    for p in sorted(workspace.rglob("*")) if workspace.is_dir() else ():
        if p.is_file():
            bench.fixture_names.add(p.name.lower())
            bench.fixture_lines |= content_lines(p.read_text(encoding="utf-8", errors="ignore"))
    for path in sorted(bench_dir.rglob("*.y*ml")):
        if "workspace" not in path.relative_to(bench_dir).parts:
            for s in _strings_in(yaml.safe_load(path.read_text(encoding="utf-8"))):
                bench.fixture_lines |= content_lines(s)
    for cfg in (load_sources(sources_file) if sources_file else {}).values():
        files, info = _pinned_files(cfg, repo)
        bench.sources.append(info)
        for name, text in (files or {}).items():
            if name.endswith((".yaml", ".yml")):
                data = yaml.safe_load(text)
                bench.prompts += _prompts_in(data, Path(name).stem, f"git:{cfg['commit'][:12]}")
                for s in _strings_in(data):
                    bench.fixture_lines |= content_lines(s)
            else:
                bench.fixture_lines |= content_lines(text)
    seen: set[tuple[str, str]] = set()
    unique = []
    for prompt in bench.prompts:  # the same task on disk and at the pinned commit counts once
        if (key := (prompt.task_id, normalize(prompt.text))) not in seen:
            seen.add(key)
            unique.append(prompt)
    bench.prompts = unique
    return bench


# --- The distill side ---------------------------------------------------------


def distill_prompts(tasks_dir: Path = DISTILL_DIR / "tasks") -> list[Prompt]:
    return [Prompt(t.id, text) for t in load_tasks(tasks_dir) for text in t.prompts]


def distill_fixtures(distill_dir: Path = DISTILL_DIR) -> tuple[dict[str, str], dict[str, set[str]]]:
    """(base name -> path) of workspace files, and (where -> content lines) of workspace files and
    files embedded in tasks."""
    names: dict[str, str] = {}
    lines: dict[str, set[str]] = {}
    workspace = distill_dir / "workspace"
    for p in sorted(workspace.rglob("*")):
        if p.is_file():
            names[p.name.lower()] = p.relative_to(distill_dir).as_posix()
            lines[p.relative_to(distill_dir).as_posix()] = content_lines(p.read_text(encoding="utf-8"))
    for t in load_tasks(distill_dir / "tasks"):
        for name, text in t.files.items():
            lines[f"{t.id}:{name}"] = content_lines(text)
    return names, lines


# --- Comparisons --------------------------------------------------------------


def compare(distill: list[Prompt], bench: list[Prompt]) -> list[Pair]:
    """Every pair, the closest first (by Jaccard, then shared run)."""
    pairs = [pair(d, b) for d in distill for b in bench]
    return sorted(pairs, key=lambda p: (-p.jaccard, -p.run, p.distill.task_id, p.bench.task_id))


def failures(pairs: list[Pair]) -> list[Pair]:
    return [p for p in pairs if p.fails]


def fixture_failures(distill_dir: Path, bench: Bench) -> list[str]:
    names, lines = distill_fixtures(distill_dir)
    found = [
        f"file name {path} is also a KestrelBench fixture" for n, path in names.items() if n in bench.fixture_names
    ]
    for where, own in lines.items():
        found += [f"{where}: line {line!r} is also in KestrelBench" for line in sorted(own & bench.fixture_lines)]
    return found


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile of sorted values (0 when there are none)."""
    if not values:
        return 0.0
    return values[min(len(values) - 1, round(q / 100 * (len(values) - 1)))]


def distribution(prompts: list[Prompt], *, exclude_related: bool) -> dict[str, Any]:
    """Both measures over pairs of bench prompts. exclude_related drops pairs from the same task
    (turns of one conversation) and pairs with identical text (benign/attack twins share a prompt
    on purpose), leaving pairs that are meant to be different tasks."""
    pairs = [
        (a, b)
        for a, b in itertools.combinations(prompts, 2)
        if not exclude_related or (a.task_id != b.task_id and normalize(a.text) != normalize(b.text))
    ]
    scored = [pair(a, b) for a, b in pairs]
    js = sorted(p.jaccard for p in scored)
    rs = sorted(float(p.run) for p in scored)
    qs = (50, 95, 99, 99.9)
    return {
        "pairs": len(scored),
        "jaccard": {f"p{q:g}": _quantile(js, q) for q in qs} | {"max": js[-1] if js else 0.0},
        "run": {f"p{q:g}": _quantile(rs, q) for q in qs} | {"max": rs[-1] if rs else 0.0},
        "top": sorted(scored, key=lambda p: (-p.jaccard, -p.run))[:12],
    }


def chat_probe(bench: Bench) -> list[tuple[str, str]]:
    """(probe, what the pipeline's prompt filters would do with it)."""
    index = PromptIndex([p.text for p in bench.prompts])
    return [
        (text, "contamination_prompt" if index.exact(text) else "near_duplicate" if index.near(text) else "kept")
        for text in CHAT_PROBES
    ]


# --- Report and CLI -----------------------------------------------------------


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def report(bench: Bench, pairs: list[Pair], fixture_problems: list[str], distill_dir: Path) -> str:
    def table(name: str, d: dict[str, Any]) -> str:
        j, r = d["jaccard"], d["run"]
        return (
            f"| {name} | {d['pairs']:,} | {j['p50']:.3f} | {j['p95']:.3f} | {j['p99']:.3f} | {j['p99.9']:.3f} | "
            f"{j['max']:.3f} | {r['p50']:.0f} | {r['p95']:.0f} | {r['p99']:.0f} | {r['p99.9']:.0f} | {r['max']:.0f} |"
        )

    every = distribution(bench.prompts, exclude_related=False)
    related_out = distribution(bench.prompts, exclude_related=True)
    failed = failures(pairs)
    review = [p for p in pairs if p.review]
    distill = {p.distill for p in pairs}
    lines = [
        "# Distill set vs KestrelBench: overlap",
        "",
        "Generated by `uv run python scripts/distill_overlap.py --report evals/reports/distill-overlap.md`"
        " (no model calls). Logic: `src/kestrel/distill/overlap.py`.",
        "",
        "## Sources",
        "",
    ]
    for s in bench.sources:
        if s["source"] == "disk":
            lines.append(f"- Files on disk under `{_rel(Path(s['path']))}` (main suite on this branch).")
        else:
            state = f"{s.get('files', 0)} files read" if s.get("available") else s.get("note", "not available")
            lines.append(
                f"- `{s['source'].removeprefix('git:')}` at commit `{s['commit']}` (tag `{s.get('tag')}`): {state}."
            )
    lines += [
        f"- {len(bench.prompts)} KestrelBench prompts (all turns), {len(distill)} distill prompts"
        f" from `{_rel(distill_dir)}`.",
        "",
        "## KestrelBench against itself",
        "",
        "How similar are two KestrelBench prompts that are meant to be different tasks? Word 3-gram Jaccard and the"
        " longest shared run of words, over every pair of bench prompts.",
        "",
        "| Pairs | n | J p50 | J p95 | J p99 | J p99.9 | J max | run p50 | run p95 | run p99 | run p99.9 | run max |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        table("all", every),
        table("excluding twins and same-task", related_out),
        "",
        "*Twins* are benign/attack pairs of the safety split that share their prompt on purpose; *same-task* pairs"
        " are turns of one conversation. Both are excluded from the second table, which the thresholds are based on.",
        "",
        "Closest pairs that remain (second table):",
        "",
    ]
    lines += [
        f"- {p.jaccard:.3f} / {p.run} words: `{p.distill.task_id}` vs `{p.bench.task_id}`"
        for p in related_out["top"][:8]
    ]
    lines += [
        "",
        "## Thresholds",
        "",
        f"- **Fail:** exact match, Jaccard >= **{FAIL_JACCARD}**, or a shared run >= **{FAIL_RUN} words**:"
        f" just above the second table's p99.9 ({related_out['jaccard']['p99.9']:.3f} and"
        f" {related_out['run']['p99.9']:.0f}). Above that, even different bench tasks are mostly reusing a"
        " sentence; a distill prompt that close is copying.",
        f"- **Review:** Jaccard >= **{REVIEW_JACCARD}** or a shared run >= **{REVIEW_RUN} words**: just above the p99"
        f" ({related_out['jaccard']['p99']:.3f} and {related_out['run']['p99']:.0f}), a level different bench tasks"
        " almost never reach. Listed below for a human to read; nothing was reworded to get under it.",
        f"- **Fixtures:** a distill workspace file with the same base name as a bench workspace file, or a content line"
        f" of {MIN_LINE_WORDS}+ words (normalized) that also appears in the bench workspace, a bench task's embedded"
        " files, or the safety split's tasks and pages.",
        "",
        'Why not fail at p99: it would trip on a single shared 3-gram such as "what time is it", which is ordinary'
        " phrasing, not copying. A test recomputes the distribution (disk, plus the pinned commit when the"
        " clone has it) with the same exclusions and fails if the fail thresholds no longer sit above its p99.9.",
        "",
        "## Results",
        "",
        f"- Prompt pairs compared: {len(pairs):,}. **Fail: {len(failed)}.** Review: {len(review)}.",
        f"- Fixture problems: **{len(fixture_problems)}**.",
    ]
    for p in failed:
        lines.append(f"  - FAIL {p.jaccard:.3f} / {p.run} words: `{p.distill.task_id}` vs `{p.bench.task_id}`")
    lines += [f"  - {x}" for x in fixture_problems]
    lines += [
        "",
        "## Review band (every pair)",
        "",
        "| Jaccard | Run | Distill | Distill prompt | Bench | Bench prompt |",
        "|---|---|---|---|---|---|",
    ]

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines += [
        f"| {p.jaccard:.3f} | {p.run} | `{p.distill.task_id}` | {cell(p.distill.text)}"
        f" | `{p.bench.task_id}` | {cell(p.bench.text)} |"
        for p in sorted(review, key=lambda p: (-p.jaccard, -p.run, p.distill.task_id, p.bench.task_id))
    ]
    probe = chat_probe(bench)
    dropped = [x for x in probe if x[1] != "kept"]
    lines += [
        "",
        "## Ordinary chat",
        "",
        f"`kestrel distill build` applies the same fail thresholds to every user message of every example,"
        f" including chats rated /good. Of {len(probe)} everyday requests, **{len(dropped)}** would be dropped:",
        "",
        "| Request | Pipeline |",
        "|---|---|",
    ]
    lines += [f"| {cell(text)} | {outcome} |" for text, outcome in probe]
    lines += [
        "",
        "Generic requests that happen to match a bench prompt's wording are lost from training. That is the intended"
        " trade: KestrelBench stays a fair test, and the distill task set supplies graded examples of the same"
        " behaviors in other words.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--distill-dir", type=Path, default=DISTILL_DIR, help="folder with tasks/ and workspace/")
    parser.add_argument("--bench-dir", type=Path, default=BENCH_DIR, help="KestrelBench folder on disk")
    parser.add_argument(
        "--sources", type=Path, default=SOURCES_FILE, help="pinned sources (default bench_sources.toml)"
    )
    parser.add_argument("--no-sources", action="store_true", help="files on disk only")
    parser.add_argument("--report", type=Path, help="also write the Markdown report here")
    args = parser.parse_args(argv)

    bench = load_bench(args.bench_dir, None if args.no_sources else args.sources)
    distill = distill_prompts(args.distill_dir / "tasks")
    pairs = compare(distill, bench.prompts)
    failed = failures(pairs)
    fixtures = fixture_failures(args.distill_dir, bench)
    review = [p for p in pairs if p.review]
    for s in bench.sources[1:]:
        print(f"Source {s['source']} @ {s['commit'][:12]}: " + ("read" if s.get("available") else s.get("note", "")))
    print(f"{len(distill)} distill prompts x {len(bench.prompts)} KestrelBench prompts = {len(pairs):,} pairs")
    print(f"Fail (exact, Jaccard >= {FAIL_JACCARD} or run >= {FAIL_RUN} words): {len(failed)}")
    for p in failed:
        print(
            f"  {p.jaccard:.3f} / {p.run}  {p.distill.task_id}: {p.distill.text!r}\n"
            f"            {p.bench.task_id}: {p.bench.text!r}"
        )
    print(f"Review (Jaccard >= {REVIEW_JACCARD} or run >= {REVIEW_RUN} words): {len(review)}")
    print(f"Fixture problems: {len(fixtures)}")
    for x in fixtures:
        print(f"  {x}")
    if args.report:
        args.report.write_text(report(bench, pairs, fixtures, args.distill_dir), encoding="utf-8", newline="\n")
        print(f"Report: {args.report}")
    ok = not failed and not fixtures
    print("Overlap check passed." if ok else "OVERLAP CHECK FAILED")
    return 0 if ok else 1
