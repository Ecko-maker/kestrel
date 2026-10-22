"""The web console in demo mode on a throwaway workspace, plus an optional screenshot run.

Uses Kestrel's built-in demo provider (src/kestrel/demo.py): everything is real except
the model. It runs on a temporary copy of workspace/ with its own trace database, so
your real files and traces are untouched.

    uv run python scripts/demo_console.py            # serve it; open the printed URL yourself
    uv run python scripts/demo_console.py --shots    # drive Edge with Playwright, save screenshots

--shots also saves docs/screenshots/theme/: idle, thinking, tool, approval and error in the
classic and HUD looks, plus the HUD at phone width.
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

from kestrel.demo import DEMO_NOTICE, DEMO_PROMPTS, DemoLLM
from kestrel.llm import LLMError

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8799

NOTE, EMAIL, SUSPICIOUS = DEMO_PROMPTS[3], DEMO_PROMPTS[4], DEMO_PROMPTS[5]
TOKYO = DEMO_PROMPTS[0]
OUTAGE = "Show me what a provider outage looks like"
THEME_SHOTS = SHOTS / "theme"


class ShotsLLM(DemoLLM):
    """The demo model, plus one prompt that fails like a provider outage, for the error screenshot.
    The agent's real error path handles it (LLMError -> error event -> error answer)."""

    def chat(self, messages, tools=None, on_text=None):
        if str(messages[-1].get("content")) == OUTAGE:
            time.sleep(self.delay)
            raise LLMError("demo", "simulated outage for the screenshot run (503 Service Unavailable)")
        return super().chat(messages, tools, on_text)


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
    llm = ShotsLLM(delay=0.6)

    def make_agent(on_event, approver):
        return Agent(
            llm, on_event=on_event, tracer=tracer, gate=ApprovalGate(approver, log_path=tmp / "approvals.jsonl")
        )

    info = {
        "demo": True,
        "demo_notice": DEMO_NOTICE,
        "demo_prompts": DEMO_PROMPTS,
        "chat_available": True,
        "problem": None,
        "models": [{"provider": "demo", "model": "scripted"}],
        "tools": [{"name": t.name, "risk": t.risk, "server": t.server} for t in tools.registry.tools.values()],
    }
    config = WebConfig(port=PORT, session_info=info)
    server = uvicorn.Server(
        uvicorn.Config(create_app(make_agent, tracer, config), host="127.0.0.1", port=PORT, log_level="warning")
    )
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
        THEME_SHOTS.mkdir(exist_ok=True)
        for look in ("classic", "hud"):
            theme_shots(browser, url, look)
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
    page.wait_for_selector("[data-approval=send_message] >> text=running about 10 minutes late")
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


class EventGate:
    """Forwards the console's WebSocket traffic and can hold the server's events after a chosen one,
    so a short state (thinking, a running tool) stays on screen for a screenshot. The events are
    the agent's real ones, only delayed."""

    def __init__(self):
        self.client = None
        self.pause_after: str | None = None
        self.paused = False
        self.held: list = []

    def route(self, ws) -> None:
        server = ws.connect_to_server()
        self.client = ws
        ws.on_message(server.send)
        server.on_message(self._from_server)

    def _from_server(self, message) -> None:
        if self.paused:
            self.held.append(message)
            return
        self.client.send(message)
        if self.pause_after and json.loads(message).get("type") == self.pause_after:
            self.paused, self.pause_after = True, None

    def wait_paused(self, page) -> None:
        for _ in range(200):
            if self.paused:
                return
            page.wait_for_timeout(50)  # Playwright delivers WebSocket callbacks while waiting
        raise TimeoutError("the expected event never came")

    def release(self) -> None:
        self.paused = False
        held, self.held = self.held, []
        for message in held:
            self._from_server(message)


def theme_shots(browser, url: str, look: str) -> None:
    """Idle, thinking, tool, approval and error in one look; for the HUD also the phone width."""
    out = THEME_SHOTS
    # 1x: the HUD's grid texture makes 2x PNGs ~4 times bigger
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme="light")
    page.add_init_script(f"try {{ localStorage.setItem('kestrel-look', '{look}') }} catch (e) {{}}")
    gate = EventGate()
    page.route_web_socket("**/ws", gate.route)
    page.goto(url)
    page.wait_for_selector("text=Connected")
    page.wait_for_timeout(1600)  # the HUD's boot sequence (1.2 s) has finished
    page.screenshot(path=out / f"{look}-idle.png")

    box = page.get_by_label("Message")

    def send(text):
        box.fill(text)
        box.press("Enter")

    def wait_idle():
        page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        page.wait_for_timeout(300)

    gate.pause_after = "step_started"  # model call in flight
    send(TOKYO)
    gate.wait_paused(page)
    page.wait_for_timeout(700)
    page.screenshot(path=out / f"{look}-thinking.png")
    gate.pause_after = "tool_call"  # tool running, its result held back
    gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(500)
    page.screenshot(path=out / f"{look}-tool.png")
    gate.release()
    wait_idle()

    send(NOTE)
    page.wait_for_selector("[data-approval=create_note]")
    page.wait_for_timeout(500)
    page.screenshot(path=out / f"{look}-approval.png")
    page.get_by_role("button", name="Approve", exact=True).click()
    wait_idle()

    send(OUTAGE)
    page.locator("text=simulated outage >> visible=true").first.wait_for()
    page.wait_for_timeout(1000)  # after the red flash, the steady red state
    page.screenshot(path=out / f"{look}-error.png")
    page.close()

    if look == "hud":
        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1.5)
        phone.add_init_script("try { localStorage.setItem('kestrel-look', 'hud') } catch (e) {}")
        phone.goto(url)
        phone.wait_for_selector("textarea[placeholder^='Ask Kestrel']")
        phone.wait_for_timeout(1600)
        phone.screenshot(path=out / "hud-mobile-idle.png")
        phone.get_by_label("Message").fill(NOTE)
        phone.get_by_label("Message").press("Enter")
        phone.wait_for_selector("[data-approval=create_note]")
        phone.wait_for_timeout(500)
        phone.screenshot(path=out / "hud-mobile-approval.png")
        phone.get_by_role("button", name="Approve", exact=True).click()
        phone.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        phone.close()


def show_results(tmp: Path) -> None:
    outbox = sorted(p.name for p in (tmp / "workspace" / "outbox").iterdir())
    print("outbox:", outbox)
    print("audit log:")
    for line in (tmp / "approvals.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        target = e["args"].get("to", e["args"].get("title", ""))
        print(f"  {e['tool']:<13} {e['decision']:<9} {target!s:<22} {e['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true", help="take screenshots with Edge (Playwright)")
    args = parser.parse_args()
    url, tmp = start_server()
    print(f"Demo console: {url}")
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
