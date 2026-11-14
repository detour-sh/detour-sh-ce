# Environments

Decision (2026-11-14): no separate dev environment is needed.

- The app is a Docker image people run themselves. CI (`ci.yml`) runs the unit tests,
  builds the image and runs the browser tests on every pull request. That is the staging step.
- Local mode (default) has no hosted environment. Hosted mode (`DETOUR_HOSTED=true`) is for
  self-hosters; this project does not run a hosted instance.
- No secrets are needed to build or test. Release signing uses GitHub Actions only.
