# Contributing

Thanks for helping improve Detour Community Edition.

## Before you start

- For bugs and small fixes, open a pull request directly or file an issue first.
- For new features or larger changes, open an issue to discuss the idea before
  you write code.
- For security issues, follow [SECURITY.md](SECURITY.md) instead of opening an
  issue.

## Development setup

You need Python 3.12 and Docker.

Run the app without Docker:

```bash
cd app
DETOUR_DATA_DIR=./.data python server.py
```

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

On other platforms, run the same steps by hand:

```bash
docker compose up -d --build detour
docker compose --profile test run --rm --no-deps ui-test
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

The portal and its tests use only the Python standard library, so local
development without Docker doesn't need these packages. Only the MCP server
does.

The logo and favicons are pixel art drawn separately at 16 and 32 pixels in
`scripts/build_brand.py`. To change them, edit the geometry there and run:

```bash
uv run --no-project --with pillow==11.3.0 python scripts/build_brand.py
```

## Releases

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version
   heading, and update the version in the README quick start.
2. Merge to `main` and wait for CI to pass.
3. Tag and push: `git tag -a v0.2.0 -m v0.2.0 && git push origin v0.2.0`.

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
