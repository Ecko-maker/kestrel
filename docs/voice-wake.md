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
| `KESTREL_WAKE_MIN_BATTERY` | 30 | On battery, pause below this charge; resume at this + 5 (35%) or when plugged in. |
| `KESTREL_WAKE_ON_BATTERY` | on | An override: `off` never listens on battery ("Paused: on battery (KESTREL_WAKE_ON_BATTERY=off)"). |
| `KESTREL_USER_NAME` | none | The name in the greeting (printable characters, at most 40). |
| `KESTREL_WAKE_LISTEN_SECONDS` | 8 | How long the HUD stays in Listening after a wake, unless you type or send first (1-60). |
| `KESTREL_WAKE_THRESHOLD` | 0.5 | Minimum Vosk confidence for "kestrel". Provisional: choose it from your measurement below. |
| `KESTREL_WAKE_MODEL` | `%LOCALAPPDATA%\kestrel\models\vosk-model-small-en-us-0.15` | The model folder. |

The console's switch (`POST /api/wake`) turns listening off and on while the server runs. It needs the
same login as every other API route, and requests from another site's page are refused.

## The HUD's modes

Each mode has its own falcon pose, ring motion, colour and text label, so colour is never the only cue.
`console/src/hud/useHud.ts` (`hudPhase`) decides; agent work always wins, so an approval card is never
hidden by a wake. Screenshots: `docs/screenshots/theme/hud-mode-*.png`.

| Mode | When | Falcon | Rings | Colour | Label |
|---|---|---|---|---|---|
| Standby | wake word on, mic open, nothing happening | perched, eyes softly glowing | one slow, faint ripple | dim teal | "Standby" |
| (fly-in) | the moment "Kestrel" is heard | flies in from outside the rings, wings open | a soft ring | bright | "Hello" + the greeting |
| Listening | after a wake, until you type, send, or `KESTREL_WAKE_LISTEN_SECONDS` pass | wings half raised, slow beats | a ring that swells with the live mic level | green | "Listening…" |
| Thinking | waiting for the model | flapping, lifted | rotating segments | cyan | "Thinking…" |
| Answering | the answer's text arriving | wings fully open, a steady glide | a pulse per ~40 characters, at most every 0.45 s | warm white-gold | "Answering…" |
| Done | 1.4 s after an answer | swoops back, wings open | the answer burst | cyan | "Done" |
| Ready | the wake word off, nothing happening | perched | slow pulse | cyan | "Ready" |

Tool, approval and error are unchanged. Under reduced motion nothing moves (no pose change, ripple,
level ring motion or pulses); the colour and the label still change.

**The mic level** is the only thing about the audio that ever leaves the wake service: one number,
0 to 1 (the RMS of each 0.1 s chunk, in dBFS mapped from -60..0, smoothed), sent as
`{"type": "wake_level", "level": 0.42}` about 10 times a second, and only during Listening. Typing in
the composer (a `wake_dismiss` message) or sending a message ends Listening on the server too, so the
level stops at once.

## What it can and can't do on this PC (Modern Standby)

This laptop uses Modern Standby (`powercfg /a`: "Standby (S0 Low Power Idle)"). On such PCs, Microsoft
documents that the system starts entering standby when the display turns off, including after the idle
timeout, and that all desktop apps, Kestrel included, are then paused.

### The battery rule

| Power | Listening | Keep-awake request | Shown as |
|---|---|---|---|
| Plugged in | yes | held | "Listening", HUD: "keeping PC awake" |
| Battery, at or above `KESTREL_WAKE_MIN_BATTERY` (30%), saver off | yes, while the PC is awake | never | "Listening (battery 64%)" |
| Battery below the minimum | paused until 35% or plugged in | never | "Paused: battery low (28%)" |
| Battery, Energy Saver (Battery Saver) on | paused until the saver is off | never | "Paused: power saver" |
| Battery, `KESTREL_WAKE_ON_BATTERY=off` | paused | never | "Paused: on battery (KESTREL_WAKE_ON_BATTERY=off)" |

