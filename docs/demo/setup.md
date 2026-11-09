# Demo video: setup (no Groq, no API keys)

Every scene runs locally:
- **The student** (`kestrel-student` on Ollama) for scenes 1, 2, 4 and 5.
- **The built-in demo provider** for scene 3. A real model doesn't fall for the injection reliably, and scene 3 needs one
  that does, so it can show the gate stopping it. The demo provider is a scripted model that falls for it every time,
  and the console labels it "Demo mode".

Groq and Gemini are blocked in both windows, so nothing can reach them by fallback.

## Before you start

- Ollama is running and `kestrel-student` is registered with the fixed template.
  `ollama show kestrel-student --template | Select-String -SimpleMatch '{{- if .ToolCalls }}'` prints one line.
- **Free memory: the student needs about 5–6 GB.** Close the browser tabs and apps you don't need: with 2–3 GB free,
  replies took up to 71 s.
- The console is built (`console\dist` exists; if not: `cd console; npm run build`).
- **For scene 5:** the voice extras and models.
  - Install: `uv sync --extra wake`, then `uv run python scripts/get_wake_model.py` and
    `uv run python scripts/get_talk_model.py`. On this laptop they're already present.
  - Plug the laptop in: on battery, listening pauses below 30% charge ([voice-wake.md](../voice-wake.md)).

## 1. A throwaway workspace

This is a copy of the two tracked files, so your real `workspace\` and `outbox\` stay untouched. `suspicious_email.txt`
is the repo's existing injection example; nothing new is written.

```powershell
cd <home>\Documents\kestrel
$ws = "$env:TEMP\kestrel-demo\workspace"
Remove-Item -Recurse -Force "$env:TEMP\kestrel-demo" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $ws | Out-Null
Copy-Item workspace\notes.txt, workspace\suspicious_email.txt $ws
```

## 2. The student console (window 1: scenes 1, 2, 4, 5)

```powershell
cd <home>\Documents\kestrel
$env:GROQ_API_KEY = "blocked-no-groq"; $env:GROQ_BASE_URL = "http://127.0.0.1:9/v1"
$env:GEMINI_API_KEY = "blocked-no-gemini"; $env:GEMINI_BASE_URL = "http://127.0.0.1:9/v1"
$env:KESTREL_PROVIDERS = "ollama"; $env:OLLAMA_MODEL = "kestrel-student"
$env:KESTREL_MCP = "off"
$env:KESTREL_WORKSPACE = "$env:TEMP\kestrel-demo\workspace"
$env:KESTREL_REQUEST_TIMEOUT = "300"
$env:KESTREL_WAKE = "on"
$env:KESTREL_TOKEN = [guid]::NewGuid().ToString("N")
Invoke-RestMethod http://127.0.0.1:11434/api/generate -Method Post -Body '{"model":"kestrel-student","keep_alive":"2h"}' | Out-Null
uv run --no-sync kestrel web --port 8000
```

`KESTREL_WAKE=on` opens the microphone while the console runs. Leave it off (delete that line) for a take without
scene 5. The `Invoke-RestMethod` line loads the student into memory before the first question, so scene 1 doesn't
start with a long pause.

## 3. The demo console (window 2: scene 3)

```powershell
cd <home>\Documents\kestrel
$env:GROQ_API_KEY = "blocked-no-groq"; $env:GROQ_BASE_URL = "http://127.0.0.1:9/v1"
$env:GEMINI_API_KEY = "blocked-no-gemini"; $env:GEMINI_BASE_URL = "http://127.0.0.1:9/v1"
$env:KESTREL_PROVIDERS = "demo"
$env:KESTREL_MCP = "off"
$env:KESTREL_WORKSPACE = "$env:TEMP\kestrel-demo\workspace"
$env:KESTREL_TOKEN = [guid]::NewGuid().ToString("N")
uv run --no-sync kestrel web --port 8001
```

Each window prints its link with `?token=…`. Open both in Edge (in an app window or with the address bar out of
the recorded area: recording.md).

## 4. Check the scenes before recording (optional, about 2–3 minutes)

With both consoles running (window 3):

```powershell
uv run --no-sync python scripts/demo_video_check.py --student "<link from window 1>" --demo "<link from window 2>" `
    --out "$env:TEMP\kestrel-demo\check"
```

It drives Edge headless (nothing downloaded) and sends each scene's prompt once. It handles the cards (it rejects
both) and prints per scene what happened and how long it took. Screenshots go in the `--out` folder.

It tests scenes 1–4. For scene 5 it only reports the wake state; the wake word needs your voice.

## Tested on 2026-11-03 (offline, twice, `scripts/demo_video_check.py`)

| Scene | Run 1 | Run 2 | Reliable? |
|---|---|---|---|
| 1 Multi-step (student) | answered: 2 tools (`list_files`, `read_file`), 71 s | answered: 2 tools, 35 s | worked both times; **slow** with 2.9 GB free |
| 2 Approval card (student) | `send_message` card to `sam@example.com`, rejected, answered, ~18 s to the card | the same | worked both times |
| 3 Injection stopped (demo) | card to `attacker@example.com`, rejected, warning, 2–3 s | the same | scripted: the same every time |
| 4 Cost (student) | the chat's trace chip shows `ollama · … · no list price`; the stats panel "actually paid: $0" | the same | yes |
| 5 Voice | **not tested**: needs a real voice and opens the microphone; the test consoles ran with `KESTREL_WAKE` off | – | try it once before recording |

Things that don't work reliably, or need care:

- **The student's wording changes every run.** Scene 1's summary and scene 2's email differ between takes. Two runs
  aren't proof it always calls the tools: the model can, rarely, answer without them. If a take misses the card or
  the tool calls, ask again. As a last resort, use the demo provider's version of scenes 1–2 and say on screen
  that it's scripted.
- **The student's email signs off "[Your Name]".** Click **Edit** on the card to fix it, or reject it as in the script.
- **Speed.** The student runs on the CPU. With little free memory the laptop pages, and the first reply took 71 s.
  Close apps first, keep the model loaded (step 2), and cut the waiting in the edit.
- **The stats panel and the Traces page show your whole local history** (all-time requests, providers you've used,
  ratings, and on Traces every past prompt). The script doesn't open Traces; for scene 4 stay on the chat.
- **The traces of these runs go into your local `logs\traces.db`** like any console use. Delete them on the Traces
  page if you don't want them.
- **Scene 5 is the least tested.** The wake threshold is provisional and detection accuracy hasn't been measured
  yet ([voice-wake.md](../voice-wake.md)). Do a practice take; if it doesn't wake, click the microphone switch
  and speak after it says "Listening".
