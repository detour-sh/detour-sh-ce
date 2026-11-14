# Hosting requirements and handoff

Spec, status and open work for running Detour CE somewhere other than loopback.
Written so a new session can pick this up without the original conversation.
User-facing setup is in [hosting.md](hosting.md).

**Decision taken:** option B, supported private hosting behind an identity-aware
proxy, single user or small trusted team. Front door: Cloudflare Access plus a
Cloudflare Tunnel. Not in scope for CE: multi-tenant SaaS (Clerk, Neon, per-user
tenancy, RBAC). That is a separate edition.

**Status legend:** Done = code and unit tests merged on branch. Partial = some of it.
Todo = not started. Untested = written but never run against the real service.

## 1. Baseline findings that drove this work

Review of v0.2.0 (before this change):

1. Portal had no authentication. Every `/api/*` route was open, including key creation, full export and import. Protection was only the loopback port bind plus Host and Origin checks.
2. The Host and Origin checks hardcoded `127.0.0.1`/`localhost` and `http://`, so an HTTPS reverse proxy failed them. The easy "fix" is loosening them, leaving the open API exposed.
3. The MCP SDK enables DNS-rebinding protection only when told the bind host is loopback. Detour binds `0.0.0.0` in the container and passed no explicit settings, so the Host check on the MCP endpoint was not guaranteed.
4. Agent keys never expired and were not scoped to routes. MCP traffic was plain HTTP.
5. `http.server` had no read timeout and no connection cap (slowloris, thread exhaustion). Import had no count limit.
6. `log_message` claimed to drop query strings but logged the full request line.
7. The compose file set no memory, CPU or process limits.
8. (Checked and dropped.) The README's `v0.1.1` quick start is the latest release; `v0.2.0` in the upgrade steps is only an example. Not a defect.

## 2. Requirements

