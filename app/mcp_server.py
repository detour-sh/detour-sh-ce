"""Detour MCP server.

Agents read context and propose changes. They never write directly: each
proposal waits in the portal's Review queue unless the route's policy lets it
apply on its own.
"""
from __future__ import annotations

import functools
import os
import sys

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations

import store

PORT = int(os.environ.get("DETOUR_MCP_PORT", "3788"))
MCP_RESOURCE = f"http://127.0.0.1:{PORT}/mcp"
AUTH_MODE = os.environ.get("DETOUR_MCP_AUTH_MODE", "key").strip().lower()
ALLOW_STDIO = os.environ.get("DETOUR_MCP_ALLOW_STDIO", "false").lower() == "true"
LOCAL_AGENT = {"id": None, "name": "Local agent", "can_propose": True}
# Set only by the --stdio entry point, after the user opted in with DETOUR_MCP_ALLOW_STDIO.
# HTTP calls never see it, so a request without a token still can't skip key checks.
STDIO_TRANSPORT = False


class LocalAccessKeyVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        key = store.verify_key(token)
        if not key:
            return None
        scopes = ["detour:read"] + (["detour:propose"] if key["can_propose"] else [])
        return AccessToken(token=token, client_id=key["id"], subject=key["id"], scopes=scopes, resource=MCP_RESOURCE,
                           claims={"name": key["name"]})


def current_key() -> dict:
    """The access key behind this call, read fresh so portal changes apply at once."""
    if AUTH_MODE != "key" or STDIO_TRANSPORT:
        return LOCAL_AGENT
    token = get_access_token()
    key = store.get_key(token.client_id) if token else None
    if key is None:
        raise ValueError("This access key was revoked. Create a new one in the Detour portal under Agent connections.")
    return key


