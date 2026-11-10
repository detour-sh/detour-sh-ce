from __future__ import annotations

import os
import sys

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from server import MCP_AUTH_MODE, get_artifact, get_route, init_db, list_artifacts, list_routes, route_context, verify_mcp_access_key


PORT = int(os.environ.get("DETOUR_MCP_PORT", "3788"))
MCP_RESOURCE = f"http://127.0.0.1:{PORT}/mcp"
ALLOW_STDIO = os.environ.get("DETOUR_MCP_ALLOW_STDIO", "false").lower() == "true"


class LocalAccessKeyVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        key = verify_mcp_access_key(token)
        if not key:
            return None
        return AccessToken(
            token=token,
            client_id=key["id"],
            subject=key["id"],
            scopes=["detour:read"],
            resource=MCP_RESOURCE,
            claims={"name": key["name"]},
        )


mcp_options = {}
if MCP_AUTH_MODE == "key":
    mcp_options = {
        "token_verifier": LocalAccessKeyVerifier(),
        "auth": AuthSettings(
            issuer_url="http://127.0.0.1:3787",
            resource_server_url=MCP_RESOURCE,
            required_scopes=["detour:read"],
            validate_token_resource=True,
        ),
    }

mcp = MCPServer(
    "Detour",
    instructions="Read context from the developer's local Detours and artifacts. Global context outranks Project context, which outranks Sub-detour context. Ask which Detour applies when the active project is unclear.",
    **mcp_options,
)


@mcp.tool()
def list_detour_routes() -> list[dict]:
    """List local Detours with scope and parent so you can choose the right context stack."""
    return [
        {"id": route["id"], "title": route["title"], "description": route["description"], "scope": route["scope"], "parent_id": route["parent_id"], "item_count": len(route["items"])}
        for route in list_routes()
    ]


@mcp.tool()
def get_detour_route(route: str) -> dict:
    """Get bounded context for one Detour, including Global and parent context with explicit precedence."""
    selected = get_route(route)
    if selected is None:
        selected = next((item for item in list_routes() if item["title"].casefold() == route.strip().casefold()), None)
    if selected is None:
        raise ValueError("Route not found. Call list_detour_routes to find its name.")
    return route_context(selected)


@mcp.tool()
def search_detour_artifacts(query: str, artifact_type: str = "") -> list[dict]:
    """Find local artifacts by title, body, or tag; returns short previews."""
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    params = {"q": [query]}
    if artifact_type:
        params["type"] = [artifact_type]
    return [
        {"id": item["id"], "type": item["type"], "title": item["title"], "tags": item["tags"], "preview": item["body_markdown"][:240]}
        for item in list_artifacts(params)[:20]
    ]


@mcp.tool()
def get_detour_artifact(artifact_id: str) -> dict:
    """Read one active artifact by ID, including its notes and reference locations."""
    artifact = get_artifact(artifact_id)
    if artifact is None or artifact["status"] != "active":
        raise ValueError("Artifact not found")
    return {
        "id": artifact["id"],
        "type": artifact["type"],
        "title": artifact["title"],
        "body_markdown": artifact["body_markdown"][:8000],
        "notes_markdown": artifact["notes_markdown"][:8000],
        "tags": artifact["tags"],
        "local_path": artifact["local_path"],
        "cloud_url": artifact["cloud_url"],
        "file_name": artifact["file_name"],
        "file_size": artifact["file_size"],
        "file_sha256": artifact["file_sha256"],
    }


if __name__ == "__main__":
    init_db()
    if sys.argv[1:] == ["--stdio"]:
        if MCP_AUTH_MODE == "key" and not ALLOW_STDIO:
            raise SystemExit("stdio MCP is disabled while access-key authentication is enabled")
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT, stateless_http=True, json_response=True)
