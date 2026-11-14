import asyncio
import contextlib
import io
import json
import socket
import tempfile
import threading
import time
import os
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock
from urllib.request import Request, urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import access
import server
import store

TEAM = "acme.cloudflareaccess.com"
PORTAL_AUD = "portal-aud-tag"
MCP_AUD = "mcp-aud-tag"
GOOD_ENV = {
    "DETOUR_ACCESS_MODE": "proxy",
    "DETOUR_PUBLIC_URL": "https://detour.example.com",
    "DETOUR_MCP_PUBLIC_URL": "https://detour-mcp.example.com",
    "DETOUR_ACCESS_TEAM_DOMAIN": TEAM,
    "DETOUR_ACCESS_AUD": PORTAL_AUD,
    "DETOUR_MCP_ACCESS_AUD": MCP_AUD,
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(aud=PORTAL_AUD, key=KEY, iss=f"https://{TEAM}", exp_in=300, **claims):
    now = int(time.time())
    payload = {"aud": [aud], "iss": iss, "iat": now, "exp": now + exp_in, "email": "ben@example.com", **claims}
    return jwt.encode(payload, key, algorithm="RS256")


def verifier(aud=PORTAL_AUD, emails=frozenset()):
    return access.AccessVerifier(TEAM, aud, emails, key_resolver=lambda token: KEY.public_key())


class ConfigTests(unittest.TestCase):
    def test_default_is_local(self):
        config = access.load_config({})
        self.assertFalse(config.hosted)

    def test_valid_proxy_config(self):
        config = access.load_config(GOOD_ENV)
        self.assertTrue(config.hosted)
        self.assertEqual(config.portal_host, "detour.example.com")
        self.assertEqual(config.portal_origin, "https://detour.example.com")

    def test_unsafe_or_incomplete_proxy_config_is_refused(self):
        bad = [
            {"DETOUR_ACCESS_MODE": "open"},
            {"DETOUR_PUBLIC_URL": ""},
            {"DETOUR_PUBLIC_URL": "http://detour.example.com"},
            {"DETOUR_PUBLIC_URL": "https://detour.example.com/path"},
            {"DETOUR_MCP_PUBLIC_URL": "https://detour.example.com"},  # same host as the portal
            {"DETOUR_ACCESS_TEAM_DOMAIN": ""},
            {"DETOUR_ACCESS_TEAM_DOMAIN": "https://acme.cloudflareaccess.com"},
            {"DETOUR_ACCESS_AUD": ""},
            {"DETOUR_MCP_ACCESS_AUD": ""},
            {"DETOUR_MCP_AUTH_MODE": "none"},
            {"DETOUR_MCP_ALLOW_STDIO": "true"},
        ]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(access.ConfigError):
                access.load_config({**GOOD_ENV, **override})


class VerifierTests(unittest.TestCase):
    def test_accepts_a_valid_token(self):
        self.assertEqual(verifier().verify(make_token())["email"], "ben@example.com")

    def test_rejects_bad_tokens(self):
        cases = {
            "missing": None,
            "garbage": "not-a-jwt",
            "wrong audience": make_token(aud="someone-else"),
            "wrong issuer": make_token(iss="https://evil.example.com"),
            "expired": make_token(exp_in=-3600),
            "wrong signing key": make_token(key=OTHER_KEY),
            "unsigned": jwt.encode({"aud": [PORTAL_AUD], "iss": f"https://{TEAM}", "iat": 1, "exp": 9999999999}, None, algorithm="none"),
        }
        for name, token in cases.items():
            with self.subTest(name), self.assertRaises(access.AccessDenied):
                verifier().verify(token)

    def test_email_allowlist(self):
        allowed = verifier(emails=frozenset({"ben@example.com", "@corp.example"}))
        allowed.verify(make_token())
        allowed.verify(make_token(email="sam@corp.example"))
        with self.assertRaises(access.AccessDenied):
            allowed.verify(make_token(email="mallory@evil.example"))
        with self.assertRaises(access.AccessDenied):
            allowed.verify(make_token(email=""))  # a service token has no email

    def test_mcp_verifier_ignores_the_portal_email_allowlist(self):
        config = access.load_config({**GOOD_ENV, "DETOUR_ACCESS_ALLOWED_EMAILS": "ben@example.com"})
        mcp = access.mcp_verifier(config, key_resolver=lambda token: KEY.public_key())
        mcp.verify(make_token(aud=MCP_AUD, email="", common_name="agent-token"))

    def test_portal_token_is_not_accepted_by_mcp(self):
        config = access.load_config(GOOD_ENV)
        mcp = access.mcp_verifier(config, key_resolver=lambda token: KEY.public_key())
        with self.assertRaises(access.AccessDenied):
            mcp.verify(make_token(aud=PORTAL_AUD))


class MiddlewareTests(unittest.TestCase):
    def call(self, token):
        sent, reached = [], []

        async def app(scope, receive, send):
            reached.append(True)

        async def send(message):
            sent.append(message)

        headers = [(b"cf-access-jwt-assertion", token.encode())] if token else []
        asyncio.run(access.AccessMiddleware(app, verifier(aud=MCP_AUD))({"type": "http", "headers": headers}, None, send))
        return sent, reached

    def test_blocks_without_a_token(self):
        sent, reached = self.call(None)
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(reached)

    def test_blocks_the_wrong_audience(self):
        sent, reached = self.call(make_token(aud=PORTAL_AUD))
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(reached)

    def test_passes_a_valid_token(self):
        sent, reached = self.call(make_token(aud=MCP_AUD))
        self.assertTrue(reached)
        self.assertEqual(sent, [])


class TransportSecurityTests(unittest.TestCase):
    def test_local_and_hosted_rules(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("the mcp package is not installed")
        local = access.mcp_transport_security(access.load_config({}))
        self.assertTrue(local.enable_dns_rebinding_protection)
        self.assertIn("127.0.0.1:*", local.allowed_hosts)
        hosted = access.mcp_transport_security(access.load_config(GOOD_ENV))
        self.assertTrue(hosted.enable_dns_rebinding_protection)
        self.assertIn("detour-mcp.example.com", hosted.allowed_hosts)
        self.assertNotIn("127.0.0.1:*", hosted.allowed_hosts)


class PortalFixture(unittest.TestCase):
    config = access.load_config({})
    portal_verifier = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.previous = store.DATA_DIR, store.DB_PATH, server.WEB_DIR, server.ACCESS, server.VERIFIER
        store.DATA_DIR = root
        store.DB_PATH = root / "detour.sqlite"
        web = root / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
        server.WEB_DIR = web
        server.reset_web_files()
        server.ACCESS = self.config
        server.VERIFIER = self.portal_verifier
        store.init_db()
        self.httpd = server.PortalServer(("127.0.0.1", 0), server.DetourHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        store.DATA_DIR, store.DB_PATH, server.WEB_DIR, server.ACCESS, server.VERIFIER = self.previous
        server.reset_web_files()
        self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request_headers = {"Content-Type": "application/json"} if body is not None else {}
        request_headers.update(headers or {})
        try:
            response = urlopen(Request(self.base + path, data=body, headers=request_headers, method=method), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            text = response.read().decode(errors="replace")
            data = json.loads(text) if response.headers.get("content-type", "").startswith("application/json") else text
            return response.status, data, response.headers


class HostedPortalTests(PortalFixture):
    config = access.load_config(GOOD_ENV)
    portal_verifier = verifier()
    HOST = {"Host": "detour.example.com"}

    def signed_in(self, **extra):
        return {**self.HOST, "Cf-Access-Jwt-Assertion": make_token(), **extra}

    def test_wrong_host_is_refused(self):
        status, _, _ = self.request("/api/session", headers={"Host": "evil.example.com", "Cf-Access-Jwt-Assertion": make_token()})
        self.assertEqual(status, 400)

    def test_loopback_host_is_not_accepted_when_hosted(self):
        status, _, _ = self.request("/api/session", headers={"Cf-Access-Jwt-Assertion": make_token()})
        self.assertEqual(status, 400)

    def test_api_needs_a_valid_token(self):
        for path in ("/api/session", "/api/routes", "/api/export.json", "/api/keys", "/", "/routes"):
            with self.subTest(path):
                self.assertEqual(self.request(path, headers=self.HOST)[0], 401)
        bad = {**self.HOST, "Cf-Access-Jwt-Assertion": make_token(aud="other")}
        self.assertEqual(self.request("/api/routes", headers=bad)[0], 401)

    def test_health_stays_open_for_the_tunnel_probe(self):
        status, data, _ = self.request("/health", headers=self.HOST)
        self.assertEqual((status, data["ok"]), (200, True))

    def test_container_healthcheck_works_from_loopback_but_nothing_else_does(self):
        loopback = {"Host": f"127.0.0.1:{self.httpd.server_port}"}
        self.assertEqual(self.request("/health", headers=loopback)[0], 200)
        self.assertEqual(self.request("/api/routes", headers=loopback)[0], 400)

    def test_signed_in_user_is_named_and_hsts_is_set(self):
        status, data, headers = self.request("/api/session", headers=self.signed_in())
        self.assertEqual(status, 200)
        self.assertEqual(data["user_name"], "ben@example.com")
        self.assertEqual(data["mcp_endpoint"], "https://detour-mcp.example.com/mcp")
        self.assertIn("max-age", headers["strict-transport-security"])
        self.assertNotIn("data_path", data)

    def test_writes_need_the_public_origin(self):
        route = {"name": "app"}
        self.assertEqual(self.request("/api/routes", "POST", route, self.signed_in(Origin="https://evil.example.com"))[0], 403)
        self.assertEqual(self.request("/api/routes", "POST", route, self.signed_in(Origin="http://detour.example.com"))[0], 403)
        self.assertEqual(self.request("/api/routes", "POST", route, self.signed_in(Origin="https://detour.example.com"))[0], 201)

    def test_actor_in_activity_is_the_signed_in_user(self):
        self.request("/api/routes", "POST", {"name": "app"}, self.signed_in(Origin="https://detour.example.com"))
        _, data, _ = self.request("/api/events", headers=self.signed_in())
        self.assertEqual(data["events"][0]["actor"], "ben@example.com")


class LocalPortalTests(PortalFixture):
    def test_local_mode_needs_no_token_and_sends_no_hsts(self):
        status, data, headers = self.request("/api/session", headers={"Host": f"127.0.0.1:{self.httpd.server_port}"})
        self.assertEqual(status, 200)
        self.assertNotIn("strict-transport-security", headers)
        self.assertEqual(data["user_name"], server.USER_NAME)

    def test_import_is_size_limited(self):
        routes = [{"id": str(n), "name": f"r{n}"} for n in range(store.MAX_IMPORT_ROUTES + 1)]
        status, data, _ = self.request("/api/import", "POST", {"format": store.EXPORT_FORMAT, "routes": routes, "items": [], "targets": []},
                                       {"Host": f"127.0.0.1:{self.httpd.server_port}"})
        self.assertEqual(status, 400)
        self.assertIn("too many", data["error"])


class ServerHardeningTests(unittest.TestCase):
    def test_slow_clients_time_out(self):
        self.assertTrue(0 < server.DetourHandler.timeout <= 30)

    def test_connection_cap_returns_503(self):
        class Capped(server.PortalServer):
            max_connections = 1

        httpd = Capped(("127.0.0.1", 0), server.DetourHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_port
            first = socket.create_connection(("127.0.0.1", port))
            first.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % port)  # headers never finished: holds the slot
            time.sleep(0.3)
            second = socket.create_connection(("127.0.0.1", port))
            second.settimeout(3)
            self.assertTrue(second.recv(100).startswith(b"HTTP/1.1 503"))
            second.close()
            first.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_request_log_drops_query_strings(self):
        handler = server.DetourHandler.__new__(server.DetourHandler)
        handler.client_address = ("127.0.0.1", 1)
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer), mock.patch.dict(os.environ, {"DETOUR_QUIET_HTTP": "false"}):
            handler.log_message('"%s" %s %s', "GET /api/events?token=secret HTTP/1.1", "200", "-")
        self.assertIn("GET /api/events HTTP/1.1", buffer.getvalue())
        self.assertNotIn("secret", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
