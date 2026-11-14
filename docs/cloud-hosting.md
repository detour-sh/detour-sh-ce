# Hosting Detour in the cloud

Detour is **local first**. Most people should run it on their own computer and stop
there. This guide is for the cases where that isn't enough: you want one Detour that
several of your own devices can reach, or an AI tool that runs in the cloud needs to
read it.

Hosting it means you operate it. Read the [security overview](security.md) and the
[disclaimer](../DISCLAIMER.md) first. This guide lists safe options and what to check.
It doesn't promise that any host is secure. Provider features and prices change, so
confirm details in the provider's current documentation.

## Pick a path

| | **A. Private tunnel** | **B. Private HTTPS name** | **C. Public HTTPS** |
|---|---|---|---|
| What it is | A small remote machine. You reach `127.0.0.1` on it through an SSH, SSM or Tailscale tunnel. | The same machine, with an HTTPS name that only your devices or your sign-in provider can reach. | An HTTPS name anyone can reach, protected by Detour's own sign-in. |
| Open to the internet | No | No, or only behind your identity provider | Yes |
| Cloud-hosted AI agents can connect | No | Only if you let them through | Yes |
| `DETOUR_PUBLIC_URL` | Leave empty | Set | Set |
| Upkeep | Lowest | Low | Highest: patching, monitoring, rotating |
| Recommendation | **Start here.** | If you want it on a phone or a second computer. | Only if something in the cloud must call your MCP endpoint. |

**Path A needs no changes to Detour.** Run it with the normal `docker compose up -d` on
the remote machine. Its ports are published on that machine's loopback address only, and
the tunnel carries them to your computer, so the portal and your AI tools see
`http://127.0.0.1:3787` and `http://127.0.0.1:3788/mcp` as usual.

## What every path needs

These are true on any host.

- **One instance.** Detour keeps its data in SQLite on one volume. Don't run two
  copies against the same data, and don't scale it out.
- **A persistent disk** mounted at `/data`. If the disk is lost, the data is lost.
- **A local disk, not a network share.** SQLite is unreliable on NFS, SMB and some
  shared file systems. Use block storage attached to one machine.
- **Two ports inside the container**: 3787 (portal) and 3788 (MCP at `/mcp`). Platforms
  that publish only one port need a small proxy that sends `/mcp` to 3788 and the rest
  to 3787.
- **A health check** on `GET /health` over port 3787. It works on any host name.
- **About 256 MB of memory** is enough for one person.
- **Secrets from a secret store**, not from files in your repository.

## What Detour checks, and what you must do

| Detour refuses to start unless | You must also |
|---|---|
| `DETOUR_PUBLIC_URL` is a plain `https://` origin | Terminate TLS in front of Detour with a valid certificate |
| `DETOUR_PORTAL_TOKEN` is set, 24+ characters | Use 32+ random characters, stored in a secret store |
| MCP access keys are on | Make one key per AI tool, and revoke the ones you drop |
| The request's host name matches `DETOUR_PUBLIC_URL` | Pass the original `Host` header through your proxy |

You must also keep ports 3787 and 3788 off the public network, so only your proxy
reaches them.

## Path C setup on one VPS

This example works on any Linux host that runs Docker: AWS EC2 or Lightsail, a
DigitalOcean Droplet, Hetzner, and so on.

1. **Make a small instance** with a persistent disk, and point a DNS name at it.
2. **Firewall:** allow inbound 443 and 80 (for certificates). Allow 22 only from your
   address, or use the provider's session tool instead and leave 22 closed. Block
   everything else, including 3787 and 3788.
3. **Install Docker and Caddy** from their official instructions.
4. **Get Detour** from a tagged release, then create `.env` next to
   `docker-compose.yml` and restrict it with `chmod 600 .env`:

   ```bash
   DETOUR_PUBLIC_URL=https://detour.example.com
   DETOUR_PORTAL_TOKEN=<output of: openssl rand -hex 32>
   ```

5. **Start it.** Compose still publishes the ports on `127.0.0.1` only, which is what
   you want behind a proxy on the same machine:

   ```bash
   docker compose up -d --build
   ```

6. **Point Caddy at it.** In `/etc/caddy/Caddyfile`, then `systemctl reload caddy`:

   ```text
   detour.example.com {
       handle /mcp* {
           reverse_proxy 127.0.0.1:3788
       }
       handle {
           reverse_proxy 127.0.0.1:3787
       }
   }
   ```

   Caddy gets a certificate on its own and keeps the original `Host` header.
7. **Check it.** Open the name in a browser and sign in with the token. From another
   machine, confirm that `curl http://<server-ip>:3787` and `:3788` time out, and that
   `curl -i https://detour.example.com/api/routes` returns `401`.
