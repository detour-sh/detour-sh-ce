import { useState } from "react";
import {
  Button,
  Checkbox,
  CodeSnippet,
  InlineNotification,
  Modal,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Tag,
  TextInput,
  Toggle,
} from "@carbon/react";
import { Add } from "@carbon/icons-react";
import { api } from "../api.js";
import { useSession, usePageTitle } from "../App.jsx";
import { ErrorNote, Loading } from "../components/common.jsx";
import { relativeTime, useResource } from "../util.js";

const PLACEHOLDER = "<YOUR_DETOUR_KEY>";

function setups(endpoint, key) {
  const header = { Authorization: `Bearer ${key}` };
  return [
    { name: "Claude Code", note: "Run this in a terminal, then check it with /mcp.", code: `claude mcp add --transport http detour ${endpoint} --header "Authorization: Bearer ${key}"` },
    { name: "Claude Desktop", note: "Settings → Developer → Edit Config, add this, then restart Claude Desktop.", code: JSON.stringify({ mcpServers: { detour: { type: "http", url: endpoint, headers: header } } }, null, 2) },
    { name: "VS Code", note: "Run MCP: Add Server, or add this to your user MCP configuration.", code: JSON.stringify({ servers: { detour: { type: "http", url: endpoint, headers: header } } }, null, 2) },
    { name: "Cursor", note: "Add this to ~/.cursor/mcp.json, then restart Cursor.", code: JSON.stringify({ mcpServers: { detour: { url: endpoint, headers: header } } }, null, 2) },
    { name: "Codex", note: "Add a Streamable HTTP server with this endpoint and header.", code: JSON.stringify({ mcpServers: { detour: { type: "http", url: endpoint, headers: header } } }, null, 2) },
    { name: "Other", note: "Use Streamable HTTP with the endpoint and an Authorization header.", code: `Endpoint: ${endpoint}\nHeader: Authorization: Bearer ${key}` },
  ];
}

function SetupTabs({ endpoint, token }) {
  return (
    <Tabs>
      <TabList aria-label="Setup by tool" contained>
        {setups(endpoint, token).map((s) => (
          <Tab key={s.name}>{s.name}</Tab>
        ))}
      </TabList>
      <TabPanels>
        {setups(endpoint, token).map((s) => (
          <TabPanel key={s.name} className="dt-stack">
            <p className="dt-muted">{s.note}</p>
            <CodeSnippet type="multi" feedback="Copied" wrapText>
              {s.code}
            </CodeSnippet>
          </TabPanel>
        ))}
      </TabPanels>
    </Tabs>
  );
}

export default function Connections() {
  usePageTitle("Agent connections");
  const { session } = useSession();
  const keys = useResource(() => api.keys(), []);
  const [name, setName] = useState("");
  const [canPropose, setCanPropose] = useState(true);
  const [created, setCreated] = useState(null);
  const [revoking, setRevoking] = useState(null);
  const [error, setError] = useState(null);
  const endpoint = session?.mcp_endpoint || "http://127.0.0.1:3788/mcp";
  const authOff = session && session.mcp_auth !== "key";

  async function run(fn) {
    setError(null);
    try {
      await fn();
      keys.reload();
    } catch (err) {
      setError(err);
    }
  }

  async function create(event) {
    event.preventDefault();
    await run(async () => {
      const result = await api.createKey({ name, can_propose: canPropose });
      setCreated(result);
      setName("");
    });
  }

  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Agent connections</h1>
          <p>Give each AI tool its own key. Detour stores only a hash of it. Agents with a key can read your routes, and if you allow it, propose changes for you to review.</p>
        </div>
      </div>

      <div className="dt-stack">
        <section className="dt-tile" aria-labelledby="endpoint-heading">
          <h2 id="endpoint-heading">MCP endpoint</h2>
          <CodeSnippet type="single" feedback="Copied">
            {endpoint}
          </CodeSnippet>
          <p className="dt-muted">Only tools on this computer can connect. Cloud-hosted agents can't reach 127.0.0.1.</p>
        </section>

        {authOff ? (
          <InlineNotification
            kind="warning"
            lowContrast
            hideCloseButton
            title="MCP authentication is off"
            subtitle="Any program on this computer can read your routes and propose changes. Restart with DETOUR_MCP_AUTH_MODE=key to require keys."
          />
        ) : (
          <section className="dt-tile" aria-labelledby="keys-heading">
            <h2 id="keys-heading">Access keys</h2>
            <form className="dt-form-row" onSubmit={create} aria-label="Create an access key">
              <TextInput id="key-name" labelText="Key name" placeholder="Claude Code on this laptop" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
              <Checkbox id="key-propose" labelText="Can propose changes" checked={canPropose} onChange={(_, { checked }) => setCanPropose(checked)} />
              <Button type="submit" renderIcon={Add} disabled={!name.trim()}>
                Create key
              </Button>
            </form>
            <ErrorNote error={error} title="Couldn't save that" onClose={() => setError(null)} />
            {keys.loading && !keys.data ? <Loading /> : null}
            {keys.data?.access_keys.length ? (
              <ul className="dt-rows" aria-label="Access keys">
                {keys.data.access_keys.map((key) => (
                  <li key={key.id} className="dt-row">
                    <span className="dt-row-main">
                      <strong>{key.name}</strong>
                      <div className="dt-label">
                        Created {relativeTime(key.created_at).toLowerCase()} · last used {relativeTime(key.last_used_at).toLowerCase()}
                      </div>
                    </span>
                    {key.revoked_at ? (
                      <Tag size="sm" type="gray">
                        Revoked
                      </Tag>
                    ) : (
                      <>
                        <Toggle
                          id={`propose-${key.id}`}
                          size="sm"
                          labelText={`${key.name} can propose changes`}
                          hideLabel
                          labelA="Read only"
                          labelB="Can propose"
                          toggled={key.can_propose}
                          onToggle={(value) => run(() => api.updateKey(key.id, { can_propose: value }))}
                        />
                        <Button size="sm" kind="danger--ghost" onClick={() => setRevoking(key)}>
                          Revoke
                        </Button>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            ) : keys.data ? (
              <p className="dt-muted">No keys yet.</p>
            ) : null}
          </section>
        )}

        <section className="dt-tile" aria-labelledby="setup-heading">
          <h2 id="setup-heading">Set up a tool</h2>
          <p className="dt-muted">Replace {PLACEHOLDER} with a key from above. Each tool should have its own key.</p>
          <SetupTabs endpoint={endpoint} token={PLACEHOLDER} />
        </section>
      </div>

      {created ? (
        <Modal open passiveModal modalHeading="Copy this key now" modalLabel={created.access_key.name} onRequestClose={() => setCreated(null)} size="md">
          <div className="dt-stack">
            <p>It won't be shown again. If you lose it, revoke it and create another.</p>
            <CodeSnippet type="single" feedback="Copied">
              {created.token}
            </CodeSnippet>
            <SetupTabs endpoint={endpoint} token={created.token} />
          </div>
        </Modal>
      ) : null}
      {revoking ? (
        <Modal
          open
          danger
          size="sm"
          modalHeading={`Revoke “${revoking.name}”?`}
          primaryButtonText="Revoke key"
          secondaryButtonText="Cancel"
          onRequestClose={() => setRevoking(null)}
          onRequestSubmit={async () => {
            await run(() => api.revokeKey(revoking.id));
            setRevoking(null);
          }}
        >
          <p>The tool using this key loses access right away. Proposals it already made stay in Review.</p>
        </Modal>
      ) : null}
    </div>
  );
}
