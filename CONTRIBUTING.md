# Contributing

Thanks for helping improve Detour Community Edition.

## Before you start

- For bugs and small fixes, open a pull request directly or file an issue first.
- For new features or larger changes, open an issue to discuss the idea before
  you write code.
- For security issues, follow [SECURITY.md](SECURITY.md) instead of opening an
  issue.

## Reporting bugs

Use the bug report form. Fill in every field and tick the confirmations. Reports
that skip the form, are mostly links, or are unreviewed AI output are closed
without a reply. Maintainers label the rest `needs-triage` and answer as time
allows.

## Development setup

You need Python 3.12, Node.js 24 and Docker.

The portal UI lives in `app/web` (React, Vite and IBM's Carbon Design System).
Build it once, then run the portal without Docker:

```bash
cd app/web
npm ci
npm run build
cd ..
DETOUR_DATA_DIR=./.data python server.py
```

`app/web/.npmrc` turns off install scripts, because Carbon's packages run an
IBM telemetry script on install. Keep it that way.

While you work on the UI, run `npm run dev` in `app/web`. Vite serves the UI
with hot reload and sends API calls to the portal on port 3787.

### UI rules

The portal follows Carbon so that it stays consistent and can be upgraded:

- Use Carbon components. The only custom patterns are `ProposalCard`,
  `TargetList`, `HistoryList`, `CheckResults` and `ThemePicker` in
  `app/web/src/components`.
- Themes are Carbon tokens. `app/web/src/theme.js` turns six colors into
  `--cds-*` variables, so don't restyle components for a theme. Change the
  palette or the derivation there, and run `npm test` in `app/web`. The tests
  check that every preset has readable contrast (WCAG AA).
- Type follows Carbon's scale: 16px (`body-02`) for lead text, 14px for UI and
  labels, and about 70 characters at most per line of prose.
- Use Carbon tokens for color, spacing and type in `styles.scss`. Don't
  hard-code colors, sizes or fonts.
- The portal and the MCP server both go through `app/store.py`. Add behavior
  there, not in the UI or in a single tool.

Run the tests from the repository root:

```bash
python -B -m unittest discover -s app -p 'test_*.py'
```

Run the full local check on Windows: unit tests, container build, health check,
and browser tests at desktop and mobile sizes. Screenshots go to
`artifacts/ui/`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-local.ps1
```

The browser tests create routes and keys, so the check runs under a separate
Compose project, `detour-check`, with its own volume, and deletes that volume
at the end. Your own Detour data isn't touched. Stop your own Detour first,
because both use ports 3787 and 3788.

On other platforms, run the same steps by hand:

```bash
docker compose -p detour-check up -d --build --wait detour
docker compose -p detour-check --profile test run --rm --build --no-deps ui-test
docker compose -p detour-check --profile test down -v
```

## Dependencies and assets

Python dependencies for the container image are pinned with hashes for Linux
and Python 3.12. Dependabot updates them monthly. To change them by hand, edit
`app/requirements.in`, then regenerate the lock file with
[uv](https://docs.astral.sh/uv/):

```bash
cd app
uv pip compile requirements.in --python-version 3.12 --python-platform linux --generate-hashes -o requirements.txt
```

The portal uses only the Python standard library. The MCP server and
`test_mcp.py` need the `mcp` package; without it, those tests are skipped.

Web dependencies are pinned to exact versions in `app/web/package.json`.
Dependabot updates them monthly, as one group.

The logo and favicons are pixel art drawn separately at 16 and 32 pixels in
`scripts/build_brand.py`. To change them, edit the geometry there and run:

```bash
uv run --no-project --with pillow==11.3.0 python scripts/build_brand.py
```

## Releases

Until 1.0, every release is a patch (0.3.1, 0.3.2, ...) unless the maintainer
decides otherwise. Merged changes wait under `[Unreleased]` and go out together,
not one release per pull request. Security fixes go out the same day. A minor
release (0.x.0) is the maintainer's call. Suggest one when a release renames or
removes an MCP tool or input, changes the export format, removes a setting, adds
a schema migration, or adds features big enough to announce. See
[decision 0002](docs/decisions/0002-patch-releases-by-default.md).

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version
   heading, and update the version in the README quick start and in
   `app/server.py`.
2. Merge to `main` and wait for CI to pass.
3. Run the **_manual:release** workflow on `main` with the version, such as
   `0.3.1`. It tags the commit and starts the release. It accepts only the next
   patch after the latest tag. For a minor or major version, tick **Minor or
   major release**. Pushing a `v*.*.*` tag by hand also works for a patch. The
   run on the tag repeats the checks, and won't publish a minor or major
   version from a hand-pushed tag.

Every release run checks that `CHANGELOG.md`, `VERSION` in `app/server.py` and
the README's install commands all name the version, and stops before publishing
if any of them don't.

The release workflow runs the tests, publishes signed amd64 and arm64 images
to GitHub Container Registry, and creates the GitHub release from the
changelog. The release also includes the image SBOM and a checksums file, each
with a Sigstore signature bundle.

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests for behavior changes.
- Make sure the tests pass and CI is green.
- Don't add new runtime dependencies without discussing them first.
- Never commit secrets, keys, or personal data.

By submitting a contribution, you agree it's licensed under the
[Apache License 2.0](LICENSE).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
