"""The data step's queue, offline: the frozen copy and its pins, the queue's own decisions (run in PowerShell from the
file's functions, with no model call), the run commands against the verdict's files, and the estimates."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [s for s in ("pwsh", "powershell") if shutil.which(s)]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_dq", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{name}_dq"] = module
    spec.loader.exec_module(module)
    return module


freeze = load("data_freeze")
estimate = load("data_estimate")
verdict = load("rca_verdict")
QUEUE = (ROOT / "scripts" / freeze.QUEUE).read_text(encoding="utf-8")


def repo_files() -> dict[str, bytes]:
    return {Path(p).name: (ROOT / p).read_bytes().replace(b"\r\n", b"\n") for p in freeze.FILES}


# --- the frozen copy ----------------------------------------------------------------------------------------------


def test_the_queue_pins_the_helpers_it_ships_with() -> None:
    contents = repo_files()
    assert set(freeze.pins(contents[freeze.QUEUE].decode("utf-8"))) == {"groq_quota.py", "data_gate.py"}
    assert freeze.check(contents, freeze.QUEUE) == []
    contents["data_gate.py"] += b"# changed\n"
    assert [p.split(":")[0] for p in freeze.check(contents, freeze.QUEUE)] == ["data_gate.py"]


def test_the_copy_is_written_once_outside_git(tmp_path: Path) -> None:
    dest, contents = tmp_path / "kestrel-data-queue", repo_files()
    manifest = freeze.write(contents, dest, "abc123", freeze.LABEL).read_text(encoding="utf-8")
    assert "data queue, from commit abc123" in manifest and "data_freeze.py" in manifest
    with pytest.raises(SystemExit, match="already holds other files"):
        freeze.write({**contents, "data_gate.py": b"other\n"}, dest, "def456", freeze.LABEL)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit, match="inside a git folder"):
        freeze.write(contents, tmp_path / "repo" / "queue", "abc123", freeze.LABEL)


def test_the_gate_helper_imports_nothing_from_kestrel() -> None:
    text = (ROOT / "scripts" / "data_gate.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import) kestrel", text, re.MULTILINE)


# --- the queue's text ---------------------------------------------------------------------------------------------


def test_files_and_locks_are_the_verdicts() -> None:
    named = dict(re.findall(r'^\$(B1|A1b?|A2|A3) = "\$Results\\([^"]+)"', QUEUE, re.MULTILINE))
    assert named == {s: x.name for s, x in verdict.SPECS.items()}
    assert f'"$Results\\{verdict.QUEUE_LOCK}"' in QUEUE and f'$Tag = "{verdict.TAG}"' in QUEUE


def test_run_counts_are_the_verdicts() -> None:
    got = {m[1]: int(m[2]) for m in re.finditer(r"Invoke-QueueRun \"(\w+) [^\"]*\" \$\w+ \$\w+ (\d+) ", QUEUE)}
    assert got == {s: x.runs for s, x in verdict.SPECS.items()}  # A3's last step is 30 (the first is 5)
    assert "$RunA3First $A3 5 $CapA3First" in QUEUE


def test_every_cap_starts_at_zero_and_the_rules_are_in() -> None:
    assert dict(re.findall(r"\[int\]\$(Cap\w+) = (\d+)", QUEUE)) == dict.fromkeys(
        ("CapB1", "CapA1", "CapA1b", "CapA2", "CapA3First", "CapA3"), "0"
    )
    assert "$Margin = 20000" in QUEUE and "--wait-for-quota" in QUEUE
    assert QUEUE.isascii()  # Windows PowerShell 5.1 reads a BOM-less file as ANSI
    gate = QUEUE.index("$g = Get-Gate")
    assert QUEUE.index('Invoke-QueueRun "A1b') < gate < QUEUE.index('Invoke-QueueRun "A2')
    assert gate < QUEUE.index('if ($g -eq "FAIL")') < QUEUE.index('Invoke-QueueRun "A2') < QUEUE.index('"A3 rca, first')


def test_only_b1_runs_without_the_data_tools() -> None:
    runs = dict(re.findall(r"^\$Run(\w+) = (.*)$", QUEUE, re.MULTILINE))
    assert "--tools" not in runs["B1"]
    assert all('"--tools", "data"' in runs[r] for r in ("A1", "A1b", "A2", "A3"))
    assert '"rca-*-1"' in runs["A3First"] and runs["A3First"].startswith("$RunA3 +")


def test_the_first_five_are_one_per_scenario() -> None:
    import fnmatch

    from kestrel.bench import rca

    first = [t for t in rca.load_rca().tasks if fnmatch.fnmatch(t.id, "rca-*-1")]
    assert len(first) == 5 and len({t.meta.scenario for t in first}) == 5


# --- the queue's decisions, in PowerShell ---------------------------------------------------------------------------


def functions() -> str:
    """The functions only: nothing that starts a run."""
    return QUEUE[QUEUE.index("function Log(") : QUEUE.index("# --- start ---")]


def powershell(shell: str, body: str, folder: Path) -> str:
    script = folder / "t.ps1"
    script.write_text(
        '$ErrorActionPreference = "Stop"\n$Margin = 20000\n$NeedFree = $Margin\n$Results = "r"\n'
        '$QueueLog = "q.log"\n$QueueLock = "q.lock"\n' + functions() + body,
        encoding="ascii",
    )
    got = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        cwd=folder,
        timeout=120,
        check=False,
    )
    return got.stdout + got.stderr


@pytest.mark.skipif(not SHELLS, reason="no PowerShell")
@pytest.mark.parametrize("shell", SHELLS)
def test_attempt_decisions(shell: str, tmp_path: Path) -> None:
    cases = [
        (100_000, 0, 50_000, "start 50000"),  # free is the smaller
        (100_000, 0, 500_000, "start 80000"),  # the cap minus the margin
        (100_000, 60_000, 500_000, "start 20000"),
        (100_000, 60_001, 500_000, "stop 0"),
        (39_999, 0, 500_000, "stop 0"),
        (100_000, 0, 0, "wait 0"),
        (100_000, 0, -5_000, "wait 0"),  # never on negative quota
        (100_000, 0, 19_999, "wait 19999"),
    ]
    body = "".join(
        f'$d = Get-AttemptDecision {cap} {spent} {free}; Write-Output "$($d.action) $($d.budget)"\n'
        for cap, spent, free, _ in cases
    )
    assert powershell(shell, body, tmp_path).split("\n")[: len(cases)] == [c[3] for c in cases]


@pytest.mark.skipif(not SHELLS, reason="no PowerShell")
@pytest.mark.parametrize("shell", SHELLS)
def test_a3_is_done_at_5_then_at_30(shell: str, tmp_path: Path) -> None:
    def file(name: str, graded: int, total: int) -> None:
        rows = [{"status": "pass" if i < graded else "skipped"} for i in range(total)]
        data = {"tasks": rows, "summary": {"billable_tokens_total": 7}, "meta": {"agent": {"sha": "x"}}}
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")

    states = [(4, 5), (5, 5), (5, 30), (30, 30)]
    body = ""
    for i, (g, t) in enumerate(states):
        file(f"f{i}.json", g, t)
        body += f'$c = Get-Counts "f{i}.json"; Write-Output "$(Test-StepDone $c 5) $(Test-StepDone $c 30)"\n'
    body += '$c = Get-Counts "missing.json"; Write-Output "$(Test-StepDone $c 5) $(Test-StepDone $c 30)"\n'
    got = powershell(shell, body, tmp_path).split("\n")[:5]
    assert got == ["False False", "True False", "True False", "True True", "False False"]


@pytest.mark.skipif(not SHELLS, reason="no PowerShell")
@pytest.mark.parametrize("shell", SHELLS)
def test_a_run_with_no_cap_stops_before_anything_starts(shell: str, tmp_path: Path) -> None:
    body = 'Invoke-QueueRun "A2 main" @("x") "none.json" 100 0 "CapA2" ""\nWrite-Output "REACHED"\n'
    out = powershell(shell, body, tmp_path)
    assert "no approved cap" in out and "-CapA2" in out and "REACHED" not in out and "START" not in out


@pytest.mark.skipif(not SHELLS, reason="no PowerShell")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("started", "noted"), [("2020-01-01T08:00:00-05:00", True), ("2999-01-01T08:00:00Z", False)])
def test_the_drift_note(shell: str, started: str, noted: bool, tmp_path: Path) -> None:
    (tmp_path / "b1.json").write_text(json.dumps({"meta": {"attempts": [{"started": started}]}}), encoding="utf-8")
    out = powershell(shell, 'Write-DriftNote "b1.json" "a2.json"\nWrite-Output "done"\n', tmp_path)
    assert "done" in out and ("DRIFT CONTROL NEEDED" in out) is noted


@pytest.mark.skipif(not SHELLS, reason="no PowerShell")
@pytest.mark.parametrize("shell", SHELLS)
def test_it_refuses_to_run_from_the_repo(shell: str) -> None:
    got = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / freeze.QUEUE), "-DryRun"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert got.returncode == 1 and "frozen copy" in got.stdout


# --- the estimates --------------------------------------------------------------------------------------------------


def test_the_schema_sizes_behind_f() -> None:
    """The default, Google and data registries' schema characters."""
    from kestrel.bench.stubs import main_registry

    def size(**kw: bool) -> int:
        return sum(len(json.dumps(s)) for s in main_registry([], **kw).schemas())

    base = size()
    assert size(google=True) - base == estimate.GOOGLE_CHARS
    assert size(data=True) - base == estimate.DATA_CHARS
    assert round(estimate.F_PLAN, 2) == 1.62


