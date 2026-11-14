import http.client
import importlib
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import store

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:  # the MCP library is only needed for these tests
    HAVE_MCP = False


@unittest.skipUnless(HAVE_MCP, "the mcp package is not installed")
class McpToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR, store.DB_PATH
        store.DATA_DIR = Path(self.tmp.name)
        store.DB_PATH = store.DATA_DIR / "detour.sqlite"
        store.init_db()
        with mock.patch.dict(os.environ, {"DETOUR_MCP_AUTH_MODE": "key"}):
            import mcp_server
            self.mcp = importlib.reload(mcp_server)
        self.key = store.create_key({"name": "Claude Code"})
        self.glob = store.create_route({"name": "Global", "scope": "global"})
        self.app = store.create_route({"name": "app", "policy": "notes", "targets": [{"kind": "folder", "value": "~/code/app"}]})
        store.create_item(self.glob["id"], {"kind": "rule", "title": "Keep ports local"})

    def tearDown(self):
        store.DATA_DIR, store.DB_PATH = self.previous
        self.tmp.cleanup()

    def as_key(self, key_id):
        token = mock.Mock(client_id=key_id)
        return mock.patch.object(self.mcp, "get_access_token", return_value=token)

    def test_read_tools(self):
        self.assertEqual([r["name"] for r in self.mcp.find_routes(folder="/home/me/code/app")], ["Global", "app"])
        self.assertEqual(len(self.mcp.find_routes()), 2)
        context = self.mcp.get_context(folder="/home/me/code/app")
        self.assertEqual(context["layers"][0]["items"][0]["title"], "Keep ports local")
        self.assertEqual(self.mcp.search_context("ports")[0]["route"], "Global")
        with self.assertRaises(ValueError):
            self.mcp.get_context()
        store.update_route(self.app["id"], {"paused": True})
        with self.assertRaisesRegex(ValueError, "paused"):
            self.mcp.get_context(route="app")

    def test_propose_and_check_proposals(self):
        key_id = self.key["access_key"]["id"]
        with self.as_key(key_id):
            note = self.mcp.propose_change("add", "seen twice", route="app", title="WSL", body="needs WSL 2")
            rule = self.mcp.propose_change("add", "asked", route="app", kind="rule", title="Sign releases")
            self.assertEqual(note["outcome"], "applied")
            self.assertEqual(rule["outcome"], "waiting_for_review")
            self.assertEqual(self.mcp.get_proposal(rule["id"])["status"], "pending")
            self.assertEqual(len(self.mcp.list_my_proposals()), 2)
            with self.assertRaisesRegex(ValueError, "route not found"):
                self.mcp.propose_change("add", "r", route="missing", title="x")
        other = store.create_key({"name": "Other"})["access_key"]["id"]
        with self.as_key(other), self.assertRaises(ValueError):
            self.mcp.get_proposal(rule["id"])

    def test_read_only_and_revoked_keys(self):
        reader = store.create_key({"name": "Reader", "can_propose": False})["access_key"]["id"]
        with self.as_key(reader), self.assertRaisesRegex(ValueError, "read-only"):
            self.mcp.propose_change("add", "r", route="app", title="x")
        store.revoke_key(reader)
        with self.as_key(reader), self.assertRaisesRegex(ValueError, "revoked"):
            self.mcp.propose_change("add", "r", route="app", title="x")

    def test_get_checks_and_report_results(self):
        store.create_item(self.app["id"], {"kind": "check", "title": "env list check", "body": "dev, test, stage, prod"})
        plan = self.mcp.get_checks(folder="/home/me/code/app")
        self.assertEqual([c["name"] for c in plan["checks"]], ["env list check"])
        with self.assertRaises(ValueError):
            self.mcp.get_checks()
        reader = store.create_key({"name": "Reader", "can_propose": False})["access_key"]["id"]
        with self.as_key(reader):  # results are a log, not a context change, so read-only keys may report
            run = self.mcp.report_check_results([{"check": "env list check", "status": "pass", "evidence": "envs.tf has all four"}],
                                                folder="/home/me/code/app")
        self.assertEqual((run["outcome"], run["agent"]), ("passed", "Reader"))
        self.assertIn("✓ env list check", run["summary"])
        with self.as_key(reader), self.assertRaisesRegex(ValueError, "get_checks"):
            self.mcp.report_check_results([{"check": "nope", "status": "pass", "evidence": "x"}], folder="/home/me/code/app")
        store.update_route(self.app["id"], {"paused": True})
        with self.assertRaisesRegex(ValueError, "paused"):
            self.mcp.get_checks(route="app")

    def test_tools_are_annotated(self):
        tools = {tool.name: tool for tool in self.mcp.mcp._tool_manager.list_tools()} if hasattr(self.mcp.mcp, "_tool_manager") else None
        if tools is None:
            self.skipTest("tool manager not exposed by this MCP version")
        self.assertTrue(tools["get_context"].annotations.read_only_hint)
        self.assertFalse(tools["propose_change"].annotations.read_only_hint)
        self.assertTrue(tools["get_checks"].annotations.read_only_hint)
        self.assertFalse(tools["report_check_results"].annotations.destructive_hint)
        # A retried auto-approved note would be added twice, so clients must not retry blindly.
        self.assertFalse(tools["propose_change"].annotations.idempotent_hint)

    def test_opted_in_stdio_acts_as_the_local_agent(self):
        with mock.patch.object(self.mcp, "get_access_token", return_value=None):
            with self.assertRaisesRegex(ValueError, "revoked"):
                self.mcp.propose_change("add", "r", route="app", title="over HTTP")
            with mock.patch.object(self.mcp, "STDIO_TRANSPORT", True):
                note = self.mcp.propose_change("add", "r", route="app", title="over stdio")
                self.assertEqual(note["outcome"], "applied")
                self.assertEqual(self.mcp.get_proposal(note["id"])["status"], "auto")
                self.assertEqual([p["id"] for p in self.mcp.list_my_proposals()], [note["id"]])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(HAVE_MCP, "the mcp package is not installed")
