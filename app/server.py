"""Detour portal: a JSON API for the web UI, plus the built UI itself.

The portal is the user's side of Detour. Everything it can do, it does through
the same functions in store.py that the MCP server uses, so the UI and agents
never drift apart.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import store

VERSION = "0.2.0"
APP_NAME = "detour"
PORT = int(os.environ.get("PORT", "3787"))
MCP_PORT = int(os.environ.get("DETOUR_MCP_PORT", "3788"))
USER_NAME = os.environ.get("DETOUR_USER_NAME", "Local user").strip() or "Local user"
WEB_DIR = Path(os.environ.get("DETOUR_WEB_DIR", str(Path(__file__).with_name("web") / "dist")))
BRAND_DIR = Path(__file__).with_name("brand")
BRAND_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/assets/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/assets/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
MAX_BODY_BYTES = 2_000_000
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "detour"}

# Paths the single-page app handles. Anything else that isn't a file or the API is a 404.
APP_PATHS = re.compile(r"^/(?:|review|routes|routes/[A-Za-z0-9_-]{1,64}|activity|connections|settings)$")

SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}

init_db = store.init_db

_web_files: dict[str, Path] | None = None


def web_files() -> dict[str, Path]:
    """Map each built UI file to its URL path. Built once; only listed files are ever served."""
    global _web_files
    if _web_files is None:
        files: dict[str, Path] = {}
        if WEB_DIR.is_dir():
            for path in WEB_DIR.rglob("*"):
                if path.is_file():
                    files["/" + path.relative_to(WEB_DIR).as_posix()] = path
        _web_files = files
    return _web_files


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain; charset=utf-8",
}


def content_type_for(path: str) -> str:
    # Explicit, so the answer doesn't depend on the host's MIME registry.
    return CONTENT_TYPES.get(Path(path).suffix.lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def reset_web_files() -> None:
    global _web_files
    _web_files = None


def session_info() -> dict:
    counts = store.proposal_counts()
    return {
        "user_name": USER_NAME,
        "version": VERSION,
        "nested_enabled": store.NESTED_DETOURS_ENABLED,
        "mcp_endpoint": f"http://127.0.0.1:{MCP_PORT}/mcp",
        "mcp_auth": os.environ.get("DETOUR_MCP_AUTH_MODE", "key").strip().lower(),
        "pending_count": counts["pending"],
        "data_path": str(store.DB_PATH),
    }


class DetourHandler(BaseHTTPRequestHandler):
    server_version = "detour"
    sys_version = ""

    def version_string(self) -> str:
        return "detour"

    def log_message(self, format: str, *args) -> None:  # keep request lines, drop query strings
        if os.environ.get("DETOUR_QUIET_HTTP") != "true":
            super().log_message(format, *args)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    # ------------------------------------------------------------ dispatch

    def dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if len(path) > 1:
            path = path.rstrip("/")
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        try:
            if not self.valid_host():
                return self.json({"error": "invalid host"}, HTTPStatus.BAD_REQUEST)
            if method in ("POST", "PUT"):
                if not self.valid_write_origin():
                    return self.json({"error": "cross-origin write denied"}, HTTPStatus.FORBIDDEN)
                if self.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return self.json({"error": "application/json required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            if path.startswith("/api/"):
                return self.api(method, path, params)
            if method != "GET":
                return self.json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)
            if path == "/health":
                return self.json({"ok": True, "service": APP_NAME, "version": VERSION})
            if path in BRAND_FILES:
                name, content_type = BRAND_FILES[path]
                return self.file(BRAND_DIR / name, content_type, "public, max-age=86400")
            files = web_files()
            if path in files and path != "/index.html":
                cache = "public, max-age=31536000, immutable" if path.startswith("/assets/") else "no-cache"
                return self.file(files[path], content_type_for(path), cache)
            if APP_PATHS.match(path):
                index = files.get("/index.html")
                if index is None:
                    return self.text("The portal UI isn't built. Run npm run build in app/web, or use the Docker image.",
                                     "text/plain; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE)
                return self.file(index, "text/html; charset=utf-8", "no-cache")
            return self.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except store.Forbidden as exc:
            self.json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except store.Conflict as exc:
            self.json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except RequestTooLarge:
            self.json({"error": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except KeyError as exc:
            self.json({"error": str(exc).strip("'\"")}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001 - last line of defense; details go to the log only
            print(f"request failed: {type(exc).__name__}: {exc}", flush=True)
            self.json({"error": "internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def api(self, method: str, path: str, params: dict) -> None:
        parts = path.removeprefix("/api/").split("/")
        head, rest = parts[0], parts[1:]
        body = self.body_json() if method in ("POST", "PUT") else {}
        created = HTTPStatus.CREATED

        if head == "session" and not rest and method == "GET":
            return self.json(session_info())

        if head == "routes":
            if not rest:
                if method == "GET":
                    return self.json({"routes": store.list_routes()})
                if method == "POST":
                    return self.json({"route": store.create_route(body)}, created)
            if rest == ["reorder"] and method == "POST":
                return self.json({"routes": store.reorder_routes(body.get("order"))})
            route_id = rest[0] if rest else ""
            tail = rest[1:]
            if not tail:
                if method == "GET":
                    route = store.get_route(route_id)
                    if route is None:
                        raise KeyError("route not found")
                    return self.json({"route": route, "items": store.list_items(route["id"])})
                if method == "PUT":
                    return self.json({"route": store.update_route(route_id, body)})
            if tail == ["delete"] and method == "POST":
                store.delete_route(route_id)
                return self.json({"ok": True})
            if tail == ["items"] and method == "POST":
                return self.json({"item": store.create_item(route_id, body)}, created)
            if tail == ["targets"] and method == "POST":
                return self.json({"target": store.add_target(route_id, body)}, created)
            if tail == ["context"] and method == "GET":
                return self.json(store.get_context(route_ref=route_id, mark_used=False))
            if tail == ["events"] and method == "GET":
                return self.json({"events": store.list_events(route_id, int(params.get("limit", "100")))})

        if head == "items" and len(rest) >= 1:
            item_id, tail = rest[0], rest[1:]
            if not tail and method == "PUT":
                return self.json({"item": store.update_item(item_id, body)})
            if tail == ["retire"] and method == "POST":
                return self.json({"item": store.set_item_status(item_id, "retired")})
            if tail == ["restore"] and method == "POST":
                return self.json({"item": store.set_item_status(item_id, "active")})
            if tail == ["move"] and method == "POST":
                return self.json({"item": store.move_item(item_id, str(body.get("route_id", "")))})
            if tail == ["delete"] and method == "POST":
                store.delete_item(item_id)
                return self.json({"ok": True})

        if head == "targets" and len(rest) == 2 and rest[1] == "remove" and method == "POST":
            store.remove_target(rest[0])
            return self.json({"ok": True})

        if head == "proposals":
            if not rest and method == "GET":
                return self.json({"proposals": store.list_proposals(params.get("status", "pending")), "counts": store.proposal_counts()})
            if len(rest) == 2 and method == "POST":
                proposal_id, verb = rest
                if verb == "approve":
                    return self.json({"proposal": store.approve_proposal(proposal_id, body.get("changes") or {})})
                if verb == "reject":
                    return self.json({"proposal": store.reject_proposal(proposal_id, str(body.get("note", "")))})
                if verb == "reopen":
                    return self.json({"proposal": store.reopen_proposal(proposal_id)})

        if head == "events":
            if not rest and method == "GET":
                return self.json({"events": store.list_events(None, int(params.get("limit", "100")))})
            if len(rest) == 2 and rest[1] == "undo" and method == "POST":
                return self.json(store.undo_event(rest[0]))

        if head == "keys":
            if not rest:
                if method == "GET":
                    return self.json({"access_keys": store.list_keys()})
                if method == "POST":
                    return self.json(store.create_key(body), created)
            if len(rest) == 1 and method == "PUT":
                return self.json({"access_key": store.update_key(rest[0], body)})
            if len(rest) == 2 and rest[1] == "revoke" and method == "POST":
                return self.json({"access_key": store.revoke_key(rest[0])})

        if head == "export.json" and not rest and method == "GET":
            return self.json(store.export_all(), headers={"content-disposition": 'attachment; filename="detour-export.json"'})
        if head == "import" and not rest and method == "POST":
            return self.json(store.import_all(body), created)

        return self.json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------ request checks

    def valid_host(self) -> bool:
        host = self.headers.get("host", "")
        if not host or "@" in host or "," in host:
            return False
        try:
            parsed = urlparse(f"//{host}")
            return parsed.hostname in ALLOWED_HOSTS and parsed.port == self.server.server_port
        except ValueError:
            return False

    def valid_write_origin(self) -> bool:
        if self.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        origin = self.headers.get("origin")
        if not origin:
            return True
        return origin == f"http://{self.headers['host']}"

    def body_json(self) -> dict:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise RequestTooLarge()
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    # ------------------------------------------------------------ responses

    def send(self, raw: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, cache: str = "no-store", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", cache)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK, headers: dict | None = None) -> None:
        self.send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status, headers=headers)

    def text(self, payload: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send(payload.encode("utf-8"), content_type, status)

    def file(self, path: Path, content_type: str, cache: str) -> None:
        self.send(path.read_bytes(), content_type, cache=cache)


class RequestTooLarge(Exception):
    pass


def main() -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DetourHandler)
    print(f"Detour portal running on http://127.0.0.1:{PORT} with data at {store.DB_PATH}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
