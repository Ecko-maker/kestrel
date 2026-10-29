# The Listening window after a wake: before and after the fix

Known issue `KI-pending-wake-window-start` (closed by this fix). Offline, local models only, 2026-10-28, on
`voice/talk` (before: `5e36551`; after: the same plus the one-line fix in `src/kestrel/wake/service.py`).
Numbers only; no transcript text is reproduced here.

## The defect

`WakeService.on_wake` read the clock first, then wrote the wake to the trace store and made two
Windows calls (wake the display, bring the console to the front), and only then set the end of the
Listening window as `first clock reading + window`. Whatever those three calls took was lost from
Listening. On Windows CI a slow SQLite write made `test_level_events_carry_one_number_and_only_while_listening`
(a 0.4 s window) flaky. The fix sets the end from a clock reading taken after the three calls; the
cooldown still counts from the wake itself.

## What measures it

The offline evals on the recordings (`wake_eval.py`, `talk_eval.py`) don't run `WakeService.on_wake`:
`talk_eval.py` simulates the recording after a wake from positions in the audio, with no clock and no
trace write. They can't see this defect, so they are run here as a **no-regression check only**. The
window itself is measured by `scripts/wake_window_measure.py`: the real `on_wake` on a test clock,
each of the three calls taking a set time.

```powershell
uv run python scripts/wake_window_measure.py --json evals/results/voice/window-<side>.json
uv run python scripts/wake_eval.py --positives data/wake/positives --background data/wake/background --json evals/results/voice/wake-<side>.json
uv run python scripts/talk_eval.py --background data/wake/background --json evals/results/voice/talk-<side>.json
```

(`docs/voice-wake.md` also passes `--phrases data/wake/phrases`; that folder doesn't exist on this
laptop, so phrases were not measured on either side.)

## Listening window (`wake_window_measure.py`)

Listening time from the moment `on_wake` returns, against the announced window (`listen_seconds` in
the wake event). Greet: a wake only greets (8 s window). Talk: a spoken request is recorded (26 s).

| Each call takes | Three calls | Announced | Listening before | Lost before | Listening after | Lost after |
|---|---|---|---|---|---|---|
| 0 s | 0 s | 8 s / 26 s | 8.00 / 26.00 s | 0 s | 8.00 / 26.00 s | 0 s |
| 0.05 s | 0.15 s | 8 s / 26 s | 7.85 / 25.85 s | 0.15 s | 8.00 / 26.00 s | 0 s |
| 0.25 s | 0.75 s | 8 s / 26 s | 7.25 / 25.25 s | 0.75 s | 8.00 / 26.00 s | 0 s |
| 1.00 s | 3.00 s | 8 s / 26 s | 5.00 / 23.00 s | 3.00 s | 8.00 / 26.00 s | 0 s |

Before, Listening lost exactly the time the three calls took; after, none. How long the calls take on
this laptop was not measured (the real Windows calls wake the display and move windows).

## No-regression check (same recordings, same models)

Every number in both results files was compared (strings skipped): **wake 88 of 89 identical, talk
436 of 481 identical; every difference is a timing field** (CPU share, latencies), which vary run to
run. No detection, word-error, path or false-wake number changed.

| Metric | Before | After |
|---|---|---|
| Wake @ 0.9: clips detected | 19/30 (95% CI 44–80%) | 19/30 (95% CI 44–80%) |
| Wake @ 0.9: false wakes per hour (1.0 h background) | 2.0 | 2.0 |
| Wake: CPU, share of one core (timing) | 3.39% | 3.09% |
| Talk, no hint: word error rate, normalised | 15.7% | 15.7% |
| Talk, no hint: send / check | 15 / 6 | 15 / 6 |
| Talk, no hint: latency median / max (timing) | 0.85 / 0.94 s | 0.82 / 0.89 s |
| Talk, hint: word error rate, normalised | 21.3% | 21.3% |
| End to end @ wake 0.9: false wakes / false requests per hour | 2 / 2.0 | 2 / 2.0 |
| Background speech that would pass if recorded, per hour | 544 | 544 |

## Test

`test_a_slow_trace_write_or_windows_call_does_not_shorten_listening` (`tests/test_wake.py`): a 0.5 s
trace write and two 0.1 s Windows calls against a 0.4 s window. It failed before the fix (the window
had already ended when `on_wake` returned) and passes after.
