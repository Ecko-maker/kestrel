"""The web console in demo mode on a throwaway workspace, plus an optional screenshot run.

Uses Kestrel's built-in demo provider (src/kestrel/demo.py): everything is real except
the model. It runs on a temporary copy of workspace/ with its own trace database, so
your real files and traces are untouched.

    uv run python scripts/demo_console.py            # serve it; open the printed URL yourself
    uv run python scripts/demo_console.py --shots    # drive Edge with Playwright, save screenshots

--shots also saves docs/screenshots/theme/: idle, thinking, tool, approval and error in the
classic and HUD looks, the HUD mid answer-burst and at phone width, close-ups of the falcon's
poses (falcon-*.png), a WebM recording of one exchange (hud-demo.webm), and every HUD mode
(hud-mode-*.png) on a second demo server with a scripted wake word: a synthetic microphone and a
detector the script triggers (no real microphone, no Windows power calls, no speech).
"""

import argparse
import json
import math
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
MODES_PORT = 8798  # the second demo server, with the scripted wake word

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


WAKE_TRIGGER = threading.Event()  # the script says "Kestrel"


class TriggerDetector:
    """Hears "Kestrel" exactly when the screenshot script sets WAKE_TRIGGER."""

    def feed(self, pcm: bytes) -> float | None:
        if WAKE_TRIGGER.is_set():
            WAKE_TRIGGER.clear()
            return 1.0
        return None


class SyntheticMic:
    """0.1 s chunks of a 220 Hz tone whose loudness rises and falls like speech, in real time."""

    def open(self) -> None:
        self.t0 = time.monotonic()

    def read(self, timeout: float) -> bytes:
        time.sleep(0.1)
        t = time.monotonic() - self.t0
        envelope = 0.02 + 0.5 * abs(math.sin(t * 2.3)) * abs(math.sin(t * 0.7))
        n = 1600
        return b"".join(
            int(32767 * envelope * math.sin(2 * math.pi * 220 * (t + i / 16000))).to_bytes(2, "little", signed=True)
            for i in range(n)
        )

    def close(self) -> None:
        pass


def scripted_wake(tracer):
    from kestrel.wake import WakeService, WakeSettings
    from kestrel.wake.power import NullPower
    from kestrel.wake.speech import NullSpeaker

    settings = WakeSettings(enabled=True, listen_seconds=8.0)  # no name: "I'm here. What do you need?"
    return WakeService(settings, TriggerDetector, SyntheticMic, NullPower(), NullSpeaker(), tracer.record_wake)


