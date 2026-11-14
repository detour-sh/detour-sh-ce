"""Settings for running Detour on a server instead of on your own computer.

Local mode (the default) has no sign-in and refuses public clients; see netguard.py.
Hosted mode is opt-in. It needs DETOUR_HOSTED=true and the HTTPS address people will
use in DETOUR_PUBLIC_URL, and it requires the owner to sign in (see auth.py). Bad
combinations stop Detour from starting, so a half-configured server never goes live
without a login.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

LOCAL_NAMES = {"localhost", "127.0.0.1"}
DEFAULT_PORTS = {"http": 80, "https": 443}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Hosting:
    hosted: bool = False
    public_url: str | None = None  # https://detour.example.com
    host: str | None = None  # detour.example.com, as a browser sends it in the Host header
    mcp_public_url: str | None = None

    @property
    def secure_cookie(self) -> bool:
        return bool(self.public_url and self.public_url.startswith("https://"))

    @property
    def mcp_url(self) -> str | None:
        if self.mcp_public_url:
            return self.mcp_public_url
        if not self.public_url:
            return None
        hostname = urlsplit(self.public_url).hostname
        return f"{urlsplit(self.public_url).scheme}://{hostname}:3788/mcp"

    def allowed_origins(self) -> set[str]:
        return {self.public_url} if self.public_url else set()


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "false").strip().lower() == "true"


def _check_scheme(parts, name: str) -> None:
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{name} must be a full URL such as https://detour.example.com")
    if parts.username or parts.password:
        raise ConfigError(f"{name} must not contain a username or password")
    if parts.query or parts.fragment:
        raise ConfigError(f"{name} must not contain a query or fragment")
    if parts.scheme == "http" and parts.hostname not in LOCAL_NAMES:
        raise ConfigError(f"{name} must use https unless it points at localhost. Never serve Detour over plain http on a network.")
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError as exc:
        raise ConfigError(f"{name} has an invalid port") from exc


def load(env: Mapping[str, str] | None = None) -> Hosting:
    env = os.environ if env is None else env
    hosted = _flag(env, "DETOUR_HOSTED")
    public = env.get("DETOUR_PUBLIC_URL", "").strip()
    mcp_public = env.get("DETOUR_MCP_PUBLIC_URL", "").strip()
    if not hosted:
        if public or mcp_public:
            raise ConfigError("DETOUR_PUBLIC_URL needs DETOUR_HOSTED=true. A public address without sign-in would expose your data.")
        return Hosting()
    if not public:
        raise ConfigError("DETOUR_HOSTED=true needs DETOUR_PUBLIC_URL, for example https://detour.example.com")
    if env.get("DETOUR_MCP_AUTH_MODE", "key").strip().lower() not in ("", "key"):
        raise ConfigError("Hosted mode requires MCP access keys. Remove DETOUR_MCP_AUTH_MODE or set it to key.")
    parts = urlsplit(public)
    _check_scheme(parts, "DETOUR_PUBLIC_URL")
    if parts.path not in ("", "/"):
        raise ConfigError("DETOUR_PUBLIC_URL must not contain a path")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if parts.port and parts.port != DEFAULT_PORTS[parts.scheme]:
        host = f"{host}:{parts.port}"
    origin = f"{parts.scheme}://{host}"
    if mcp_public:
        _check_scheme(urlsplit(mcp_public), "DETOUR_MCP_PUBLIC_URL")
    return Hosting(hosted=True, public_url=origin, host=host, mcp_public_url=mcp_public or None)
