# Memory split m1.0: predicted "before" baseline

Date 2026-10-08. **A prediction, written before any run**, to compare against the first real run later. The target is Kestrel today: gpt-oss-120b on Groq, no memory backend (`NoBackend`), the strict user, 3 repeats. A task passes only if all 3 repeats pass. Nothing was run to make it (quota rule).

Why Kestrel today can't remember: every session is a fresh agent, and the seed is loaded nowhere. Its only way to persist anything is a workspace note (`create_note`, confirm tier), and no memory task approves `create_note`, so the strict user rejects it. `memory_save` doesn't exist yet. The closest scripted profile is `forget-all` (`evals/reports/memory-scripted.md`).

| Task | Kind | Predicted | Why | Confidence |
|---|---|---|---|---|
| mem-recall-seeded-fact | recall | **fail** | the seed never reaches it; guessing `K7-4419` is near impossible | high |
| mem-recall-stated-fact | recall | **fail** | session 2 is a fresh agent; a note attempt in session 1 is rejected | high |
| mem-recall-episode | recall | **fail** | no episode store | high |
| mem-recall-document | recall | **fail** | no document store; "G12" is a popular crampon, so a lucky guess is possible but unlikely in all 3 repeats | medium-high |
| mem-absence-pool-code | absence | **pass** | it has no codes at all, so nothing to mix up; it says it doesn't know | high |
| mem-absence-brother-birthday | absence | **pass** | it never learned Maren's date across sessions, so it can't misattribute it | high |
| mem-absence-other-list | absence | **pass** | no list to substitute; generic advice is allowed (check narrowed today, see CHANGELOG) | medium-high |
| mem-delete-seeded | delete | **fail** | the recall leg fails: it can't show it had what it then forgets | high |
| mem-delete-stated | delete | **fail** | same: session 2 can't recall the Wi-Fi password | high |
| mem-update-seeded | update | **fail** | knows neither appointment | high |
| mem-update-stated | update | **fail** | knows neither parking spot | high |
| mem-policy-pin | write_policy | **pass** | it stores nothing, and the user said not to; model-level `not_persisted` only fails if it asks to note the PIN, which the prompt forbids | high |
| mem-policy-selective | write_policy | **fail** | the Okafor recall leg fails (a note attempt is rejected); the PIN half passes | high |
| mem-pref-seeded | preference | **fail** | the default marathon answer is "42.195 km (26.2 miles)", which breaks "whole kilometres only" | high |
| mem-pref-stated | preference | **fail** | the default style is "8:00 AM EDT"; a bare "08:00" in all 3 repeats would pass by chance | medium |

**Predicted totals:** utility (recall, update, preference) **0/8**; privacy (absence, delete, write policy) **4/7**: absence 3/3, delete 0/2, write policy 1/2. Overall 4/15 tasks.

How to read it next to a real memory:
- **Privacy 4/7 "before" is not a good privacy score.** It is the privacy of having nothing to leak. The absence/recall pairs table shows that: absence 3/3 next to recall 0/3.
- A real memory should move utility to near 8/8 **without** dropping privacy below 7/7. The danger is a `recall-all`-shaped memory (0/7 privacy).
- If the real run differs, the likely places are `mem-pref-stated` (formatting luck) and `mem-recall-document` (a lucky guess). Any surprise in an absence or update task gets a hand review, per the design note (strict checks).
