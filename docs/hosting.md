# Hosting Detour online

Detour is built to run on your own machine. This guide covers the one supported
way to run it on a server you can reach over the internet: **behind Cloudflare
Access, through a Cloudflare Tunnel.**

> Status: first implementation. The code paths are covered by unit tests, but the
> full hosted flow has not yet been run against a real Cloudflare account. Follow
> [Verify the deployment](#verify-the-deployment) before you trust it with data.
> The full spec and open work are in [hosting-requirements.md](hosting-requirements.md).

## Why not just publish the ports?

The portal has **no sign-in of its own**. Anyone who can reach it can read and change
everything, create agent keys, and export all your data. In local mode that is safe
because only your machine can reach `127.0.0.1`. Never publish ports 3787 or 3788
directly, and never put them behind a reverse proxy that doesn't authenticate users.

Hosted mode fixes this by refusing every request that doesn't carry a valid
Cloudflare Access token, which Detour checks itself (signature, issuer, audience,
expiry). A request that bypasses the proxy gets a 401.

## How it fits together

```
 You (browser) ──► detour.example.com ──┐
                    Cloudflare Access   │   Cloudflare Tunnel (outbound only,
 Agent (MCP) ───► detour-mcp.example.com┤   no inbound ports on your server)
   + service token   Cloudflare Access  │
                                        ▼
                          cloudflared ──► detour:3787  portal  (checks Access JWT)
                                      └─► detour:3788  MCP     (checks Access JWT + Detour key)
```

Two hostnames, two Access applications, two audiences:

| | Portal | MCP endpoint |
|---|---|---|
| Hostname | `detour.example.com` | `detour-mcp.example.com` |
| Who gets in | People you list (email) | Agents holding a Cloudflare service token |
| Second lock | none | Detour access key (`Authorization: Bearer dtr_...`) |
| Origin port | 3787 | 3788 |

An agent therefore needs **both** a Cloudflare service token and a Detour key.
Stealing one is not enough.

## What you need

- A Cloudflare account with Zero Trust enabled (the free plan covers up to 50 users).
- A domain on Cloudflare.
- A host that runs Docker Compose v2.24 or newer: a small VPS, a home server, or a Raspberry Pi. It only needs outbound internet.

## Credentials

Create these as **GitHub Actions secrets** on the repository that runs the setup
workflow. Use exactly these names.

| Secret name | What it is | Where to get it |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | API token with **Access: Apps and Policies: Edit** on the account | Cloudflare dashboard, My Profile, API Tokens |
| `CLOUDFLARE_ACCOUNT_ID` | Your account ID | Cloudflare dashboard, any domain's Overview page, right-hand column |

On the server (not in GitHub), put these in a `.env` file next to the compose files. Never commit it.

| Variable | Example | Meaning |
|---|---|---|
| `DETOUR_PUBLIC_URL` | `https://detour.example.com` | Portal address. Must be https and a bare origin. |
| `DETOUR_MCP_PUBLIC_URL` | `https://detour-mcp.example.com` | MCP address. Must use a different hostname. |
| `DETOUR_ACCESS_TEAM_DOMAIN` | `myteam.cloudflareaccess.com` | Your Zero Trust team domain. |
| `DETOUR_ACCESS_AUD` | `4f1a...` | Audience tag of the **portal** Access application. |
| `DETOUR_MCP_ACCESS_AUD` | `9c2b...` | Audience tag of the **MCP** Access application. |
| `DETOUR_ACCESS_ALLOWED_EMAILS` | `me@example.com,@corp.example` | Optional second check inside Detour. Exact emails or `@domain`. |
| `DETOUR_KEY_TTL_DAYS` | `90` | Default lifetime of new agent keys. `0` means no expiry. The hosted compose file defaults to 90. |
| `TUNNEL_TOKEN` | `eyJh...` | Token of your Cloudflare Tunnel. A secret. |

## Setup

1. **Create the tunnel.** Zero Trust dashboard, Networks, Tunnels, Create. Copy the
   token into `TUNNEL_TOKEN`. Add two public hostnames:
   - `detour.example.com` → service `http://detour:3787`
   - `detour-mcp.example.com` → service `http://detour:3788`
2. **Create the Access applications.** In GitHub, run the **manual-hosting** workflow
   (Actions tab) with action `configure-access`. Leave `dry_run` on the first time to
   see what it will send. It creates one self-hosted Access application per hostname,
   an email policy for the portal, and a service-token policy for MCP, and prints
   both audience tags in the job summary. Or create the same things by hand in the dashboard.
3. **Create a service token for each agent.** Zero Trust, Access, Service Auth,
   Create Service Token. The secret is shown once. Store it in that agent's config.
4. **Start Detour on the server:**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.hosted.yml up -d --build
   ```
   If a setting is missing or unsafe, Detour exits immediately and says which one.
   Check with `docker compose logs detour`.
5. **Create an agent key** in the portal under Agent connections. Set an expiry.

## Connect an agent

The agent sends the Cloudflare service token and the Detour key:

```bash
claude mcp add --transport http detour https://detour-mcp.example.com/mcp \
  --header "CF-Access-Client-Id: <service token id>" \
  --header "CF-Access-Client-Secret: <service token secret>" \
  --header "Authorization: Bearer <detour key>"
```

Cloud-hosted agents can now reach Detour. This also means your rules and runbooks
leave your machine, so only put in what you are happy to store on that host.

## Verify the deployment

Run these after setup. All of them should hold.

1. Open the portal URL signed out. Cloudflare asks you to sign in. Detour never sees an anonymous request.
2. `curl -i https://detour.example.com/api/routes` returns a Cloudflare login redirect or 403, not data.
3. From the server, `curl -i -H 'Host: detour.example.com' http://localhost:3787/api/routes` is not possible (the port isn't published). From inside the Docker network, the same request returns **401**.
4. `curl -i https://detour-mcp.example.com/mcp` without headers is refused. With only the service token it returns 401 from Detour. With both it answers.
5. Open Activity in the portal after making a change. The actor is your email.
6. A key created with an expiry shows `expires_at` in `GET /api/keys` and stops working after that date.

## Operating it

- **Backups.** Detour only backs up before a schema upgrade. Schedule the volume backup from the README (cron on the host) and copy it off the machine.
- **Updates.** Pull a new release tag, rebuild, restart. The tunnel image tag is pinned in `docker-compose.hosted.yml`; update it deliberately.
- **Revoking access.** Remove the person from the Access policy (instant for new requests; their session cookie lasts up to the application's session duration). Revoke agent keys in the portal. Delete a service token in Cloudflare to cut an agent off.
- **If something looks wrong.** Stop the tunnel (`docker compose stop tunnel`). Detour is unreachable from the internet within seconds.

## Known limits

Single user or a small trusted team sharing one data set. Everyone who passes the
Access policy sees and can change everything, and every agent key reads every route.
There is no per-user data, no per-key route scoping and no tenant separation yet.
See hosting-requirements.md, items H-7 and H-8.
