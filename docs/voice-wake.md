# Voice wake-up ("Kestrel")

Say "Kestrel" near the PC: the display wakes, the console comes to the front, the falcon flies in
(HUD look) and a fixed greeting is spoken: "I'm here, {name}. What do you need?", or "I'm here. What do
you need?" without a name. That is all a wake does. It makes no model call, runs no tool and can't
answer an approval: approvals stay clicks.

Everything is local and free: Vosk for detection, Windows' own speech voices for the greeting.
No audio, text or event leaves the PC.

## Turn it on

1. Install the optional dependencies (about 16 MB): `uv sync --extra wake`
   (while Claude Code runs the kestrel MCP server: `uv sync --inexact --no-install-project --extra wake`).
2. Get the model once (41 MB zip, 68 MB unpacked, outside the repo, SHA-256 pinned in the script):
   `uv run python scripts/get_wake_model.py`
3. Add to `.env` yourself:

   ```
   KESTREL_WAKE=on
   KESTREL_USER_NAME=YourName        # optional; no name means "I'm here. What do you need?"
   ```

4. Start the console: `uv run kestrel web`. The nav shows a microphone switch; red "Listening" means
   the microphone is open.

### A console window that is already there when the display wakes

Install the console as an Edge app window and let it start with Windows:

1. Give the console a fixed token, so the app window keeps working across restarts. Generate one:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   and add it to `.env` yourself as `KESTREL_TOKEN=<the value>`. Never commit it; `.env` is git-ignored.
2. Open the console link printed by `kestrel web` in Edge, then *Settings and more > Apps > Install
   this site as an app*. In `edge://apps`, open the app's *Details* and turn on *Auto-start on device login*.
3. Keep the app window open (it can be behind other windows or minimized). On a wake, Kestrel finds it
   by its title ("Kestrel") and brings it to the front. If Windows refuses, the taskbar button flashes.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `KESTREL_WAKE` | off | The master switch. Off: no listening thread exists and the microphone is never opened. |
| `KESTREL_WAKE_ON_BATTERY` | off | Listen on battery too. By default listening pauses on battery ("Paused: on battery"). |
| `KESTREL_USER_NAME` | none | The name in the greeting (printable characters, at most 40). |
| `KESTREL_WAKE_THRESHOLD` | 0.5 | Minimum Vosk confidence for "kestrel". Provisional: choose it from your measurement below. |
| `KESTREL_WAKE_MODEL` | `%LOCALAPPDATA%\kestrel\models\vosk-model-small-en-us-0.15` | The model folder. |

The console's switch (`POST /api/wake`) turns listening off and on while the server runs. It needs the
same login as every other API route, and requests from another site's page are refused.

## What it can and can't do on this PC (Modern Standby)

This laptop uses Modern Standby (`powercfg /a`: "Standby (S0 Low Power Idle)"). On such PCs, Microsoft
documents that the system starts entering standby when the display turns off, including after the idle
timeout, and that all desktop apps, Kestrel included, are then paused.

- **Display off, PC awake: works.** While listening on mains power, Kestrel holds a "system required"
  power request ("Kestrel is listening for its wake word"), so the screen can turn off without the PC
  going into standby. The HUD shows "keeping PC awake" whenever it holds it. It is cleared the moment
  listening stops: switch off, battery, or server shutdown.
- **On battery:** listening pauses by default. With `KESTREL_WAKE_ON_BATTERY=on` it listens without
  holding the request, so Windows may still put the PC into standby (power requests block standby for
  at most 5 minutes on battery anyway), and then nothing is heard.
- **Locked screen:** the display can wake, but it shows the lock screen. Kestrel never unlocks; the
  greeting is spoken, and the console is visible once you sign in.
- **Asleep (lid closed, power button, Sleep, hibernate): impossible.** A sleeping PC runs no Kestrel
  code, so the microphone isn't read. No software setting changes that.

Waking the display uses two calls: `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` and one synthetic
mouse move of zero pixels (`SendInput`). Never a click, a key press or a real movement (pinned by a test).

## Privacy

