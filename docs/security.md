# Security

Detour is **local first**. It is built to run on one computer, for one person, in
one Docker container. You can host it somewhere else if you secure it, and this
page says what that takes. Read the [disclaimer](../DISCLAIMER.md) too: you are
responsible for how you run Detour.

## Intended use

| Use | Status | What it means |
|---|---|---|
| **On your own computer** | Recommended | One user. Ports published on `127.0.0.1` only. Local AI tools connect with their own access key. |
| **On a remote machine you reach privately** | Supported | An SSH or SSM tunnel, or a Tailscale network, carries `127.0.0.1` to you. Nothing is open to the internet. |
| **Hosted at a public HTTPS address** | Supported if secured | You set `DETOUR_PUBLIC_URL` and a sign-in token. Detour refuses to start otherwise. See [cloud-hosting.md](cloud-hosting.md). |
| **Shared by several people** | Not supported | One token means one user. Everyone with it has everything. |
| **Open to the internet without sign-in or HTTPS** | Not possible | Detour refuses a non-HTTPS public address. |
| **Holding passwords, API keys or other secrets** | Don't | Detour stores context for AI tools to read. Keep secrets out of it. |

## How Detour is protected

Each layer works on its own, so one mistake doesn't expose everything.

| Layer | On your computer | Hosted |
|---|---|---|
| **Network reach** | Compose publishes both ports on `127.0.0.1` only. | You choose. Put a reverse proxy or tunnel in front, publish only 443, and keep 3787 and 3788 off the public network. |
| **Portal sign-in** | Always required. Detour makes a token on first start. | Always required. You set `DETOUR_PORTAL_TOKEN`. |
| **Session cookie** | `HttpOnly`, `SameSite=Strict`, signed, 24 hours by default. | Also `Secure` and `__Host-` prefixed, plus HSTS. |
| **Wrong guesses** | Five wrong tokens in a minute lock sign-in for a minute. | Same. |
| **Host header** | Only `127.0.0.1`, `localhost`, `[::1]` and the service name, each on the right port. This stops DNS-rebinding. | Your public name only, plus loopback for health checks. |
| **Cross-site requests** | Writes must be JSON from the same origin and not marked cross-site. | Same, against your `https://` origin. |
| **MCP access keys** | Required on every request. One revocable key per AI tool. Keys can be read-only. Only a hash is stored. | Same. `DETOUR_MCP_AUTH_MODE=none` is refused. |
| **MCP Host and Origin** | Checked against the same allowlist as the portal. | Same. |
| **Agent changes** | Agents never write directly. Each change is a proposal you review. Every change is logged under **Activity** and can be undone unless something changed it again. | Same. |
| **Container** | Runs as a non-root user, read-only filesystem, all capabilities dropped, `no-new-privileges`, temporary files in memory. | Same. Keep these settings on your host. |
| **Browser** | A strict Content Security Policy, no framing, no sniffing, no referrer. | Same. |
| **Supply chain** | Base images pinned by digest, dependencies pinned by hash, signed release images, an SBOM and an OpenSSF Scorecard check. | Verify the image signature before you deploy. |

Settings that don't make sense stop Detour from starting, with a message that says
which setting is wrong. Nothing falls back to something weaker. That includes an
unknown `DETOUR_MCP_AUTH_MODE`, a short `DETOUR_PORTAL_TOKEN`, a `DETOUR_PUBLIC_URL`
that isn't `https://`, and hosted mode without a token.

## The sign-in token

- **Locally**, Detour makes a random token on first start and saves it as
  `/data/portal-token` in the data volume, readable by the app only. To see it:

  ```bash
  docker compose exec detour cat /data/portal-token
  ```

- **Hosted**, you set `DETOUR_PORTAL_TOKEN` yourself. Use at least 32 random
  characters, such as the output of `openssl rand -hex 32`, and keep it in your
  host's secret store, not in a file in the repository.
- **To change it**, set a new `DETOUR_PORTAL_TOKEN`, or delete `/data/portal-token`
  and restart. Everyone is signed out. Do this if you think it leaked.
- Signing out ends that session at once. A restart keeps other sessions signed in.

## Access keys

Make one key per AI tool under **Agent connections**, and untick **Can propose
changes** for tools that should only read. A key is shown once. Revoke any key you
no longer use or might have shared. Revoking takes effect on the next request.
Review the list now and then: **last used** shows keys nobody uses.

Treat a key like a password. Anyone who has one can read every route you haven't
paused and can propose changes, which you'll still review.

## What Detour does not protect against

- **Someone who has your token or a key.** There are no accounts, no second factor
  and no per-person permissions.
- **Malware, or other people, on the machine that runs it.** Anyone who can run
  Docker commands can read the data volume, including the token and the database.
- **Prompt injection.** AI tools read your routes as text. A route item that came from
  somewhere you don't trust can try to steer the tool that reads it. Review
  proposals before approving them, and don't paste content you don't trust into a
  route.
- **What your AI tools do with context.** Detour hands context to the tool that asks.
  That tool's own provider terms apply from there.
- **A bad proxy or firewall.** Detour can't see whether your proxy really uses
  HTTPS, or whether your firewall is open. The hosting guide lists what to check.
- **Lost backups.** Backups contain your context and the sign-in token. Store them
  as carefully as the install itself.

## Keeping it healthy

- Run a tagged release, and upgrade when a new one is published. Subscribe to
  releases and security advisories in the GitHub repository.
- Back up the data volume before upgrading. The README has the commands.
- Every few months, review keys, revoke the unused ones and rotate the token on a
  hosted install.
- Check the image signature before you deploy it. The command is in each release's
  notes.

## If something goes wrong

1. Rotate the sign-in token and restart. That signs everyone out.
2. Revoke every access key under **Agent connections**, then make new ones.
3. Look at **Activity** for changes you didn't make. Undo them.
4. If the host may be compromised, stop the container, keep the volume for
   evidence, and restore a clean backup on a fresh host.
5. If you think the problem is in Detour itself, report it privately. See
   [SECURITY.md](../SECURITY.md).
