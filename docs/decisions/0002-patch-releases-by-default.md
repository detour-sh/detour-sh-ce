# 0002: Patch releases by default before 1.0

- Status: accepted
- Date: 2026-11-14

## Context

0.2.0 and 0.3.0 shipped within the same hour. Each one was a minor bump, chosen because
the work felt like a feature, not because anything about it called for one. At that pace
the minor number tells users nothing, and 1.0 starts to look close when it isn't.

Before 1.0, semver puts no rules on what goes into a minor or a patch release. We have to
make our own.

## Decision

- **Every release is a patch by default:** 0.3.1, 0.3.2 and so on. That covers fixes, small
  features, UI changes, docs and dependency updates.
- **A minor release (0.x.0) needs the owner's say-so.** A contributor or agent can suggest
  one, with the reason, when a release:
  - renames or removes an MCP tool or one of its inputs,
  - changes the export or import format,
  - removes a setting or environment variable,
  - adds a schema migration (after one, the older release won't start on that data), or
  - adds a set of features big enough to announce.

  The owner decides. Nobody bumps the minor number on their own.
- **1.0 is the owner's call.** It promises that the MCP tools, the data format and the
  configuration stay stable.
- **Batch changes.** Merged work collects under `[Unreleased]` in `CHANGELOG.md`, and merging
  a pull request doesn't start a release. Release when users have something worth taking, at
  most about once a week. Security fixes go out the same day.
- **The release workflow enforces it.** Started from Actions, it accepts only the next patch
  after the latest tag. The next minor or major version needs the **Minor or major
  release** box ticked. Skipped numbers are always refused.

## Consequences

- Version numbers grow slowly, and a minor bump tells users to read the notes.
- A patch release can add a schema migration if the owner doesn't want a minor for it. The
  release notes must then say that going back to an earlier patch won't work.
- Pushing a `v*.*.*` tag by hand skips the check. Start releases from Actions.

## Revisit when

- We reach 1.0. After that, semver's own rules apply.
