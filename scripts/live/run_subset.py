"""Live check: run demo prompts on real providers (needs keys in .env and uses real quota).

    uv run python scripts/live/run_subset.py groq 0,1,2,3,4,5 5    # provider, prompt indices, pause (s)
    uv run python scripts/live/mixed_providers.py                  # Gemini -> Groq -> Gemini conversation
    uv run python scripts/live/console_gemini.py                   # web console on Gemini, driven in Edge

Approvals follow the demo: approve the note, reject the first email draft, reject unknown recipients.
Runs on a temporary copy of workspace/; traces go to logs/traces.db.
"""

import sys
import time

indices = [int(i) for i in sys.argv[2].split(",")]
pause = float(sys.argv[3]) if len(sys.argv) > 3 else 0

import live_demo_lib as lib  # noqa: E402

for n, i in enumerate(indices):
    if n and pause:
        time.sleep(pause)
    lib.run_prompt(lib.DEMO_PROMPTS[i])
lib.show_files()
