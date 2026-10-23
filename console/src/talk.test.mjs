// `npm test`: a spoken request becomes a chat turn like a typed one, only marked (for the mic icon).
import assert from "node:assert/strict";
import { test } from "node:test";

import { reduce } from "./useChat.ts";

// Ids and timestamps are generated per turn and step; the voice mark is the one intended difference.
const comparable = (turn) =>
  JSON.parse(JSON.stringify(turn, (key, value) => (["id", "startedAt", "voice"].includes(key) ? undefined : value)));

const event = (type, data = {}) => ({ kind: "event", event: { type, trace_id: "t1", ...data } });

const play = (voice) =>
  [event("step_started", { step: 1 }), event("text_delta", { step: 1, text: "It's 9 pm." }), event("done")].reduce(
    reduce,
    reduce([], { kind: "send", text: "time?", voice }),
  );

test("a spoken request is the same turn as a typed one, plus the voice mark", () => {
  const [typed] = reduce([], { kind: "send", text: "What time is it in Tokyo?" });
  const [spoken] = reduce([], { kind: "send", text: "What time is it in Tokyo?", voice: true });
  assert.equal(typed.voice, undefined);
  assert.equal(spoken.voice, true);
  assert.deepEqual(comparable(spoken), comparable(typed));
});

test("events for a spoken turn are folded in exactly like a typed one", () => {
  const [typed] = play(false);
  const [spoken] = play(true);
  assert.deepEqual(comparable(spoken), comparable(typed));
  assert.equal(spoken.voice, true);
});
