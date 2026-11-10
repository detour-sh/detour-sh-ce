import json
import base64
import http.client
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server


class LocalAppTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).parent / ".data" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.previous_paths = server.DATA_DIR, server.DB_PATH, server.EXPORT_DIR
        server.DATA_DIR = test_root
        server.DB_PATH = test_root / f"test-{uuid.uuid4().hex}.sqlite"
        server.EXPORT_DIR = test_root
        server.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DetourHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        test_db = server.DB_PATH
        server.DATA_DIR, server.DB_PATH, server.EXPORT_DIR = self.previous_paths
        if test_db.parent == Path(__file__).parent / ".data" / "tests":
            test_db.unlink(missing_ok=True)

    def request(self, path, method="GET", payload=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        request_headers = {"Content-Type": "application/json"} if body is not None else {}
        request_headers.update(headers or {})
        request = Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode(), response.headers

    def test_artifact_create_search_update_archive_and_export(self):
        status, body, _ = self.request(
            "/api/artifacts", "POST",
            {"type": "skill", "title": "Review changes", "body_markdown": "Check the diff", "tags": ["review"]},
        )
        self.assertEqual(status, 201)
        artifact = json.loads(body)["artifact"]
        self.assertEqual(artifact["visibility"], "private")

        status, body, _ = self.request("/api/search?q=diff")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifacts"][0]["id"], artifact["id"])

        status, body, _ = self.request(
            f"/api/artifacts/{artifact['id']}", "PUT", {"body_markdown": "Check tests too"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifact"]["body_markdown"], "Check tests too")

        status, body, _ = self.request(
            "/api/bundles", "POST",
            {"title": "Review pack", "audience": "agent", "artifact_ids": [artifact["id"]]},
        )
        self.assertEqual(status, 201)
        bundle_id = json.loads(body)["bundle"]["id"]
        status, body, _ = self.request(f"/api/bundles/{bundle_id}/export.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifacts"][0]["id"], artifact["id"])
        status, body, _ = self.request(f"/api/bundles/{bundle_id}/export.md")
        self.assertEqual(status, 200)
        self.assertIn("Check tests too", body)

        status, body, _ = self.request(f"/api/artifacts/{artifact['id']}/archive", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifact"]["status"], "archived")

    def test_mcp_access_key_is_hashed_listed_verified_and_revoked(self):
        status, body, _ = self.request("/api/mcp-keys", "POST", {"name": "Codex laptop"})
        self.assertEqual(status, 201)
        created = json.loads(body)
        token = created["token"]
        key_id = created["access_key"]["id"]
        self.assertTrue(token.startswith("dtr_"))
        with closing(sqlite3.connect(server.DB_PATH)) as conn:
            stored_hash, = conn.execute("SELECT token_hash FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone()
        self.assertEqual(stored_hash, server.hash_mcp_access_key(token))
        self.assertNotEqual(stored_hash, token)

        status, body, _ = self.request("/api/mcp-keys")
        self.assertEqual(status, 200)
        listed = json.loads(body)["access_keys"][0]
        self.assertEqual(listed["name"], "Codex laptop")
        self.assertNotIn("token", listed)
        self.assertNotIn("token_hash", listed)
        self.assertEqual(server.verify_mcp_access_key(token)["id"], key_id)

        status, _, _ = self.request(f"/api/mcp-keys/{key_id}/revoke", "POST", {})
        self.assertEqual(status, 200)
        self.assertIsNone(server.verify_mcp_access_key(token))

    def test_connections_explains_authenticated_harness_setup(self):
        status, body, _ = self.request("/connections")
        self.assertEqual(status, 200)
        self.assertIn("Harness keys", body)
        self.assertIn("Authorization", body)
        self.assertIn("&lt;YOUR_DETOUR_KEY&gt;", body)

    def test_theme_settings_and_test_pattern_are_rendered(self):
        status, body, _ = self.request("/settings")
        self.assertEqual(status, 200)
        self.assertIn('data-theme-picker', body)
        self.assertIn('data-accent-picker', body)
        self.assertIn('data-text-size-picker', body)
        self.assertIn('href="/test-pattern"', body)
        self.assertIn('class="settings-button active"', body)
        self.assertIn('href="/settings#profile"', body)
        self.assertIn("Local user", body)
        self.assertNotIn('class="health-link"', body)
        self.assertNotIn('class="topbar-actions"', body)
        status, body, _ = self.request("/test-pattern")
        self.assertEqual(status, 200)
        self.assertIn('data-testid="pattern-ready"', body)
        self.assertIn('data-pattern-toggle', body)
        self.assertIn('id="pattern-dialog"', body)

    def test_bundle_update_and_read(self):
        status, body, _ = self.request("/api/bundles", "POST", {"title": "First", "audience": "agent"})
        self.assertEqual(status, 201)
        bundle_id = json.loads(body)["bundle"]["id"]
        status, body, _ = self.request(f"/api/bundles/{bundle_id}", "PUT", {"title": "Second"})
        self.assertEqual(status, 200)
        status, body, _ = self.request(f"/api/bundles/{bundle_id}")
        self.assertEqual(json.loads(body)["bundle"]["title"], "Second")

    def test_rejects_cross_origin_and_non_json_writes(self):
        payload = {"type": "note", "title": "Injected", "body_markdown": "No"}
        for headers, expected in [
            ({"Origin": "http://evil.example"}, 403),
            ({"Origin": "null"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ]:
            with self.subTest(headers=headers):
                status, _, _ = self.request("/api/artifacts", "POST", payload, headers)
                self.assertEqual(status, expected)
        status, body, _ = self.request("/api/artifacts")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifacts"], [])

    def test_rejects_rebound_host_and_large_body(self):
        status, _, _ = self.request("/health", headers={"Host": "evil.example"})
        self.assertEqual(status, 400)
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        connection.putrequest("POST", "/api/artifacts")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "1000001")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

    def test_portal_shell_and_stylesheet(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn('aria-label="Primary"', body)
        self.assertIn('href="/artifacts"', body)
        self.assertIn('<title>Detours - Detour</title>', body)
        self.assertIn('Create Detour', body)
        self.assertIn('class="action-menu"', body)
        self.assertIn('aria-label="Add Detour"', body)
        self.assertIn('class="action-menu-trigger"', body)
        self.assertIn('class="action-menu-icon"', body)
        self.assertIn('aria-expanded="false"', body)
        self.assertNotIn('<summary aria-label="Add Detour"', body)
        self.assertIn('href="/bundles"', body)
        self.assertIn('href="/connections"', body)
        self.assertNotIn('id="new-artifact"', body)
        status, body, _ = self.request("/connections")
        self.assertEqual(status, 200)
        self.assertIn("http://127.0.0.1:3788/mcp", body)
        self.assertIn("Authorization", body)
        self.assertIn('id="claude-desktop"', body)
        self.assertIn("Harness keys", body)
        self.assertIn("data-copy-target", body)
        self.assertNotIn('id="new-bundle"', body)
        self.assertIn('href="/assets/portal.css"', body)
        status, body, _ = self.request("/artifacts")
        self.assertEqual(status, 200)
        self.assertIn('<title>Context - Detour</title>', body)
        self.assertIn('>Context</a>', body)
        self.assertNotIn('>Routes</a>', body)
        self.assertNotIn('id="new-artifact"', body)
        self.assertNotIn('id="new-bundle"', body)
        status, body, _ = self.request("/bundles")
        self.assertEqual(status, 200)
        self.assertIn('<title>Bundles - Detour</title>', body)
        self.assertIn('id="new-bundle"', body)
        self.assertNotIn('id="new-artifact"', body)
        status, body, headers = self.request("/assets/portal.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["content-type"])
        self.assertIn(".app-shell", body)

    def test_compose_runs_web_and_mcp_in_one_container(self):
        compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:3787:3787"', compose)
        self.assertIn('"127.0.0.1:3788:3788"', compose)
        self.assertNotIn("detour-mcp:", compose)
        dockerfile = (Path(__file__).parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "/app/run.py"]', dockerfile)

    def test_artifacts_filters_results(self):
        self.request("/api/artifacts", "POST", {"type": "skill", "title": "Review builds", "body_markdown": "Check image"})
        self.request("/api/artifacts", "POST", {"type": "note", "title": "Meeting recap", "body_markdown": "Thursday"})
        status, body, _ = self.request("/artifacts?type=skill&q=builds")
        self.assertEqual(status, 200)
        self.assertIn("Review builds", body)
        self.assertNotIn("Meeting recap", body)
        self.assertIn('<option value="skill" selected>', body)

    def test_route_groups_artifacts_and_references(self):
        status, body, _ = self.request("/api/artifacts", "POST", {"type": "repo", "title": "Router repo", "body_markdown": "Source code", "notes_markdown": "Run the local services first.", "local_path": "C:\\work\\router", "cloud_url": "https://example.org/router"})
        self.assertEqual(status, 201)
        artifact_id = json.loads(body)["artifact"]["id"]
        self.assertEqual(json.loads(body)["artifact"]["notes_markdown"], "Run the local services first.")
        status, body, _ = self.request("/api/routes", "POST", {"title": "Automated Router", "description": "Internal product context"})
        self.assertEqual(status, 201)
        route_id = json.loads(body)["route"]["id"]
        status, body, _ = self.request(f"/api/routes/{route_id}/items", "POST", {"kind": "artifact", "artifact_id": artifact_id})
        self.assertEqual(status, 201)
        item_id = json.loads(body)["item"]["id"]
        status, _, _ = self.request(f"/api/routes/{route_id}/items", "POST", {"kind": "repo", "label": "Router repo", "url": "https://example.org/router"})
        self.assertEqual(status, 201)
        status, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual(status, 200)
        context = json.loads(body)
        self.assertEqual(context["format"], "detour.context.v2")
        self.assertEqual(context["precedence"], "global > project > sub")
        self.assertEqual([item["kind"] for item in context["items"]], ["artifact", "repo"])
        self.assertEqual(context["items"][0]["type"], "repo")
        self.assertEqual(context["items"][0]["notes_markdown"], "Run the local services first.")
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Automated Router", body)
        self.assertIn("Repository <strong>1</strong>", body)
        status, body, _ = self.request(f"/routes/{route_id}")
        self.assertEqual(status, 200)
        self.assertIn("Router repo", body)
        self.assertIn("Save something useful", body)
        self.assertIn('class="capture-layout"', body)
        self.assertIn("Save context", body)
        self.assertIn('id="add-link-dialog"', body)
        self.assertIn('aria-label="Add context"', body)
        self.assertIn('id="gather-context"', body)
        self.assertIn("<span>Edit</span>", body)
        self.assertIn('class="row-action-icon"', body)
        self.assertIn('class="route-item-actions"', body)
        status, body, _ = self.request(f"/api/routes/{route_id}/items/{item_id}/remove", "POST", {})
        self.assertEqual(status, 200)
        status, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual(len(json.loads(body)["items"]), 1)

    def test_global_project_and_nested_context_precedence(self):
        original = server.NESTED_DETOURS_ENABLED
        server.NESTED_DETOURS_ENABLED = True
        try:
            _, body, _ = self.request("/api/routes", "POST", {"title": "Global", "scope": "global"})
            global_id = json.loads(body)["route"]["id"]
            self.request(f"/api/routes/{global_id}/artifacts", "POST", {"type": "note", "title": "Global rule", "body_markdown": "Owners required"})
            _, body, _ = self.request("/api/routes", "POST", {"title": "Project A", "scope": "project"})
            project_id = json.loads(body)["route"]["id"]
            self.request(f"/api/routes/{project_id}/artifacts", "POST", {"type": "note", "title": "Project rule", "body_markdown": "Project context"})
            _, body, _ = self.request("/api/routes", "POST", {"title": "A3", "scope": "sub", "parent_id": project_id})
            sub_id = json.loads(body)["route"]["id"]
            self.request(f"/api/routes/{sub_id}/artifacts", "POST", {"type": "note", "title": "A3 rule", "body_markdown": "A3 context"})
            status, body, _ = self.request(f"/api/routes/{sub_id}/context")
            self.assertEqual(status, 200)
            context = json.loads(body)
            self.assertEqual([level["detour"]["title"] for level in context["context_stack"]], ["Global", "Project A", "A3"])
            self.assertEqual([level["detour"]["authority"] for level in context["context_stack"]], [3, 2, 1])
            self.assertEqual([item["title"] for item in context["items"]], ["Global rule", "Project rule", "A3 rule"])
        finally:
            server.NESTED_DETOURS_ENABLED = original

    def test_sub_detour_requires_advanced_mode_and_parent(self):
        original = server.NESTED_DETOURS_ENABLED
        try:
            server.NESTED_DETOURS_ENABLED = False
            status, _, _ = self.request("/api/routes", "POST", {"title": "A3", "scope": "sub", "parent_id": "missing"})
            self.assertEqual(status, 400)
            server.NESTED_DETOURS_ENABLED = True
            status, _, _ = self.request("/api/routes", "POST", {"title": "A3", "scope": "sub", "parent_id": "missing"})
            self.assertEqual(status, 400)
        finally:
            server.NESTED_DETOURS_ENABLED = original

    def test_artifacts_index_groups_routes_and_supports_edit_and_attach(self):
        _, body, _ = self.request("/api/artifacts", "POST", {"type": "doc", "title": "Topology", "body_markdown": "Graph"})
        artifact_id = json.loads(body)["artifact"]["id"]
        _, body, _ = self.request("/api/routes", "POST", {"title": "Router"})
        route_id = json.loads(body)["route"]["id"]
        status, body, _ = self.request("/artifacts")
        self.assertEqual(status, 200)
        self.assertIn("Unattached", body)
        self.assertIn("data-route-attach", body)
        status, _, _ = self.request(f"/api/routes/{route_id}/items", "POST", {"kind": "artifact", "artifact_id": artifact_id})
        self.assertEqual(status, 201)
        status, body, _ = self.request(f"/api/artifacts/{artifact_id}", "PUT", {"title": "System topology", "tags": ["system"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["artifact"]["title"], "System topology")
        status, body, _ = self.request("/artifacts")
        self.assertEqual(status, 200)
        self.assertIn("Router", body)
        self.assertNotIn("Unattached", body)
        self.assertIn("<span>Edit</span>", body)

    def test_route_export_and_import_remap_content(self):
        _, body, _ = self.request("/api/routes", "POST", {"title": "Export me", "description": "Portable context"})
        route_id = json.loads(body)["route"]["id"]
        self.request(f"/api/routes/{route_id}/environment", "POST", {"name": "TF_WORKSPACE", "value": "preview"})
        self.request(f"/api/routes/{route_id}/tag-sets", "POST", {"name": "Stage", "tags": ["preview", "production", "qa"]})
        _, body, _ = self.request(f"/api/routes/{route_id}/artifacts", "POST", {"type": "skill", "title": "Review", "body_markdown": "Check it", "tags": ["qa"]})
        artifact_id = json.loads(body)["artifact"]["id"]
        self.request(f"/api/routes/{route_id}/items", "POST", {"kind": "repo", "label": "Source", "url": "https://example.org/source"})
        status, body, headers = self.request(f"/api/routes/{route_id}/export.json")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        export = json.loads(body)
        self.assertEqual(export["format"], "detour.route.export.v1")
        self.assertEqual(export["items"][0]["artifact_id"], artifact_id)
        status, body, _ = self.request("/api/routes/import", "POST", export)
        self.assertEqual(status, 201)
        imported = json.loads(body)["route"]
        self.assertNotEqual(imported["id"], route_id)
        self.assertEqual(imported["title"], "Export me")
        self.assertEqual([item["kind"] for item in imported["items"]], ["artifact", "repo"])
        self.assertNotEqual(imported["items"][0]["artifact_id"], artifact_id)
        _, body, _ = self.request(f"/api/routes/{imported['id']}/environment")
        self.assertEqual(json.loads(body)["environment"][0]["value"], "preview")
        _, body, _ = self.request(f"/api/routes/{imported['id']}/tag-sets")
        self.assertEqual(json.loads(body)["tag_sets"][0]["tags"], ["preview", "production", "qa"])

    def test_detour_order_is_saved_and_shared_by_the_portal(self):
        route_ids = []
        for title in ("First", "Second", "Third"):
            status, body, _ = self.request("/api/routes", "POST", {"title": title})
            self.assertEqual(status, 201)
            route_ids.append(json.loads(body)["route"]["id"])

        reordered = [route_ids[2], route_ids[0], route_ids[1]]
        status, body, _ = self.request("/api/routes/reorder", "POST", {"route_ids": reordered})
        self.assertEqual(status, 200)
        self.assertEqual([route["id"] for route in json.loads(body)["routes"]], reordered)

        status, body, _ = self.request("/api/routes")
        self.assertEqual(status, 200)
        self.assertEqual([route["id"] for route in json.loads(body)["routes"]], reordered)
        status, portal, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertLess(portal.index("Third"), portal.index("First"))
        self.assertIn('data-move-route="up"', portal)
        self.assertIn('draggable="true"', portal)

    def test_workspace_and_detour_environment_flags_reach_ai_context(self):
        status, _, _ = self.request("/api/environment", "POST", {"name": "NEVER_DELETE", "value": "true", "description": "Protect infrastructure"})
        self.assertEqual(status, 201)
        _, body, _ = self.request("/api/routes", "POST", {"title": "Terraform"})
        route_id = json.loads(body)["route"]["id"]
        status, _, _ = self.request(f"/api/routes/{route_id}/environment", "POST", {"name": "TF_WORKSPACE", "value": "staging"})
        self.assertEqual(status, 201)
        status, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual(status, 200)
        context = json.loads(body)
        self.assertEqual({item["name"]: item["value"] for item in context["effective_environment"]}, {"NEVER_DELETE": "true", "TF_WORKSPACE": "staging"})
        status, portal, _ = self.request(f"/routes/{route_id}")
        self.assertEqual(status, 200)
        self.assertIn("Environment and flags", portal)
        self.assertIn("TF_WORKSPACE", portal)

    def test_environment_flags_reject_secrets_and_can_be_removed(self):
        status, _, _ = self.request("/api/environment", "POST", {"name": "API_TOKEN", "value": "not-safe"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/environment", "POST", {"name": "TF_IN_AUTOMATION", "value": "true"})
        self.assertEqual(status, 201)
        status, _, _ = self.request("/api/environment/TF_IN_AUTOMATION/remove", "POST", {})
        self.assertEqual(status, 200)
        _, body, _ = self.request("/api/environment")
        self.assertEqual(json.loads(body)["environment"], [])

    def test_global_and_multiple_detour_tag_sets_control_capture(self):
        status, body, _ = self.request("/api/tag-sets", "POST", {"name": "Priority", "tags": "low, high"})
        self.assertEqual(status, 201)
        global_set = json.loads(body)["tag_set"]
        self.assertEqual(global_set["tags"], ["low", "high"])
        _, body, _ = self.request("/api/routes", "POST", {"title": "Tagged project"})
        route_id = json.loads(body)["route"]["id"]
        for name, tags in (("Stage", ["development", "production"]), ("Area", ["api", "frontend"])):
            status, _, _ = self.request(f"/api/routes/{route_id}/tag-sets", "POST", {"name": name, "tags": tags})
            self.assertEqual(status, 201)
        status, _, _ = self.request(f"/api/routes/{route_id}/artifacts", "POST", {"type": "note", "title": "Valid", "tags": ["high", "api"]})
        self.assertEqual(status, 201)
        status, _, _ = self.request(f"/api/routes/{route_id}/artifacts", "POST", {"type": "note", "title": "Invalid", "tags": ["email-api"]})
        self.assertEqual(status, 400)
        _, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual([item["name"] for item in json.loads(body)["tag_sets"]], ["Priority", "Area", "Stage"])
        _, portal, _ = self.request(f"/routes/{route_id}")
        self.assertIn("CONTROLLED TAGS", portal)
        self.assertIn('<optgroup label="Area">', portal)

    def test_detour_reorder_rejects_incomplete_or_duplicate_orders(self):
        _, body, _ = self.request("/api/routes", "POST", {"title": "One"})
        route_id = json.loads(body)["route"]["id"]
        status, _, _ = self.request("/api/routes/reorder", "POST", {"route_ids": []})
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/routes/reorder", "POST", {"route_ids": [route_id, route_id]})
        self.assertEqual(status, 400)

    def test_file_artifact_upload_is_hashed_and_attached(self):
        _, body, _ = self.request("/api/routes", "POST", {"title": "Files"})
        route_id = json.loads(body)["route"]["id"]
        payload = {"filename": "topology.md", "content_base64": base64.b64encode(b"# topology").decode(), "notes_markdown": "Read before changing the graph."}
        status, body, _ = self.request(f"/api/routes/{route_id}/files", "POST", payload)
        self.assertEqual(status, 201)
        artifact = json.loads(body)["artifact"]
        self.assertEqual(artifact["type"], "file")
        self.assertEqual(artifact["file_name"], "topology.md")
        self.assertEqual(artifact["file_size"], 10)
        self.assertEqual(len(artifact["file_sha256"]), 64)
        self.assertEqual(artifact["notes_markdown"], "Read before changing the graph.")
        self.assertEqual(artifact["blob_path"].split("/")[-1].split("\\")[-1], f"{artifact['id']}.md")
        Path(artifact["blob_path"]).unlink(missing_ok=True)

    def test_route_rejects_untrusted_or_missing_items(self):
        status, body, _ = self.request("/api/routes", "POST", {"title": "Safety"})
        route_id = json.loads(body)["route"]["id"]
        for payload in (
            {"kind": "artifact", "artifact_id": "missing"},
            {"kind": "file", "artifact_id": "missing"},
            {"kind": "mcp", "url": "https://example.org"},
            {"kind": "link", "label": "bad", "url": "javascript:alert(1)"},
            {"kind": "repo", "label": "credential", "url": "https://user:pass@example.org/repo"},
        ):
            with self.subTest(payload=payload):
                status, _, _ = self.request(f"/api/routes/{route_id}/items", "POST", payload)
                self.assertEqual(status, 400)
        status, body, _ = self.request(f"/api/routes/{route_id}")
        self.assertEqual(json.loads(body)["route"]["items"], [])
        status, _, _ = self.request("/api/routes", "POST", {"title": "Cross origin"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_compose_service_host_is_allowed_for_ui_checks(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        connection.request("GET", "/health", headers={"Host": f"detour:{self.httpd.server_port}"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()

    def test_route_escapes_display_and_excludes_archived_artifact_context(self):
        _, body, _ = self.request("/api/artifacts", "POST", {"type": "doc", "title": "<script>alert(1)</script>", "body_markdown": "Secret draft"})
        artifact_id = json.loads(body)["artifact"]["id"]
        _, body, _ = self.request("/api/routes", "POST", {"title": "<b>Router</b>"})
        route_id = json.loads(body)["route"]["id"]
        self.request(f"/api/routes/{route_id}/items", "POST", {"kind": "artifact", "artifact_id": artifact_id})
        _, body, _ = self.request("/")
        self.assertIn("&lt;b&gt;Router&lt;/b&gt;", body)
        self.assertNotIn("<b>Router</b>", body)
        _, body, _ = self.request(f"/routes/{route_id}")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.request(f"/api/artifacts/{artifact_id}/archive", "POST", {})
        _, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual(json.loads(body)["items"], [])

    def test_create_artifact_inside_route_and_reject_old_urls(self):
        _, body, _ = self.request("/api/routes", "POST", {"title": "Automated Router"})
        route_id = json.loads(body)["route"]["id"]
        status, body, _ = self.request(f"/api/routes/{route_id}/artifacts", "POST", {"type": "doc", "title": "Topology", "body_markdown": "Service graph"})
        self.assertEqual(status, 201)
        artifact_id = json.loads(body)["artifact"]["id"]
        status, body, _ = self.request(f"/api/routes/{route_id}/context")
        self.assertEqual(json.loads(body)["items"][0]["id"], artifact_id)
        for old_path in ("/library", f"/stacks/{route_id}", "/api/stacks", "/api/records"):
            status, _, _ = self.request(old_path)
            self.assertEqual(status, 404)
        status, _, _ = self.request("/api/routes/missing/artifacts", "POST", {"type": "note", "title": "Orphan", "body_markdown": "No"})
        self.assertEqual(status, 404)
        _, body, _ = self.request("/api/artifacts?q=Orphan")
        self.assertEqual(json.loads(body)["artifacts"], [])

    def test_migrates_existing_routes_artifacts_and_bundles(self):
        original_path = server.DB_PATH
        legacy_path = original_path.with_name(f"legacy-{uuid.uuid4().hex}.sqlite")
        try:
            with closing(sqlite3.connect(legacy_path)) as conn:
                conn.executescript("""
                    CREATE TABLE records (id TEXT PRIMARY KEY, type TEXT, title TEXT, slug TEXT,
                        body_markdown TEXT, tags_json TEXT, status TEXT, visibility TEXT,
                        created_at TEXT, updated_at TEXT);
                    CREATE TABLE stacks (id TEXT PRIMARY KEY, title TEXT, description TEXT,
                        created_at TEXT, updated_at TEXT);
                    CREATE TABLE stack_items (id TEXT PRIMARY KEY, stack_id TEXT, kind TEXT,
                        record_id TEXT, label TEXT, url TEXT, created_at TEXT);
                    CREATE TABLE bundles (id TEXT PRIMARY KEY, title TEXT, slug TEXT,
                        description TEXT, audience TEXT, record_ids_json TEXT,
                        created_at TEXT, updated_at TEXT);
                """)
                ts = "2026-09-17T00:00:00Z"
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             ("a1", "doc", "Topology", "topology", "Graph", "[]", "active", "private", ts, ts))
                conn.execute("INSERT INTO stacks VALUES (?, ?, ?, ?, ?)", ("r1", "Automated Router", "Context", ts, ts))
                conn.execute("INSERT INTO stack_items VALUES (?, ?, ?, ?, ?, ?, ?)", ("i1", "r1", "record", "a1", "", "", ts))
                conn.execute("INSERT INTO bundles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             ("b1", "Review", "review", "", "agent", '["a1"]', ts, ts))
                conn.commit()
            server.DB_PATH = legacy_path
            server.init_db()
            server.init_db()
            route = server.get_route("r1")
            self.assertEqual(route["items"][0]["kind"], "artifact")
            self.assertEqual(route["items"][0]["artifact"]["title"], "Topology")
            self.assertEqual(server.get_bundle("b1")["artifact_ids"], ["a1"])
            with closing(sqlite3.connect(legacy_path)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"routes", "artifacts", "route_items"} <= tables)
            self.assertFalse({"stacks", "records", "stack_items"} & tables)
        finally:
            server.DB_PATH = original_path
            legacy_path.unlink(missing_ok=True)

    def test_favicons_are_served_and_banner_is_generic(self):
        for path, (name, content_type) in server.BRAND_FILES.items():
            with urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200, path)
                self.assertEqual(response.headers["content-type"], content_type)
                self.assertEqual(response.headers["server"], "detour")
                self.assertEqual(response.read(), (server.BRAND_DIR / name).read_bytes())
        _, page, _ = self.request("/")
        self.assertIn('rel="icon" href="/favicon.ico"', page)


class DatabaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.previous_paths = server.DATA_DIR, server.DB_PATH, server.EXPORT_DIR
        server.DATA_DIR = root
        server.DB_PATH = root / "detour.sqlite"
        server.EXPORT_DIR = root / "exports"

    def tearDown(self):
        server.DATA_DIR, server.DB_PATH, server.EXPORT_DIR = self.previous_paths
        self.tmp.cleanup()

    def backups(self):
        return sorted((server.DATA_DIR / "backups").glob("*.sqlite"))

    def test_new_database_uses_wal_and_current_schema_without_backup(self):
        server.init_db()
        with closing(sqlite3.connect(server.DB_PATH)) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], server.SCHEMA_VERSION)
        self.assertEqual(self.backups(), [])

    def test_existing_unversioned_database_is_backed_up_before_migration(self):
        with closing(sqlite3.connect(server.DB_PATH)) as conn:
            conn.execute("CREATE TABLE routes (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT INTO routes VALUES ('r1', 'Kept', '', 't', 't')")
            conn.commit()
        server.init_db()
        server.init_db()
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT title FROM routes").fetchone()[0], "Kept")
        self.assertEqual(server.get_route("r1")["title"], "Kept")

    def test_newer_schema_is_refused(self):
        with closing(sqlite3.connect(server.DB_PATH)) as conn:
            conn.execute(f"PRAGMA user_version = {server.SCHEMA_VERSION + 1}")
        with self.assertRaises(RuntimeError):
            server.init_db()

    def test_old_backups_are_pruned(self):
        server.init_db()
        with closing(sqlite3.connect(server.DB_PATH)) as conn:
            for _ in range(server.BACKUPS_TO_KEEP + 2):
                server.backup_database(conn, 0)
        self.assertEqual(len(self.backups()), server.BACKUPS_TO_KEEP)


class BrandAndHeaderTests(unittest.TestCase):
    def test_brand_files_exist(self):
        for name, _ in server.BRAND_FILES.values():
            self.assertTrue((server.BRAND_DIR / name).is_file(), name)

    def test_server_header_hides_versions(self):
        self.assertEqual(server.DetourHandler.version_string(None), "detour")


if __name__ == "__main__":
    unittest.main()
