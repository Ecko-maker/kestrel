"""scripts/fingerprint_inputs.py: a dump of this tree reproduces the bench's agent fingerprint, and the
diff names exactly what changed."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from kestrel.bench.runner import agent_fingerprint

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fingerprint_inputs.py"
spec = importlib.util.spec_from_file_location("fingerprint_inputs_script", SCRIPT)
assert spec is not None and spec.loader is not None
fi = importlib.util.module_from_spec(spec)
sys.modules["fingerprint_inputs_script"] = fi
spec.loader.exec_module(fi)


def test_dump_of_this_tree_reproduces_the_fingerprint(tmp_path, monkeypatch):
    monkeypatch.delenv("KESTREL_REQUEST_TIMEOUT", raising=False)
    out = tmp_path / "now.json"
    subprocess.run([sys.executable, "-I", str(SCRIPT), "dump", str(ROOT / "src"), str(out)], check=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["fingerprint"] == agent_fingerprint(fi.PROVIDER, fi.MODEL)
    assert len(data["tools"]) == data["fingerprint"]["tool_count"]


def _dump(prompt: str, desc: str, caps: list[str], **fp: object) -> dict:
    tool = {"schema": {"function": {"name": "t", "description": desc}}, "risk": "safe", "capabilities": caps}
    return {"fingerprint": {"sha": "x", **fp}, "system_prompt": prompt, "tools": {"t": tool}}


def test_diff_names_each_change():
    a = _dump("one\ntwo", "Reads.", [], tools="aaa")
    b = _dump("one\nthree", "Reads.", ["reads_local"], tools="bbb", approval_rules="tiers-v2.1")
    lines = fi.diff(a, b)
    assert "  approval_rules: (absent) -> tiers-v2.1" in lines and "  tools: aaa -> bbb" in lines
    assert "  -two" in lines and "  +three" in lines
    assert "tool t: changed" in lines and "    capabilities: [] -> ['reads_local']" in lines
    assert not any(line.startswith("    schema.") for line in lines)


def test_identical_dumps():
    a = _dump("p", "d", [])
    assert fi.diff(a, a)[1:] == ["system prompt: identical", "tool t: identical"]
