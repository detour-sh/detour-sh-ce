// Theme engine. Pure functions plus two small DOM/storage helpers at the bottom.
//
// Carbon exposes every color as a --cds-* CSS custom property, so a theme is just six
// colors turned into the handful of Carbon tokens that matter. No component is restyled.

const CHOICE_KEY = "detour-theme";
const CUSTOM_KEY = "detour-theme-custom";

/** The six colors a person can set. Order is the order they appear in Settings. */
export const COLOR_FIELDS = [
  { key: "primary", label: "Primary", help: "Main buttons, focus rings, selected items" },
  { key: "secondary", label: "Secondary", help: "Secondary buttons" },
  { key: "text", label: "Text", help: "Body text and headings" },
  { key: "link", label: "Links", help: "Links and inline actions" },
  { key: "background", label: "Background", help: "The page behind everything" },
  { key: "header", label: "Header", help: "The top bar" },
];

// Light and dark presets. Each one is checked for contrast in theme.test.js.
export const PRESETS = [
  { id: "ocean", label: "Ocean", base: "light", colors: { primary: "#0b6e99", secondary: "#1f3a5f", text: "#12263a", link: "#0b5cad", background: "#f4f8fb", header: "#0b3a5b" } },
  { id: "forest", label: "Forest", base: "light", colors: { primary: "#1e7d4f", secondary: "#3f4f44", text: "#18241c", link: "#1a6b44", background: "#f5f8f4", header: "#17402b" } },
  { id: "sunset", label: "Sunset", base: "light", colors: { primary: "#c2410c", secondary: "#7c2d12", text: "#2b1d16", link: "#b03a0a", background: "#fdf7f2", header: "#7c2d12" } },
  { id: "aubergine", label: "Aubergine", base: "dark", colors: { primary: "#9333ea", secondary: "#6b21a8", text: "#f5eef8", link: "#d8b4fe", background: "#1f1224", header: "#3b0f4d" } },
  { id: "midnight", label: "Midnight", base: "dark", colors: { primary: "#2563eb", secondary: "#475569", text: "#e2e8f0", link: "#93c5fd", background: "#0f172a", header: "#020617" } },
  { id: "contrast", label: "High contrast", base: "light", colors: { primary: "#0043ce", secondary: "#161616", text: "#000000", link: "#0043ce", background: "#ffffff", header: "#000000" } },
];

/** Where "Custom" starts: the first preset, so the pickers never open on something unreadable. */
export const CUSTOM_DEFAULT = PRESETS[0].colors;

const DARK_TEXT = "#161616";
const LIGHT_TEXT = "#ffffff";

