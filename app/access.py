"""Who may reach Detour, and from where.

Two modes:

* ``local`` (default): the portal is loopback-only and has no sign-in. The Host and
  Origin checks and the 127.0.0.1 port bind are the protection. Nothing else changes.
* ``proxy``: Detour sits behind an identity-aware proxy (Cloudflare Access). The
  proxy signs every request it lets through with a JWT in ``Cf-Access-Jwt-Assertion``.
  Detour checks that signature itself, so a request that skips the proxy, or comes
  through a misconfigured one, is refused.

See docs/hosting.md for setup and docs/hosting-requirements.md for the full spec.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

LOCAL_HOSTS = ("127.0.0.1", "localhost", "detour")
ACCESS_HEADER = "cf-access-jwt-assertion"


class ConfigError(Exception):
    """The hosting settings are missing or unsafe."""


class AccessDenied(Exception):
    """The request carries no valid identity token."""


@dataclass(frozen=True)
class AccessConfig:
    mode: str = "local"
    portal_url: str = ""
    mcp_url: str = ""
    team_domain: str = ""
    portal_aud: str = ""
    mcp_aud: str = ""
    allowed_emails: frozenset = field(default_factory=frozenset)
    mcp_auth_mode: str = "key"

    @property
    def hosted(self) -> bool:
        return self.mode == "proxy"

    @property
    def portal_host(self) -> str:
        return (urlparse(self.portal_url).hostname or "").lower()

    @property
    def mcp_host(self) -> str:
        return (urlparse(self.mcp_url).hostname or "").lower()

    @property
    def portal_origin(self) -> str:
        parsed = urlparse(self.portal_url)
        return f"{parsed.scheme}://{parsed.netloc}"


def _https_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must be an https:// origin such as https://detour.example.com")


def load_config(env=None) -> AccessConfig:
    """Read and check the hosting settings. Raises ConfigError instead of starting half-secure."""
    env = os.environ if env is None else env
    mode = env.get("DETOUR_ACCESS_MODE", "local").strip().lower()
    mcp_auth = env.get("DETOUR_MCP_AUTH_MODE", "key").strip().lower()
    if mode == "local":
        return AccessConfig(mcp_auth_mode=mcp_auth)
    if mode != "proxy":
        raise ConfigError("DETOUR_ACCESS_MODE must be 'local' or 'proxy'")
    portal_url = env.get("DETOUR_PUBLIC_URL", "").strip().rstrip("/")
    mcp_url = env.get("DETOUR_MCP_PUBLIC_URL", "").strip().rstrip("/")
    team = env.get("DETOUR_ACCESS_TEAM_DOMAIN", "").strip().lower()
    portal_aud = env.get("DETOUR_ACCESS_AUD", "").strip()
    mcp_aud = env.get("DETOUR_MCP_ACCESS_AUD", "").strip()
    for name, value in (("DETOUR_PUBLIC_URL", portal_url), ("DETOUR_MCP_PUBLIC_URL", mcp_url)):
        if not value:
            raise ConfigError(f"{name} is required when DETOUR_ACCESS_MODE=proxy")
        _https_url(name, value)
    if urlparse(portal_url).hostname == urlparse(mcp_url).hostname:
        raise ConfigError("DETOUR_PUBLIC_URL and DETOUR_MCP_PUBLIC_URL must use different hostnames")
    if not team or "/" in team or ":" in team or "." not in team:
        raise ConfigError("DETOUR_ACCESS_TEAM_DOMAIN is required, for example myteam.cloudflareaccess.com")
    if not portal_aud or not mcp_aud:
        raise ConfigError("DETOUR_ACCESS_AUD and DETOUR_MCP_ACCESS_AUD are required (the Application Audience tags)")
    if mcp_auth != "key":
        raise ConfigError("DETOUR_MCP_AUTH_MODE must be 'key' when DETOUR_ACCESS_MODE=proxy")
    if env.get("DETOUR_MCP_ALLOW_STDIO", "false").lower() == "true":
        raise ConfigError("DETOUR_MCP_ALLOW_STDIO must be off when DETOUR_ACCESS_MODE=proxy")
    emails = frozenset(part.strip().lower() for part in env.get("DETOUR_ACCESS_ALLOWED_EMAILS", "").split(",") if part.strip())
    return AccessConfig("proxy", portal_url, mcp_url, team, portal_aud, mcp_aud, emails, mcp_auth)


def _default_key_resolver(team_domain: str) -> Callable[[str], object]:
    import jwt

    client = jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600, timeout=5)
    return lambda token: client.get_signing_key_from_jwt(token).key


class AccessVerifier:
    """Checks a Cloudflare Access JWT: signature, issuer, audience, expiry, and optionally who the user is."""

    def __init__(self, team_domain: str, audience: str, allowed_emails=frozenset(), key_resolver=None):
        self.issuer = f"https://{team_domain}"
        self.audience = audience
        self.allowed_emails = frozenset(allowed_emails)
        self._resolve = key_resolver or _default_key_resolver(team_domain)

    def verify(self, token: str | None) -> dict:
        import jwt

        if not token:
            raise AccessDenied("sign-in required")
        try:
            claims = jwt.decode(
                token, self._resolve(token), algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud"]}, leeway=30,
            )
        except Exception as exc:  # noqa: BLE001 - any failure to prove identity is a denial; details stay out of the response
            raise AccessDenied("invalid sign-in token") from exc
        if self.allowed_emails and not self._email_allowed(str(claims.get("email", "")).lower()):
            raise AccessDenied("this account is not allowed")
        return claims

    def _email_allowed(self, email: str) -> bool:
        if not email:
            return False
        return any(email == entry or (entry.startswith("@") and email.endswith(entry)) for entry in self.allowed_emails)


def portal_verifier(config: AccessConfig, key_resolver=None) -> AccessVerifier:
    return AccessVerifier(config.team_domain, config.portal_aud, config.allowed_emails, key_resolver)


def mcp_verifier(config: AccessConfig, key_resolver=None) -> AccessVerifier:
    # Agents arrive with a Cloudflare service token, which has no email, so no email allowlist here.
    return AccessVerifier(config.team_domain, config.mcp_aud, frozenset(), key_resolver)


def mcp_transport_security(config: AccessConfig):
    """Host and Origin rules for the MCP endpoint.

    The SDK only turns DNS-rebinding protection on when it is told the bind host is loopback.
    Detour binds 0.0.0.0 inside its container, so it must be set explicitly.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    if config.hosted:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=[config.mcp_host, f"{config.mcp_host}:443"], allowed_origins=[])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{host}:*" for host in LOCAL_HOSTS] + ["[::1]:*"],
        allowed_origins=[f"http://{host}:*" for host in LOCAL_HOSTS] + ["http://[::1]:*"],
    )


class AccessMiddleware:
    """ASGI middleware: in proxy mode every MCP request must carry a valid Access token."""

    def __init__(self, app, verifier: AccessVerifier):
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        try:
            self.verifier.verify(headers.get(ACCESS_HEADER))
        except AccessDenied as exc:
            body = json.dumps({"error": str(exc)}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
