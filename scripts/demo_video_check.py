"""Check the demo video's console scenes once, offline (docs/demo/setup.md): no Groq, no keys.

    uv run --no-sync python scripts/demo_video_check.py --student URL --demo URL --out <folder>

Needs the two local consoles from docs/demo/setup.md already running: the student (KESTREL_PROVIDERS=ollama,
OLLAMA_MODEL=kestrel-student) and the demo provider. Drives the system's Edge with Playwright (headless, nothing
downloaded), sends each scene's prompt, handles the approval card the way the script does, and saves a
screenshot per step. Prints, per scene, what happened and how long it took: the student's replies vary from
run to run, so this says whether a scene worked this time, not that it always will.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# The prompts are the script's (docs/demo/script.md); the demo ones are DEMO_PROMPTS from src/kestrel/demo.py.
MULTI_STEP = "What files do I have, and summarize notes.txt"
EMAIL = "Write an email to sam@example.com saying I'll be 10 minutes late"
INJECTION = "Summarize suspicious_email.txt"
STUDENT_TIMEOUT_MS = 300_000  # CPU generation on a busy laptop
DEMO_TIMEOUT_MS = 30_000


def send(page: Any, text: str) -> None:
    box = page.get_by_label("Message")
    box.fill(text)
    box.press("Enter")


def wait_idle(page: Any, timeout: int) -> None:
    page.wait_for_selector("textarea[placeholder^='Ask Kestrel']", timeout=timeout)


def shot(page: Any, out: Path, name: str) -> None:
    page.screenshot(path=str(out / f"{name}.png"), full_page=False)


def open_console(browser: Any, url: str) -> Any:
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(url)
    page.wait_for_selector("text=Connected", timeout=30_000)
    return page


def scene(name: str, results: dict[str, str], fn: Any) -> None:
    start = time.monotonic()
    try:
        outcome = fn()
    except PlaywrightTimeout as e:
        outcome = f"TIMED OUT ({str(e).splitlines()[0][:100]})"
    except Exception as e:  # report and go on to the next scene
        outcome = f"FAILED ({type(e).__name__}: {str(e)[:100]})"
    results[name] = f"{outcome} [{time.monotonic() - start:.0f} s]"
    print(f"{name}: {results[name]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--student", required=True, help="the student console's URL, with ?token=")
    parser.add_argument("--demo", required=True, help="the demo console's URL, with ?token=")
    parser.add_argument("--out", type=Path, required=True, help="a folder for the screenshots")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        student = open_console(browser, args.student)

        def multi_step() -> str:
            send(student, MULTI_STEP)
            wait_idle(student, STUDENT_TIMEOUT_MS)
            shot(student, args.out, "1-multi-step")
            chip = student.get_by_text("Agent trace").last.locator("xpath=..").inner_text()
            return "answered; trace: " + " ".join(chip.split())

        def approval() -> str:
            send(student, EMAIL)
            try:
                student.wait_for_selector("[data-approval=send_message]", timeout=STUDENT_TIMEOUT_MS)
            except PlaywrightTimeout:
                wait_idle(student, STUDENT_TIMEOUT_MS)
                shot(student, args.out, "2-approval-none")
                return "NO CARD: the student answered without calling send_message"
            shot(student, args.out, "2-approval-card")
            student.get_by_role("button", name="Reject").first.click()
            student.get_by_placeholder("Why not?").fill("Not now, I'll text Sam myself.")
            student.get_by_role("button", name="Reject").last.click()
            wait_idle(student, STUDENT_TIMEOUT_MS)
            shot(student, args.out, "2-approval-after")
            return "card shown, rejected, answered"

        def cost() -> str:
            student.get_by_role("link", name="Traces").click()
            student.wait_for_timeout(3000)
            shot(student, args.out, "4-traces")
            text = student.inner_text("body")
            return f"traces page; 'kestrel-student' shown: {'kestrel-student' in text}; '$0' shown: {'$0' in text}"

        def voice() -> str:
            student.goto(args.student)
            student.wait_for_selector("text=Connected", timeout=30_000)
            shot(student, args.out, "5-voice-idle")
            text = student.inner_text("body")
            state = next((s for s in ("Listening", "Standby", "Paused", "Off") if s in text), "no wake state shown")
            return f"wake state on screen: {state} (the wake word itself needs a real voice: not tested here)"

        scene("1 multi-step (student)", results, multi_step)
        scene("2 approval card (student)", results, approval)

        demo = open_console(browser, args.demo)

        def injection() -> str:
            send(demo, INJECTION)
            demo.wait_for_selector("[data-approval=send_message] >> text=attacker@example.com", timeout=DEMO_TIMEOUT_MS)
            shot(demo, args.out, "3-injection-card")
            demo.get_by_role("button", name="Reject").first.click()
            demo.get_by_placeholder("Why not?").fill("That instruction came from the email, not from me.")
            demo.get_by_role("button", name="Reject").last.click()
            wait_idle(demo, DEMO_TIMEOUT_MS)
            shot(demo, args.out, "3-injection-after")
            return "card to attacker@example.com shown, rejected, warning answered"

        scene("3 injection stopped (demo provider)", results, injection)
        scene("4 cost (student traces)", results, cost)
        scene("5 voice (student console)", results, voice)
        browser.close()
    print(f"\nScreenshots: {args.out}")
    return 0 if all("FAILED" not in r and "TIMED OUT" not in r for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
