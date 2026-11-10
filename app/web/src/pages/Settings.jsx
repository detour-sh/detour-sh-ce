import { useRef, useState } from "react";
import { Button, InlineNotification, RadioButton, RadioButtonGroup } from "@carbon/react";
import { Download, Upload } from "@carbon/icons-react";
import { api } from "../api.js";
import { useSession, usePageTitle } from "../App.jsx";
import { ErrorNote } from "../components/common.jsx";
import { readThemeChoice, saveThemeChoice } from "../util.js";

export default function SettingsPage() {
  usePageTitle("Settings");
  const { session, refreshSession } = useSession();
  const [theme, setTheme] = useState(readThemeChoice());
  const [imported, setImported] = useState(null);
  const [error, setError] = useState(null);
  const fileInput = useRef(null);

  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(null);
    setImported(null);
    try {
      const data = JSON.parse(await file.text());
      setImported(await api.importAll(data));
      refreshSession();
    } catch (err) {
      setError(err instanceof SyntaxError ? new Error("That file isn't valid JSON.") : err);
    }
  }

  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Settings</h1>
          <p>{session ? `Signed in locally as ${session.user_name}.` : ""}</p>
        </div>
      </div>
      <div className="dt-stack">
        <section className="dt-tile" aria-labelledby="appearance-heading">
          <h2 id="appearance-heading">Appearance</h2>
          <RadioButtonGroup
            legendText="Color theme"
            name="theme"
            valueSelected={theme}
            onChange={(value) => {
              setTheme(value);
              saveThemeChoice(value);
            }}
          >
            <RadioButton id="theme-system" value="system" labelText="Match the system" />
            <RadioButton id="theme-light" value="light" labelText="Light" />
            <RadioButton id="theme-dark" value="dark" labelText="Dark" />
          </RadioButtonGroup>
        </section>

        <section className="dt-tile" aria-labelledby="data-heading">
          <h2 id="data-heading">Your data</h2>
          <p className="dt-muted">
            Export every route, item and target as JSON. Importing adds the routes in a file as new routes and never changes existing ones. Access keys, proposals and history
            are not exported.
          </p>
          <div className="dt-actions">
            <Button kind="tertiary" renderIcon={Download} href="/api/export.json" download="detour-export.json">
              Export JSON
            </Button>
            <Button kind="tertiary" renderIcon={Upload} onClick={() => fileInput.current?.click()}>
              Import JSON
            </Button>
            <input ref={fileInput} type="file" accept="application/json,.json" className="dt-sr-only" tabIndex={-1} aria-hidden="true" onChange={importFile} />
          </div>
          {imported ? (
            <InlineNotification
              kind="success"
              lowContrast
              title="Imported"
              subtitle={`${imported.routes} routes, ${imported.items} items and ${imported.targets} targets.`}
              onCloseButtonClick={() => setImported(null)}
            />
          ) : null}
          <ErrorNote error={error} title="Couldn't import" onClose={() => setError(null)} />
        </section>

        {session ? (
          <section className="dt-tile" aria-labelledby="about-heading">
            <h2 id="about-heading">About this install</h2>
            <dl className="dt-dl">
              <div>
                <dt>Version</dt>
                <dd>{session.version}</dd>
              </div>
              <div>
                <dt>MCP endpoint</dt>
                <dd className="dt-code">{session.mcp_endpoint}</dd>
              </div>
              <div>
                <dt>Database</dt>
                <dd className="dt-code">{session.data_path}</dd>
              </div>
              <div>
                <dt>Sub-detours</dt>
                <dd>{session.nested_enabled ? "On" : "Off (DETOUR_ENABLE_NESTED_DETOURS)"}</dd>
              </div>
            </dl>
          </section>
        ) : null}
      </div>
    </div>
  );
}