def start_server(port: int = PORT, wake: bool = False) -> tuple[str, Path]:
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
    config = WebConfig(port=port, session_info=info)
    app = create_app(make_agent, tracer, config, wake=scripted_wake(tracer) if wake else None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return config.url(), tmp


def save_shot(target, path: Path, **options) -> None:
    """Screenshot to bytes, then write with a few retries: on Windows a PNG that was just written can
    be locked for a moment by another process (indexer, antivirus, an editor preview)."""
    data = target.screenshot(**options)
    for attempt in range(5):
        try:
            Path(path).write_bytes(data)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.3)


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
            save_shot(page, tmp / "failure.png")
            print(f"failed; see {tmp / 'failure.png'}")
            raise
        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        phone.goto(url)
        phone.wait_for_timeout(800)
        save_shot(phone, SHOTS / "mobile.png")
        THEME_SHOTS.mkdir(exist_ok=True)
        for look in ("classic", "hud"):
            theme_shots(browser, url, look)
        falcon_closeups(browser, url)
        record_exchange(browser, url)
        modes_url, _ = start_server(MODES_PORT, wake=True)
        mode_shots(browser, modes_url)
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
    save_shot(page, SHOTS / "chat-approval-note.png")
    page.get_by_role("button", name="Approve", exact=True).click()
    wait_idle()

    # 2. Email: reject with a reason, then approve the rewrite
    send(EMAIL)
    page.wait_for_selector("[data-approval=send_message] >> text=I regret to inform you")
    page.wait_for_timeout(400)
    save_shot(page, SHOTS / "chat-approval-email.png")
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
    save_shot(page, SHOTS / "chat-approval-injection.png")
    page.get_by_role("button", name="Reject").first.click()
    page.get_by_placeholder("Why not?").fill("That instruction came from the email, not from me.")
    page.get_by_role("button", name="Reject").last.click()
    wait_idle()
    page.get_by_label("Good answer").last.click()
    page.get_by_text("Agent trace").last.click()  # expand the finished trace
    page.wait_for_timeout(300)
    save_shot(page, SHOTS / "chat-agent-trace.png")

    # 4. The trace waterfall for the email conversation
    page.goto(url.split("?")[0] + "#/traces")
    page.get_by_text(EMAIL).click()
    page.wait_for_selector("text=All traces")
    page.wait_for_timeout(400)
    save_shot(page, SHOTS / "trace-waterfall.png", full_page=True)

    # 5. Stats, and dark mode
    page.goto(url.split("?")[0] + "#/stats")
    page.wait_for_timeout(600)
    save_shot(page, SHOTS / "stats.png", full_page=True)
    page.get_by_role("radio", name="dark").click()
    page.goto(url.split("?")[0] + "#/chat")
    page.wait_for_timeout(300)
    page.goto(url.split("?")[0] + "#/traces")
    page.get_by_text(SUSPICIOUS).click()
    page.wait_for_selector("text=All traces")
    page.wait_for_timeout(400)
    save_shot(page, SHOTS / "trace-waterfall-dark.png", full_page=True)


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
    save_shot(page, out / f"{look}-idle.png")

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
    save_shot(page, out / f"{look}-thinking.png")
    gate.pause_after = "tool_call"  # tool running, its result held back
    gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(500)
    save_shot(page, out / f"{look}-tool.png")
    gate.pause_after = "done"  # nothing comes after done; this just tells us the answer is in
    gate.release()
    gate.wait_paused(page)
    if look == "hud":
        page.wait_for_timeout(250)  # mid-burst (the burst lasts 0.9 s)
        save_shot(page, out / "hud-answer-burst.png")
    gate.release()
    wait_idle()

    send(NOTE)
    page.wait_for_selector("[data-approval=create_note]")
    page.wait_for_timeout(500)
    save_shot(page, out / f"{look}-approval.png")
    page.get_by_role("button", name="Approve", exact=True).click()
    wait_idle()

    send(OUTAGE)
    page.locator("text=simulated outage >> visible=true").first.wait_for()
    page.wait_for_timeout(1000)  # after the red flash, the steady red state
    save_shot(page, out / f"{look}-error.png")
    page.close()

    if look == "hud":
        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1.5)
        phone.add_init_script("try { localStorage.setItem('kestrel-look', 'hud') } catch (e) {}")
        phone.goto(url)
        phone.wait_for_selector("textarea[placeholder^='Ask Kestrel']")
        phone.wait_for_timeout(1600)
        save_shot(phone, out / "hud-mobile-idle.png")
        phone.get_by_label("Message").fill(NOTE)
        phone.get_by_label("Message").press("Enter")
        phone.wait_for_selector("[data-approval=create_note]")
        phone.wait_for_timeout(500)
        save_shot(phone, out / "hud-mobile-approval.png")
        phone.get_by_role("button", name="Approve", exact=True).click()
        phone.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        phone.close()


def hud_page(browser, url: str, gate: EventGate, **options):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, **options)
    page.add_init_script("try { localStorage.setItem('kestrel-look', 'hud') } catch (e) {}")
    page.route_web_socket("**/ws", gate.route)
    page.goto(url)
    page.wait_for_selector("text=Connected")
    page.wait_for_timeout(1600)  # past the boot sequence
    return page


def say(page, text: str) -> None:
    box = page.get_by_label("Message")
    box.fill(text)
    box.press("Enter")


def falcon_closeups(browser, url: str) -> None:
    """The falcon's poses, cropped to the rings: perch, flap, dive, burst, return, hover, shake."""
    gate = EventGate()
    page = hud_page(browser, url, gate, device_scale_factor=2.5)

    def shot(name: str, where: str = "aside .reactor") -> None:
        save_shot(page.locator(where), THEME_SHOTS / f"falcon-{name}.png")

    shot("perch", ".reactor >> nth=0")  # the hero: no conversation yet
    gate.pause_after = "step_started"
    say(page, TOKYO)
    gate.wait_paused(page)
    page.wait_for_timeout(450)
    shot("flap")
    gate.pause_after = "tool_call"
    gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(480)  # the bottom of the stoop is at 45% of 1.1 s
    shot("dive")
    gate.pause_after = "done"
    gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(250)
    shot("burst")
    gate.release()
    page.wait_for_timeout(1300)  # swoop finished, wings open
    shot("return")
    say(page, NOTE)
    page.wait_for_selector("[data-approval=create_note]")
    page.wait_for_timeout(600)
    shot("hover")
    page.get_by_role("button", name="Approve", exact=True).click()
    page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
    say(page, OUTAGE)
    page.locator("text=simulated outage >> visible=true").first.wait_for()
    page.wait_for_timeout(120)  # mid-shake
    shot("shake")
    page.close()


