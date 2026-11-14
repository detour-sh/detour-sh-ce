import http.client
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import auth
import hosting
import server
import store

PUBLIC = "detour.example.com"
ORIGIN = f"https://{PUBLIC}"
PASSWORD = "correct horse battery"


class HostedTestCase(unittest.TestCase):
    hosted = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.previous = store.DATA_DIR, store.DB_PATH, server.WEB_DIR, server.HOSTING, server.ALLOW_PUBLIC_CLIENTS
        store.DATA_DIR = root
        store.DB_PATH = root / "detour.sqlite"
        web = root / "web"
        (web / "assets").mkdir(parents=True)
        (web / "index.html").write_text('<!doctype html><div id="root"></div>', encoding="utf-8")
        server.WEB_DIR = web
        server.reset_web_files()
        server.ALLOW_PUBLIC_CLIENTS = False
        env = {"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": ORIGIN, "DETOUR_MCP_PUBLIC_URL": "https://mcp.example.com/mcp"} if self.hosted else {}
        server.HOSTING = hosting.load(env)
        auth.THROTTLE.reset()
        store.init_db()
        self.start_server(server.DetourHandler)

    def start_server(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        store.DATA_DIR, store.DB_PATH, server.WEB_DIR, server.HOSTING, server.ALLOW_PUBLIC_CLIENTS = self.previous
        server.reset_web_files()
        self.tmp.cleanup()

    def call(self, path, method="GET", payload=None, host=PUBLIC, origin=ORIGIN, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        if method != "GET" and origin:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = f"{auth.SESSION_COOKIE}={cookie}"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        data = json.loads(text) if response.getheader("content-type", "").startswith("application/json") else text
        return response.status, data, response

    def set_up_owner(self):
        code = auth.issue_setup_code()
        status, data, response = self.call("/api/auth/setup", "POST", {
            "setup_code": code, "display_name": "Alex", "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(status, 201, data)
        return response.getheader("set-cookie").split(";", 1)[0].split("=", 1)[1]


class HostedModeTests(HostedTestCase):
    def test_status_before_setup(self):
        status, data, _ = self.call("/api/auth/status")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"hosted": True, "setup_required": True, "signed_in": False, "user_name": None})

    def test_every_data_route_is_locked_without_a_session(self):
        for method, path in (("GET", "/api/session"), ("GET", "/api/routes"), ("GET", "/api/export.json"), ("GET", "/api/keys"),
                             ("POST", "/api/keys"), ("POST", "/api/import"), ("POST", "/api/routes"), ("GET", "/api/events"),
                             ("GET", "/api/nope")):
            with self.subTest(path=path, method=method):
                status, data, _ = self.call(path, method, {} if method == "POST" else None)
                self.assertEqual(status, 401)
                self.assertTrue(data["setup_required"])
                self.assertNotIn("routes", data)

    def test_the_app_shell_and_health_stay_public(self):
        self.assertEqual(self.call("/health")[0], 200)
        status, html, _ = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("root", html)

    def test_other_hostnames_are_refused(self):
        for host in ("evil.example.com", f"{PUBLIC}:9999", f"{PUBLIC}.evil.com", f"x@{PUBLIC}", ""):
            with self.subTest(host=host):
                self.assertEqual(self.call("/health", host=host)[0], 400)

    def test_health_check_on_loopback_still_works(self):
        self.assertEqual(self.call("/health", host=f"127.0.0.1:{self.port}")[0], 200)

    def test_setup_signs_in_and_cookie_is_locked_down(self):
        code = auth.issue_setup_code()
        status, _, _ = self.call("/api/auth/setup", "POST", {"setup_code": "WRONGWRONG00", "display_name": "A", "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(status, 403)
        status, data, response = self.call("/api/auth/setup", "POST", {
            "setup_code": code, "display_name": "Alex", "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(status, 201)
        self.assertTrue(data["signed_in"])
        cookie = response.getheader("set-cookie")
        for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(flag, cookie)
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(self.call("/api/routes", cookie=token)[0], 200)
        self.assertEqual(self.call("/api/routes", cookie="bogus")[0], 401)
        status, data, _ = self.call("/api/auth/status", cookie=token)
        self.assertEqual((data["signed_in"], data["user_name"], data["setup_required"]), (True, "Alex", False))

    def test_setup_is_closed_once_an_owner_exists(self):
        self.set_up_owner()
        status, _, _ = self.call("/api/auth/setup", "POST", {"setup_code": "x"})
        self.assertEqual(status, 409)

    def test_sign_in_sign_out(self):
        self.set_up_owner()
        status, _, _ = self.call("/api/auth/sign-in", "POST", {"password": "wrong wrong wrong"})
        self.assertEqual(status, 401)
        status, _, response = self.call("/api/auth/sign-in", "POST", {"password": PASSWORD})
        self.assertEqual(status, 200)
        token = response.getheader("set-cookie").split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(self.call("/api/routes", cookie=token)[0], 200)
        status, _, response = self.call("/api/auth/sign-out", "POST", {}, cookie=token)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", response.getheader("set-cookie"))
        self.assertEqual(self.call("/api/routes", cookie=token)[0], 401)

    def test_lockout_returns_429_with_retry_after(self):
        self.set_up_owner()
        for _ in range(auth.SIGN_IN_FREE_ATTEMPTS):
            self.call("/api/auth/sign-in", "POST", {"password": "wrong wrong wrong"})
        status, _, response = self.call("/api/auth/sign-in", "POST", {"password": PASSWORD})
        self.assertEqual(status, 429)
        self.assertGreater(int(response.getheader("retry-after")), 0)

    def test_writes_must_come_from_the_https_public_origin(self):
        token = self.set_up_owner()
        for origin in ("https://evil.example.com", f"http://{PUBLIC}", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.call("/api/routes", "POST", {"name": "x", "scope": "global"}, origin=origin, cookie=token)[0], 403)
        status, _, _ = self.call("/api/routes", "POST", {"name": "ok", "scope": "global"}, cookie=token)
        self.assertEqual(status, 201)

    def test_public_internet_clients_reach_only_the_login(self):
        # Behind a load balancer every client is "public" to the container; the login is the gate.
        class Remote(server.DetourHandler):
            client_address = property(lambda self: ("93.184.216.34", 40000), lambda self, value: None)

        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.start_server(Remote)
        self.assertEqual(self.call("/api/auth/status")[0], 200)
        self.assertEqual(self.call("/api/routes")[0], 401)

    def test_change_password_over_http(self):
        token = self.set_up_owner()
        status, _, response = self.call("/api/auth/sign-in", "POST", {"password": PASSWORD})
        other = response.getheader("set-cookie").split(";", 1)[0].split("=", 1)[1]
        body = {"current_password": PASSWORD, "new_password": "a brand new passphrase", "new_password_confirm": "a brand new passphrase"}
        # Not signed in: refused, and nothing leaks.
        self.assertEqual(self.call("/api/auth/password", "POST", body)[0], 401)
        # Wrong current password.
        self.assertEqual(self.call("/api/auth/password", "POST", {**body, "current_password": "not the password at all"}, cookie=token)[0], 403)
        # A bad origin is refused like any other write.
        self.assertEqual(self.call("/api/auth/password", "POST", body, origin="https://evil.example.com", cookie=token)[0], 403)
        status, data, _ = self.call("/api/auth/password", "POST", body, cookie=token)
        self.assertEqual(status, 200)
        self.assertTrue(data["signed_in"])
        self.assertEqual(self.call("/api/routes", cookie=token)[0], 200)  # this browser stays signed in
        self.assertEqual(self.call("/api/routes", cookie=other)[0], 401)  # every other browser is signed out
        self.assertEqual(self.call("/api/auth/sign-in", "POST", {"password": "a brand new passphrase"})[0], 200)

    def test_change_password_validates_the_new_one(self):
        token = self.set_up_owner()
        body = {"current_password": PASSWORD, "new_password": "short", "new_password_confirm": "short"}
        status, data, _ = self.call("/api/auth/password", "POST", body, cookie=token)
        self.assertEqual(status, 400)
        self.assertIn("at least", data["error"])

    def test_https_is_pinned_with_hsts_and_pages_are_not_cached(self):
        _, _, response = self.call("/health")
        self.assertIn("max-age", response.getheader("strict-transport-security"))
        self.assertEqual(response.getheader("cache-control"), "no-store")

    def test_session_reports_the_public_mcp_address(self):
        token = self.set_up_owner()
        _, data, _ = self.call("/api/session", cookie=token)
        self.assertEqual(data["mcp_endpoint"], "https://mcp.example.com/mcp")
        self.assertTrue(data["hosted"])


class LocalModeUnchangedTests(HostedTestCase):
    hosted = False

    def test_no_sign_in_in_local_mode(self):
        host = f"127.0.0.1:{self.port}"
        status, data, response = self.call("/api/auth/status", host=host)
        self.assertEqual(data, {"hosted": False, "setup_required": False, "signed_in": True, "user_name": "Local user"})
        self.assertIsNone(response.getheader("strict-transport-security"))
        self.assertEqual(self.call("/api/routes", host=host)[0], 200)
        for action in ("setup", "sign-in", "sign-out", "password"):
            self.assertEqual(self.call(f"/api/auth/{action}", "POST", {}, host=host, origin=f"http://{host}")[0], 404)

    def test_the_public_hostname_is_not_accepted_in_local_mode(self):
        self.assertEqual(self.call("/health", host=PUBLIC)[0], 400)


class CommandTests(HostedTestCase):
    def run_command(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = server.command(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_setup_code_and_reset(self):
        code, out, _ = self.run_command("setup-code")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}")
        self.set_up_owner()
        code, _, err = self.run_command("setup-code")
        self.assertEqual(code, 1)
        self.assertIn("already set up", err)
        code, _, err = self.run_command("reset-owner")
        self.assertEqual(code, 1)  # needs --yes
        self.assertTrue(auth.owner_exists())
        code, out, _ = self.run_command("reset-owner", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(auth.owner_exists())
        self.assertRegex(out, r"[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}")

    def test_commands_refuse_to_run_in_local_mode(self):
        server.HOSTING = hosting.load({})
        code, _, err = self.run_command("setup-code")
        self.assertEqual(code, 1)
        self.assertIn("hosted mode", err)


if __name__ == "__main__":
    unittest.main()