8. **Connect an AI tool** with a key from **Agent connections**. The endpoint shown is
   `https://detour.example.com/mcp`.

## Platform notes

**Fit** says how well each service suits one SQLite database on one disk.

| Platform | Fit | Notes |
|---|---|---|
| **AWS EC2 or Lightsail** | Good | Cheapest and simplest on AWS. Use the smallest instance with an EBS or attached disk. For Path A, use **SSM Session Manager port forwarding** and keep inbound closed. For Path C, use a security group that allows only 80 and 443, and keep the token in Secrets Manager or SSM Parameter Store. Check the current free tier. |
| **AWS ECS on EC2 with an EBS volume** | OK | Works with one task and an attached volume. More setup than a single instance for no gain here. |
| **AWS Fargate, App Runner, Lambda** | Poor | No local persistent disk, or only EFS, which is a network share. Don't use. |
| **DigitalOcean Droplet** | Good | Add a Cloud Firewall that allows 80 and 443 only, or use an SSH tunnel for Path A. Turn on Droplet backups or take snapshots. |
| **DigitalOcean App Platform** | Poor | The container's disk is not persistent. Don't use. |
| **Fly.io** | OK | Use one machine with one volume, and keep secrets in `fly secrets`. Two services on one name need path routing, which Fly's proxy doesn't do, so put a small proxy in front or give the MCP endpoint its own name. Check Fly's current docs. |
| **Render, Railway and similar** | OK, with care | Need a paid persistent disk or volume, one instance, and a way to route `/mcp` to port 3788 because they publish one port. Check each provider's current docs. |
| **Google Cloud Run, Azure Container Apps** | Poor | Scale to zero and use an ephemeral disk or a network share. Use a VM on the same cloud instead. |
| **Hetzner, Linode, OVH and other VPS** | Good | Same shape as EC2: one VM, local disk, firewall, Caddy. |
| **Kubernetes** | OK | A single-replica StatefulSet with one persistent volume claim, a NetworkPolicy, and an Ingress that routes `/mcp`. Heavy for one user. |
| **Tailscale** | Good (Path B) | Run Detour on a machine in your tailnet, then use Tailscale's HTTPS serve feature to publish it at a tailnet name. Set `DETOUR_PUBLIC_URL` to that name. Only your devices can reach it. |
| **Cloudflare Tunnel with Cloudflare Access** | Good (Path B) | The tunnel makes an outbound connection, so no inbound ports are open. Access puts your single sign-on in front of the portal. Send `/mcp` to port 3788 with a path rule, and exempt that path from Access, because MCP clients can't complete a browser sign-in. It is protected by access keys instead. |

## Hardening checklist

Before you rely on a hosted install, check each item.

- [ ] Only 80 and 443 (or nothing) are open to the internet. 3787 and 3788 are not.
- [ ] `curl -i https://<name>/api/routes` returns `401`, and the plain `http://` address
      redirects to HTTPS or refuses.
- [ ] The token is 32+ random characters and lives in a secret store.
- [ ] `DETOUR_MCP_AUTH_MODE` is unset. It must be `key`.
- [ ] The container runs from a tagged, signed release image, not a local build of `main`.
- [ ] The container keeps the settings in `docker-compose.yml`: non-root, read-only,
      capabilities dropped, `no-new-privileges`.
- [ ] The operating system gets automatic security updates.
- [ ] Backups of `/data` run on a schedule, go to separate storage, and you've tested a
      restore. Backups include the token.
- [ ] SSH uses keys only, with password logins turned off, or is closed in favor of a
      session tool.
- [ ] Each AI tool has its own key, and you've revoked any you no longer use.
- [ ] Provider logs are on, and a billing or uptime alert tells you when it breaks.
- [ ] You've read the [disclaimer](../DISCLAIMER.md) and are happy to carry the
      responsibility.

## Backups and upgrades

- **Back up** with the volume commands in the README, or take a disk snapshot. Do it
  before every upgrade.
- **Upgrade** by pulling the new release tag and rebuilding, or by changing the image
  tag. Detour saves a copy of the database before it migrates it.
- **Restore** into an empty volume with the container stopped.

## What not to do

- Don't publish ports 3787 or 3788 on `0.0.0.0` or open them in a firewall.
- Don't run it behind a proxy that rewrites or drops the `Host` header.
- Don't put the token in the repository, a container image, a shared chat or a ticket.
- Don't run two instances against one database, or put the database on a network share.
- Don't leave `DETOUR_MCP_AUTH_MODE=none` in place. A hosted install refuses it anyway.
- Don't share one install between people. Run one each.
