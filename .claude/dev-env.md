# Dev environment

Decision: no separate dev environment. Detour CE runs locally in one container or with
`python server.py`, and the browser tests use their own `detour-check` Compose project that is
deleted afterwards. There is no hosted service to stage.

- Portal UI: `cd app/web && npm ci && npm run build`. `npm test` runs the theme tests
  (Node's built-in runner, no install needed).
- Backend: `python -B -m unittest discover -s app -p 'test_*.py'`.
- Portal sign-in: the portal makes a token on first start in `$DETOUR_DATA_DIR/portal-token`, or set
  `DETOUR_PORTAL_TOKEN`. The browser tests sign in with `DETOUR_PORTAL_TOKEN`.
- Hosted mode (`DETOUR_PUBLIC_URL`) has no staging environment. It is covered by unit tests in
  `app/test_security.py`, `app/test_server.py` and `app/test_mcp.py`.
