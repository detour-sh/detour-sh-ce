import { useState } from "react";
import { Button, InlineNotification, PasswordInput, TextInput } from "@carbon/react";
import { api } from "../api.js";

// Hosted mode only. First visit: claim the portal with the setup code from the server log.
// After that: sign in with the password.
export default function SignIn({ status, onDone }) {
  const setup = status.setup_required;
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (setup) {
        await api.setup({ setup_code: code, display_name: name, password, password_confirm: confirm });
      } else {
        await api.signIn({ password });
      }
      setPassword("");
      setConfirm("");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="dt-auth" id="main-content">
      <form className="dt-tile dt-auth-card" onSubmit={submit} aria-labelledby="auth-heading">
        <h1 id="auth-heading">{setup ? "Set up Detour" : "Sign in to Detour"}</h1>
        {setup ? (
          <p className="dt-muted">
            Enter the one-time setup code from Detour's log, then choose a name and a password. To get a new code, run{" "}
            <code>docker compose exec detour python /app/server.py setup-code</code> on the server.
          </p>
        ) : null}
        {error ? <InlineNotification kind="error" lowContrast hideCloseButton title="Couldn't continue" subtitle={error.message} /> : null}
        {setup ? (
          <>
            <TextInput id="auth-code" labelText="Setup code" autoComplete="off" autoCapitalize="characters" spellCheck={false} required value={code} onChange={(e) => setCode(e.target.value)} />
            <TextInput id="auth-name" labelText="Your name" autoComplete="name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
          </>
        ) : null}
        <PasswordInput
          id="auth-password"
          labelText={setup ? "Password (12 characters or more)" : "Password"}
          autoComplete={setup ? "new-password" : "current-password"}
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {setup ? (
          <PasswordInput id="auth-confirm" labelText="Confirm password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        ) : null}
        <Button type="submit" disabled={busy}>
          {setup ? "Create owner" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}
