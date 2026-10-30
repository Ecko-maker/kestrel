// `npm test`: the wake word's status line in the nav and the HUD (Node strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";

import { wakeLabel } from "./wakeLabel.ts";

const base = { available: true, enabled: true };

test("listening shows the battery only when on battery", () => {
  assert.equal(wakeLabel({ ...base, listening: true, on_ac: true, battery: 80 }), "Listening");
  assert.equal(wakeLabel({ ...base, listening: true, on_ac: false, battery: 64 }), "Listening (battery 64%)");
  assert.equal(wakeLabel({ ...base, listening: true, on_ac: false, battery: null }), "Listening");
});

test("paused shows the server's reason", () => {
  assert.equal(wakeLabel({ ...base, listening: false, paused: "battery low (28%)" }), "Paused: battery low (28%)");
  assert.equal(wakeLabel({ ...base, listening: false, paused: "power saver" }), "Paused: power saver");
  assert.equal(wakeLabel({ ...base, enabled: false, listening: false, paused: "off" }), "Wake word off");
});
