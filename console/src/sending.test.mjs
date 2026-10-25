// `npm test`: the send countdown for a spoken request (sending.ts). The timer itself runs on the
// server (tests/test_talk.py); these check what the console shows and what it sends back.
import assert from "node:assert/strict";
import { test } from "node:test";

import { boxAfterCancel, cancelMessage, isCancelKey, pendingAfter, secondsLeft } from "./sending.ts";

const pending = (now = 1000) =>
  pendingAfter(null, { type: "voice_pending", id: "r1", text: "List my files.", seconds: 2 }, now);

test("voice_pending starts the countdown, voice_request (sent) ends it", () => {
  const p = pending();
  assert.deepEqual(p, { id: "r1", text: "List my files.", seconds: 2, receivedAt: 1000 });
  assert.equal(secondsLeft(p, 1500), 1.5);
  assert.equal(secondsLeft(p, 4000), 0);
  assert.equal(pendingAfter(p, { type: "voice_request", text: "List my files." }, 3000), null);
});

test("Esc cancels: the key is Escape and the message names this request", () => {
  assert.equal(isCancelKey("Escape"), true);
  assert.equal(isCancelKey("Enter"), false);
  assert.deepEqual(cancelMessage(pending(), "esc"), { type: "voice_cancel", id: "r1", by: "esc" });
});

test("the Cancel button cancels the same way", () => {
  assert.deepEqual(cancelMessage(pending(), "button"), { type: "voice_cancel", id: "r1", by: "button" });
});

test("a cancel (Esc, button or voice) stops the countdown and puts the text in the message box", () => {
  for (const by of ["esc", "button", "voice"]) {
    const event = { type: "voice_cancelled", id: "r1", text: "List my files.", by };
    assert.equal(pendingAfter(pending(), event, 1200), null);
    assert.deepEqual(boxAfterCancel(event), { text: "List my files.", why: "cancelled" });
  }
});

test("a cancel for another request leaves the current countdown alone", () => {
  const p = pending();
  assert.equal(pendingAfter(p, { type: "voice_cancelled", id: "old", text: "x" }, 1200), p);
  assert.equal(pendingAfter(p, { type: "approval_required", approval_id: "r1" }, 1200), p);
});

test("cancelling never produces an approval answer", () => {
  const message = cancelMessage(pending(), "esc");
  assert.notEqual(message.type, "approval_response");
  assert.equal("decision" in message || "approval_id" in message, false);
});