class McpSettingsTests(unittest.TestCase):
    """A wrong setting stops the server. It never quietly turns authentication off."""

    def load(self, **env):
        base = {"DETOUR_MCP_AUTH_MODE": "key", "DETOUR_PUBLIC_URL": ""}
        with mock.patch.dict(os.environ, {**base, **env}):
            import mcp_server
            return importlib.reload(mcp_server)

    def test_unknown_auth_modes_refuse_to_start(self):
        for mode in ("keys", "off", "false", "disabled"):
            with self.subTest(mode=mode), self.assertRaisesRegex(SystemExit, "DETOUR_MCP_AUTH_MODE"):
                self.load(DETOUR_MCP_AUTH_MODE=mode)

    def test_cloud_mode_refuses_to_start_without_keys(self):
        with self.assertRaisesRegex(SystemExit, "none"):
            self.load(DETOUR_PUBLIC_URL="https://detour.example.com", DETOUR_MCP_AUTH_MODE="none")

    def test_cloud_mode_advertises_the_public_address(self):
        module = self.load(DETOUR_PUBLIC_URL="https://detour.example.com")
        self.assertEqual(module.MCP_RESOURCE, "https://detour.example.com/mcp")
        security_settings = module.transport_security(module.SETTINGS)
        self.assertTrue(security_settings.enable_dns_rebinding_protection)
        self.assertIn("detour.example.com", security_settings.allowed_hosts)
        self.assertIn("https://detour.example.com", security_settings.allowed_origins)
        self.assertNotIn("*", "".join(security_settings.allowed_hosts))

    def test_local_default_stays_on_loopback_with_keys(self):
        module = self.load()
        self.assertEqual((module.AUTH_MODE, module.SETTINGS.bind_host, module.MCP_RESOURCE), ("key", "127.0.0.1", "http://127.0.0.1:3788/mcp"))


