// `npm test`: the listening switch remembered per browser (listening.ts), the Speaker switch's message, and
// New conversation's reset (useChat.ts).
import assert from "node:assert/strict";
import { test } from "node:test";

import { listeningToApply } from "./listening.ts";
import { reduce, speakerMessage } from "./useChat.ts";

test("a browser that turned listening off turns it off again after a server restart", () => {
  assert.equal(listeningToApply("off", { available: true, enabled: true }), false);
  assert.equal(listeningToApply("on", { available: true, enabled: false }), true);
});

test("nothing is sent when the state already matches, nothing was remembered, or there's no wake word", () => {
  assert.equal(listeningToApply("off", { available: true, enabled: false }), null);
  assert.equal(listeningToApply(null, { available: true, enabled: true }), null);
  assert.equal(listeningToApply("maybe", { available: true, enabled: true }), null);
  assert.equal(listeningToApply("off", { available: false }), null);
});

test("the Speaker switch is one message the server understands", () => {
  assert.deepEqual(speakerMessage(false), { type: "speaker", on: false });
  assert.deepEqual(speakerMessage(true), { type: "speaker", on: true });
});

test("New conversation clears every turn", () => {
  const turns = reduce(reduce([], { kind: "send", text: "one" }), { kind: "send", text: "two", voice: true });
  assert.equal(turns.length, 2);
  assert.deepEqual(reduce(turns, { kind: "reset" }), []);
});
