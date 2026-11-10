import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import store


class StoreCase(unittest.TestCase):
    """Each test gets its own empty database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR, store.DB_PATH, store.NESTED_DETOURS_ENABLED
        store.DATA_DIR = Path(self.tmp.name)
        store.DB_PATH = store.DATA_DIR / "detour.sqlite"
        store.NESTED_DETOURS_ENABLED = True
        store.init_db()
        self.agent = store.create_key({"name": "Claude Code"})["access_key"]

    def tearDown(self):
        store.DATA_DIR, store.DB_PATH, store.NESTED_DETOURS_ENABLED = self.previous
        self.tmp.cleanup()

    def project(self, name="app", **extra):
        return store.create_route({"name": name, **extra})


class RouteAndItemTests(StoreCase):
    def test_route_scopes_and_validation(self):
        glob = store.create_route({"name": "Global", "scope": "global"})
        project = self.project(targets=[{"kind": "folder", "value": "C:\\code\\app\\"}])
        sub = store.create_route({"name": "Releases", "scope": "sub", "parent_id": project["id"]})
        self.assertEqual(project["targets"][0]["value"], "C:/code/app")
        self.assertEqual(sub["parent"]["name"], "app")
        self.assertEqual([c["name"] for c in store.get_route(project["id"])["children"]], ["Releases"])
        with self.assertRaises(ValueError):
            store.create_route({"name": "Bad", "scope": "sub", "parent_id": glob["id"]})
        with self.assertRaises(ValueError):
            store.create_route({"name": ""})
        with self.assertRaises(ValueError):
            store.add_target(glob["id"], {"kind": "folder", "value": "/x"})
        store.NESTED_DETOURS_ENABLED = False
        with self.assertRaises(ValueError):
            store.create_route({"name": "Off", "scope": "sub", "parent_id": project["id"]})

    def test_item_changes_are_recorded_and_undoable(self):
        route = self.project()
        item = store.create_item(route["id"], {"kind": "rule", "title": "Use tabs", "body": "Always"})
        store.update_item(item["id"], {"title": "Use spaces"})
        events = store.list_events(route["id"])
        self.assertEqual([e["action"] for e in events], ["edit", "add", "create"])
        store.undo_event(events[0]["id"])
        self.assertEqual(store.list_items(route["id"])[0]["title"], "Use tabs")
        with self.assertRaises(store.Conflict):
            store.undo_event(events[0]["id"])
        # Undoing the add is refused once the item changed again.
        store.update_item(item["id"], {"body": "Mostly"})
        with self.assertRaises(store.Conflict):
            store.undo_event(events[1]["id"])

    def test_retire_restore_move_and_delete(self):
        a, b = self.project("a"), self.project("b")
        item = store.create_item(a["id"], {"kind": "note", "title": "N"})
        store.set_item_status(item["id"], "retired")
        self.assertEqual(store.list_items(a["id"], include_retired=False), [])
        store.set_item_status(item["id"], "active")
        store.move_item(item["id"], "b")
        self.assertEqual(store.list_items(b["id"])[0]["id"], item["id"])
        store.delete_route(b["id"])
        self.assertIsNone(store.get_route(b["id"]))

    def test_targets_deduplicate_and_normalize(self):
        route = self.project()
        first = store.add_target(route["id"], {"kind": "repo", "value": "git@github.com:Owner/App.git"})
        second = store.add_target(route["id"], {"kind": "repo", "value": "https://github.com/owner/app"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["value"], "github.com/owner/app")
        with self.assertRaises(ValueError):
            store.add_target(route["id"], {"kind": "url", "value": "javascript:alert(1)"})
        store.remove_target(first["id"])
        self.assertEqual(store.get_route(route["id"])["targets"], [])


class ProposalTests(StoreCase):
    def test_add_waits_then_approve_with_edits_then_undo(self):
        route = self.project()
        result = store.propose(self.agent, {"action": "add", "route": "app", "kind": "rule", "title": "Sign releases", "body": "cosign", "reason": "asked twice"})
        self.assertEqual(result["outcome"], "waiting_for_review")
        self.assertEqual(store.list_items(route["id"]), [])
        self.assertEqual(store.get_route(route["id"])["pending_count"], 1)
        approved = store.approve_proposal(result["id"], {"title": "Sign every release"})
        self.assertEqual(approved["status"], "approved")
        item = store.list_items(route["id"])[0]
        self.assertEqual((item["title"], item["created_by"]), ("Sign every release", "Claude Code"))
        reopened = store.reopen_proposal(result["id"])
        self.assertEqual(reopened["status"], "pending")
        self.assertEqual(store.list_items(route["id"]), [])
        with self.assertRaises(ValueError):
            store.approve_proposal(result["id"], {"route_id": "x"})

    def test_notes_policy_auto_applies_only_notes(self):
        route = self.project(policy="notes")
        note = store.propose(self.agent, {"action": "add", "route": route["id"], "kind": "note", "title": "WSL", "reason": "r"})
        rule = store.propose(self.agent, {"action": "add", "route": route["id"], "kind": "rule", "title": "Rule", "reason": "r"})
        self.assertEqual((note["outcome"], note["status"]), ("applied", "auto"))
        self.assertEqual(rule["outcome"], "waiting_for_review")
        self.assertEqual([i["title"] for i in store.list_items(route["id"])], ["WSL"])

    def test_read_only_paused_and_read_only_keys_are_refused(self):
        self.project("ro", policy="read")
        paused = self.project("paused")
        store.update_route(paused["id"], {"paused": True})
        for name in ("ro", "paused"):
            with self.assertRaises(store.Forbidden):
                store.propose(self.agent, {"action": "add", "route": name, "title": "x", "reason": "r"})
        self.project("open")
        reader = store.create_key({"name": "Reader", "can_propose": False})["access_key"]
        with self.assertRaises(store.Forbidden):
            store.propose(reader, {"action": "add", "route": "open", "title": "x", "reason": "r"})

    def test_rejected_proposals_are_not_refiled(self):
        self.project()
        first = store.propose(self.agent, {"action": "add", "route": "app", "title": "Expose port", "reason": "r"})
        again = store.propose(self.agent, {"action": "add", "route": "app", "title": "expose port", "reason": "r"})
        self.assertEqual((again["id"], again["outcome"]), (first["id"], "already_pending"))
        store.reject_proposal(first["id"], "Keep it local")
        third = store.propose(self.agent, {"action": "add", "route": "app", "title": "Expose port", "reason": "r"})
        self.assertEqual(third["outcome"], "previously_rejected")
        self.assertEqual(third["decision_note"], "Keep it local")
        self.assertEqual(store.proposal_counts()["pending"], 0)

    def test_edit_retire_move_and_stale_proposals(self):
        a, b = self.project("a"), self.project("b")
        item = store.create_item(a["id"], {"kind": "runbook", "title": "Release", "body": "1. test\n2. tag"})
        edit = store.propose(self.agent, {"action": "edit", "item_id": item["id"], "body": "1. check CI\n2. tag", "reason": "CI runs tests"})
        self.assertEqual(edit["before"]["body"], "1. test\n2. tag")
        store.update_item(item["id"], {"title": "Release checklist"})
        with self.assertRaises(store.Conflict):
            store.approve_proposal(edit["id"])
        retire = store.propose(self.agent, {"action": "retire", "item_id": item["id"], "reason": "old"})
        store.approve_proposal(retire["id"])
        self.assertEqual(store.list_items(a["id"])[0]["status"], "retired")
        store.set_item_status(item["id"], "active")
        move = store.propose(self.agent, {"action": "move", "item_id": item["id"], "to_route": "b", "reason": "belongs there"})
        self.assertEqual(store.get_route(b["id"])["pending_count"], 1)
        store.approve_proposal(move["id"])
        self.assertEqual(store.list_items(b["id"])[0]["id"], item["id"])
        with self.assertRaises(ValueError):
            store.propose(self.agent, {"action": "edit", "item_id": item["id"], "reason": "nothing"})

    def test_create_route_proposal(self):
        parent = self.project("app")
        result = store.propose(self.agent, {"action": "create_route", "name": "Taxes", "description": "Receipts",
                                            "targets": [{"kind": "folder", "value": "~/Documents/taxes"}], "reason": "asked"})
        self.assertIsNone(result["route"])
        approved = store.approve_proposal(result["id"], {"name": "Tax paperwork"})
        route = store.get_route(approved["route"]["id"])
        self.assertEqual((route["name"], route["targets"][0]["value"]), ("Tax paperwork", "~/Documents/taxes"))
        with self.assertRaises(ValueError):
            store.propose(self.agent, {"action": "create_route", "name": "G", "scope": "global", "reason": "r"})
        sub = store.propose(self.agent, {"action": "create_route", "name": "Sub", "scope": "sub", "parent": "app", "reason": "r"})
        self.assertEqual(sub["parent"]["id"], parent["id"])


class ContextTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.glob = store.create_route({"name": "Global", "scope": "global"})
        self.app = self.project(targets=[{"kind": "folder", "value": "~/code/app"}, {"kind": "repo", "value": "github.com/me/app"},
                                         {"kind": "topic", "value": "billing"}])
        self.sub = store.create_route({"name": "Releases", "scope": "sub", "parent_id": self.app["id"], "targets": [{"kind": "file", "value": "CHANGELOG.md"}]})
        self.other = self.project("other", targets=[{"kind": "folder", "value": "/srv/other"}])
        for route, title in ((self.glob, "G rule"), (self.app, "App rule"), (self.sub, "Sub rule"), (self.other, "Other rule")):
            store.create_item(route["id"], {"kind": "rule", "title": title})

    def names(self, **where):
        return [layer["route"]["name"] for layer in store.get_context(**where)["layers"]]

    def test_matching_by_folder_repo_topic_and_file(self):
        self.assertEqual(self.names(folder="/home/me/code/app/src"), ["Global", "app"])
        self.assertEqual(self.names(folder="C:\\Users\\me\\code\\app"), ["Global", "app"])
        self.assertEqual(self.names(repo="git@github.com:me/app.git"), ["Global", "app"])
        self.assertEqual(self.names(topic="Fix the billing export"), ["Global", "app"])
        self.assertEqual(self.names(file="/home/me/code/app/CHANGELOG.md"), ["Global", "app", "Releases"])
        self.assertEqual(self.names(folder="/srv/other"), ["Global", "other"])
        self.assertEqual(self.names(folder="/nowhere"), ["Global"])

    def test_named_route_includes_parents_and_global_in_order(self):
        context = store.get_context(route_ref="Releases")
        self.assertEqual([(l["authority"], l["route"]["name"]) for l in context["layers"]], [(1, "Global"), (2, "app"), (3, "Releases")])
        self.assertIn("Global outranks Project", context["precedence"])

    def test_paused_routes_and_retired_items_are_left_out(self):
        store.update_route(self.app["id"], {"paused": True})
        self.assertEqual(self.names(folder="~/code/app"), ["Global"])
        store.update_route(self.app["id"], {"paused": False})
        item = store.list_items(self.app["id"])[0]
        store.set_item_status(item["id"], "retired")
        layer = store.get_context(route_ref="app")["layers"][1]
        self.assertEqual(layer["items"], [])

    def test_reading_marks_items_used_and_search(self):
        store.get_context(route_ref="app")
        used = store.list_items(self.app["id"])[0]["last_used_at"]
        self.assertIsNotNone(used)
        self.assertIsNone(store.list_items(self.other["id"])[0]["last_used_at"])
        self.assertEqual([r["title"] for r in store.search_items("app rule")], ["App rule"])
        self.assertEqual(store.search_items("100%"), [])
        with self.assertRaises(ValueError):
            store.search_items(" ")


class KeyAndExportTests(StoreCase):
    def test_keys_are_hashed_toggled_and_revoked(self):
        created = store.create_key({"name": "Cursor", "can_propose": False})
        token = created["token"]
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            stored = conn.execute("SELECT token_hash FROM mcp_access_keys WHERE id = ?", (created["access_key"]["id"],)).fetchone()[0]
        self.assertNotIn(token, stored)
        self.assertFalse(store.verify_key(token)["can_propose"])
        store.update_key(created["access_key"]["id"], {"can_propose": True})
        self.assertTrue(store.verify_key(token)["can_propose"])
        store.revoke_key(created["access_key"]["id"])
        self.assertIsNone(store.verify_key(token))
        self.assertIsNone(store.verify_key(""))

    def test_export_import_round_trip(self):
        app = self.project(targets=[{"kind": "topic", "value": "x"}])
        store.create_route({"name": "Sub", "scope": "sub", "parent_id": app["id"]})
        store.create_item(app["id"], {"kind": "decision", "title": "D", "body": "B"})
        exported = store.export_all()
        counts = store.import_all(json.loads(json.dumps(exported)))
        self.assertEqual(counts, {"routes": 2, "items": 1, "targets": 1})
        subs = [r for r in store.list_routes() if r["scope"] == "sub"]
        self.assertEqual(len(subs), 2)
        self.assertNotEqual(subs[0]["parent_id"], subs[1]["parent_id"])
        with self.assertRaises(ValueError):
            store.import_all({"format": "nope"})


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR, store.DB_PATH
        store.DATA_DIR = Path(self.tmp.name)
        store.DB_PATH = store.DATA_DIR / "detour.sqlite"

    def tearDown(self):
        store.DATA_DIR, store.DB_PATH = self.previous
        self.tmp.cleanup()

    def backups(self):
        return sorted((store.DATA_DIR / "backups").glob("*.sqlite"))

    def test_new_database_uses_wal_and_current_schema_without_backup(self):
        store.init_db()
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertEqual(self.backups(), [])

    def test_newer_schema_is_refused(self):
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            conn.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
        with self.assertRaises(RuntimeError):
            store.init_db()

    def test_old_backups_are_pruned(self):
        store.init_db()
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            for _ in range(store.BACKUPS_TO_KEEP + 2):
                store.backup_database(conn, 0)
        self.assertEqual(len(self.backups()), store.BACKUPS_TO_KEEP)

    def test_migrates_a_0_1_database(self):
        ts = "2026-09-17T00:00:00Z"
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            conn.executescript("""
                CREATE TABLE artifacts (id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, slug TEXT NOT NULL,
                  body_markdown TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',
                  visibility TEXT NOT NULL DEFAULT 'private', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  local_path TEXT NOT NULL DEFAULT '', cloud_url TEXT NOT NULL DEFAULT '', notes_markdown TEXT NOT NULL DEFAULT '',
                  file_name TEXT NOT NULL DEFAULT '', file_size INTEGER NOT NULL DEFAULT 0, file_sha256 TEXT NOT NULL DEFAULT '', blob_path TEXT NOT NULL DEFAULT '');
                CREATE TABLE bundles (id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                  audience TEXT NOT NULL, artifact_ids_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE routes (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'project', parent_id TEXT);
                CREATE TABLE route_items (id TEXT PRIMARY KEY, route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL CHECK (kind IN ('artifact','note','link','repo')), artifact_id TEXT, label TEXT NOT NULL DEFAULT '',
                  url TEXT NOT NULL DEFAULT '', body_markdown TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
                CREATE TABLE mcp_access_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                  last_used_at TEXT, revoked_at TEXT);
                CREATE TABLE environment_flags (scope_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (scope_id, name));
                CREATE TABLE tag_sets (id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, name TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                PRAGMA user_version = 1;
            """)
            rows = [
                ("a1", "skill", "Review changes", "review", "Check the diff", "[]", "active", "private", ts, ts, "", "", "Run tests first", "", 0, "", ""),
                ("a2", "repo", "Router repo", "router", "", "[]", "active", "private", ts, ts, "C:\\work\\router", "https://github.com/me/router.git", "", "", 0, "", ""),
                ("a3", "doc", "Old spec", "old", "v1", "[]", "archived", "private", ts, ts, "", "", "", "", 0, "", ""),
                ("a4", "note", "Loose note", "loose", "Not on any route", "[]", "active", "private", ts, ts, "", "", "", "", 0, "", ""),
            ]
            conn.executemany("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.execute("INSERT INTO routes VALUES ('g', 'Global', 'Everything', 0, ?, ?, 'global', NULL)", (ts, ts))
            conn.execute("INSERT INTO routes VALUES ('r1', 'Router', 'Context', 1, ?, ?, 'project', NULL)", (ts, ts))
            conn.execute("INSERT INTO routes VALUES ('r2', 'Router docs', '', 2, ?, ?, 'sub', 'r1')", (ts, ts))
            for item in (("i1", "r1", "artifact", "a1", "", "", ""), ("i2", "r1", "artifact", "a2", "", "", ""), ("i3", "r2", "artifact", "a3", "", "", ""),
                         ("i4", "r1", "note", None, "Heads up", "", "Ports are local"), ("i5", "r1", "link", None, "Spec", "https://example.org/spec", ""),
                         ("i6", "r1", "repo", None, "Mirror", "https://gitlab.com/me/router", "")):
                conn.execute("INSERT INTO route_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*item, ts))
            conn.execute("INSERT INTO mcp_access_keys VALUES ('k1', 'Old key', ?, ?, NULL, NULL)", (store.hash_key("dtr_old"), ts))
            conn.execute("INSERT INTO environment_flags VALUES ('workspace', 'NEVER_DELETE', 'true', 'Hard rule', ?, ?)", (ts, ts))
            conn.commit()

        store.init_db()
        store.init_db()

        self.assertEqual(len(self.backups()), 1)
        routes = {r["name"]: r for r in store.list_routes()}
        self.assertEqual(set(routes), {"Global", "Router", "Router docs", "Imported from Detour 0.1"})
        self.assertEqual(routes["Router docs"]["parent_id"], "r1")
        self.assertEqual({(t["kind"], t["value"]) for t in routes["Router"]["targets"]},
                         {("repo", "github.com/me/router"), ("folder", "C:/work/router"), ("repo", "gitlab.com/me/router")})
        items = {i["title"]: i for i in store.list_items("r1")}
        self.assertEqual(items["Review changes"]["kind"], "runbook")
        self.assertIn("Run tests first", items["Review changes"]["body"])
        self.assertEqual(items["Heads up"]["kind"], "note")
        self.assertEqual(items["Spec"]["body"], "https://example.org/spec")
        self.assertEqual(store.list_items("r2")[0]["status"], "retired")
        self.assertEqual(store.list_items("Imported from Detour 0.1")[0]["title"], "Loose note")
        self.assertEqual(store.list_items("g")[0]["body"], "NEVER_DELETE=true\n\nHard rule")
        key = store.verify_key("dtr_old")
        self.assertFalse(key["can_propose"])
        with closing(sqlite3.connect(store.DB_PATH)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({"artifacts", "bundles", "route_items", "environment_flags", "tag_sets"} & tables)
        self.assertFalse([t for t in tables if t.startswith("legacy_")])


if __name__ == "__main__":
    unittest.main()
