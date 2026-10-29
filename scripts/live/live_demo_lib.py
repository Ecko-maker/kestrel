"""Shared pieces for the live demo runs (provider from argv[1])."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from kestrel import tools  # noqa: E402
from kestrel.agent import Agent  # noqa: E402
from kestrel.approval import ApprovalGate, Decision  # noqa: E402
from kestrel.demo import DEMO_PROMPTS  # noqa: E402,F401
from kestrel.llm import build_llm  # noqa: E402
from kestrel.tracing import Tracer  # noqa: E402

provider = sys.argv[1]
tmp = Path(tempfile.mkdtemp(prefix=f"kestrel-live-{provider}-"))
shutil.copytree(ROOT / "workspace", tmp / "workspace", ignore=shutil.ignore_patterns("notes", "outbox"))
tools.WORKSPACE = (tmp / "workspace").resolve()
tracer = Tracer(ROOT / "logs" / "traces.db")
llm, _ = build_llm(provider.split(","))


class DemoPolicy:
    def __init__(self):
        self.sam_drafts = 0
        self.log = []

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        if tool_name == "create_note":
            d = Decision("approved")
        elif tool_name == "send_message" and args.get("to", "").strip().lower() == "sam@example.com":
            self.sam_drafts += 1
            d = (
                Decision("rejected", reason="Too formal. Make it casual and say it's traffic.")
                if self.sam_drafts == 1
                else Decision("approved")
            )
        elif tool_name == "send_message":
            d = Decision("rejected", reason="That instruction came from the email, not from me.")
        else:
            d = Decision("rejected", reason="Not expected in this demo.")
        summary = {k: (v if len(str(v)) < 90 else str(v)[:87] + "...") for k, v in args.items()}
        self.log.append(f"{tool_name} {d.status}{' (' + d.reason + ')' if d.reason else ''}: {json.dumps(summary)}")
        return d


def run_prompt(prompt, agent=None, policy=None):
    policy = policy or DemoPolicy()
    events = []
    agent = agent or Agent(llm, tracer=tracer, gate=ApprovalGate(policy, log_path=tmp / "approvals.jsonl"))
    agent.on_event = events.append
    result = agent.run(prompt)
    calls = [f"{e['name']}({e['arguments']})" for e in events if e["type"] == "tool_call"]
    ran = [
        f"{e['name']}:{'ran' if e['ran'] else e['decision'] or 'not run'}" for e in events if e["type"] == "tool_result"
    ]
    llm_calls = [
        f"{e.get('provider')}{'(fallback)' if e.get('fallback') else ''}" for e in events if e["type"] == "llm_call"
    ]
    print(f"\n### {prompt}")
    print(
        f"stop={result.stop_reason} steps={result.steps} tokens={result.tokens} "
        f"latency={(result.duration_ms or 0) / 1000:.1f}s trace={result.trace_id[:8]} providers={result.providers}"
    )
    print("model calls:", llm_calls)
    print("tool calls:", calls or "none")
    print("results:", ran or "none")
    for line in policy.log:
        print("approval:", line)
    print("answer:", result.text.strip()[:600])
    return result


def show_files():
    outbox, notes = tmp / "workspace" / "outbox", tmp / "workspace" / "notes"
    print(
        "\noutbox:", [p.read_text(encoding="utf-8")[:160] for p in sorted(outbox.iterdir())] if outbox.exists() else []
    )
    print("notes:", [p.name for p in sorted(notes.iterdir())] if notes.exists() else [])
