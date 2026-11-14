# 0001: Local first, sign-in everywhere, hosting only when secured

- Status: accepted
- Date: 2026-11-14

## Context

Detour 0.2 was built for one computer. The portal on port 3787 had no sign-in. Its only
guard was the published address, `127.0.0.1`, plus Host and Origin checks. Anything that
reached the port could create access keys, export every route and import data.
`DETOUR_MCP_AUTH_MODE` turned authentication off for any value except `key`, so a typo left
MCP open, and the MCP server didn't check Host or Origin. The checks also hard-coded
`127.0.0.1`, so hosting Detour on a real domain meant patching the code.

## Options

**a) Local only, no portal sign-in.** Least code, but one open port or port forward
exposes everything.

**b) Local first, sign-in always, hosted mode that refuses to start unless it is
secured.** One token for one user. The same checks run locally and hosted.

**c) An identity provider such as OIDC for hosted use.** Gives accounts and
multi-factor sign-in, but adds an external account and a multi-user data model, which
the Community Edition avoids.

## Decision

Option b. The portal always requires a sign-in token. MCP always requires an access key.
Wrong settings stop startup. Hosted mode needs `DETOUR_PUBLIC_URL` (https), a token you
set, and key-authenticated MCP.

## Consequences

- Upgrading asks existing users to sign in once. Documented in the changelog.
- One user per install. Several people means several installs, or option c in a future
  edition.
- Sessions are signed cookies with no server store, so restarts keep people signed in.
  Rotating the token signs everyone out.
- One SQLite volume means one instance. The hosting guide steers away from platforms that
  scale to zero or use network disks.
- Detour can't verify that a proxy really terminates TLS. The hosting guide has a checklist.
