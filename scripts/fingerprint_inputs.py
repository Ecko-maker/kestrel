"""What the agent fingerprint (bench/runner.py agent_fingerprint) hashes, rebuilt from a source tree, and a
readable diff of two such dumps. Offline: imports the tree's code, makes no model calls, reads no .env.

    git archive c7c0d4f src | tar -x -C <tmp>/old      (and 75ce54c into <tmp>/new)
    uv run --no-sync python -I scripts/fingerprint_inputs.py dump <tmp>/old/src <tmp>/old.json
    uv run --no-sync python -I scripts/fingerprint_inputs.py dump <tmp>/new/src <tmp>/new.json
    uv run --no-sync python -I scripts/fingerprint_inputs.py diff <tmp>/old.json <tmp>/new.json

Run each dump in its own process (-I, so only the given tree's `kestrel` is importable). The fingerprint is
computed for groq / openai/gpt-oss-120b with KESTREL_REQUEST_TIMEOUT unset, as the bench records it.
"""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

PROVIDER, MODEL = "groq", "openai/gpt-oss-120b"


def dump(src: Path) -> dict[str, Any]:
    sys.path.insert(0, str(src.resolve()))
    os.environ.pop("KESTREL_REQUEST_TIMEOUT", None)
    from kestrel import tools
    from kestrel.bench import runner

    return {
        "fingerprint": runner.agent_fingerprint(PROVIDER, MODEL),
        "system_prompt": runner.SYSTEM_PROMPT,
        "tools": {
            name: {"schema": t.schema, "risk": t.risk, "capabilities": sorted(getattr(t, "capabilities", ()) or ())}
            for name, t in sorted(tools.registry.tools.items())
        },
    }


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    fa, fb = a["fingerprint"], b["fingerprint"]
    out = [f"fingerprint {fa['sha']} -> {fb['sha']}"]
    for k in sorted((set(fa) | set(fb)) - {"sha"}):
        if fa.get(k) != fb.get(k):
            out.append(f"  {k}: {fa.get(k, '(absent)')} -> {fb.get(k, '(absent)')}")
    if a["system_prompt"] == b["system_prompt"]:
        out.append("system prompt: identical")
    else:
        out.append("system prompt:")
        out += [
            "  " + line
            for line in difflib.unified_diff(
                a["system_prompt"].splitlines(), b["system_prompt"].splitlines(), "old", "new", lineterm=""
            )
        ]
    ta, tb = a["tools"], b["tools"]
    for name in sorted(set(ta) - set(tb)):
        out.append(f"tool {name}: removed")
    for name in sorted(set(tb) - set(ta)):
        out.append(f"tool {name}: added")
    for name in sorted(set(ta) & set(tb)):
        x, y = ta[name], tb[name]
        fx, fy = x["schema"].get("function", x["schema"]), y["schema"].get("function", y["schema"])
        parts = []
        for k in sorted(set(fx) | set(fy)):
            if fx.get(k) != fy.get(k):
                parts.append(f"    schema.{k}: {json.dumps(fx.get(k), sort_keys=True)}")
                parts.append(f"         -> {json.dumps(fy.get(k), sort_keys=True)}")
        for k in ("risk", "capabilities"):
            if x[k] != y[k]:
                parts.append(f"    {k}: {x[k]} -> {y[k]}")
        out.append(f"tool {name}: " + ("identical" if not parts else "changed"))
        out += parts
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="rebuild the fingerprint's inputs from a source tree")
    d.add_argument("src", type=Path, help="the tree's src/ folder")
    d.add_argument("out", type=Path)
    c = sub.add_parser("diff", help="readable diff of two dumps")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        data = dump(args.src)
        args.out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        print(data["fingerprint"]["sha"])
    else:
        load = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
        print("\n".join(diff(load(args.a), load(args.b))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
