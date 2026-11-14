import { useState } from "react";
import { Button, Form, InlineNotification, PasswordInput, Stack } from "@carbon/react";
import { Login } from "@carbon/icons-react";
import { api } from "../api.js";
import { usePageTitle } from "../App.jsx";

const DISCLAIMER = "https://github.com/detour-sh/detour-sh-ce/blob/main/DISCLAIMER.md";

export default function SignIn({ cloud, onSignedIn }) {
  usePageTitle("Sign in");
  const [token, setToken] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.signIn(token.trim());
      setToken("");
      onSignedIn();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <main id="main-content" className="dt-signin" tabIndex={-1}>
      <Form className="dt-tile dt-signin-card" onSubmit={submit} aria-labelledby="signin-heading">
        <Stack gap={5}>
          <div>
            <h1 id="signin-heading">Sign in to Detour</h1>
            <p className="dt-muted">
              {cloud
                ? "Enter the sign-in token your administrator set in DETOUR_PORTAL_TOKEN."
                : "Enter the sign-in token Detour made for this install. It is saved in the data volume."}
            </p>
          </div>
          <PasswordInput
            id="signin-token"
            labelText="Sign-in token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
          {cloud ? null : (
            <p className="dt-muted">
              To show it, run <code className="dt-code">docker compose exec detour cat /data/portal-token</code>
            </p>
          )}
          {error ? <InlineNotification kind="error" lowContrast hideCloseButton title="Couldn't sign in" subtitle={error.message} /> : null}
          <Button type="submit" renderIcon={Login} disabled={busy || !token.trim()}>
            Sign in
          </Button>
          <p className="dt-muted dt-signin-note">
            By signing in you accept the{" "}
            <a href={DISCLAIMER} target="_blank" rel="noopener noreferrer">
              disclaimer and terms of use
            </a>
            . Detour is provided as is, and you are responsible for how you run it.
          </p>
        </Stack>
      </Form>
    </main>
  );
}