- **Plugged in, display off: works.** Kestrel holds a "system required" power request ("Kestrel is
  listening for its wake word"), so the screen can turn off without the PC going into standby. The HUD
  shows "keeping PC awake" only when Windows granted it. It is cleared at once on a switch to battery,
  and when listening stops (switch off, low battery, saver, server shutdown).
- **On battery, the PC is allowed to sleep.** No request is held, so Windows goes to standby as usual
  to save the battery: about 5 minutes after the screen turns off (Microsoft's documented limit for
  apps keeping a battery-powered PC out of standby; not measured on this laptop). From then on Kestrel
  can't hear anything until the PC wakes. When it wakes, Kestrel notices (a jump in the clock between
  two loop turns), reads the power state again and reopens the microphone stream, so listening resumes
  on its own.
- **How power is read:** `GetSystemPowerStatus` every 5 seconds (mains or battery, percent, and the
  saver flag `SystemStatusFlag`). One call costs microseconds, and polling this often means plugging in
  resumes within seconds and the keep-awake request is dropped within seconds of unplugging. Energy
  Saver applies on battery only here: plugged in, Kestrel listens even if the saver is forced on.
- **A stream that goes quiet** (no audio for 5 seconds, e.g. the microphone was unplugged or didn't
  come back after standby) is closed and reopened.
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
| Only a loudness number reaches the console, only while Listening | `wake_level` carries `type` and `level` and nothing else; none in Standby or after Listening ends | `test_level_events_carry_one_number_and_only_while_listening`, `test_typing_or_sending_ends_the_listening_window` |
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

## Talking to Kestrel

After a wake, say your request. Kestrel records it in memory, transcribes it on this PC, and the
console sends the text exactly like typed text (your message shows a mic icon). The answer is
spoken: its first two sentences, then "The rest is on screen."

**Turn it on:** `uv run python scripts/get_talk_model.py` once (whisper.cpp base.en, 5-bit, MIT,
57 MB, SHA-256 pinned; `--model tiny.en-q5_1` for the faster 31 MB fallback). With `KESTREL_WAKE=on`
and the model present, spoken requests are on; `KESTREL_TALK=off` keeps a wake to greeting only.

| Setting | Default | Meaning |
|---|---|---|
| `KESTREL_TALK` | on | `off`: a wake only greets |
| `KESTREL_TALK_MODEL` | `base.en-q5_1` | `tiny.en-q5_1`: about 3x faster, about twice the errors on real speech |
| `KESTREL_TALK_MIN_CONFIDENCE` | 0.45 | below it: "check first" instead of sending (provisional; chosen from your requests + background) |
| `KESTREL_TALK_HINT` | off | the vocabulary hint; `on` to use it (off since 2026-10-24: it was worse, see below) |
| `KESTREL_TALK_VOCAB_FILE` | `data/talk/vocab.txt` | names for the hint, one per line, `#` comments (git-ignored; you add them) |

The console's **Speak answers** switch (remembered per browser) turns the spoken answers off.

**How a request ends.** Each 0.1 s chunk's loudness is compared with the room's noise floor (learned
while waiting for the wake word). The request starts at the first chunk 0.15 above it and ends after
1 s of quiet, or at 20 s. Nothing said within 3 s of the greeting: "I didn't catch that". Kestrel's
own voice (the greeting) is never recorded as your request.

**The vocabulary hint (off by default).** With `KESTREL_TALK_HINT=on`, whisper.cpp gets a short initial prompt (at most 200 characters, built by
`transcribe.talk_prompt`, the same function for the server and `talk_eval.py`): "Kestrel" and its
tools in plain words (note, notes, todo list, workspace, file, folder, email, search, calculator,
weather), then names from `KESTREL_TALK_VOCAB_FILE` until the limit. It is never built from the
evaluation sentences. Note: pywhispercpp keeps parameters from one call to the next, so the prompt is
passed on every call ("" for none); leaving it out would silently reuse the previous one.

