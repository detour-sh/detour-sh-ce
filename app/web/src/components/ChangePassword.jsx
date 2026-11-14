import { useState } from "react";
import { Button, InlineNotification, PasswordInput } from "@carbon/react";
import { api } from "../api.js";
import { ErrorNote } from "./common.jsx";

// Hosted mode only. Changing the password signs every other browser out.
export default function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError(null);
    setDone(false);
    setBusy(true);
    try {
      await api.changePassword({ current_password: current, new_password: next, new_password_confirm: confirm });
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="dt-tile" aria-labelledby="password-heading">
      <h2 id="password-heading">Password</h2>
      <p className="dt-muted">Changing your password signs out every other browser. This one stays signed in.</p>
      <form className="dt-auth-form" onSubmit={submit} aria-label="Change password">
        <PasswordInput id="pw-current" labelText="Current password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <PasswordInput id="pw-new" labelText="New password (12 characters or more)" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        <PasswordInput id="pw-confirm" labelText="Confirm new password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <div className="dt-actions">
          <Button type="submit" kind="tertiary" disabled={busy}>
            Change password
          </Button>
        </div>
      </form>
      {done ? <InlineNotification kind="success" lowContrast title="Password changed" subtitle="Other browsers have been signed out." onCloseButtonClick={() => setDone(false)} /> : null}
      <ErrorNote error={error} title="Couldn't change the password" onClose={() => setError(null)} />
    </section>
  );
}
