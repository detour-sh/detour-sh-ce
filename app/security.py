"""Deployment settings and portal sign-in, shared by the portal and the MCP server.

Detour is local first. With no settings it listens on this computer only, and anything that
reaches the portal still has to sign in. Cloud mode turns on when DETOUR_PUBLIC_URL is set.
It then also requires an operator-chosen sign-in token, HTTPS and key-authenticated MCP.
Bad settings stop startup. Nothing here falls back to a weaker setting.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

MIN_TOKEN_CHARS = 24
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 60
SESSION_VERSION = "v1"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MCP_AUTH_MODES = ("key", "none")


class ConfigError(ValueError):
    """A setting is wrong. The message says which one and what to do."""


def _port(env: Mapping[str, str], name: str, default: int) -> int:
    raw = str(env.get(name, "")).strip() or str(default)
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name} must be a port number from 1 to 65535.")
    return port


def _public_url(raw: str) -> tuple[str, str] | None:
    """Return (origin, host header) for a plain https origin, or None when no URL is set."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ConfigError("DETOUR_PUBLIC_URL isn't a valid URL.") from exc
    problem = None
    if parts.scheme != "https":
        problem = "must start with https://. Detour won't serve a public address over plain HTTP"
    elif not parts.hostname:
        problem = "needs a host name"
    elif parts.username or parts.password:
        problem = "can't contain a user name or password"
    elif parts.path not in ("", "/") or parts.query or parts.fragment:
        problem = "must be just the address, like https://detour.example.com, with no path"
    if problem:
        raise ConfigError(f"DETOUR_PUBLIC_URL {problem}.")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return f"https://{netloc}", netloc


@dataclass(frozen=True)
class Settings:
    public_url: str | None
    public_host: str | None
    portal_port: int
    mcp_port: int
    bind_host: str
    session_hours: int
    mcp_auth: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        public = _public_url(str(env.get("DETOUR_PUBLIC_URL", "")))
        mcp_auth = str(env.get("DETOUR_MCP_AUTH_MODE", "key")).strip().lower() or "key"
        if mcp_auth not in MCP_AUTH_MODES:
            raise ConfigError(f"DETOUR_MCP_AUTH_MODE must be key or none, not “{mcp_auth}”. Leave it as key unless you are testing.")
        if public and mcp_auth != "key":
            raise ConfigError("DETOUR_MCP_AUTH_MODE can't be none when DETOUR_PUBLIC_URL is set. A public MCP endpoint always needs access keys.")
        raw_hours = str(env.get("DETOUR_SESSION_HOURS", "")).strip() or "24"
        try:
            hours = int(raw_hours)
        except ValueError:
            hours = 0
        if not 1 <= hours <= 168:
            raise ConfigError("DETOUR_SESSION_HOURS must be a whole number from 1 to 168.")
        return cls(
            public_url=public[0] if public else None,
            public_host=public[1] if public else None,
            portal_port=_port(env, "PORT", 3787),
            mcp_port=_port(env, "DETOUR_MCP_PORT", 3788),
            bind_host=str(env.get("DETOUR_BIND_HOST", "")).strip() or "127.0.0.1",
            session_hours=hours,
            mcp_auth=mcp_auth,
        )

    @property
    def cloud(self) -> bool:
        return self.public_url is not None

    @property
    def secure_cookies(self) -> bool:
        return self.cloud

    @property
    def mcp_url(self) -> str:
        return f"{self.public_url}/mcp" if self.public_url else f"http://127.0.0.1:{self.mcp_port}/mcp"

    def local_hosts(self, port: int) -> set[str]:
        # "detour" is the Compose service name, used by the browser tests and other containers.
        return {f"{name}:{port}" for name in (*LOOPBACK_HOSTS, "detour")}

    @property
    def portal_hosts(self) -> set[str]:
        return self.local_hosts(self.portal_port) | ({self.public_host} if self.public_host else set())

    @property
    def mcp_hosts(self) -> set[str]:
        return self.local_hosts(self.mcp_port) | ({self.public_host} if self.public_host else set())

    @property
    def portal_origins(self) -> set[str]:
        local = {f"http://{host}" for host in self.local_hosts(self.portal_port)}
        return local | ({self.public_url} if self.public_url else set())

    @property
    def mcp_origins(self) -> set[str]:
        local = {f"http://{host}" for host in self.local_hosts(self.mcp_port)}
        return local | ({self.public_url} if self.public_url else set())


