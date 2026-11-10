# Detour Community Edition

When the context is not in the prompt, take a Detour.

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

```bash
git clone https://github.com/detour-sh/detour-sh-ce.git
cd detour-sh-ce
docker compose up -d --build
```

Open <http://127.0.0.1:3787>.

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

To back up the volume:

```bash
docker run --rm -v detour-sh-ce_detour-data:/data -v "$PWD":/backup alpine tar czf /backup/detour-backup.tgz -C /data .
```

The volume name starts with the folder name you cloned into. Check it with
`docker volume ls`.

To upgrade:

```bash
git pull
docker compose up -d --build
```

Existing data is migrated automatically on startup. Take a backup first.

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