| Guarantee | How it holds | Test (`tests/test_wake.py`) |
|---|---|---|
| Off by default | No thread, no microphone unless `KESTREL_WAKE=on` | `test_off_by_default` |
| You can see when the microphone is open | "Listening" is sent before the stream opens and after it closes | `test_wake_flow_end_to_end` |
| Audio is never saved | Chunks go from PortAudio's callback to a bounded in-memory queue to the detector, then are dropped | `test_audio_is_never_written_to_disk` |
| Audio is never sent | The wake modules import no network library | `test_wake_code_imports_no_network_agent_model_or_tool_module` |
| A wake records only its time | One span named `wake`, attributes `{"kestrel.wake": "detected"}` | `test_a_wake_records_only_its_time` |
| Wake records never become data | No row in `traces`, so not in Traces, Stats, `kestrel export` or `kestrel distill build` | `test_wake_records_are_hidden_from_traces_stats_and_export`, `test_wake_records_never_reach_the_table_the_distillation_dataset_reads` |
| No model call, no tool, no approval | The service holds no agent, model or tool | `test_a_wake_makes_no_model_call_runs_no_tool_and_leaves_approvals_to_clicks` |

`kestrel distill build` lives on `phase4/trace-dataset`. On 2026-10-22 its `read_traces` was run (from
that branch's code, unchanged) on a database holding one agent trace and one wake record: it returned
the agent trace only. When the branches meet, the test above should be joined by one that calls
`distill.dataset.build` directly.

Only `scripts/wake_record.py` saves audio, and only when you run it, to the folder you choose
(default `data/wake/`, git-ignored). `scripts/get_wake_model.py` is the only wake code that uses the
network, once, to fetch the model.

## Measuring it (no numbers are claimed until this is done)

1. **Positives:** `uv run python scripts/wake_record.py` records 30 clips of 2 s, Enter before each.
   Vary distance (desk, across the room), volume, speed and tone; include a few with background noise.
2. **Background:** an hour or more of audio with no "Kestrel" in it: a podcast or TV, saved as WAV,
   FLAC, OGG or MP3 in `data/wake/background/`. More hours give a tighter false-wake rate.
3. **Run:** `uv run python scripts/wake_eval.py --positives data/wake/positives --background data/wake/background`.
   It uses the same detector code as the live service and reports, for each threshold: clips detected
   (with a 95% Clopper-Pearson interval), false wakes (with the 10-second cooldown) per hour, and CPU.
4. **Choose the threshold:** the highest one that still detects at least 90% of your clips, then
   check its false wakes per hour. Targets: under 1 per hour of TV/podcast audio. Set it as
   `KESTREL_WAKE_THRESHOLD` and record the table in this file.

If the false-wake rate stays high at every threshold, Vosk isn't good enough for this room: the
detector sits behind an interface (`kestrel.wake.detector.Detector`), and openWakeWord with a custom
"kestrel" model is the planned alternative (see design decision 18).

### Measured so far

- Feasibility (2026-10-22, synthetic clips from the two local Windows voices, not your voice): 4/4
  "Kestrel" clips spotted, 0 wakes on 10 look-alike sentences. Synthetic speech is far easier than a
  real room; this shows only that the word is in the model's vocabulary and the pipeline works.
- CPU while listening (2026-10-23, `scripts/wake_measure.py --minutes 10`, mains power, real microphone and model):
  **1.3% of one core** on average; per minute 0.2% to 5.5% (more while people talk, when Vosk decodes more).
- Keep-awake request in `powercfg /requests`: not checked yet (needs an elevated prompt; known issue #47).
  Without elevation, Windows granted the request and released it on demand (`keep_awake` returned True, then False).
- Accuracy and false wakes per hour: **not measured yet** (waiting for your recordings).

## Components

| File | Role |
|---|---|
| `src/kestrel/wake/detector.py` | `Detector` interface; `VoskDetector` with the grammar `["kestrel", "[unk]"]` |
| `src/kestrel/wake/audio.py` | Microphone (sounddevice) as in-memory chunks |
| `src/kestrel/wake/service.py` | The flow: cooldown, display, window, greeting, `wake` event, trace |
| `src/kestrel/wake/power.py` | Windows calls through ctypes: battery, keep-awake, display, window |
| `src/kestrel/wake/speech.py` | The greeting through SAPI 5 (comtypes) on its own thread |
| `src/kestrel/wake/greeting.py` | The fixed greeting and the name's cleaning |
| `console/src/hud/useHud.ts` | `waking` phase and `arrive` pose; the fly-in plays once per wake |
| `console/src/components/WakeChip.tsx` | The switch and the "Listening" indicator |

Licences: Vosk and its small English model Apache-2.0; sounddevice MIT (bundles PortAudio, MIT);
comtypes MIT; soundfile BSD-3-Clause (bundles libsndfile, LGPL-2.1).