def agent_errors(func):
    """Turn store errors into plain messages an agent can act on."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyError as exc:
            raise ValueError(f"{str(exc).strip(chr(39))}. Call find_routes or search_context to look it up.") from exc
        except (store.Forbidden, store.Conflict) as exc:
            raise ValueError(str(exc)) from exc
    return wrapper


options = {}
if AUTH_MODE == "key":
    options = {
        "token_verifier": LocalAccessKeyVerifier(),
        "auth": AuthSettings(issuer_url="http://127.0.0.1:3787", resource_server_url=MCP_RESOURCE,
                             required_scopes=["detour:read"], validate_token_resource=True),
    }

mcp = MCPServer(
    "Detour",
    instructions=(
        "Detour holds the user's context: rules, decisions, runbooks, prompts, notes and references, grouped into routes. "
        "At the start of a task call get_context with the folder, repo or topic you are working on. "
        "Global outranks Project, and Project outranks Sub-detour. "
        "When you learn something the user will want next time (a rule they repeat, a decision, a fix to an outdated runbook), "
        "call propose_change with a short reason. The user reviews proposals in the Detour portal; don't ask them to copy anything by hand."
    ),
    **options,
)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Not idempotent: on a route that auto-approves notes, sending the same note twice adds it twice.
PROPOSE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


@mcp.tool(annotations=READ)
@agent_errors
def find_routes(folder: str = "", repo: str = "", topic: str = "", file: str = "", url: str = "") -> list[dict]:
    """List the routes that apply to what you're working on. Pass any of: the working folder, the git remote URL,
    a few topic words, a file path, or an artifact URL. Global routes always apply. With nothing passed, lists every route."""
    if not any((folder, repo, topic, file, url)):
        return [{"id": r["id"], "name": r["name"], "scope": r["scope"], "description": r["description"], "paused": r["paused"],
                 "applies_to": [f"{t['kind']} {t['value']}" for t in r["targets"]]} for r in store.list_routes()]
    return store.find_routes(folder, repo, topic, file, url)


@mcp.tool(annotations=READ)
@agent_errors
def get_context(route: str = "", folder: str = "", repo: str = "", topic: str = "", file: str = "", url: str = "") -> dict:
    """Get the merged context to follow, highest authority first. Name a route (ID or name), or describe where you are
    working with folder, repo, topic, file or url and Detour picks the matching routes."""
    if not any((route, folder, repo, topic, file, url)):
        raise ValueError("Pass a route, or the folder, repo, topic, file or url you are working on.")
    if route:
        found = store.get_route(route)
        if found and found["paused"]:
            raise ValueError(f"Agent access to “{found['name']}” is paused by the user.")
    return store.get_context(route, folder, repo, topic, file, url)


@mcp.tool(annotations=READ)
@agent_errors
def search_context(query: str) -> list[dict]:
    """Search item titles and text across every route you can read. Returns short previews; use get_context for full text."""
    return store.search_items(query)


@mcp.tool(annotations=PROPOSE)
@agent_errors
def propose_change(
    action: str,
    reason: str,
    source: str = "",
    route: str = "",
    item_id: str = "",
    kind: str | None = None,
    title: str | None = None,
    body: str | None = None,
    to_route: str = "",
    name: str = "",
    scope: str = "project",
    parent: str = "",
    description: str = "",
    targets: list[dict] | None = None,
) -> dict:
    """Propose a change to the user's context. Nothing changes until the user approves it, unless the route auto-approves notes.

    action: "add" (needs route, kind, title, body), "edit" (needs item_id and any of kind, title, body),
    "retire" (needs item_id), "move" (needs item_id and to_route), or "create_route" (needs name; optional scope
    "project" or "sub" with parent, description, and targets like [{"kind": "folder", "value": "~/code/app"}]).
    kind is one of rule, decision, runbook, prompt, note, reference.
    reason: one or two sentences the user will read, saying why this is worth keeping.
    source: where it came from, such as the task or session name.

    The result's "outcome" is applied, waiting_for_review, already_pending, or previously_rejected (with the user's note).
    Don't propose a rejected change again unless the user asks."""
    payload: dict = {"action": action, "reason": reason, "source": source}
    if route:
        payload["route"] = route
    if item_id:
        payload["item_id"] = item_id
    for field, value in (("kind", kind), ("title", title), ("body", body)):
        if value is not None:
            payload[field] = value
    if action == "add":
        payload.setdefault("kind", "note")
    if to_route:
        payload["to_route"] = to_route
    if action == "create_route":
        payload.update({"name": name, "scope": scope, "parent": parent, "description": description, "targets": targets or []})
    result = store.propose(current_key(), payload)
    return {key: result[key] for key in ("id", "outcome", "status", "action", "route", "to_route", "proposed", "decision_note")}


@mcp.tool(annotations=READ)
@agent_errors
def get_proposal(proposal_id: str) -> dict:
    """Check one of your proposals: pending, approved, auto (applied by policy) or rejected, with the user's note if they left one."""
    key = current_key()
    result = store.get_proposal(proposal_id, key["id"])  # None for the local agent: it can see every proposal
    return {key_: result[key_] for key_ in ("id", "status", "action", "route", "proposed", "decision_note", "decided_at")}


@mcp.tool(annotations=READ)
@agent_errors
def list_my_proposals(status: str = "all") -> list[dict]:
    """List proposals made with this access key. status: pending, approved, rejected, auto, decided or all."""
    key = current_key()
    results = store.list_proposals(status, key["id"], limit=50)
    return [{k: r[k] for k in ("id", "status", "action", "route", "proposed", "decision_note", "created_at")} for r in results]


if __name__ == "__main__":
    store.init_db()
    if sys.argv[1:] == ["--stdio"]:
        if AUTH_MODE == "key" and not ALLOW_STDIO:
            raise SystemExit("stdio MCP is disabled while access-key authentication is enabled")
        STDIO_TRANSPORT = True
        mcp.run(transport="stdio")
    else:
        import uvicorn

        app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=mcp.settings.log_level.lower(), server_header=False)
