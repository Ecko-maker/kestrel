"""The agent loop: let the model call tools until it can answer in plain text.

Robustness lives here too: tool calls in one turn run in parallel, history is kept
under a token budget, and every run ends with an explicit reason instead of a crash.
"""

import json
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal, Protocol

from kestrel.llm import LLMError
from kestrel.tools import ToolRegistry, registry as default_registry

SYSTEM_PROMPT = (
    "You are Kestrel, a concise and helpful personal AI assistant. "
    "Use your tools whenever they give a more accurate answer than memory: the time, "
    "arithmetic, the user's workspace files, or anything recent on the web. "
    "If a tool returns an error, read it, fix your call, and try again."
)
MAX_CONTEXT_TOKENS = 16_000  # history budget; Groq's free tier limits tokens per minute
MAX_PARALLEL_TOOLS = 8


class ChatModel(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...


# Called after each tool runs: (name, arguments_json, result)
StepCallback = Callable[[str, str, str], None]


@dataclass
class AgentResult:
    text: str
    steps: int  # model calls made for this message
    stop_reason: Literal["answered", "max_steps", "error"]
    providers: list[str] = field(default_factory=list)  # who answered, in order of first use


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token count: ~4 characters per token is close enough for budgeting."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        tools: ToolRegistry = default_registry,
        max_steps: int = 8,
        system_prompt: str = SYSTEM_PROMPT,
        on_tool_step: StepCallback | None = None,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
    ):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.on_tool_step = on_tool_step
        self.max_context_tokens = max_context_tokens
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def run(self, user_text: str) -> AgentResult:
        """Answer one user message, running as many tool rounds as needed (up to max_steps)."""
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        steps, providers, tools_used = 0, [], Counter()

        try:
            while steps < self.max_steps:
                self.trim_history()
                reply = self.llm.chat(self.messages, self.tools.schemas())
                steps += 1
                provider = getattr(self.llm, "last_provider", None)
                if provider and provider not in providers:
                    providers.append(provider)
                self.messages.append(reply)

                calls = reply.get("tool_calls")
                if not calls:
                    text = reply.get("content") or "(The model returned an empty reply. Try rephrasing.)"
                    return AgentResult(text, steps, "answered", providers)

                for call, result in zip(calls, self._run_tools(calls)):
                    tools_used[call.get("function", {}).get("name", "?")] += 1
                    self.messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        except LLMError as e:
            del self.messages[checkpoint:]  # roll back the turn so history stays valid for next time
            return AgentResult(f"Sorry, I couldn't get an answer from any model. {e}", steps, "error", providers)
        except Exception as e:  # a bug or a response shape we didn't expect: report it, don't crash
            del self.messages[checkpoint:]
            return AgentResult(f"Sorry, something went wrong: {type(e).__name__}: {e}", steps, "error", providers)

        used = ", ".join(f"{name} x{n}" for name, n in tools_used.items())
        text = (f"I hit my limit of {self.max_steps} steps before finishing, so I stopped rather than "
                f"loop forever. Tools I ran: {used}. Ask me to continue, or try a narrower question.")
        self.messages.append({"role": "assistant", "content": text})
        return AgentResult(text, steps, "max_steps", providers)

    def _run_tools(self, calls: list[dict]) -> list[str]:
        """Run all tool calls from one model turn concurrently; results keep the calls' order."""

        def run_one(call: dict) -> str:
            fn = call.get("function") or {}
            return self.tools.execute(fn.get("name", ""), fn.get("arguments"))

        if len(calls) == 1:
            results = [run_one(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(len(calls), MAX_PARALLEL_TOOLS)) as pool:
                results = list(pool.map(run_one, calls))  # map() returns results in input order

        if self.on_tool_step:  # report after, in order, so parallel output isn't interleaved
            for call, result in zip(calls, results):
                fn = call.get("function") or {}
                self.on_tool_step(fn.get("name", "?"), fn.get("arguments") or "{}", result)
        return results

    def trim_history(self) -> None:
        """Drop the oldest whole turns until history fits the budget. A turn is a user
        message plus everything after it up to the next user message, so a tool call is
        never separated from its result. The system prompt and current turn always stay."""
        while estimate_tokens(self.messages) > self.max_context_tokens:
            turn_starts = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
            if len(turn_starts) < 2:
                return  # only the current turn is left; nothing safe to drop
            del self.messages[turn_starts[0]:turn_starts[1]]
