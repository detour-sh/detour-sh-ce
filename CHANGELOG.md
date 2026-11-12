# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Detour 0.2 rebuilds the portal around one idea: agents propose, you decide.

### Changed

- New portal built on IBM's Carbon Design System (React), in light and dark
  themes. It replaces the server-rendered pages.
- "Detours" are now **routes**. A route applies wherever its targets match: a
  repo, a folder, a file, a topic, an artifact or a URL.
- Context items have one of six kinds: rule, decision, runbook, prompt, note or
  reference.
- New MCP tools: `find_routes`, `get_context`, `search_context`,
  `propose_change`, `get_proposal` and `list_my_proposals`. They replace the
  four 0.1 tools.
- Upgrading migrates 0.1 data automatically, after the usual backup. Artifacts
  attached to a Detour become items, repositories and local paths become
  targets (their notes become a reference item), and environment flags become
  rules. Unattached artifacts move to a route named "Imported from Detour 0.1",
  with archived ones kept as retired items. Keys from 0.1 stay read-only until
  you allow proposals for them.
- New logo and favicons.
- Updated the MCP library to 2.3.0.

### Removed

- Bundles, tag sets and environment flags. If you rely on bundle exports,
  export them from 0.1 before upgrading.

### Added

- **Checks**: a new item kind for requirements an agent verifies and reports as
  pass or fail. New MCP tools `get_checks` and `report_check_results`, a
  **Check runs** page, and a **Checks** tab on each route where you can include
  other routes' checks or skip a check (optionally running another instead).
  Upgrading rebuilds the items table to allow the new kind, after the usual
  backup.
- **Review**: approve, edit or reject what agents propose, with their reason
  and a before-and-after view.
- A review policy and a pause switch for each route.
- History for every change, with undo, on each route and under **Activity**.
- Per-key permission to propose changes.
- JSON export and import of all routes.
- Releases include the image SBOM and a checksums file, each with a Sigstore
  signature.

## [0.1.1] - 2026-11-10

### Changed

- New logo and favicons.
- Updated the MCP library to 2.3.0.

### Added

- Releases include the image SBOM and a checksums file, each with a Sigstore
  signature.

## [0.1.0] - 2026-11-10

### Added

- Local web portal and MCP server in one Docker container.
- Global, Project, and optional nested Detours for organizing context.
- Notes, docs, skills, prompts, decisions, runbooks, links, repositories, and
  file uploads.
- Read-only MCP access with a separate revocable key for each AI tool.
- Markdown and JSON bundle exports.
- Automatic database backup before schema migrations, kept in the data volume.
- Signed multi-architecture images (amd64 and arm64) on GitHub Container
  Registry.

[Unreleased]: https://github.com/detour-sh/detour-sh-ce/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/detour-sh/detour-sh-ce/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/detour-sh/detour-sh-ce/releases/tag/v0.1.0