| ID | Requirement | Status | Where | Acceptance |
|---|---|---|---|---|
| H-1 | Hosted mode must be explicit. Default stays loopback-only and unchanged. | Done | `access.py` `load_config`, `DETOUR_ACCESS_MODE` | Default config is `local`; `test_access.ConfigTests` |
| H-2 | Refuse to start with missing or unsafe hosting config (non-https URL, same host for portal and MCP, no audience, MCP auth off, stdio on). | Done | `access.load_config`, `run.py` exit code 2 | `ConfigTests.test_unsafe_or_incomplete_proxy_config_is_refused`; manual: `DETOUR_ACCESS_MODE=proxy python app/run.py` exits 2 with a message |
| H-3 | Portal requires a valid Cloudflare Access JWT on every request except `/health`. Verify RS256 signature against the team JWKS, issuer, audience, expiry. | Done | `access.AccessVerifier`, `server.dispatch` | `VerifierTests`, `HostedPortalTests.test_api_needs_a_valid_token` |
| H-4 | In hosted mode Host must equal the public hostname and writes must come from the exact public https origin. Loopback Host is accepted only for `/health`. | Done | `server.valid_host`, `valid_write_origin` | `HostedPortalTests` |
| H-5 | MCP endpoint requires a valid Access JWT (separate audience, accepts service tokens) **and** a Detour key. Explicit Host/Origin protection in both modes. | Done | `mcp_server.build_app`, `access.AccessMiddleware`, `access.mcp_transport_security` | `test_mcp.McpHttpGateTests`, `MiddlewareTests` |
| H-6 | Record the real actor. Events and `created_by` use the signed-in email instead of "You". | Done | `store.actor_var`, `server.dispatch` | `HostedPortalTests.test_actor_in_activity_is_the_signed_in_user`, `KeyExpiryAndActorTests` |
| H-7 | Agent keys can expire. Default TTL configurable. Schema migration v2 to v3 with backup. | Done (API only) | `store.create_key`, `DETOUR_KEY_TTL_DAYS`, `expires_at` column | `KeyExpiryAndActorTests`, `MigrationTests.test_v2_database_gains_key_expiry` |
| H-7a | Portal UI: expiry picker when creating a key, show `expires_at` and "expired" state on Agent connections. | **Todo** | `app/web/src/pages/Connections.jsx` | Playwright test in `app/ui-tests` |
| H-7b | Rotate a key (new token, same name and permissions, old key valid for a short overlap). | **Todo** | store + API + UI | unit + UI test |
| H-8 | Scope an agent key to chosen routes. Today every key reads every unpaused route. Enforce in `find_routes`, `get_context`, `search_context`, `propose`. Needs schema v4 (`key_routes` table) and a migration that keeps existing keys unrestricted. | **Todo** | `store.py`, `mcp_server.py`, UI | A key scoped to route A cannot read or propose on route B through any tool; tests for each tool |
| H-9 | Server resilience: request read timeout, connection cap with 503, import size limits, strip query strings from logs. | Done | `server.PortalServer`, `DetourHandler.timeout`, `store.MAX_IMPORT_*` | `ServerHardeningTests`, `test_import_is_size_limited` |
| H-9a | Rate limiting per client (Detour-side or Cloudflare WAF rate rule). Edge rule is the cheaper route: add a rate-limiting rule on both hostnames. | **Todo** | Cloudflare config (add to `manual-hosting` workflow) or app | Burst of N requests gets 429 |
| H-10 | Container hardening: memory, CPU, pids limits; hosted compose publishes no ports. | Done | `docker-compose.yml`, `docker-compose.hosted.yml` | `docker compose config` renders; limits visible. **Untested** on a live Docker host |
| H-11 | Reference deployment: Cloudflare Tunnel sidecar, both hostnames, `.env` driven. | Done, Untested | `docker-compose.hosted.yml` | Follow "Verify the deployment" in hosting.md on a real account |
| H-12 | Pin the `cloudflared` image by digest, and have Dependabot watch it. | **Todo** | `docker-compose.hosted.yml`, `.github/dependabot.yml` | `image: cloudflare/cloudflared@sha256:...` |
| H-13 | Setup automation: GitHub workflow that creates the two Access applications and policies and prints audience tags. | Done, **Untested** | `.github/workflows/manual-hosting.yml` | Run with `dry_run=true`, then once for real on a test zone. Verify Cloudflare API shapes against current docs first |
| H-14 | Create service tokens and the tunnel from the workflow. Secret values are shown only once, so decide how to hand them over (GitHub environment secret via API, or leave to the dashboard). | Todo | workflow | decision recorded in this file |
| H-15 | Scheduled off-box backups of the data volume (R2 or S3), with restore test. Detour only backs up before migrations today. | **Todo** | host cron or a workflow, plus README | Restore into an empty volume reproduces the data |
| H-16 | Audit trail export: signed-in email, key id and IP for every read and write, downloadable. Today reads are not logged, only `last_used_at`. | **Todo** | store + API + UI | Reads by a key appear in the export |
| H-17 | Observability: structured JSON logs, `/metrics` or equivalent, documented alerts (auth failures, 5xx, backup age). | **Todo** | `server.py`, `mcp_server.py` | Log line per request with request id, no secrets |
| H-18 | CI security gates: CodeQL, dependency-review, secret scanning. Every action must be pinned by SHA, like the existing workflows. Look the SHAs up at the time. Apply the `_auto:`/`_manual:` naming rules when the workflow is next touched. | **Todo** | `.github/workflows/ci.yml` | CodeQL runs on PRs |
| H-19 | End-to-end hosted smoke test in CI: start the hosted compose stack with a stub JWKS server and assert the verify list in hosting.md items 1-6. | **Todo** | `app/ui-tests` or a new job | Job fails if an unauthenticated request returns data |
| H-20 | Prompt-injection posture: the "Auto-approve notes" policy lets an agent write text that other agents later read as trusted. In hosted mode default new routes to "Ask me every time", and show a warning on the Auto-approve option. | **Todo** | `store.POLICIES` default stays `ask`; add UI warning | UI shows warning text |
| H-21 | Threat model section and a SECURITY.md update describing hosted mode as supported scope. | Partial | this file section 3 | SECURITY.md lists hosted mode |
| H-22 | At release time, bump the pinned tag in the README quick start and the `CHANGELOG`; mention hosted mode in the release notes. | Todo (release step) | README, CHANGELOG | Quick start tag equals the new release |

## 3. Threat model (hosted mode)

Assets: the user's rules, decisions, runbooks and prompts (may contain internal
process, infrastructure names, sometimes credentials pasted by mistake); the ability
to change what agents are told to do.

