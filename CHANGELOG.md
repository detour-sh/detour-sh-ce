# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Route map**: a drag-and-drop canvas (new **Route map** page) where you draw
  the road an agent must travel for a route. Boxes are **stops** (rules to
  follow) and **gates** (requirements to verify with evidence). Lines are the
  **road** between them, or **detours**: labelled bypasses such as "mvp deploy"
  that let an agent skip stops on purpose.
- MCP tools `get_route_plan` and `report_route_progress`. The plan joins the
  roads of every layer that applies (Global, then Project, then Sub-detour). An
  agent reports each stop. A stop it leaves out is "not run", a stop after a
  failed one is "blocked", and every detour it takes is logged with its reason.
- Route runs appear on the Route map page, under **Runs**.
- Maps are included in exports and imports.

Route maps make skipped rules visible. They can't physically stop an agent
from ignoring a stop.

## [0.2.0] - 2026-11-14

Detour 0.2 rebuilds the portal around one idea: agents propose, you decide.

### Added

- Themes. Settings now offers Carbon light and dark, six ready-made themes
  (Ocean, Forest, Sunset, Aubergine, Midnight and High contrast), and a custom
  theme where you pick six colors: primary, secondary, text, links, background
  and header. Shades, borders and button labels are worked out from them, and
  Settings warns when a pair is hard to read. Your choice stays in your
  browser.

### Changed

- Readability: lead text is 16px, labels are 14px, long paragraphs stop at
  about 70 characters, Settings uses a narrower column, and tab content lines
  up with the page title.
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

[Unreleased]: https://github.com/detour-sh/detour-sh-ce/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/detour-sh/detour-sh-ce/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/detour-sh/detour-sh-ce/releases/tag/v0.1.0
