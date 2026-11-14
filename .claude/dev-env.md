# Dev environment

Decision: no separate dev environment. Detour CE runs locally in one container or with
`python server.py`, and the browser tests use their own `detour-check` Compose project that is
deleted afterwards. There is no hosted service to stage.

- Portal UI: `cd app/web && npm ci && npm run build`. `npm test` runs the theme tests
  (Node's built-in runner, no install needed).
- Backend: `python -B -m unittest discover -s app -p 'test_*.py'`.
- Hosted mode (`DETOUR_HOSTED=true`, see the README) is for self-hosters. This project runs no hosted
  instance, so it needs no staging either. To try it locally, set `DETOUR_PUBLIC_URL=http://localhost:3790`
  and `PORT=3790`; `http` is accepted for `localhost` only.
