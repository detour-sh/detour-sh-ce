import importlib
import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
