import { useState } from "react";
import { Button, Select, SelectItem, TextInput } from "@carbon/react";
import { Add } from "@carbon/icons-react";
import { api } from "../api.js";
import { TARGET_HELP, TARGET_LABELS } from "../util.js";
import { ErrorNote, TargetTag } from "./common.jsx";

const PLACEHOLDERS = {
  repo: "github.com/owner/name",
  folder: "~/code/my-project",
  file: "docs/CHANGELOG.md",
  topic: "billing, invoices",
  artifact: "https://claude.ai/artifact/…",
  url: "https://example.com/spec",
};

/** Where a route applies, with add and remove. */
export default function TargetList({ route, onChanged }) {
  const [kind, setKind] = useState("folder");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged?.();
      return true;
    } catch (err) {
      setError(err);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(event) {
    event.preventDefault();
    if (await run(() => api.addTarget(route.id, { kind, value }))) setValue("");
  }

  return (
    <div className="dt-stack">
      {route.targets.length ? (
        <ul className="dt-rows" aria-label="Targets">
          {route.targets.map((target) => (
            <li key={target.id} className="dt-row">
              <span className="dt-row-kind">
                <TargetTag kind={target.kind} />
              </span>
              <code className="dt-code dt-row-main">{target.value}</code>
              <span className="dt-row-meta">{TARGET_HELP[target.kind]}</span>
              <Button size="sm" kind="ghost" disabled={busy} onClick={() => run(() => api.removeTarget(target.id))} aria-label={`Remove ${TARGET_LABELS[target.kind]} ${target.value}`}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="dt-muted">No targets yet. Agents only get this route when they ask for it by name.</p>
      )}
      <form className="dt-form-row" onSubmit={add} aria-label="Add a target">
        <Select id="target-kind" labelText="Applies to" value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(TARGET_LABELS).map(([key, label]) => (
            <SelectItem key={key} value={key} text={label} />
          ))}
        </Select>
        <TextInput id="target-value" labelText="Value" placeholder={PLACEHOLDERS[kind]} value={value} onChange={(e) => setValue(e.target.value)} maxLength={500} />
        <Button type="submit" kind="tertiary" size="md" renderIcon={Add} disabled={busy || !value.trim()}>
          Add target
        </Button>
      </form>
      <ErrorNote error={error} title="Couldn't save the target" onClose={() => setError(null)} />
    </div>
  );
}
