# Presentation copilot: idea (stub)

**Stages 1-2 now have a design:** [presentation-copilot-design.md](presentation-copilot-design.md) (2026-11-23,
open questions with the owner). Where the two differ, the design is newer. This stub stays for stages 3-4.

**Status:** idea only, added 2026-11-21 as a future roadmap item (`docs/STATUS.md`, Open). No design, no code,
no model calls, no Groq quota, **no approval lines**. **Starts after the data-rca measurements**
(`docs/data-rca-design.md`, decision 35), which are queued after the Google step's after side.

**Idea:** Kestrel helps you while you present data. Before the talk it prepares likely questions with grounded
answers; during the talk it shows you private cue cards when someone asks a question. It answers from
`kestrel-data` tool results, never from memory, and it never speaks or acts unless you ask it to.

## Stages

Each stage is useful on its own and ships opt-in, off by default. Each one gets its own pre-registered plan
(metrics and pass rules frozen before any run) and is measured before the next one starts.

| Stage | What you get | Input | Output |
|---|---|---|---|
| 1. Prep mode | Anticipated Q&A for a deck or dataset: likely questions, answers grounded in `kestrel-data` results | your deck / dataset | a Q&A sheet |
| 2. Live text mode | You type a question during the talk; a private cue card comes back | typed text | cue card, on your screen only |
| 3. Live listening | Kestrel hears a question in the room and shows a muted cue card | local speech-to-text, question detection | cue card, silent; a visible "listening" indicator while on |
| 4. Speak mode | Kestrel reads a card aloud | a tap on that card | speech for that one card |

- **Stage 3:** speech-to-text runs **locally** (no audio leaves the laptop). Default is **transcribe and discard**:
  audio and text are dropped once the question is detected and handled. Nothing is stored unless the owner turns
  it on. The indicator is visible to the room whenever listening is on.
- **Stage 4:** speech happens only on an explicit tap, per card, never on its own.

## Guardrails (all stages)

1. **Read-only tools only** in presentation mode. The tool set has no send, write, delete, pay or post tool.
2. **Room speech is untrusted and tainting.** Anything heard (or pasted from what someone said) is untrusted data,
   never instructions. It taints the session under the tiers-v2 rules (`src/kestrel/permissions.py`). Stage 3 gets
   its own injection tasks (spoken attacks) in the safety split.
3. **Every number is checked against a tool result.** A deterministic check runs before a card is shown: each
   number on the card must match a value in this session's tool results. A card with an unmatched number is not
   shown as an answer.
4. **Below medium confidence, the card says "needs checking"** instead of an answer. Confidence comes from the
   evidence, as in data-rca (computed, not stated by the model).
5. **Parking lot + post-meeting summary.** Questions it can't answer well go into a parking lot. After the
   meeting you get a summary: the parked questions and the cards shown, sent to your own chat (Telegram), nowhere else.
6. **Recording consent.** Listening to other people can need their consent, and the law depends on where you are
   (some places require everyone's consent). The owner tells the room before turning on stage 3. The
   visible indicator and transcribe-and-discard default support that; they don't replace it.

## Measurement (sketch, to be pre-registered per stage)

- Stage 1: share of answers whose numbers all match tool results; coverage against a held-out list of questions.
- Stage 2: cue card latency (p50/p95); number-check pass rate; how well "needs checking" matches wrong answers.
- Stage 3: question detection precision and recall on scripted, consented test recordings; spoken-injection
  attack success (gate: 0%); no audio or text kept after a run with the default setting.
- Stage 4: no speech without a tap (tested); tap-to-speech latency.

## Open questions (for the design)

- Local STT model and size (a download over 100 MB needs the owner's OK first).
- Whether the parking lot keeps the question text by default, given transcribe-and-discard.
- Reuse of the Phase 5 voice work (`docs/voice-wake.md`) for stages 3-4.
- Where the cue cards appear (console, phone, a second screen).