**Three paths, and the hallucination guard.** Whisper can invent text from silence or noise (a 100 Hz
hum came out as "(dramatic music)" in a test here). Before anything reaches the agent:
- **drop** (Kestrel says "I didn't catch that"; nothing is sent or shown): under 0.4 s of speech
  above the noise floor; an empty transcript; a known filler as the whole normalised transcript
  ("thank you for watching", "thanks for watching", "you", "thank you", "bye", lone punctuation,
  sound labels such as "[BLANK_AUDIO]"; "thank you for watching the match with me" is kept); or the
  vocabulary hint echoed back (two or more of its words, in order: Whisper can return its prompt on
  near-silence);
- **check first** (Kestrel says "Please check what I heard on screen."): confidence under
  `KESTREL_TALK_MIN_CONFIDENCE`. The text goes into the most recently used console's message box,
  unsent, under "Did I hear that right?"; you edit or send it (then it is a spoken request like any
  other), or discard it. whisper.cpp's per-segment no-speech probability isn't exposed by
  pywhispercpp, so the closest signal is used: the segment confidence (the geometric mean of its
  token probabilities) stands for 1 - no-speech; whisper.cpp's own no-speech threshold (0.6) also
  stays on inside the model;
- **send**: everything else, exactly like typed text.

**Confidence doesn't catch every bad transcript.** Whisper can be wrong and sure of itself: on set 1,
req-018 with the hint came out as "Draft a shorting human-saged to Priya." at confidence 0.931 and would
have been sent. "Check first" catches some mishearings, not all. That is why a spoken request is only
ever a request: voice can never approve anything, and every action stays behind the same approval
cards as typed text (a misheard request can at worst ask for something you then reject on screen).

**What is never spoken:** an approval card's details. When one appears during a spoken request,
Kestrel says only "I need your approval on screen." Voice can't approve: a spoken "approve" is just
chat text (and is refused while the agent waits), and only a click resolves the card. Typed questions
are never spoken. Kestrel never says its own name (it would wake itself): "Kestrel" in spoken text
becomes "I". Saying "Kestrel" while it speaks stops the speech.

