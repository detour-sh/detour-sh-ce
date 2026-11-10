<p align="center">
  <img src=".github/assets/logo.svg" width="96" height="96" alt="Detour logo">
</p>

<h1 align="center">Detour Community Edition</h1>

<p align="center">When the context is not in the prompt, take a Detour.</p>

<p align="center">
  <a href="https://github.com/detour-sh/detour-sh-ce/actions/workflows/ci.yml"><img src="https://github.com/detour-sh/detour-sh-ce/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/detour-sh/detour-sh-ce"><img src="https://api.scorecard.dev/projects/github.com/detour-sh/detour-sh-ce/badge" alt="OpenSSF Scorecard"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-176448" alt="License: Apache-2.0"></a>
</p>

Detour is a local MCP server and web portal for your AI development tools. Save
rules, decisions, runbooks, prompts, skills, notes, and project knowledge once,
and your AI tools can read it back in any session. You don't have to paste it
again or depend on a harness's memory.

- Runs on your machine in one Docker container. No account, no cloud service.
- Your data stays in a local Docker volume until you choose to export it.
- AI tools get read-only access, and each tool uses its own revocable key.

## Requirements

- Docker with Docker Compose v2 (Docker Desktop on macOS or Windows).
- An MCP-capable AI tool, such as Claude Code, Codex, VS Code, Cursor, Windsurf,
  Zed, Gemini CLI, or JetBrains AI Assistant.

## Quick start

Use a tagged release. `main` can change between releases.

```bash
git clone --branch v0.1.1 https://github.com/detour-sh/detour-sh-ce.git
cd detour-sh-ce
docker compose up -d --build
```

Open <http://127.0.0.1:3787>.

### Use the prebuilt image instead

Each release publishes a signed image for amd64 and arm64. To run it instead
of building locally:

```bash
DETOUR_IMAGE=ghcr.io/detour-sh/detour-sh-ce:0.1.1 docker compose up -d --no-build
```

To check the signature first, run the `cosign verify` command from the
[release notes](https://github.com/detour-sh/detour-sh-ce/releases).

## Connect your AI tool

1. In the portal, open **Connections** and create a key for each AI tool you use.
2. Copy the configuration shown for your tool. The MCP endpoint is
   `http://127.0.0.1:3788/mcp`, and each request sends the key as
   `Authorization: Bearer <key>`.
3. Restart or reload the tool, then ask it to use Detour.

If a tool stops needing access, revoke its key under **Connections**.

Cloud-hosted agents can't reach `127.0.0.1`, so only tools running on the same
machine can connect.

## Organize context

A **Detour** is a named set of context:

- **Global Detour:** applies to all of your work.
- **Project Detour:** applies to one project.
- **Sub-detour:** optional, nested under a Project Detour.

When context overlaps, Global outranks Project, and Project outranks
Sub-detour.

## Configuration

Set these variables before running `docker compose up`:

| Variable | Default | Purpose |
|---|---|---|
| `DETOUR_USER_NAME` | `Local user` | Display name shown in the portal. |
| `DETOUR_ENABLE_NESTED_DETOURS` | `false` | Set to `true` to allow Sub-detours. |
| `DETOUR_IMAGE` | `detour-ce:local` | Image to run. Set to a release image to skip building. |

For example:

```bash
DETOUR_USER_NAME=Alex docker compose up -d --build
```

In PowerShell, set `$env:DETOUR_USER_NAME="Alex"` first, then run
`docker compose up -d --build`.

## Keep it local

The ports are bound to `127.0.0.1` on purpose. Don't expose ports 3787 or 3788
through a reverse proxy, tunnel, or port forward.

## Data, backup, and upgrades

Detour stores everything in the `detour-data` Docker volume.

To back up the volume to `detour-backup.tgz` in the current folder (macOS,
Linux, or PowerShell):

```bash
docker run --rm -v detour-sh-ce_detour-data:/data:ro -v "${PWD}:/backup" alpine tar czf /backup/detour-backup.tgz -C /data .
```

To restore it into an empty volume, stop Detour first, then run:

```bash
docker run --rm -v detour-sh-ce_detour-data:/data -v "${PWD}:/backup" alpine tar xzf /backup/detour-backup.tgz -C /data
```

The volume name starts with the folder name you cloned into. Check it with
`docker volume ls`.

To upgrade, check out the new release tag and rebuild:

```bash
git fetch --tags
git checkout v0.2.0
docker compose up -d --build
```

Replace `v0.2.0` with the release you want. See [CHANGELOG.md](CHANGELOG.md)
for what changed. When an upgrade changes the database, Detour first saves a
copy in the `backups` folder of the data volume and keeps the last five. If
you go back to an older release after an upgrade, Detour won't start on the
newer database. Restore the matching backup instead.

To stop Detour, run `docker compose down`. Your data stays in the volume. Only
`docker compose down -v` deletes it.

## Troubleshooting

- **Portal doesn't load:** run `docker compose ps` and check that the `detour`
  service is `healthy`. Use `docker compose logs detour` to see errors.
- **Port already in use:** another process is using 3787 or 3788. Stop it, or
  change the left-hand port numbers in `docker-compose.yml`.
- **AI tool can't connect:** confirm the key hasn't been revoked and that the
  tool sends the `Authorization` header. Some tools need a restart after config
  changes.

## Getting help

- Bugs and feature requests: [open an issue](https://github.com/detour-sh/detour-sh-ce/issues/new/choose).
- Security issues: please don't open a public issue. See [SECURITY.md](SECURITY.md).
- Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
