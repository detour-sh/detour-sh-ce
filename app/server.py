"""Detour portal: a JSON API for the web UI, plus the built UI itself.

The portal is the user's side of Detour. Everything it can do, it does through
the same functions in store.py that the MCP server uses, so the UI and agents
never drift apart.

Only the built UI files, /health and the sign-in calls are public. Every other API call
needs the session cookie that /api/signin sets. See security.py and docs/security.md.
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

import security
import store

VERSION = "0.2.0"
APP_NAME = "detour"
USER_NAME = os.environ.get("DETOUR_USER_NAME", "Local user").strip() or "Local user"
WEB_DIR = Path(os.environ.get("DETOUR_WEB_DIR", str(Path(__file__).with_name("web") / "dist")))
BRAND_DIR = Path(__file__).with_name("brand")
BRAND_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/assets/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/assets/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
MAX_BODY_BYTES = 2_000_000

# Replaced by configure() when the server starts. Until then nothing private is served.
SETTINGS = security.Settings.from_env({})
SIGN_IN: security.SignIn | None = None


def configure(settings: security.Settings, sign_in: security.SignIn | None) -> None:
    global SETTINGS, SIGN_IN
    SETTINGS, SIGN_IN = settings, sign_in

# Paths the single-page app handles. Anything else that isn't a file or the API is a 404.
APP_PATHS = re.compile(r"^/(?:|review|routes|routes/[A-Za-z0-9_-]{1,64}|checks|activity|connections|settings)$")

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
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}
HSTS = "max-age=31536000"

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


def session_info(signed_in: bool) -> dict:
    """What the UI needs on load. A visitor who hasn't signed in learns only that they must."""
    if not signed_in:
        return {"signed_in": False, "cloud": SETTINGS.cloud}
    counts = store.proposal_counts()
    return {
        "signed_in": True,
        "cloud": SETTINGS.cloud,
        "user_name": USER_NAME,
        "version": VERSION,
        "nested_enabled": store.NESTED_DETOURS_ENABLED,
        "mcp_endpoint": SETTINGS.mcp_url,
        "mcp_auth": SETTINGS.mcp_auth,
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
            # Platform health probes often send the container's own address as the host.
            if path != "/health" and not self.valid_host():
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
                return self.json({"ok": True, "service": APP_NAME})
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

        # The only calls that work without a session.
        if head == "session" and not rest and method == "GET":
            return self.json(session_info(self.authenticated()))
        if head == "signin" and not rest and method == "POST":
            return self.sign_in(self.body_json())
        if head == "signout" and not rest and method == "POST":
            return self.sign_out()
        if not self.authenticated():
            return self.json({"error": "sign in required"}, HTTPStatus.UNAUTHORIZED)

        body = self.body_json() if method in ("POST", "PUT") else {}
        created = HTTPStatus.CREATED

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
            if tail == ["checks"] and method == "GET":
                return self.json(store.plan_checks(route_ref=route_id))
            if tail == ["skips"] and method == "POST":
                return self.json({"skip": store.add_skip(route_id, body)}, created)
            if tail == ["includes"] and method == "POST":
                return self.json({"route": store.add_include(route_id, body)}, created)
            if len(tail) == 3 and tail[0] == "includes" and tail[2] == "remove" and method == "POST":
                return self.json({"route": store.remove_include(route_id, tail[1])})
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

        if head == "checks" and not rest and method == "GET":
            return self.json({"checks": store.list_checks()})
        if head == "skips" and len(rest) == 2 and rest[1] == "remove" and method == "POST":
            store.remove_skip(rest[0])
            return self.json({"ok": True})
        if head == "check-runs" and method == "GET":
            if not rest:
                return self.json({"runs": store.list_check_runs(int(params.get("limit", "50")))})
            if len(rest) == 1:
                return self.json({"run": store.get_check_run(rest[0])})

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

    # ------------------------------------------------------------ sign-in

    def authenticated(self) -> bool:
        return SIGN_IN is not None and SIGN_IN.valid(SIGN_IN.read_cookie(self.headers.get("cookie")))

    def sign_in(self, body: dict) -> None:
        if SIGN_IN is None:
            return self.json({"error": "sign-in is not set up"}, HTTPStatus.UNAUTHORIZED)
        wait = SIGN_IN.retry_after()
        if wait:
            return self.json({"error": f"too many attempts. Try again in {wait} seconds"}, HTTPStatus.TOO_MANY_REQUESTS,
                             headers={"retry-after": str(wait)})
        if not SIGN_IN.check_token(body.get("token")):
            SIGN_IN.record_failure()
            return self.json({"error": "that token isn't right"}, HTTPStatus.UNAUTHORIZED)
        self.json({"ok": True}, headers={"set-cookie": SIGN_IN.set_cookie(SIGN_IN.issue())})

    def sign_out(self) -> None:
        if SIGN_IN is None:
            return self.json({"ok": True})
        SIGN_IN.revoke(SIGN_IN.read_cookie(self.headers.get("cookie")))
        self.json({"ok": True}, headers={"set-cookie": SIGN_IN.clear_cookie()})

    # ------------------------------------------------------------ request checks

    def valid_host(self) -> bool:
        host = self.headers.get("host", "").lower()
        if not host or "@" in host or "," in host:
            return False
        return host in SETTINGS.local_hosts(self.server.server_port) or host == SETTINGS.public_host

    def valid_write_origin(self) -> bool:
        if self.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        origin = self.headers.get("origin")
        if not origin:
            return True
        allowed = {f"http://{host}" for host in SETTINGS.local_hosts(self.server.server_port)}
        if SETTINGS.public_url:
            allowed.add(SETTINGS.public_url)
        return origin.lower() in allowed

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
        if SETTINGS.cloud:
            self.send_header("strict-transport-security", HSTS)
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
    try:
        settings = security.Settings.from_env()
        token = security.load_portal_token(settings, os.environ, store.DATA_DIR)
    except security.ConfigError as exc:
        raise SystemExit(f"Detour can't start: {exc}") from exc
    configure(settings, security.SignIn(token, settings))
    init_db()
    server = ThreadingHTTPServer((settings.bind_host, settings.portal_port), DetourHandler)
    where = settings.public_url or f"http://127.0.0.1:{settings.portal_port}"
    print(f"Detour portal running on {where} with data at {store.DB_PATH}", flush=True)
    if not os.environ.get("DETOUR_PORTAL_TOKEN", "").strip():
        print("Sign in with the token in /data/portal-token. Show it with: docker compose exec detour cat /data/portal-token", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
