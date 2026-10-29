// WCAG AA (4.5:1) for the HUD's text colours, read from hud.css so the test follows the palette.
// Tailwind's amber/red/emerald are not redefined by the HUD; their hex values below are sRGB
// approximations of Tailwind 4's oklch values, used only to check them against the new navy.
// Run: node --test src/hud/contrast.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("./hud.css", import.meta.url), "utf8");
const vars = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
const v = (name) => {
  assert.ok(vars[name], `--${name} missing from hud.css`);
  return vars[name];
};

const TW = { "amber-100": "#fef3c6", "amber-300": "#ffd236", "amber-950": "#461901", "red-300": "#ffa2a2", "emerald-300": "#5ee9b5" };

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const toHex = (c) => "#" + c.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
const over = (fg, alpha, bg) => toHex(rgb(fg).map((c, i) => c * alpha + rgb(bg)[i] * (1 - alpha)));
function luminance(hex) {
  const [r, g, b] = rgb(hex).map((c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].toSorted((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const page = v("color-stone-950");
const card = v("color-stone-900");
// Brightest spot of the page background: the top glow plus a grid line.
const pageBright = over(v("hud-cyan"), 0.08 + 0.035, page);

const pairs = [
  ...["50", "100", "200", "300", "400", "500"].flatMap((n) => [
    [`stone-${n} on page`, v(`color-stone-${n}`), pageBright],
    [`stone-${n} on card`, v(`color-stone-${n}`), card],
  ]),
  ["white on primary button (accent-600)", "#ffffff", v("color-accent-600")],
  ["accent-300 on card", v("color-accent-300"), card],
  ["accent-200 on page", v("color-accent-200"), pageBright],
  ["wordmark on page", v("hud-text"), pageBright],
  ["amber phase text on page", TW["amber-300"], pageBright],
  ["red phase text on card", TW["red-300"], card],
  ["emerald log text on card", TW["emerald-300"], card],
  ["approval header text on amber-950/30 over card", TW["amber-100"], over(TW["amber-950"], 0.3, card)],
  ["user bubble (stone-900 on stone-200)", card, v("color-stone-200")],
];

for (const [name, fg, bg] of pairs) {
  test(`AA contrast: ${name}`, () => {
    const r = ratio(fg, bg);
    assert.ok(r >= 4.5, `${name}: ${r.toFixed(2)}:1 (${fg} on ${bg})`);
  });
}
