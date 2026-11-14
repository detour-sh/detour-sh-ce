"""Refuse clients that connect from the public internet.

Detour has no sign-in on its portal, so it must only be reachable from the machine
it runs on or a private network. Docker Compose publishes the ports on 127.0.0.1,
but someone can still start the image with `docker run -p 3787:3787` or edit the
Compose file. This is the second line of defence: by default the portal and the MCP
server answer only clients with a loopback, private, link-local or CGNAT address
(the last covers Tailscale) and reject a public client with 403.

It is a safety net, not a replacement for binding to 127.0.0.1. A reverse proxy or
tunnel running on the same host shows up as a private address, so never put one in
front of Detour.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping

SETTING = "DETOUR_ALLOW_PUBLIC_CLIENTS"
REFUSAL = b'{"error": "public clients are not allowed; Detour is for local use"}'


def public_clients_allowed(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get(SETTING, "false").strip().lower() == "true"


def client_allowed(address: str | None, allow_public: bool) -> bool:
    if allow_public:
        return True
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global


class ClientGuard:
    """ASGI middleware that applies client_allowed to every HTTP and WebSocket request."""

    def __init__(self, app, allow_public: bool | None = None):
        self.app = app
        self.allow_public = public_clients_allowed() if allow_public is None else allow_public

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not client_allowed(client[0] if client else None, self.allow_public):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 403, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(REFUSAL)).encode()),
                    (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": REFUSAL})
                return
        await self.app(scope, receive, send)
