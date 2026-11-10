# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/detour-sh/detour-sh-ce/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/detour-sh/detour-sh-ce/releases/tag/v0.1.0
