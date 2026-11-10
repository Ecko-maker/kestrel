// `npm test`: the sidebar's two always-visible controls, "New conversation" (under Chat) and the Speaker switch
// (next to Wake word), in both looks, on an empty chat, with no wake word and with no voice on the server.
// The component is compiled with Vite's own bundler (rolldown) and rendered to HTML with react-dom/server.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { before, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { newConversationState, speakerState } from "./sidebar.ts";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "node_modules", ".cache", "kestrel-sidebar-test");
let Sidebar, createElement, renderToStaticMarkup;

before(async () => {
  const { build } = await import("rolldown");
  mkdirSync(outDir, { recursive: true });
  await build({
    input: join(here, "components", "Sidebar.tsx"),
    external: [/^react($|\/)/, /^react-dom($|\/)/, /^lucide-react($|\/)/],
    platform: "node",
    transform: { jsx: { runtime: "automatic" } },
    output: { dir: outDir, format: "esm", entryFileNames: "sidebar.mjs" },
    logLevel: "silent",
  });
  ({ Sidebar } = await import(pathToFileURL(join(outDir, "sidebar.mjs")).href));
  ({ createElement } = await import("react"));
  ({ renderToStaticMarkup } = await import("react-dom/server"));
});

const noop = () => undefined;
const props = (over = {}) => ({
  look: "classic",
  setLook: noop,
  theme: "system",
  setTheme: noop,
  page: "chat",
  session: null,
  connection: "open",
  turns: 0,
  running: false,
  onNewConversation: noop,
  wakeStatus: { available: true, enabled: true, speech: true },
  onToggleWake: noop,
  speakAnswers: true,
  onSpeakAnswers: noop,
  ...over,
});
const render = (over) => renderToStaticMarkup(createElement(Sidebar, props(over)));

// The <button> whose aria-label starts with `label`, as its opening tag.
function button(html, label) {
  const tag = html.match(new RegExp(`<button[^>]*aria-label="${label}[^"]*"[^>]*>`));
  assert.ok(tag, `no "${label}" button in the sidebar`);
  return tag[0];
}
const isDisabled = (tag) => /\sdisabled=""/.test(tag);

for (const look of ["classic", "hud"]) {
  test(`${look}: "New conversation" is shown on an empty chat, disabled, right under Chat`, () => {
    const html = render({ look, turns: 0 });
    const tag = button(html, "New conversation");
    assert.ok(isDisabled(tag));
    assert.match(tag, /title="Nothing to clear yet"/);
    const chat = html.indexOf('href="#/chat"');
    const fresh = html.indexOf('aria-label="New conversation');
    const traces = html.indexOf('href="#/traces"');
    assert.ok(chat < fresh && fresh < traces, "under Chat, before Traces");
  });

  test(`${look}: "New conversation" is enabled once the chat has messages`, () => {
    assert.ok(!isDisabled(button(render({ look, turns: 2 }), "New conversation")));
  });

  test(`${look}: the Speaker switch is shown next to Wake word, enabled when the server can speak`, () => {
    const html = render({ look });
    const tag = button(html, "Speaker");
    assert.ok(!isDisabled(tag));
    assert.ok(html.indexOf('aria-label="Wake word') < html.indexOf('aria-label="Speaker'), "after the wake chip");
  });

  test(`${look}: with the wake word off on the server, the Speaker switch is shown, disabled, saying why`, () => {
    const tag = button(render({ look, wakeStatus: { available: false } }), "Speaker");
    assert.ok(isDisabled(tag));
    assert.match(tag, /title="No speech output: start Kestrel with KESTREL_WAKE=on"/);
  });

  test(`${look}: in demo mode the Speaker switch is shown, disabled, saying why`, () => {
    const tag = button(render({ look, session: { demo: true, models: [] }, wakeStatus: { available: false } }), "Speaker");
    assert.ok(isDisabled(tag));
    assert.match(tag, /title="No speech output in demo mode"/);
  });

  test(`${look}: with no voice on the server, the Speaker switch is shown, disabled, saying why`, () => {
    const tag = button(render({ look, wakeStatus: { available: true, enabled: true, speech: false } }), "Speaker");
    assert.ok(isDisabled(tag));
    assert.match(tag, /needs Windows' speech engine|needs Windows&#x27; speech engine/);
  });
}

test("New conversation: disabled while disconnected, while Kestrel works, and with nothing to clear", () => {
  assert.equal(newConversationState(3, false, false).disabled, true);
  assert.equal(newConversationState(3, true, true).disabled, true);
  assert.equal(newConversationState(0, false, true).title, "Nothing to clear yet");
  assert.deepEqual(newConversationState(3, false, true).disabled, false);
});

test("Speaker: an older server that doesn't report `speech` keeps the switch usable", () => {
  assert.equal(speakerState({ available: true }, false).disabled, false);
});