export function normalizeHex(value) {
  if (typeof value !== "string") return null;
  const hex = value.trim().replace(/^#/, "").toLowerCase();
  if (/^[0-9a-f]{3}$/.test(hex)) return `#${[...hex].map((c) => c + c).join("")}`;
  return /^[0-9a-f]{6}$/.test(hex) ? `#${hex}` : null;
}

function channels(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex([r, g, b]) {
  return `#${[r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("")}`;
}

function luminance(hex) {
  const [r, g, b] = channels(hex).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio, 1 to 21. */
export function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
}

/** Whichever of white or Carbon's near-black reads better on this color. */
export function readableOn(hex) {
  return contrastRatio(hex, LIGHT_TEXT) >= contrastRatio(hex, DARK_TEXT) ? LIGHT_TEXT : DARK_TEXT;
}

/** Blend: pct percent of b over a. */
export function mix(a, b, pct) {
  const [ca, cb] = [channels(a), channels(b)];
  return toHex(ca.map((v, i) => v + (cb[i] - v) * (pct / 100)));
}

function isDark(background) {
  return luminance(background) < 0.4;
}

/** Keep valid colors from `input`, take the rest from `fallback`. Always returns all six. */
export function sanitizeCustom(input, fallback = CUSTOM_DEFAULT) {
  const source = input && typeof input === "object" ? input : {};
  return Object.fromEntries(COLOR_FIELDS.map(({ key }) => [key, normalizeHex(source[key]) || fallback[key]]));
}

// One label color has to work on both primary and secondary buttons, because Carbon uses one token for both.
function onColor(colors) {
  const score = (label) => Math.min(contrastRatio(label, colors.primary), contrastRatio(label, colors.secondary));
  return score(LIGHT_TEXT) >= score(DARK_TEXT) ? LIGHT_TEXT : DARK_TEXT;
}

/**
 * Anything that would make the UI hard to read. Not an error: people can keep their colors.
 * Text and links need 4.5:1 (WCAG AA). Buttons and focus rings need 3:1 against the page,
 * and the button label needs 4.5:1 on the button.
 */
export function findProblems(colors) {
  const problems = [];
  const add = (field, message) => problems.push({ field, message });
  if (contrastRatio(colors.text, colors.background) < 4.5) add("text", "Text is hard to read on this background. Aim for a contrast of at least 4.5.");
  if (contrastRatio(colors.link, colors.background) < 4.5) add("link", "Links are hard to read on this background. Aim for a contrast of at least 4.5.");
  if (contrastRatio(colors.primary, colors.background) < 3) add("primary", "The primary color blends into the background, so buttons and focus rings are hard to see.");
  const label = onColor(colors);
  if (contrastRatio(label, colors.primary) < 4.5) add("primary", "Button labels are hard to read on this primary color.");
  if (contrastRatio(label, colors.secondary) < 4.5) add("secondary", "Button labels are hard to read on this secondary color. Pick one close in lightness to the primary color.");
  return problems;
}

/** The Carbon tokens (and Detour's two header tokens) for a set of six colors. */
export function deriveTokens(colors) {
  const { primary, secondary, text, link, background, header } = colors;
  const dark = isDark(background);
  const press = dark ? LIGHT_TEXT : "#000000";
  const fromText = (pct) => mix(background, text, pct);
  const layer = fromText(5);
  const label = onColor(colors);
  const secondaryText = mix(text, background, 28);
  return {
    "--cds-background": background,
    "--cds-background-hover": fromText(8),
    "--cds-background-active": fromText(18),
    "--cds-background-selected": fromText(10),
    "--cds-background-selected-hover": fromText(14),
    "--cds-background-inverse": text,
    "--cds-background-brand": primary,
    "--cds-layer": layer,
    "--cds-layer-01": layer,
    "--cds-layer-02": fromText(9),
    "--cds-layer-hover": fromText(9),
    "--cds-layer-hover-01": fromText(9),
    "--cds-layer-active": fromText(18),
    "--cds-layer-active-01": fromText(18),
    "--cds-layer-selected": fromText(11),
    "--cds-layer-selected-01": fromText(11),
    "--cds-layer-accent": fromText(14),
    "--cds-layer-accent-01": fromText(14),
    "--cds-layer-accent-hover-01": fromText(20),
    "--cds-field": layer,
    "--cds-field-01": layer,
    "--cds-field-hover": fromText(9),
    "--cds-field-hover-01": fromText(9),
    "--cds-border-subtle": fromText(20),
    "--cds-border-subtle-00": fromText(20),
    "--cds-border-subtle-01": fromText(20),
    "--cds-border-subtle-02": fromText(24),
    "--cds-border-strong": fromText(55),
    "--cds-border-strong-01": fromText(55),
    "--cds-border-interactive": primary,
    "--cds-text-primary": text,
    "--cds-text-secondary": secondaryText,
    "--cds-text-helper": mix(text, background, 35),
    "--cds-text-inverse": background,
    "--cds-text-on-color": label,
    "--cds-icon-primary": text,
    "--cds-icon-secondary": secondaryText,
    "--cds-icon-interactive": primary,
    "--cds-icon-on-color": label,
    "--cds-interactive": primary,
    "--cds-focus": primary,
    "--cds-highlight": mix(background, primary, 18),
    "--cds-button-primary": primary,
    "--cds-button-primary-hover": mix(primary, press, 12),
    "--cds-button-primary-active": mix(primary, press, 24),
    "--cds-button-secondary": secondary,
    "--cds-button-secondary-hover": mix(secondary, press, 12),
    "--cds-button-secondary-active": mix(secondary, press, 24),
    "--cds-button-tertiary": primary,
    "--cds-button-tertiary-hover": mix(primary, press, 12),
    "--cds-button-tertiary-active": mix(primary, press, 24),
    "--cds-link-primary": link,
    "--cds-link-primary-hover": mix(link, text, 30),
    "--cds-link-secondary": mix(link, text, 30),
    "--cds-link-visited": mix(link, text, 40),
    "--dt-header": header,
    "--dt-header-text": readableOn(header),
    "--dt-header-hover": mix(header, readableOn(header), 14),
  };
}

/**
 * Turn the stored choice into what to paint. `colors` is null when Carbon's own
 * light or dark theme should be used untouched.
 */
export function resolveTheme(choice, custom, systemPrefersDark) {
  if (choice === "light" || choice === "dark") return { base: choice, colors: null };
  if (choice === "custom") return { base: isDark(custom.background) ? "dark" : "light", colors: custom };
  const preset = PRESETS.find((p) => p.id === choice);
  if (preset) return { base: preset.base, colors: preset.colors };
  return { base: systemPrefersDark ? "dark" : "light", colors: null };
}

// ---- Browser side: storage and painting ----

export function readThemeChoice() {
  try {
    return localStorage.getItem(CHOICE_KEY) || "system";
  } catch {
    return "system";
  }
}

export function readCustomColors() {
  try {
    return sanitizeCustom(JSON.parse(localStorage.getItem(CUSTOM_KEY)));
  } catch {
    return { ...CUSTOM_DEFAULT };
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private windows can refuse storage; the choice still applies for this visit.
  }
}

const painted = new Set();

/** Paint the page. Safe to call often. */
export function applyTheme(choice = readThemeChoice(), custom = readCustomColors()) {
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const { base, colors } = resolveTheme(choice, custom, systemDark);
  const root = document.documentElement;
  root.classList.toggle("cds--g100", base === "dark");
  root.classList.toggle("cds--g10", base === "light");
  root.dataset.theme = base;
  root.style.colorScheme = base;
  for (const name of painted) root.style.removeProperty(name);
  painted.clear();
  if (colors) {
    for (const [name, value] of Object.entries(deriveTokens(colors))) {
      root.style.setProperty(name, value);
      painted.add(name);
    }
    root.dataset.themeCustom = "true";
  } else {
    delete root.dataset.themeCustom;
  }
}

export function saveThemeChoice(choice, custom) {
  remember(CHOICE_KEY, choice);
  if (custom) remember(CUSTOM_KEY, JSON.stringify(custom));
  applyTheme(choice, custom || readCustomColors());
}
