"""Template parity: does Ollama render the student's prompts exactly as training did? (no model calls)

For every fixed conversation in distill/parity/cases.json, and every point where the model starts a turn
(before each assistant message, and at the end), the prompt is rendered twice:
- by train_format.render(), the text the student is trained on;
- by Ollama's own renderer: distill/parity/main.go, built against the Ollama version in its go.mod, which
  runs the server's OpenAI conversion and template code on train_format.OLLAMA_TEMPLATE.
They must be identical, byte for byte. A difference exits 1 and prints the first differing line.

    uv run --no-sync python scripts/template_parity.py                 # Python vs Ollama (needs the renderer)
    uv run --no-sync python scripts/template_parity.py --write-golden  # refresh distill/parity/golden.json
    uv run --no-sync python scripts/template_parity.py --golden        # Python vs golden (no Go needed)
    uv run --no-sync python scripts/template_parity.py --write-cases   # rebuild cases.json (Kestrel's tools)

The renderer is %LOCALAPPDATA%/kestrel-tools/ollama-render.exe or $KESTREL_OLLAMA_RENDER (EXPORT.md says
how to build it). The golden file records the Ollama version and the template's SHA-256, so a test can
tell when it is stale.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kestrel.distill import train_format as tf  # noqa: E402

PARITY = ROOT / "distill" / "parity"
CASES = PARITY / "cases.json"
GOLDEN = PARITY / "golden.json"


def ollama_version() -> str:
    for line in (PARITY / "go.mod").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("require github.com/ollama/ollama"):
            return line.split()[-1]
    raise SystemExit("distill/parity/go.mod doesn't pin github.com/ollama/ollama")


def template_sha() -> str:
    return hashlib.sha256(tf.OLLAMA_TEMPLATE.encode("utf-8")).hexdigest()


def points(cases: list[dict[str, Any]]) -> list[tuple[str, list[dict], list[dict]]]:
    """(name, messages up to a generation point, tools) for every point in every case."""
    out = []
    for case in cases:
        msgs, tools = case["messages"], case.get("tools") or []
        cuts = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
        if msgs[-1]["role"] != "assistant":
            cuts.append(len(msgs))
        for k in cuts:
            out.append((f"{case['name']}@{k}", msgs[:k], tools))
    return out


def renderer() -> Path:
    path = Path(
        os.environ.get("KESTREL_OLLAMA_RENDER")
        or Path(os.environ.get("LOCALAPPDATA", "")) / "kestrel-tools" / "ollama-render.exe"
    )
    if not path.is_file():
        raise SystemExit(
            f"Ollama renderer not found at {path}: build it (distill/EXPORT.md, 'Template parity') or use --golden"
        )
    return path


def render_ollama(pts: list[tuple[str, list[dict], list[dict]]]) -> dict[str, str]:
    request = {
        "template": tf.OLLAMA_TEMPLATE,
        "cases": [{"name": n, "request": {"model": "student", "messages": m, "tools": t or None}} for n, m, t in pts],
    }
    done = subprocess.run(
        [str(renderer())], input=json.dumps(request), capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode:
        raise SystemExit(f"Ollama renderer failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def render_python(pts: list[tuple[str, list[dict], list[dict]]]) -> dict[str, str]:
    return {n: tf.render(m, t, generation_prompt=True)[0] for n, m, t in pts}


def first_difference(a: str, b: str) -> str:
    la, lb = a.split("\n"), b.split("\n")
    for i, (x, y) in enumerate(zip(la, lb, strict=False)):
        if x != y:
            return f"line {i + 1}:\n  training: {x!r}\n  ollama:   {y!r}"
    return f"line {min(len(la), len(lb)) + 1}: one text is longer ({len(la)} vs {len(lb)} lines)"


def compare(python: dict[str, str], other: dict[str, str], label: str) -> int:
    bad = [n for n in python if python[n] != other.get(n)]
    for n in bad:
        print(f"MISMATCH {n} (training vs {label}), {first_difference(python[n], other.get(n, ''))}")
    print(f"{len(python) - len(bad)}/{len(python)} prompts identical ({label}).")
    return 1 if bad else 0


def write_cases() -> None:
    from kestrel.tools import registry  # Kestrel's real tool schemas, including keys Ollama drops

    kestrel_tools = registry.schemas()
    rich_tool = {
        "type": "function",
        "function": {
            "name": "plan_trip",
            "description": "Plan a trip <with> 'quotes' & \"double\" — unicode é",
            "parameters": {
                "type": "object",
                "properties": {
                    "zeta": {"type": "string", "enum": ["a", "b"], "description": "last key, sorted first?"},
                    "alpha": {"type": ["string", "null"], "description": "two types"},
                    "stops": {"type": "array", "items": {"type": "object", "properties": {"z": {"type": "string"}}}},
                    "mode": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "nested": {
                        "type": "object",
                        "properties": {"second": {"type": "integer"}, "first": {"type": "string"}},
                        "required": ["first"],
                    },
                    "limit": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": ["zeta", "alpha"],
            },
        },
    }

    def call(cid: str, name: str, args: Any) -> dict:
        return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    read = call("c1", "read_file", {"path": "notes.txt"})
    tricky_args = {
        "z": 2.0,
        "a": 1e-07,
        "list": [1, "two", True, None],
        "obj": {"y": "<b>&</b>", "x": "tab\there\nnew"},
        "é": "ü",
    }
    cases = [
        {
            "name": "system-tools-user",
            "tools": kestrel_tools,
            "messages": [
                {"role": "system", "content": "You are Kestrel."},
                {"role": "user", "content": "What's in notes.txt?"},
            ],
        },
        {
            "name": "one-tool-call",
            "tools": kestrel_tools,
            "messages": [
                {"role": "system", "content": "You are Kestrel."},
                {"role": "user", "content": "Read notes.txt."},
                {"role": "assistant", "content": "", "tool_calls": [read]},
                {"role": "tool", "tool_call_id": "c1", "content": "<untrusted_data>milk, eggs</untrusted_data>"},
                {"role": "assistant", "content": "It lists milk and eggs."},
            ],
        },
        {
            "name": "content-and-two-calls",
            "tools": kestrel_tools,
            "messages": [
                {"role": "user", "content": "Read both files."},
                {
                    "role": "assistant",
                    "content": "Reading both.",
                    "tool_calls": [read, call("c2", "read_file", {"path": "todo.md"})],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "first"},
                {"role": "tool", "tool_call_id": "c2", "content": "second"},
                {"role": "assistant", "content": "Done."},
            ],
        },
        {
            "name": "multi-turn",
            "tools": kestrel_tools,
            "messages": [
                {"role": "system", "content": "You are Kestrel."},
                {"role": "user", "content": "Hi!"},
                {"role": "assistant", "content": "Hello! How can I help?"},
                {"role": "user", "content": "What time is it in Tokyo?"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [call("t1", "get_current_time", {"timezone": "Asia/Tokyo"})],
                },
                {"role": "tool", "tool_call_id": "t1", "content": "Saturday 2026-10-17 06:05:12 JST (UTC+0900)"},
                {"role": "assistant", "content": "It's 06:05 in Tokyo."},
                {"role": "user", "content": "Thanks."},
            ],
        },
        {
            "name": "no-tools-system",
            "tools": [],
            "messages": [{"role": "system", "content": "You are Kestrel."}, {"role": "user", "content": "Say hi."}],
        },
        {
            "name": "tools-no-system",
            "tools": kestrel_tools,
            "messages": [{"role": "user", "content": "List my files."}],
        },
        {
            "name": "tricky-json",
            "tools": [rich_tool],
            "messages": [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "Plan it."},
                {"role": "assistant", "content": "", "tool_calls": [call("p1", "plan_trip", tricky_args)]},
                {"role": "tool", "tool_call_id": "p1", "content": 'ok <done> & "quoted"'},
                {"role": "assistant", "content": "Planned."},
            ],
        },
        {
            "name": "collate",
            "tools": kestrel_tools,
            "messages": [
                {"role": "system", "content": "First system."},
                {"role": "system", "content": "Second system."},
                {"role": "user", "content": "Part one."},
                {"role": "user", "content": "Part two."},
                {"role": "assistant", "content": "Merged, then answered."},
                {"role": "user", "content": "Next."},
            ],
        },
        {
            "name": "empty-arguments",
            "tools": kestrel_tools,
            "messages": [
                {"role": "user", "content": "List the workspace."},
                {"role": "assistant", "content": "", "tool_calls": [call("l1", "list_files", {})]},
                {"role": "tool", "tool_call_id": "l1", "content": "notes.txt\ntodo.md"},
            ],
        },
        {
            "name": "rejection",
            "tools": kestrel_tools,
            "messages": [
                {"role": "user", "content": "Email sam@example.com that I'm late."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        call(
                            "s1", "send_message", {"to": "sam@example.com", "subject": "Late", "body": "Running late."}
                        )
                    ],
                },
                {"role": "tool", "tool_call_id": "s1", "content": "Rejected by the user: Too formal."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        call(
                            "s2",
                            "send_message",
                            {"to": "sam@example.com", "subject": "Late", "body": "Hey, running late!"},
                        )
                    ],
                },
                {"role": "tool", "tool_call_id": "s2", "content": "Message sent (simulated)."},
            ],
        },
    ]
    CASES.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(cases)} cases ({len(points(cases))} generation points) to {CASES}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--golden", action="store_true", help="compare training text with the golden file (no Go)")
    mode.add_argument("--write-golden", action="store_true", help="render with Ollama and write the golden file")
    mode.add_argument("--write-cases", action="store_true", help="rebuild cases.json from Kestrel's tools")
    args = parser.parse_args(argv)
    if args.write_cases:
        write_cases()
        return 0
    pts = points(json.loads(CASES.read_text(encoding="utf-8")))
    python = render_python(pts)
    if args.golden:
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        stale = golden["template_sha256"] != template_sha() or golden["ollama"] != ollama_version()
        if stale:
            print("golden.json is stale (template or Ollama version changed): run --write-golden")
            return 1
        return compare(python, golden["prompts"], f"golden, Ollama {golden['ollama']}")
    ollama = render_ollama(pts)
    status = compare(python, ollama, f"Ollama {ollama_version()}")
    if args.write_golden:
        if status:
            print("Not writing golden.json: the renderings differ.")
            return status
        golden = {"ollama": ollama_version(), "template_sha256": template_sha(), "prompts": ollama}
        GOLDEN.write_text(json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {GOLDEN}")
    return status


if __name__ == "__main__":
    sys.exit(main())
