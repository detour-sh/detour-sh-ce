# detour-sh-ce: project notes for AI sessions

## Dev environment decision
- **No separate dev environment needed.** Local checks are `python -B -m unittest discover -s app -p 'test_*.py'` (stdlib plus the hash-locked `app/requirements.txt`). CI (`.github/workflows/ci.yml`) is the integration environment; it builds the image and runs Playwright UI tests.
- Docker is not available in cloud authoring sessions, so image build, healthcheck and compose runs are verified only in CI or on a real host.
- Hosted mode needs a real Cloudflare account to verify end to end. Use a throwaway test zone, never production.

## Current work stream
- Hosting online (option B: Cloudflare Access + Tunnel). Spec, status and open items: `docs/hosting-requirements.md`. Setup guide: `docs/hosting.md`.
- Branch: `claude/amazing-brown-1i8htj`.

## Rules for this repo
- Local mode (`DETOUR_ACCESS_MODE` unset) must behave exactly as before.
- Backend changes are test-first; tests live next to the code in `app/test_*.py`.
- GitHub Actions must be pinned by commit SHA. New workflows follow the `_auto:` / `_manual:` naming rule; existing ones are renamed only when next touched.
- Secrets only via GitHub Actions secrets or server env files. Names are listed in `docs/hosting-requirements.md` section 7.
- Linear is updated when a work stream finishes.