@unittest.skipUnless(HAVE_MCP, "the mcp package is not installed")
class McpHttpSecurityTests(unittest.TestCase):
    """The real HTTP endpoint, over a socket, the way an agent or a hostile web page would reach it."""

    mode = "key"

    def setUp(self):
        import uvicorn
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR, store.DB_PATH
        store.DATA_DIR = Path(self.tmp.name)
        store.DB_PATH = store.DATA_DIR / "detour.sqlite"
        store.init_db()
        self.port = free_port()
        env = mock.patch.dict(os.environ, {"DETOUR_MCP_AUTH_MODE": self.mode, "DETOUR_MCP_PORT": str(self.port), "DETOUR_PUBLIC_URL": ""})
        env.start()
        self.addCleanup(env.stop)
        import mcp_server
        self.mcp = importlib.reload(mcp_server)
        self.writer = store.create_key({"name": "Writer"})
        self.reader = store.create_key({"name": "Reader", "can_propose": False})
        self.server = uvicorn.Server(uvicorn.Config(self.mcp.build_app(), host="127.0.0.1", port=self.port, log_level="warning", server_header=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.02)
        self.assertTrue(self.server.started, "MCP test server did not start")

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        store.DATA_DIR, store.DB_PATH = self.previous
        self.tmp.cleanup()

    def call(self, body, token=None, headers=None, method="POST", path="/mcp"):
        request_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Host": f"127.0.0.1:{self.port}"}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request_headers.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=request_headers)
        response = conn.getresponse()
        text = response.read().decode(errors="replace")
        conn.close()
        return response.status, text

    LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    def propose(self):
        return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "propose_change", "arguments": {"action": "add", "reason": "r", "route": "x", "title": "t"}}}


class McpKeyAuthTests(McpHttpSecurityTests):
    def test_no_key_is_refused(self):
        self.assertEqual(self.call(self.LIST)[0], 401)

    def test_a_wrong_or_malformed_key_is_refused(self):
        for token in ("dtr_nope", "x", "Bearer"):
            self.assertEqual(self.call(self.LIST, token=token)[0], 401, token)
        self.assertEqual(self.call(self.LIST, headers={"Authorization": "Basic Zm9vOmJhcg=="})[0], 401)

    def test_every_method_needs_a_key(self):
        for method in ("GET", "DELETE", "PUT"):
            self.assertIn(self.call(None, method=method)[0], (401, 405), method)
            self.assertNotEqual(self.call(None, method=method)[0], 200, method)

    def test_a_revoked_key_is_refused_at_once(self):
        self.assertEqual(self.call(self.LIST, token=self.writer["token"])[0], 200)
        store.revoke_key(self.writer["access_key"]["id"])
        self.assertEqual(self.call(self.LIST, token=self.writer["token"])[0], 401)

    def test_a_valid_key_lists_the_tools(self):
        status, text = self.call(self.LIST, token=self.reader["token"])
        self.assertEqual(status, 200)
        self.assertIn("propose_change", text)

    def test_a_read_only_key_cannot_propose(self):
        status, text = self.call(self.propose(), token=self.reader["token"])
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(text)["result"]["isError"])
        self.assertEqual(store.proposal_counts()["pending"], 0)
        store.create_route({"name": "x"})
        status, text = self.call(self.propose(), token=self.writer["token"])
        self.assertEqual(status, 200)  # the same call with a key that may propose is accepted into the Review queue
        self.assertFalse(json.loads(text)["result"].get("isError"))
        self.assertEqual(store.proposal_counts()["pending"], 1)

    def test_a_web_page_cannot_reach_it_through_a_rebound_host_name(self):
        status, _ = self.call(self.LIST, token=self.writer["token"], headers={"Host": f"attacker.example:{self.port}"})
        self.assertEqual(status, 421)

    def test_a_browser_origin_from_another_site_is_refused(self):
        status, _ = self.call(self.LIST, token=self.writer["token"], headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.call(self.LIST, token=self.writer["token"], headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_the_key_is_checked_before_the_request_body_is_parsed(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/mcp", body="{not json", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()


class McpOptedOutAuthTests(McpHttpSecurityTests):
    """DETOUR_MCP_AUTH_MODE=none is for testing on one computer. It is still loopback only and still checks Host and Origin."""

    mode = "none"

    def test_keyless_calls_work_in_the_opt_out_mode(self):
        self.assertEqual(self.call(self.LIST)[0], 200)

    def test_host_and_origin_are_still_checked(self):
        self.assertEqual(self.call(self.LIST, headers={"Host": f"attacker.example:{self.port}"})[0], 421)
        self.assertEqual(self.call(self.LIST, headers={"Origin": "https://evil.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