def load_portal_token(settings: Settings, env: Mapping[str, str], data_dir: Path) -> str:
    """The secret that signs people in to the portal.

    A token from DETOUR_PORTAL_TOKEN wins and is never written to disk. A local install makes its
    own on first start and keeps it in the data volume, readable by the app only. A cloud install must
    set one, so a lost volume or a copied image can never leave a public portal with a guessable secret.
    """
    configured = str(env.get("DETOUR_PORTAL_TOKEN", "")).strip()
    if configured:
        if len(configured) < MIN_TOKEN_CHARS:
            raise ConfigError(f"DETOUR_PORTAL_TOKEN must be at least {MIN_TOKEN_CHARS} characters. Try: openssl rand -hex 32")
        return configured
    if settings.cloud:
        raise ConfigError("DETOUR_PORTAL_TOKEN is required when DETOUR_PUBLIC_URL is set. Try: openssl rand -hex 32")
    path = data_dir / "portal-token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < MIN_TOKEN_CHARS:
            raise ConfigError(f"{path} is damaged. Delete it to make a new sign-in token, or set DETOUR_PORTAL_TOKEN.")
        return token
    token = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


class SignIn:
    """Checks the sign-in token and issues signed session cookies.

    Cookies carry an expiry and a random value, signed with a key derived from the token. Nothing is
    stored, so restarts keep people signed in, and changing the token signs everyone out.
    """

    def __init__(self, token: str, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        self._token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        self._key = hmac.new(token.encode("utf-8"), b"detour-session-" + SESSION_VERSION.encode(), hashlib.sha256).digest()
        self._settings = settings
        self._clock = clock
        self._failures: list[float] = []
        self._revoked: dict[str, int] = {}  # session nonce -> expiry. Kept in memory: a restart forgets sign-outs.

    @property
    def cookie_name(self) -> str:
        # __Host- makes browsers refuse the cookie unless it is Secure, host-only and Path=/.
        return ("__Host-" if self._settings.secure_cookies else "") + "detour_session"

    def check_token(self, candidate: object) -> bool:
        if not isinstance(candidate, str) or not candidate:
            return False
        return hmac.compare_digest(hashlib.sha256(candidate.encode("utf-8")).digest(), self._token_digest)

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expires = int(self._clock()) + self._settings.session_hours * 3600
        payload = f"{SESSION_VERSION}.{expires}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def _parse(self, cookie: str | None) -> list[str] | None:
        if not cookie or not cookie.isascii():
            return None
        parts = cookie.split(".")
        if len(parts) != 4 or parts[0] != SESSION_VERSION or not parts[1].isdigit():
            return None
        if not hmac.compare_digest(self._sign(".".join(parts[:3])), parts[3]):
            return None
        return parts

    def valid(self, cookie: str | None) -> bool:
        parts = self._parse(cookie)
        return parts is not None and int(parts[1]) > self._clock() and parts[2] not in self._revoked

    def revoke(self, cookie: str | None) -> None:
        """End one session now, so a copied cookie stops working when its owner signs out."""
        now = self._clock()
        self._revoked = {nonce: expires for nonce, expires in self._revoked.items() if expires > now}
        parts = self._parse(cookie)
        if parts is not None and int(parts[1]) > now:
            self._revoked[parts[2]] = int(parts[1])

    def revoked_count(self) -> int:
        return len(self._revoked)

    def read_cookie(self, header: str | None) -> str | None:
        if not header:
            return None
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(header)
        except CookieError:
            return None
        morsel = jar.get(self.cookie_name)
        return morsel.value if morsel else None

    def _attributes(self, max_age: int) -> str:
        secure = "; Secure" if self._settings.secure_cookies else ""
        return f"; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

    def set_cookie(self, value: str) -> str:
        return f"{self.cookie_name}={value}{self._attributes(self._settings.session_hours * 3600)}"

    def clear_cookie(self) -> str:
        return f"{self.cookie_name}={self._attributes(0)}"

    # One shared limit rather than one per address: behind a proxy every request shares an address,
    # and the token is long enough that this only has to stop floods, not stop a patient person.

    def retry_after(self) -> int:
        now = self._clock()
        self._failures = [stamp for stamp in self._failures if now - stamp < FAILURE_WINDOW_SECONDS]
        if len(self._failures) < MAX_FAILURES:
            return 0
        return max(1, math.ceil(self._failures[0] + FAILURE_WINDOW_SECONDS - now))

    def record_failure(self) -> None:
        self._failures.append(self._clock())
