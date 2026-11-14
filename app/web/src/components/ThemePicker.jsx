import { useState } from "react";
import { Button, InlineNotification, RadioTile, TextInput, TileGroup } from "@carbon/react";
import { COLOR_FIELDS, PRESETS, findProblems, normalizeHex, readCustomColors, readThemeChoice, saveThemeChoice } from "../theme.js";

const BASIC = [
  { id: "system", label: "Match the system", hint: "Light or dark, like your device" },
  { id: "light", label: "Light", hint: "Carbon light" },
  { id: "dark", label: "Dark", hint: "Carbon dark" },
];

function Swatches({ colors }) {
  return (
    <span className="dt-swatches" aria-hidden="true">
      {["background", "header", "primary", "secondary", "link"].map((key) => (
        <span key={key} className="dt-swatch" style={{ background: colors[key] }} />
      ))}
    </span>
  );
}

function ColorRow({ field, value, onChange }) {
  // Keep what's typed until it is a valid color, so a half-typed hex doesn't snap back.
  const [draft, setDraft] = useState(value);
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setDraft(value);
  }
  const typed = normalizeHex(draft);
  const invalid = !typed;
  return (
    <div className="dt-color-row">
      <input
        type="color"
        className="dt-color-input"
        aria-label={`${field.label} color`}
        value={value}
        onChange={(event) => onChange(field.key, event.target.value)}
      />
      <TextInput
        id={`color-${field.key}`}
        labelText={field.label}
        helperText={field.help}
        value={draft}
        invalid={invalid}
        invalidText="Use a hex color such as #0f62fe."
        maxLength={7}
        onChange={(event) => {
          setDraft(event.target.value);
          const hex = normalizeHex(event.target.value);
          if (hex) onChange(field.key, hex);
        }}
      />
    </div>
  );
}

/** Theme choice for Settings: Carbon's own themes, named presets, and a six color custom theme. */
export default function ThemePicker() {
  const [choice, setChoice] = useState(readThemeChoice());
  const [custom, setCustom] = useState(readCustomColors());
  const preset = PRESETS.find((p) => p.id === choice);

  function choose(next) {
    setChoice(next);
    saveThemeChoice(next, custom);
  }

  function editColor(key, value) {
    const next = { ...custom, [key]: value };
    setCustom(next);
    setChoice("custom");
    saveThemeChoice("custom", next);
  }

  function customize() {
    setCustom({ ...preset.colors });
    setChoice("custom");
    saveThemeChoice("custom", { ...preset.colors });
  }

  const problems = choice === "custom" ? findProblems(custom) : [];

  return (
    <div className="dt-stack">
      <TileGroup legend="Color theme" name="theme" valueSelected={choice} onChange={choose} className="dt-theme-tiles">
        {BASIC.map((item) => (
          <RadioTile key={item.id} id={`theme-${item.id}`} value={item.id}>
            <span className="dt-theme-name">{item.label}</span>
            <span className="dt-label">{item.hint}</span>
          </RadioTile>
        ))}
        {PRESETS.map((item) => (
          <RadioTile key={item.id} id={`theme-${item.id}`} value={item.id}>
            <span className="dt-theme-name">{item.label}</span>
            <Swatches colors={item.colors} />
          </RadioTile>
        ))}
        <RadioTile id="theme-custom" value="custom">
          <span className="dt-theme-name">Custom</span>
          <Swatches colors={custom} />
        </RadioTile>
      </TileGroup>

      {preset ? (
        <div>
          <Button kind="tertiary" size="md" onClick={customize}>
            Customize {preset.label}
          </Button>
        </div>
      ) : null}

      {choice === "custom" ? (
        <fieldset className="dt-fieldset">
          <legend className="dt-heading-compact">Your colors</legend>
          <p className="dt-muted">Changes apply as you go and stay in this browser. Text, button labels and shades are worked out from these six.</p>
          <div className="dt-colors">
            {COLOR_FIELDS.map((field) => (
              <ColorRow key={field.key} field={field} value={custom[field.key]} onChange={editColor} />
            ))}
          </div>
          {problems.map((problem) => (
            <InlineNotification key={problem.message} kind="warning" lowContrast hideCloseButton title="Hard to read" subtitle={problem.message} />
          ))}
        </fieldset>
      ) : null}
    </div>
  );
}
