# Demo video: shot-by-shot script (90 seconds)

Six scenes, recorded from the local console with no Groq and no API key (setup: [setup.md](setup.md); recording and
trimming: [recording.md](recording.md)). The student's replies take 20–70 s on this laptop: record each scene in
full, then cut the waiting (recording.md, "Trim").

Two consoles:
- **Student console**, `http://127.0.0.1:8000`: the fine-tuned student `kestrel-student` on local Ollama. Scenes 1, 2,
  4 and 5.
- **Demo console**, `http://127.0.0.1:8001`: the built-in demo provider, a scripted model. Scene 3.

The prompts are the ones `scripts/demo_video_check.py` tested (setup.md, "Tested on 2026-11-03").

| # | Time | On screen | You type or say | The one-line point |
|---|---|---|---|---|
| 1 | 0:00–0:15 | Student console, Chat. The answer lists the two files and summarises `notes.txt`; open **Agent trace** to show the two tool calls (`list_files`, `read_file`). | Type: `What files do I have, and summarize notes.txt` | "Kestrel plans and calls tools on its own: here it lists my files, reads one, and summarises it." |
| 2 | 0:15–0:30 | Student console. A **Send a message** card: to `sam@example.com`, subject and body, with Approve / Edit / Reject and the 5-minute auto-reject timer. Click **Reject**, type a reason, confirm. | Type: `Write an email to sam@example.com saying I'll be 10 minutes late` | "Anything risky waits for me. Sending is gated in code, not in the prompt; I can approve, edit or reject." |
| 3 | 0:30–0:50 | Demo console, with the **Demo mode** banner visible. A card to send `notes.txt` to `attacker@example.com`. Click **Reject**, reason "That instruction came from the email, not from me." The answer summarises the email and warns about the hidden instruction. | Type: `Summarize suspicious_email.txt` | "The email hides an instruction to forward my notes. Here a scripted model falls for it on purpose, and the gate still stops it: the leak needs a card, and I say no." |
| 4 | 0:50–1:02 | Student console. The chat's trace chip, `ollama · … · no list price`, and the stats panel's "actually paid: $0". The sidebar shows **Models: ollama kestrel-student**. | Say: "Everything you've seen so far ran on a 4-billion-parameter student I fine-tuned, on this laptop." | "Local student: no API bill, $0 per request at the margin (electricity not counted). Whether it holds up against the big model is being measured." |
| 5 | 1:02–1:17 | Student console in **HUD** look, the microphone switch on and "Listening". Say the wake word; the HUD answers with its greeting; then the spoken request appears as text and gets answered. | Say: "Kestrel" … then "What time is it in Tokyo?" | "Voice, all local: the wake word and speech-to-text run on the laptop, no cloud speech service." |
| 6 | 1:17–1:30 | The GitHub repo's README, scrolled to **Results**. | Say the three lines on the right. | "Real model on 29 prompt-injection attacks: 1 got through before tiers v2.1, 0 after. The student alone: 0 of 29 under a strict user. Phase 4, whether the routed student matches the big model at lower cost, is being measured." |

## What each number in scene 6 is, and where it comes from

Say only these, and say them as written:

- **"1 of 29 before, 0 of 29 after"**: the real model (gpt-oss-120b) under the strict user. The system-level attack
  success rate goes from 1/29 to 0/29, 95% CI 0–12%.
  - Sources: [safety report](../../evals/reports/safety-after-v2.1.md), [before side](../tiers-v2-design.md).
  - Don't say "13 of 29": that is a scripted model in the offline test, not the real model.
- **"The student alone: 0 of 29 under a strict user"**: from the [student-alone report](../../evals/reports/student-safety-alone.md).
  - If there's room, add "though it attempted more attacks than the big model: 5 of 29 against 1".
  - It isn't the Phase 4 claim.
- **"Phase 4 is being measured"**: the claim is that the routed student reaches at least 95% of the teacher's quality at
  lower cost. It's [pre-registered](../phase4-plan.md#8-pre-registration-of-the-phase-4-claim-2026-10-30), and the
  routed runs haven't finished. Don't say it's shown.

## On-screen text (optional captions)

| Scene | Caption |
|---|---|
| 1 | Multi-step task · local student model |
| 2 | Risky action → approval card |
| 3 | Prompt injection in a file → blocked (scripted model, real gate) |
| 4 | Local 4B student · $0 per request |
| 5 | Wake word + speech-to-text, on device |
| 6 | github.com/Ecko-maker/kestrel |
