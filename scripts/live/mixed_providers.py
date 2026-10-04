"""One conversation across providers: Gemini (tool call) -> Groq (uses that history) -> Gemini again.

Saves the exact request/response shapes of each model call (keys and text redacted) so a regression
test can replay them without network access.
"""

import json
import sys
from pathlib import Path

sys.argv = [sys.argv[0], "gemini,groq"]
import live_demo_lib as lib  # noqa: E402

from kestrel.agent import Agent  # noqa: E402
from kestrel.approval import ApprovalGate  # noqa: E402
from kestrel.llm import LLM  # noqa: E402

gemini, groq = LLM("gemini"), LLM("groq")
for backend in (gemini, groq):
    backend.max_retry_wait = 65  # sit out per-minute limits rather than fail this test
policy = lib.DemoPolicy()
agent = Agent(gemini, tracer=lib.tracer, gate=ApprovalGate(policy, log_path=lib.tmp / "approvals.jsonl"))

recorded = []
for label, backend in (("gemini", gemini), ("groq", groq), ("gemini", gemini)):
    original = backend._request

    def spy(_orig=original, _label=label, **kwargs):
        sent = [m for m in kwargs["messages"] if m.get("tool_calls")]
        recorded.append(
            {
                "provider": _label,
                "assistant_tool_call_messages_sent": [
                    {
                        "tool_calls": [
                            {k: (v if k != "function" else {"name": v["name"]}) for k, v in tc.items()}
                            for tc in m["tool_calls"]
                        ]
                    }
                    for m in sent
                ],
            }
        )
        return _orig(**kwargs)

    backend._request = spy

turns = [
    (gemini, "Read notes.txt and tell me in one sentence what step 2 is about."),
    (groq, "Using what you read earlier, what was step 1? Also compute 17.5% of 2,340 with the calculator."),
    (gemini, "Now what time is it in Tokyo? And which file did you read at the start of our chat?"),
]
for backend, prompt in turns:
    agent.llm = backend
    result = lib.run_prompt(prompt, agent=agent, policy=policy)
    if result.stop_reason != "answered":
        print("!!! turn failed:", result.text)
        break

out = Path(sys.path[0]) / "mixed_shapes.json"
out.write_text(json.dumps(recorded, indent=1), encoding="utf-8")
print("\nrecorded", len(recorded), "requests ->", out.name)
for r in recorded:
    shapes = [[sorted(tc.keys()) for tc in m["tool_calls"]] for m in r["assistant_tool_call_messages_sent"]]
    sig = [
        [tc.get("extra_content", {}).get("google", {}).get("thought_signature", "-")[:12] for tc in m["tool_calls"]]
        for m in r["assistant_tool_call_messages_sent"]
    ]
    print(f"  to {r['provider']:6}: tool-call messages in history {shapes}  signatures {sig}")
