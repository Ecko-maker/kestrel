// `npm test`: the HUD's event-to-state mapping (useHud.ts), driven through the real chat reducer
// (useChat.ts) with the same event sequences the agent sends. Node strips the types (Node 24).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { reduce } from "../useChat.ts";
import { BURST_MS, FALCON_POSE, answerKey, burstReducer, burstStart, phaseOf } from "./useHud.ts";

const ev = (type, data = {}) => ({ kind: "event", event: { type, trace_id: "t1", ...data } });
const play = (turns, ...actions) => actions.reduce(reduce, turns);
const pose = (turns, connection = "open") => FALCON_POSE[phaseOf(turns, connection).phase];

const llmOk = (step) => ev("llm_call", { step, ok: true, provider: "demo", model: "scripted", duration_ms: 600 });
const toolCall = (step, id = "c1", name = "get_current_time") =>
  ev("tool_call", { step, call_id: id, name, arguments: "{}", risk: "safe", server: null, external: false });
const toolResult = (step, id = "c1") =>
  ev("tool_result", { step, call_id: id, ok: true, ran: true, decision: "", result: "ok", duration_ms: 1 });
const answer = (stop = "answered") =>
  ev("answer", { text: "Done.", stop_reason: stop, steps: 2, tokens: 10, duration_ms: 900, providers: ["demo"], list_price_usd: null });

// One exchange, step by step: user message -> think -> tool -> think -> stream -> answer -> done.
const sent = play([], { kind: "send", text: "What time is it in Tokyo?" });
const thinking = play(sent, ev("step_started", { step: 1 }));
const tooling = play(thinking, llmOk(1), toolCall(1));
const toolDone = play(tooling, toolResult(1), ev("step_started", { step: 2 }));
const streaming = play(toolDone, ev("text_delta", { step: 2, text: "In Tokyo" }));
const answered = play(streaming, llmOk(2), answer());
const done = play(answered, ev("done"));

test("falcon pose for each state", () => {
  assert.equal(pose([]), "perch", "idle: perched");
  assert.equal(pose(thinking), "flap", "thinking (step started, no llm_call yet): flaps");
  assert.equal(pose(streaming), "flap", "streaming text: still flapping");
  assert.equal(pose(tooling), "dive", "a tool_call without its result: dives");
  assert.equal(pose(toolDone), "flap", "tool finished, next model call: back to flapping");
  assert.equal(pose(done), "return", "answer + done: swoops back");
  assert.equal(pose([], "closed"), "rest", "offline: rests");
});

test("waiting for approval: hovers", () => {
  const waiting = play(
    sent,
    ev("step_started", { step: 1 }),
    llmOk(1),
    toolCall(1, "c9", "create_note"),
    ev("approval_required", { approval_id: "a1", tool: "create_note", args: {}, preview: "", kind: "text", editable_field: null, allow_session: false, timeout_s: 300 }),
  );
  assert.equal(phaseOf(waiting, "open").phase, "approval");
  assert.equal(pose(waiting), "hover");
  const resolved = play(waiting, ev("approval_resolved", { approval_id: "a1", decision: "approved" }));
  assert.equal(pose(resolved), "dive", "approved, the tool now runs");
});

test("error: shakes, from the error event or a failed model call", () => {
  const failed = play(sent, ev("step_started", { step: 1 }), ev("llm_call", { step: 1, ok: false, error: "503" }));
  assert.equal(pose(failed), "shake");
  const errored = play(failed, ev("error", { message: "Sorry" }), answer("error"), ev("done"));
  assert.equal(pose(errored), "shake", "stays shaken (red) until the next message, not 'return'");
});

test("answerKey changes only on the answer event", () => {
  assert.equal(answerKey(sent), null);
  assert.equal(answerKey(streaming), null, "text_delta is not an answer");
  const more = play(streaming, ev("text_delta", { step: 2, text: " it's 9am" }));
  assert.equal(answerKey(more), null);
  assert.equal(answerKey(answered), sent[0].id, "the answer event sets it");
  assert.equal(answerKey(done), sent[0].id, "done doesn't change it");
  const next = play(done, { kind: "send", text: "again" }, ev("step_started", { step: 1 }), ev("text_delta", { step: 1, text: "x" }));
  assert.equal(answerKey(next), sent[0].id, "a new turn's streaming text doesn't change it either");
});

test("a failed turn's answer gets no burst", () => {
  const failed = play(sent, ev("step_started", { step: 1 }), ev("error", { message: "Sorry" }), answer("error"));
  assert.equal(answerKey(failed), null);
});

test("burst: fires once per answer", () => {
  let s = burstStart(null);
  s = burstReducer(s, { type: "answer", key: "turn-1", reducedMotion: false });
  assert.equal(s.playing, 1);
  const again = burstReducer(s, { type: "answer", key: "turn-1", reducedMotion: false });
  assert.equal(again, s, "same answer again (re-render): no new burst");
  s = burstReducer(s, { type: "end", id: 1 });
  assert.equal(s.playing, null);
  assert.equal(burstReducer(s, { type: "answer", key: "turn-1", reducedMotion: false }).playing, null, "never replays an old answer");
  s = burstReducer(s, { type: "answer", key: "turn-2", reducedMotion: false });
  assert.equal(s.playing, 2, "the next answer gets its own burst");
});

test("burst: not retriggered while one is playing, and the skipped answer never plays later", () => {
  let s = burstReducer(burstStart(null), { type: "answer", key: "turn-1", reducedMotion: false });
  s = burstReducer(s, { type: "answer", key: "turn-2", reducedMotion: false });
  assert.equal(s.playing, 1, "still the first burst");
  s = burstReducer(s, { type: "end", id: 1 });
  s = burstReducer(s, { type: "answer", key: "turn-2", reducedMotion: false });
  assert.equal(s.playing, null);
});

test("burst: a stale end doesn't stop a newer burst", () => {
  let s = burstReducer(burstStart(null), { type: "answer", key: "a", reducedMotion: false });
  s = burstReducer(burstReducer(s, { type: "end", id: 1 }), { type: "answer", key: "b", reducedMotion: false });
  assert.equal(burstReducer(s, { type: "end", id: 1 }).playing, 2);
});

test("burst: never under reduced motion", () => {
  const s = burstReducer(burstStart(null), { type: "answer", key: "turn-1", reducedMotion: true });
  assert.equal(s.playing, null);
  assert.equal(burstReducer(s, { type: "answer", key: "turn-1", reducedMotion: false }).playing, null, "nor later for the same answer");
});

test("burst: answers already on screen when the HUD mounts don't play", () => {
  const s = burstStart(answerKey(done));
  assert.equal(burstReducer(s, { type: "answer", key: answerKey(done), reducedMotion: false }).playing, null);
});

test("burst: every burst animation in hud.css ends within BURST_MS, under 1 s, at most one glow per 0.9 s", () => {
  assert.ok(BURST_MS < 1000);
  assert.ok(1000 / BURST_MS < 3, "WCAG 2.3.1: fewer than three flashes per second");
  const css = readFileSync(new URL("./hud.css", import.meta.url), "utf8");
  const rules = [...css.matchAll(/^[^{}\n]*(?:\.burst|\[data-burst\])[^{}\n]*\{[^}]*animation:\s*[\w-]+\s+(\d+)ms/gm)];
  assert.ok(rules.length >= 5, `found ${rules.length} burst rules`);
  const MAX_SPARK_DELAY = 60; // Reactor.tsx SPARKS: (i % 4) * 20
  for (const [rule, ms] of rules) assert.ok(Number(ms) + MAX_SPARK_DELAY <= BURST_MS, rule.split("{")[0]);
});
