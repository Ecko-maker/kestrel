"""Play Kestrel's own spoken answers out loud, so the room can be recorded for the self-wake measurement
(known issue #62: does Kestrel's voice wake Kestrel?).

Run it in one window while another records the room:
    uv run --no-sync python scripts/wake_record.py --background --minutes 15 --out data/wake/own-answers
    uv run --no-sync python scripts/play_answers.py
then measure with:
    uv run --no-sync python scripts/wake_eval.py --positives data/wake/positives-v2 --background data/wake/own-answers

The answers come from your own traces (logs/traces.db), shaped exactly as the console would speak them
(talk.spoken_answer, then for_speech: Kestrel never says its name), and are read with Windows' own voice at its
normal rate. Nothing is printed but counts and seconds: no answer text leaves this script, and nothing is saved.
"""

import argparse
import datetime as dt
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from kestrel.wake.talk import for_speech, spoken_answer


def spoken_texts(db: Path, since: float, limit: int | None = None) -> list[str]:
    """What the console would have said for each stored answer since `since`, oldest first."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT final_answer FROM traces WHERE start_time >= ? AND final_answer IS NOT NULL ORDER BY start_time",
            (since,),
        ).fetchall()
    finally:
        con.close()
    texts = [for_speech(spoken_answer(str(answer))) for (answer,) in rows]
    return texts[:limit] if limit else texts


def play(texts: list[str], say: Callable[[str], None], gap_s: float, show: Callable[[str], None] = print) -> float:
    """Say each text, with gap_s of silence between them. Returns the seconds spent speaking."""
    spoken = 0.0
    for i, text in enumerate(texts, 1):
        started = time.monotonic()
        say(text)
        spoken += time.monotonic() - started
        show(f"answer {i} of {len(texts)}: {time.monotonic() - started:.1f} s")
        time.sleep(gap_s)
    return spoken


def sapi_say() -> Callable[[str], None]:
    import comtypes.client  # optional dependency: uv sync --extra wake

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    return lambda text: voice.Speak(text)  # synchronous: returns when it has finished speaking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path("logs") / "traces.db")
    parser.add_argument("--since", default="2026-11-05", help="answers from this date on (YYYY-MM-DD, local time)")
    parser.add_argument("--limit", type=int, help="at most this many answers")
    parser.add_argument("--gap", type=float, default=3.0, help="seconds of silence between answers")
    args = parser.parse_args()
    since = dt.datetime.fromisoformat(args.since).astimezone().timestamp()
    texts = spoken_texts(args.db, since, args.limit)
    if not texts:
        print("no stored answers in that range")
        return 1
    print(f"{len(texts)} answers; start the room recording now. Speaking in 5 s.")
    time.sleep(5)
    spoken = play(texts, sapi_say(), args.gap)
    print(f"done: {spoken / 60:.1f} min of Kestrel's voice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
