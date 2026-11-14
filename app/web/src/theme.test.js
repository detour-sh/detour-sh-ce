import assert from "node:assert/strict";
import { test } from "node:test";
import {
  COLOR_FIELDS,
  PRESETS,
  contrastRatio,
  deriveTokens,
  findProblems,
  mix,
  normalizeHex,
  readableOn,
  resolveTheme,
  sanitizeCustom,
} from "./theme.js";

test("normalizeHex accepts 3 and 6 digit hex, with or without #, and rejects the rest", () => {
  assert.equal(normalizeHex("#ABC"), "#aabbcc");
  assert.equal(normalizeHex("0f62fe"), "#0f62fe");
  assert.equal(normalizeHex("  #0F62FE "), "#0f62fe");
  assert.equal(normalizeHex("#12345"), null);
  assert.equal(normalizeHex("red"), null);
  assert.equal(normalizeHex(42), null);
  assert.equal(normalizeHex(undefined), null);
});

test("contrastRatio matches the WCAG reference values", () => {
  assert.equal(contrastRatio("#000000", "#ffffff"), 21);
  assert.equal(contrastRatio("#ffffff", "#ffffff"), 1);
  // Order doesn't matter.
  assert.equal(contrastRatio("#ffffff", "#000000"), 21);
  // Carbon's default blue on white is a well known 4.5+ pair.
  assert.ok(contrastRatio("#0f62fe", "#ffffff") > 4.5);
});

test("readableOn picks the text color with the better contrast", () => {
  assert.equal(readableOn("#0f62fe"), "#ffffff");
  assert.equal(readableOn("#ffffff"), "#161616");
  assert.equal(readableOn("#f1c21b"), "#161616");
  assert.equal(readableOn("#000000"), "#ffffff");
});

test("mix blends two colors by a percentage of the second", () => {
  assert.equal(mix("#000000", "#ffffff", 0), "#000000");
  assert.equal(mix("#000000", "#ffffff", 100), "#ffffff");
  assert.equal(mix("#000000", "#ffffff", 50), "#808080");
});

test("sanitizeCustom keeps valid colors and falls back to the default for the rest", () => {
  const fallback = PRESETS.find((p) => p.id === "ocean").colors;
  const out = sanitizeCustom({ primary: "#ABCDEF", text: "nope", link: 7 }, fallback);
  assert.equal(out.primary, "#abcdef");
  assert.equal(out.text, fallback.text);
  assert.equal(out.link, fallback.link);
  assert.deepEqual(Object.keys(out).sort(), COLOR_FIELDS.map((f) => f.key).sort());
  // Garbage in, defaults out.
  assert.deepEqual(sanitizeCustom(null, fallback), fallback);
  assert.deepEqual(sanitizeCustom("{}", fallback), fallback);
});

test("every preset is readable: no contrast problems", () => {
  for (const preset of PRESETS) {
    assert.deepEqual(findProblems(preset.colors), [], `${preset.id} has contrast problems`);
  }
});

test("presets declare a base, all six colors, and unique ids", () => {
  const ids = new Set();
  for (const preset of PRESETS) {
    assert.ok(!ids.has(preset.id), `duplicate ${preset.id}`);
    ids.add(preset.id);
    assert.ok(["light", "dark"].includes(preset.base));
    for (const field of COLOR_FIELDS) assert.ok(normalizeHex(preset.colors[field.key]), `${preset.id}.${field.key}`);
  }
});

test("findProblems flags low contrast text, links and buttons", () => {
  const colors = { ...PRESETS[0].colors, text: "#cccccc", background: "#ffffff", link: "#eeeeee", primary: "#fafafa" };
  const ids = findProblems(colors).map((p) => p.field);
  assert.ok(ids.includes("text"));
  assert.ok(ids.includes("link"));
  assert.ok(ids.includes("primary"));
});

test("findProblems flags a primary and secondary that can't share one label color", () => {
  const colors = { ...PRESETS[0].colors, primary: "#ffd54a", secondary: "#102040" };
  const problems = findProblems(colors);
  assert.ok(problems.some((p) => p.field === "secondary" || p.field === "primary"));
});

test("deriveTokens maps the six colors onto Carbon tokens", () => {
  const colors = PRESETS.find((p) => p.id === "ocean").colors;
  const tokens = deriveTokens(colors);
  assert.equal(tokens["--cds-background"], colors.background);
  assert.equal(tokens["--cds-text-primary"], colors.text);
  assert.equal(tokens["--cds-link-primary"], colors.link);
  assert.equal(tokens["--cds-button-primary"], colors.primary);
  assert.equal(tokens["--cds-button-secondary"], colors.secondary);
  assert.equal(tokens["--cds-interactive"], colors.primary);
  assert.equal(tokens["--dt-header"], colors.header);
  assert.equal(tokens["--dt-header-text"], readableOn(colors.header));
  assert.equal(tokens["--cds-text-on-color"], readableOn(colors.primary));
  for (const [name, value] of Object.entries(tokens)) assert.match(value, /^#[0-9a-f]{6}$/, name);
});

test("deriveTokens makes layers and secondary text from the background and text", () => {
  const light = deriveTokens(PRESETS.find((p) => p.id === "ocean").colors);
  const dark = deriveTokens(PRESETS.find((p) => p.id === "midnight").colors);
  // A layer sits between the page and the text, and secondary text stays readable.
  assert.notEqual(light["--cds-layer-01"], light["--cds-background"]);
  assert.ok(contrastRatio(light["--cds-text-secondary"], light["--cds-layer-01"]) >= 4.5);
  assert.ok(contrastRatio(dark["--cds-text-secondary"], dark["--cds-layer-01"]) >= 4.5);
  assert.ok(contrastRatio(light["--cds-text-primary"], light["--cds-layer-accent-01"]) >= 4.5);
  assert.ok(contrastRatio(dark["--cds-text-primary"], dark["--cds-layer-accent-01"]) >= 4.5);
});

test("resolveTheme turns a stored choice into a base and optional colors", () => {
  const custom = { ...PRESETS[0].colors, primary: "#112233" };
  assert.deepEqual(resolveTheme("light", custom, false), { base: "light", colors: null });
  assert.deepEqual(resolveTheme("dark", custom, false), { base: "dark", colors: null });
  assert.deepEqual(resolveTheme("system", custom, true), { base: "dark", colors: null });
  assert.deepEqual(resolveTheme("system", custom, false), { base: "light", colors: null });
  const ocean = PRESETS.find((p) => p.id === "ocean");
  assert.deepEqual(resolveTheme("ocean", custom, true), { base: ocean.base, colors: ocean.colors });
  const midnight = PRESETS.find((p) => p.id === "midnight");
  assert.equal(resolveTheme("midnight", custom, false).base, "dark");
  // Custom derives its base from how light the background is.
  assert.deepEqual(resolveTheme("custom", { ...custom, background: "#ffffff" }, true), { base: "light", colors: { ...custom, background: "#ffffff" } });
  assert.equal(resolveTheme("custom", { ...custom, background: "#101010" }, false).base, "dark");
  // An unknown choice behaves like "system".
  assert.deepEqual(resolveTheme("nonsense", custom, false), { base: "light", colors: null });
});
