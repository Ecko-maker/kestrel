"""Record spoken requests with known text, for scripts/talk_eval.py.

Shows each sentence; press Enter, read it aloud at your normal distance and pace, press Enter again.
Saves req-NN.wav and a refs.tsv line ("file<TAB>what you read") in --out (default data/talk/requests,
which git ignores). This is the only talk code that saves audio, and only when you run it; the live
listener keeps requests in memory and never saves them.

    uv run --no-sync python scripts/talk_record.py                       # the 20 built-in requests
    uv run --no-sync python scripts/talk_record.py --set new             # 10 more, a separate set
    uv run --no-sync python scripts/talk_record.py --prompts my_requests.txt
"""

import argparse
import queue
import sys
import threading
from pathlib import Path

SAMPLE_RATE = 16_000
DEFAULT_PROMPTS = [
    "What time is it in Tokyo?",
    "What's seventeen and a half percent of two thousand three hundred forty?",
    "List the files in my workspace.",
    "Summarize notes dot t x t for me.",
    "Create a note called groceries with milk, eggs and bread.",
    "Write an email to Sam saying I'll be ten minutes late.",
    "Search the web for the latest Python release.",
    "What does the project note say about the deadline?",
    "How many days are left until the end of the year?",
    "Read the suspicious email and tell me if it's safe.",
    "Add a line to my to do list: call the dentist on Monday.",
    "What's the weather like in London this weekend?",
    "Convert one hundred and twenty dollars to euros.",
    "Remind me what I wrote about the Kestrel console.",
    "Find the meeting notes from last week.",
    "How long would it take to walk five kilometres?",
    "Draft a short thank you message to Priya.",
    "What's the square root of one thousand and twenty four?",
    "Open the file called ideas and read me the first item.",
    "Never mind, that's all for now.",
]

# A second, separate set (2026-10-24): everyday requests not in the first 20, written after the
# vocabulary hint was fixed and not used to build it (the hint is general Kestrel words + your names).
NEW_PROMPTS = [
    "What's the time in Sydney right now?",
    "Make a note that the plumber is coming on Thursday.",
    "How much is twelve percent of eight hundred and fifty?",
    "Show me what's in the reports folder.",
    "Look up how tall Mount Everest is.",
    "Send a message to Alex saying the meeting moved to three o'clock.",
    "Add batteries to my shopping list.",
    "What did I save about the trip to Lisbon?",
    "Is it going to rain in Paris tomorrow?",
    "Read me the newest file in my workspace.",
]
SETS = {
    "first": (Path("data") / "talk" / "requests", DEFAULT_PROMPTS),
    "new": (Path("data") / "talk" / "requests-new", NEW_PROMPTS),
}


def record_until_enter() -> bytes:
    import sounddevice as sd  # optional dependency: uv sync --extra wake

    chunks: queue.Queue[bytes] = queue.Queue()
    done = threading.Event()

    def callback(indata, frames, time, status):
        chunks.put(bytes(indata))

    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=1600, callback=callback):
        threading.Thread(target=lambda: (input(), done.set()), daemon=True).start()
        done.wait()
    data = bytearray()
    while not chunks.empty():
        data += chunks.get()
    return bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=sorted(SETS), default="first", help="first: 20 requests; new: 10 more")
    parser.add_argument("--out", type=Path, help="where to save (default: the set's folder under data/talk/)")
    parser.add_argument("--prompts", type=Path, help="one request per line (default: the set's built-in ones)")
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf  # optional dependency: uv sync --extra wake

    prompts = (
        [line.strip() for line in args.prompts.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.prompts
        else SETS[args.set][1]
    )
    args.out = args.out or SETS[args.set][0]
    args.out.mkdir(parents=True, exist_ok=True)
    refs = args.out / "refs.tsv"
    start = len(list(args.out.glob("req-*.wav")))
    for n, prompt in enumerate(prompts, start=1):
        print(f"\n[{n}/{len(prompts)}]  {prompt}")
        if input("Enter to start (q + Enter to stop): ").strip() == "q":
            break
        print("  recording... Enter when done")
        pcm = np.frombuffer(record_until_enter(), dtype="<i2")
        name = f"req-{start + n:03d}.wav"
        sf.write(args.out / name, pcm, SAMPLE_RATE, subtype="PCM_16")
        with refs.open("a", encoding="utf-8") as f:
            f.write(f"{name}\t{prompt}\n")
        print(f"  saved {name} ({len(pcm) / SAMPLE_RATE:.1f} s)")
    print(f"\n{len(list(args.out.glob('req-*.wav')))} requests in {args.out}; references in {refs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
