// `npm test`: Node runs this directly and strips the types from imagePolicy.ts (Node 24).
import assert from "node:assert/strict";
import { test } from "node:test";

import { imageLoads } from "./imagePolicy.ts";

const ORIGIN = "http://127.0.0.1:8765";

test("images from the console itself load", () => {
  assert.equal(imageLoads("/kestrel.svg", [], ORIGIN), true);
  assert.equal(imageLoads("http://127.0.0.1:8765/a.png", [], ORIGIN), true);
});

test("with an empty allowlist no outside image loads", () => {
  assert.equal(imageLoads("https://collector.example/p.png?d=KCAN-5d2e8f41a9c3", [], ORIGIN), false);
  assert.equal(imageLoads("//collector.example/p.png", [], ORIGIN), false); // protocol-relative
  assert.equal(imageLoads("http://localhost:8765/a.png", [], ORIGIN), false); // another origin, even if local
});

test("an allowlisted host and its subdomains load, look-alikes don't", () => {
  const allow = ["img.example"];
  assert.equal(imageLoads("https://img.example/a.png", allow, ORIGIN), true);
  assert.equal(imageLoads("https://cdn.img.example/a.png", allow, ORIGIN), true);
  assert.equal(imageLoads("https://img.example.evil.example/a.png", allow, ORIGIN), false);
  assert.equal(imageLoads("https://evilimg.example/a.png", allow, ORIGIN), false);
});

test("odd sources never load", () => {
  assert.equal(imageLoads("", [], ORIGIN), false);
  assert.equal(imageLoads("data:image/png;base64,AAAA", ["img.example"], ORIGIN), false);
  assert.equal(imageLoads("http://[bad", [], ORIGIN), false);
});