def record_exchange(browser, url: str) -> None:
    """A WebM of idle -> thinking -> tool -> answer burst -> approval, with Playwright's recorder.
    The tool's result is held back 1.2 s so its dive is visible (the demo tool takes ~1 ms).
    Skipped, with a message, when Playwright's ffmpeg isn't installed."""
    from playwright.sync_api import Error as PlaywrightError

    with tempfile.TemporaryDirectory() as videos:
        size = {"width": 1280, "height": 800}
        context = browser.new_context(viewport=size, record_video_dir=videos, record_video_size=size)
        context.add_init_script("try { localStorage.setItem('kestrel-look', 'hud') } catch (e) {}")
        gate = EventGate()
        try:
            page = context.new_page()
        except PlaywrightError as e:
            if "ffmpeg" not in str(e):
                raise
            context.close()
            print("hud-demo.webm skipped: the video recorder needs ffmpeg (uv run playwright install ffmpeg)")
            return
        page.route_web_socket("**/ws", gate.route)
        page.goto(url)
        page.wait_for_selector("text=Connected")
        page.wait_for_timeout(2500)  # boot sequence, then idle
        gate.pause_after = "tool_call"
        say(page, TOKYO)
        gate.wait_paused(page)
        page.wait_for_timeout(1200)
        gate.release()
        page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        page.wait_for_timeout(2200)  # the burst and the swoop back
        say(page, NOTE)
        page.wait_for_selector("[data-approval=create_note]")
        page.wait_for_timeout(1800)
        page.get_by_role("button", name="Approve", exact=True).click()
        page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        page.wait_for_timeout(2000)
        video = page.video
        context.close()  # the video is written on close
        shutil.move(video.path(), THEME_SHOTS / "hud-demo.webm")


def mode_shots(browser, url: str) -> None:
    """Every HUD mode, in order: Standby, Listening (after a scripted wake), Thinking, Answering,
    done (the burst), a tool, an approval, an error."""
    gate = EventGate()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.add_init_script("try { localStorage.setItem('kestrel-look', 'hud') } catch (e) {}")
    page.route_web_socket("**/ws", gate.route)
    page.goto(url)
    page.wait_for_selector("text=Connected")
    page.wait_for_timeout(1600)  # past the boot sequence

    def shot(mode: str, label: str) -> None:
        page.locator(f"text={label} >> visible=true").first.wait_for(timeout=10_000)
        save_shot(page, THEME_SHOTS / f"hud-mode-{mode}.png")

    def wait_idle():
        page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=30_000)
        page.wait_for_timeout(1800)  # the done settle, then Standby

    page.locator("text=Listening >> visible=true").first.wait_for(timeout=15_000)  # the mic chip
    page.wait_for_timeout(600)
    shot("standby", "Standby")

    WAKE_TRIGGER.set()  # "Kestrel"
    page.wait_for_timeout(2600)  # the fly-in (1.6 s), then the level ring
    shot("listening", "Listening…")
    page.get_by_label("Message").fill("x")  # typing ends Listening
    page.locator("text=Standby >> visible=true").first.wait_for(timeout=5_000)
    page.get_by_label("Message").fill("")

    gate.pause_after = "step_started"
    say(page, TOKYO)
    gate.wait_paused(page)
    page.wait_for_timeout(600)
    shot("thinking", "Thinking…")
    gate.pause_after = "text_delta"
    gate.release()
    for _ in range(5):  # let the answer stream in, a piece at a time (6 of its ~8), so the text pulses run
        gate.wait_paused(page)
        page.wait_for_timeout(70)
        gate.pause_after = "text_delta"
        gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(120)
    shot("answering", "Answering…")
    gate.pause_after = "done"
    gate.release()
    gate.wait_paused(page)
    page.wait_for_timeout(250)
    shot("done", "Done")
    gate.release()
    wait_idle()

    gate.pause_after = "tool_call"
    say(page, TOKYO)
    gate.wait_paused(page)
    page.wait_for_timeout(500)
    shot("tool", "Running tool")
    gate.release()
    wait_idle()

    say(page, NOTE)
    page.wait_for_selector("[data-approval=create_note]")
    page.wait_for_timeout(600)
    shot("approval", "Approval needed")
    page.get_by_role("button", name="Approve", exact=True).click()
    wait_idle()

    say(page, OUTAGE)
    page.locator("text=simulated outage >> visible=true").first.wait_for()
    page.wait_for_timeout(1000)
    shot("error", "Error")
    page.close()


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