def test_the_rows() -> None:
    table = {r.run.split(" ")[0] + (" 25" if "25" in r.run else ""): r for r in estimate.rows()}
    assert round(table["B1"].plan) == 271_287
    assert round(table["A1"].plan) == round(264_000 * 1.178 * estimate.F_PLAN)
    assert round(table["A2"].high) == round(238_120 * 2.185 + 33_167)
    assert round(table["A3 25"].plan) == round(25 * 15_500 * 1.31)
    assert all(r.low <= r.plan <= r.high and r.cap >= r.plan * 1.15 for r in table.values())
    assert all(r.cap % 1000 == 0 and r.cap >= 40_000 for r in table.values())  # under 40k never starts


def test_a1_gives_f(tmp_path: Path) -> None:
    base = estimate.V21_ATTACKS * estimate.B2 / estimate.V21_BENIGN
    rows = [{"id": f"atk-{i}", "status": "pass", "tokens": round(base * 2 / 90), "cached_tokens": 0} for i in range(90)]
    rows += [{"id": "atk-data-send", "status": "pass", "tokens": 10**6, "cached_tokens": 0}]  # new: not in f
    assert round(estimate.measured_f({"tasks": rows}), 2) == 2.0
    rows[0]["status"] = "skipped"
    with pytest.raises(SystemExit, match="aren't all graded"):
        estimate.measured_f({"tasks": rows})