**What leaves the microphone:** nothing but the transcript, which becomes a normal chat message. The
recording is a byte buffer in memory, handed to the recogniser and dropped; no audio is written,
logged or sent (tests: `tests/test_talk.py`). The model sees the same input for typed and spoken
requests: the voice flag stays in the console and the server, never in the agent's messages
(`test_the_model_sees_the_same_input_typed_or_spoken`; the agent's code and fingerprint are unchanged).

### Measuring it (no accuracy is claimed until you have recorded your requests)

1. `uv run python scripts/talk_record.py`: 20 built-in requests (or `--prompts file.txt`); each one is
   shown, you press Enter, read it at your normal distance and pace, press Enter. WAVs and `refs.tsv`
   go to `data/talk/requests/` (git-ignored). `--set new` records a second, separate set of 10
   everyday requests into `data/talk/requests-new/` (written after the hint was fixed, not used for it).
2. Background-only audio (podcast, TV) in `data/wake/background/`, as for the wake word.
3. `uv run python scripts/talk_eval.py --background data/wake/background` (`--set new` for the second
   set, `--model tiny.en-q5_1` to compare). Every request is transcribed twice, without and with the
   vocabulary hint, and it reports for both:
   - **word error rate, raw and normalised.** Raw: lower case and punctuation only. Normalised
     (`kestrel.wake.normalize`, a small Whisper-style English normaliser): contractions expanded,
     symbols and units as words (%, $, &), thousands separators removed, number words as digits
     ("seventeen and a half" = 17.5). "What's 17.5% of 2,340?" and "what is seventeen and a half
     percent of two thousand three hundred forty" score as identical;
   - **latency** per request (the time from the end of the recording to the transcript);
   - each request's confidence and its path: send, check first or drop (to set the confidence floor);
   - which requests the hint changed, better or worse;
   - **false requests and false checks per hour** (with the hint, as the server runs): the background
     is streamed through the same end-of-speech detector as if Kestrel were listening all the time;
     every segment it cuts is transcribed and guarded. Would-be sends are requests you never made;
     would-be checks are prompts you'd have to dismiss. An upper bound: the live listener only records
     right after a wake.

**First set, your recordings (2026-10-24, base.en 5-bit, 21 requests, no names in the vocab file yet):**

| | WER raw | WER normalised (95% CI, per request) | Median latency | Send / check / drop at 0.45 |
|---|---|---|---|---|
| no hint | 30.1% | **15.7%** (10.0-25.7%) | 0.82 s | 17 / 4 / 0 |
| hint | 30.1% | **19.7%** (12.0-37.4%) | 0.85 s | 17 / 4 / 0 |

The hint did what it was for on Kestrel words ("List the files in my workspace" 3 errors -> 0,
"notes.txt" 6 -> 4, "Create a knot called a bloishary" -> "a note called Bloishary" 3 -> 1) but three
other requests got worse ("What is 17? What is 17?" 1 -> 7, "$100 and $20" 0 -> 4, the Priya message
1 -> 5, with Priya not yet in the vocab file). Net: worse on this set, with overlapping intervals on
21 requests. The prompt was not re-worded against these sentences (that would fit the test set).

**Decision (owner, 2026-10-24): the hint is off by default.** The rule was fixed before set 2 was scored:
pool both sets and keep the hint only if pooled normalised WER is lower with it. Re-scored with the
owner's vocab file (one name), base.en 5-bit, word-weighted over all 31 requests (261 reference words):

| | Set 1 (21) | Set 2 (10) | Pooled (31 requests, 261 words) |
|---|---|---|---|
| no hint | 15.7% | 10.8% | **37 errors = 14.18%** |
| hint | 21.3% | 9.6% | **46 errors = 17.62%** |

Net +9 errors with the hint (+10 on set 1, -1 on set 2), so it loses. The code and the switch stay
(`KESTREL_TALK_HINT=on`); known issue #49 records it. Confidences (set 1, with the hint): min 0.425,
median 0.642; the threshold is still provisional.

Measured so far (research, 2026-10-23, synthetic Windows-voice requests padded to 5 s, Ryzen 7 8840HS,
8 threads; a pipeline check, not an accuracy claim): base.en 5-bit 0.80 s per request, normalised WER
1.1% (raw 12.4%: number formatting), 0 false requests in 2 minutes of synthetic hum and noise. Published
LibriSpeech test-clean WER: base.en about 4.3%, tiny.en 8.4%, Vosk small 9.85%.

## Components

| File | Role |
|---|---|
| `src/kestrel/wake/detector.py` | `Detector` interface; `VoskDetector` with the grammar `["kestrel", "[unk]"]` |
| `src/kestrel/wake/audio.py` | Microphone (sounddevice) as in-memory chunks |
| `src/kestrel/wake/service.py` | The flow: cooldown, display, window, greeting, `wake` event, trace |
| `src/kestrel/wake/power.py` | Windows calls through ctypes: power state and the battery rule (`decide`), keep-awake, display, window |
| `src/kestrel/wake/speech.py` | The greeting through SAPI 5 (comtypes) on its own thread |
| `src/kestrel/wake/greeting.py` | The fixed greeting and the name's cleaning |
| `console/src/hud/useHud.ts` | `waking` phase and `arrive` pose; the fly-in plays once per wake |
| `console/src/components/WakeChip.tsx` | The switch and the "Listening" indicator; "Speak answers" |
| `src/kestrel/wake/talk.py` | End of speech, the hallucination guard, what is spoken |
| `src/kestrel/wake/transcribe.py` | whisper.cpp (pywhispercpp), pinned model files |
| `src/kestrel/wake/normalize.py` | The text normaliser for word error rate |

Licences: Vosk and its small English model Apache-2.0; sounddevice MIT (bundles PortAudio, MIT);
comtypes MIT; soundfile BSD-3-Clause (bundles libsndfile, LGPL-2.1).