| Threat | Mitigation | Gap |
|---|---|---|
| Anonymous internet user reaches the portal | Access policy at the edge; Detour verifies the JWT itself; no published ports | Depends on correct Access policy. H-19 would test it in CI |
| Request bypasses the proxy (direct to origin, other tunnel hostname) | JWT required; Host must be the public name | None known |
| JWT forged or replayed from another app | RS256 check, issuer, audience per app, expiry. Portal and MCP use different audiences | JWKS fetched from team domain; outage blocks sign-in (cached 1 hour) |
| Stolen agent key | Needs Cloudflare service token too; keys expire (H-7); revocable | No route scoping (H-8), no rotation flow (H-7b) |
| Stolen browser session | Access session duration; HSTS; CSP; CSRF protection through exact Origin plus JSON-only writes | None known |
| Malicious or compromised agent writes bad context | All changes are proposals; user approves; undo | Auto-approve notes policy (H-20); reads not audited (H-16) |
| Prompt injection through stored context | Human review of changes | Content from approved items is trusted by agents |
| DNS rebinding against MCP | Explicit allowed hosts in both modes | None known |
| Resource exhaustion | Connection cap, timeouts, body and import limits, container limits, Cloudflare in front | Per-client rate limit (H-9a) |
| Data loss | Backup before migrations | Scheduled off-box backups (H-15) |
| Supply chain | Digest-pinned bases, hash-locked pip, cosign, SBOM, Trivy | `cloudflared` tag not yet digest-pinned (H-12) |
| Insider with portal access | Email allowlist, actor recorded on every change | One shared data set; no per-user separation |

## 4. Design notes a future session must keep

- **Local mode must not change.** `DETOUR_ACCESS_MODE` unset means exactly the old behaviour. `test_server.py` and `PortalApiTests` guard it.
- **Never add a bypass for `/health` beyond a bare ok.** It is unauthenticated on purpose (tunnel and Docker probes). Do not add data to it.
- **Hosted mode requires Detour keys on MCP.** `DETOUR_MCP_AUTH_MODE=none` is refused with `proxy`. Keep that.
- **Two audiences.** Do not reuse the portal audience for MCP, so a person's browser token can't be replayed as an agent token.
- **No new dependencies were added.** PyJWT and cryptography are already hash-locked as dependencies of `mcp`. If `mcp` drops them, add them to `requirements.in` and regenerate hashes.
- **Test seam.** `server.ACCESS` and `server.VERIFIER` are module globals tests replace; `access.AccessVerifier` takes a `key_resolver`. Real deployments use `PyJWKClient`.
- **Schema.** `SCHEMA_VERSION` is 3. The `expires_at` column is added by an `ALTER TABLE` in `init_db`, after the backup. A new migration should bump the version and extend the migration test.
- **Single source of identity.** `store.actor_var` is set per request in `server.dispatch`. Anything that writes on behalf of the user should rely on it, not hardcode "You".

## 5. Assumptions not yet verified

- Cloudflare Access sends `Cf-Access-Jwt-Assertion` to the origin for **service-token** requests as well as browser requests. Documented behaviour, not tested here.
- Cloudflare API shapes used in `manual-hosting.yml` (`/access/apps`, per-app `/policies`, `non_identity` decision, `any_valid_service_token` include) match the current API. They need checking against current docs.
- MCP clients can send three custom headers. Claude Code can. Others vary.
- `PyJWKClient` reaches `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` from the container. The default compose has no egress restriction.
- Docker was not available in the authoring session. Image build, healthcheck and the hosted compose run were not exercised. Unit tests (73) pass.

## 6. Suggested order for the next session

1. Run the verify list on a real test domain; fix whatever the unverified assumptions got wrong (section 5).
2. H-8 route scoping and H-7a/H-7b key UI. These are the biggest remaining security and usability gaps.
3. H-9a Cloudflare rate rule, H-12 digest pin, H-18 CodeQL.
4. H-15 backups, H-16 audit export, H-17 logs.
5. H-19 hosted smoke test in CI so this stays true.

## 7. Credentials and names

| Name | Where | Purpose |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | GitHub Actions secret | `manual-hosting` workflow. Permission: Access Apps and Policies Edit |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret | `manual-hosting` workflow |
| `TUNNEL_TOKEN` | server `.env` | cloudflared |
| `DETOUR_ACCESS_AUD`, `DETOUR_MCP_ACCESS_AUD` | server `.env` (not secret) | Audience tags printed by the workflow |
