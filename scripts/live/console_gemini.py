"""Drive the real `kestrel web` on Gemini in Edge: a tool call, an approval, and a streamed answer."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.path[0])
PORT = 8768

tmp = Path(tempfile.mkdtemp(prefix="kestrel-console-gemini-"))
shutil.copytree(ROOT / "workspace", tmp / "workspace", ignore=shutil.ignore_patterns("notes", "outbox"))
env = {
    **os.environ,
    "KESTREL_PROVIDERS": "gemini",
    "KESTREL_MCP": "off",
    "KESTREL_WORKSPACE": str(tmp / "workspace"),
    "KESTREL_TOKEN": "console-check-token",
    "PYTHONIOENCODING": "utf-8",
}
server = subprocess.Popen(
    ["uv", "run", "kestrel", "web", "--port", str(PORT)],
    cwd=ROOT,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
)
url = f"http://127.0.0.1:{PORT}/?token=console-check-token"
try:
    for _ in range(60):
        line = server.stdout.readline()
        if "Kestrel console:" in line:
            break
    frames: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("websocket", lambda ws: ws.on("framereceived", lambda f: frames.append(json.loads(f))))
        page.goto(url)
        page.wait_for_selector("text=Connected")
        print(
            "models shown:", page.inner_text("nav").split("Models")[-1].split("Connected")[0].strip().replace("\n", " ")
        )

        def send(text):
            box = page.get_by_label("Message")
            box.fill(text)
            box.press("Enter")

        def wait_done(n):
            t0 = time.time()
            while sum(1 for f in frames if f.get("type") == "done") < n:
                page.wait_for_timeout(250)
                if time.time() - t0 > 180:
                    raise TimeoutError("no done event")

        send("What files do I have in my workspace?")
        wait_done(1)
        page.wait_for_timeout(500)
        page.screenshot(path=OUT / "console_gemini_tool.png")

        send("Create a note called 'Console check' saying the web console works on Gemini.")
        page.wait_for_selector("[data-approval=create_note]", timeout=120_000)
        page.screenshot(path=OUT / "console_gemini_approval.png")
        page.get_by_role("button", name="Approve", exact=True).click()
        wait_done(2)
        page.wait_for_timeout(500)
        page.screenshot(path=OUT / "console_gemini_done.png")
        browser.close()

    turns, current = [], []
    for f in frames:
        current.append(f)
        if f.get("type") == "done":
            turns.append(current)
            current = []
    for n, turn in enumerate(turns, 1):
        kinds = [f["type"] for f in turn]
        deltas = [f for f in turn if f["type"] == "text_delta"]
        final_step = max((f["step"] for f in deltas), default=None)
        answer = next(f for f in turn if f["type"] == "answer")
        llm = [f for f in turn if f["type"] == "llm_call"]
        print(
            f"\nturn {n}: providers={answer['providers']} stop={answer['stop_reason']} "
            f"latency={answer['duration_ms'] / 1000:.1f}s tokens={answer['tokens']}"
        )
        print(
            "  tools:",
            [
                f"{f['name']}:{f.get('decision') or ('ran' if f['ran'] else 'not run')}"
                for f in turn
                if f["type"] == "tool_result"
            ],
        )
        print("  approvals:", [(f["tool"], f["decision"]) for f in turn if f["type"] == "approval_resolved"])
        print(
            f"  text_delta events: {len(deltas)} (final step {final_step}: "
            f"{sum(1 for f in deltas if f['step'] == final_step)} pieces)  model calls: {[f.get('model') for f in llm]}"
        )
        print("  answer:", answer["text"][:220].replace("\n", " "))
    notes = tmp / "workspace" / "notes"
    print("\nnotes written:", [p.name for p in notes.iterdir()] if notes.exists() else [])
finally:
    server.terminate()
