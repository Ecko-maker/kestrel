// `npm test`: the HUD's modes (useHud.ts hudPhase / attendingOf / pacePulse), driven through the real
// chat reducer. Each transition the owner asked for has its own test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { reduce } from "../useChat.ts";
import {
  FALCON_POSE,
  PHASE_LABEL,
  PULSE_MIN_MS,
  attendingOf,
  hudPhase,
  pacePulse,
  phaseOf,
  pulseKey,
} from "./useHud.ts";

const ev = (type, data = {}) => ({ kind: "event", event: { type, trace_id: "t1", ...data } });
const play = (turns, ...actions) => actions.reduce(reduce, turns);
const agent = (turns) => phaseOf(turns, "open").phase;

// The wake inputs, as useHud computes them, for one moment in time.
const quiet = { micOn: true, arriving: false, attending: false, settling: false };
const mode = (turns, o = {}) => hudPhase(agent(turns), { ...quiet, ...o });

test("every mode has its own pose and label", () => {
  const modes = ["standby", "attending", "thinking", "streaming"];
  assert.deepEqual(
    modes.map((m) => [FALCON_POSE[m], PHASE_LABEL[m]]),
    [
      ["perch", "Standby"],
      ["attend", "Listening…"],
      ["flap", "Thinking…"],
      ["glide", "Answering…"],
    ],
  );
  assert.equal(PHASE_LABEL.idle, "Ready", "the wake word off: not 'Standby'");
  assert.equal(new Set(Object.values(PHASE_LABEL)).size, Object.keys(PHASE_LABEL).length, "labels are all distinct");
});

test("wake -> Listening -> typing -> Standby", () => {
  assert.equal(mode([]), "standby", "mic on, nothing happening");
  assert.equal(mode([], { arriving: true, attending: true }), "waking", "the fly-in first");
  const listening = attendingOf({ windowOpen: true, dismissed: false, sentSinceWake: false });
  assert.equal(mode([], { attending: listening }), "attending");
  const typed = attendingOf({ windowOpen: true, dismissed: true, sentSinceWake: false });
  assert.equal(typed, false);
  assert.equal(mode([], { attending: typed }), "standby");
});

test("Listening times out -> Standby", () => {
  const timedOut = attendingOf({ windowOpen: false, dismissed: false, sentSinceWake: false });
  assert.equal(mode([], { attending: timedOut }), "standby");
});

test("sending a message ends Listening; then Thinking -> Answering -> done (burst) -> Standby", () => {
  const sentSinceWake = attendingOf({ windowOpen: true, dismissed: false, sentSinceWake: true });
  assert.equal(sentSinceWake, false, "a sent message ends Listening");
  const sent = play([], { kind: "send", text: "What time is it in Tokyo?" });
  const thinking = play(sent, ev("step_started", { step: 1 }));
  assert.equal(mode(thinking), "thinking");
  const answering = play(thinking, ev("text_delta", { step: 1, text: "It's 9 in Tokyo" }));
  assert.equal(mode(answering), "streaming");
  const done = play(
    answering,
    ev("llm_call", { step: 1, ok: true }),
    ev("answer", { text: "It's 9.", stop_reason: "answered", steps: 1, tokens: 1, duration_ms: 1, providers: [] }),
    ev("done"),
  );
  assert.equal(mode(done, { settling: true }), "done", "the swoop back and the burst");
  assert.equal(mode(done, { settling: false }), "standby", "then back to Standby");
  assert.equal(mode(done, { settling: false, micOn: false }), "idle", "or Ready with the wake word off");
});

test("an approval during a turn wins over Listening, the fly-in and Standby", () => {
  const waiting = play(
    [],
    { kind: "send", text: "save it" },
    ev("step_started", { step: 1 }),
    ev("llm_call", { step: 1, ok: true }),
    ev("tool_call", { step: 1, call_id: "c1", name: "create_note", arguments: "{}", risk: "confirm" }),
    ev("approval_required", { approval_id: "a1", tool: "create_note", args: {}, preview: "", kind: "text", timeout_s: 300 }),
  );
  for (const o of [{ attending: true }, { arriving: true, attending: true }, {}]) {
    assert.equal(mode(waiting, o), "approval");
  }
});

test("errors and tools also win over the wake word's modes", () => {
  assert.equal(hudPhase("error", { ...quiet, attending: true }), "error");
  assert.equal(hudPhase("tool", { ...quiet, arriving: true }), "tool");
});

test("Answering pulses follow the text but never faster than every 450 ms", () => {
  assert.equal(pulseKey("x".repeat(39)), 0);
  assert.equal(pulseKey("x".repeat(80)), 2);
  let shown = { key: 0, at: 1000 };
  shown = pacePulse(shown, 1, 1100);
  assert.deepEqual(shown, { key: 0, at: 1000 }, "too soon: held back");
  shown = pacePulse(shown, 3, 1000 + PULSE_MIN_MS);
  assert.deepEqual(shown, { key: 3, at: 1000 + PULSE_MIN_MS });
  assert.ok(1000 / PULSE_MIN_MS < 3, "WCAG 2.3.1: fewer than three pulses a second");
});

test("reduced motion: the new rings and poses stop, colour and label stay", () => {
  const css = readFileSync(new URL("./hud.css", import.meta.url), "utf8");
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  for (const ring of [".reactor .standby-ripple", ".reactor .text-pulse"]) {
    const line = reduced.split(/\r?\n/).find((l) => l.includes(ring));
    assert.ok(line && line.includes("display: none"), `${ring} is hidden under reduced motion`);
  }
  assert.match(reduced, /\.reactor \.level-ring \{ transform: none !important;/);
  assert.match(reduced, /\.reactor \.falcon-flight, \.reactor \.falcon-bob, \.reactor \.wing \{ transform: none !important; \}/);
  for (const [phase, color] of [["standby", "--hud-standby"], ["attending", "--hud-listening"], ["streaming", "--hud-answering"]]) {
    assert.ok(css.includes(`.reactor[data-phase="${phase}"] { --phase: var(${color}); }`), `${phase} has its own colour`);
  }
});
