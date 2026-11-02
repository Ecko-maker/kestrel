"""The pinned option a collection and build manifests (distill/collection-a/): byte-identical, consistent with
each other, and a train/val split by task, rebuilt from the collection manifest's sessions. No data or model
calls: the traces and the dataset stay outside git."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from kestrel.bench.tasks import load_tasks
from kestrel.distill import dataset as ds

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "distill" / "collection-a"
COLLECT = PIN / "collect-manifest-groq-5b2a242.json"
BUILD = PIN / "build-manifest-d1.2-groq-gpt-oss-120b.json"
SHA = {
    COLLECT.name: "967f8bd5989e578716d17a401e0d6e099eb95224d9e968fc29ce32642fee9480",
    BUILD.name: "e39dd6616f6e5a248c2e5ecf04d61212211a92bb8e89137bc4775604866b93dc",
}
COMMIT = "5b2a242a4a5663bd64fe531c51212e12826ff62e"
TEACHER = [{"provider": "groq", "model": "openai/gpt-oss-120b"}]


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_the_pins_are_byte_identical_and_the_readme_says_so() -> None:
    readme = (PIN / "README.md").read_text(encoding="utf-8")
    for name, sha in SHA.items():
        assert hashlib.sha256((PIN / name).read_bytes()).hexdigest() == sha, name
        assert re.search(rf"`{re.escape(name)}` \| `{sha}`", readme), name


def test_the_collection_is_complete_on_the_one_teacher() -> None:
    c = load(COLLECT)
    meta, tasks = c["meta"], c["tasks"]
    assert (meta["split"], meta["split_version"], meta["provider"], meta["model"]) == (
        "distill",
        "d1.2",
        "groq",
        "openai/gpt-oss-120b",
    )
    assert meta["git"] == COMMIT[:7] and meta["agent"]["sha"] == "35fac5ba6277" and meta["judge"] is None
    assert len(tasks) == len({t["id"] for t in tasks}) == 122 and all(t["id"].startswith("dst-") for t in tasks)
    assert all(t["providers"] == ["groq"] and t["repeat"] == 1 for t in tasks)
    assert sum(t["status"] == "pass" for t in tasks) == 104 and sum(t["status"] == "fail" for t in tasks) == 18
    assert c["summary"] == {
        "runs": 122,
        "graded": 122,
        "passed": 104,
        "errors": 0,
        "skipped": 0,
        "excluded": 0,
        "billable_tokens_total": 206_378,
    }


def test_the_build_is_from_this_collection_at_its_commit() -> None:
    b, c = load(BUILD), load(COLLECT)
    assert b["teachers"] == TEACHER and b["git"] == {"commit": COMMIT, "dirty": False}
    assert b["results"] == {c["results_file"]: {"sha256": c["results_sha256"], "source": "distill"}}
    assert b["input_db_sha256"] == c["traces_db_sha256"]
    assert b["settings"]["filters"] == list(ds.FILTERS)  # today's pipeline, as train.py requires
    dropped = b["stats"]["dropped"]
    assert {k: v for k, v in dropped.items() if v} == {"quality": 20, "rubric_task": 22}
    assert (b["stats"]["kept"], b["stats"]["train"], b["stats"]["val"]) == (89, 71, 18)
    assert b["stats"]["traces_seen"] == c["traces_in_db"] == 131


def test_the_split_is_by_task() -> None:
    """Rebuild train/val from the collection manifest alone: one session per task, the build's session hash
    (dataset.in_val at the build's val_frac), the kept tasks = passed tasks without a rubric. It must give the
    build's counts, and no task can be on both sides."""
    b, c = load(BUILD), load(COLLECT)
    tasks = c["tasks"]
    assert all(len(t["sessions"]) == 1 for t in tasks)  # every trace of a task is in one session
    sessions = [t["sessions"][0] for t in tasks]
    assert len(set(sessions)) == len(sessions)  # and no session holds two tasks
    rubric = {t.id for t in load_tasks(ROOT / "distill" / "tasks") if t.rubric is not None}
    kept = [t for t in tasks if t["status"] == "pass" and t["id"] not in rubric]
    side = {t["id"]: "val" if ds.in_val(t["sessions"][0], b["settings"]["val_frac"]) else "train" for t in kept}
    examples = {s: sum(t["traces"] for t in kept if side[t["id"]] == s) for s in ("train", "val")}
    assert examples == {"train": b["stats"]["train"], "val": b["stats"]["val"]}
    assert (sum(v == "train" for v in side.values()), sum(v == "val" for v in side.values())) == (
        b["stats"]["sessions"]["train"],
        b["stats"]["sessions"]["val"],
    )
