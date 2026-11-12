import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
import store


class PortalApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.previous = store.DATA_DIR, store.DB_PATH, server.WEB_DIR
        store.DATA_DIR = root
        store.DB_PATH = root / "detour.sqlite"
        web = root / "web"
        (web / "assets").mkdir(parents=True)
        (web / "index.html").write_text('<!doctype html><link rel="icon" href="/favicon.ico"><div id="root"></div>', encoding="utf-8")
        (web / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        server.WEB_DIR = web
        server.reset_web_files()
        store.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DetourHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        store.DATA_DIR, store.DB_PATH, server.WEB_DIR = self.previous
        server.reset_web_files()
        self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        request_headers = {"Content-Type": "application/json"} if body is not None else {}
        request_headers.update(headers or {})
        request = Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            text = response.read().decode(errors="replace")
            data = json.loads(text) if response.headers.get("content-type", "").startswith("application/json") else text
            return response.status, data, response.headers

    def test_full_review_flow_over_http(self):
        status, data, _ = self.request("/api/routes", "POST", {"name": "app", "targets": [{"kind": "folder", "value": "~/code/app"}]})
        self.assertEqual(status, 201)
        route = data["route"]
        key = store.create_key({"name": "Claude Code"})["access_key"]
        proposal = store.propose(key, {"action": "add", "route": route["id"], "kind": "rule", "title": "Sign", "body": "cosign", "reason": "asked"})

        _, data, _ = self.request("/api/session")
        self.assertEqual(data["pending_count"], 1)
        _, data, _ = self.request("/api/proposals?status=pending")
        self.assertEqual([p["id"] for p in data["proposals"]], [proposal["id"]])

        status, data, _ = self.request(f"/api/proposals/{proposal['id']}/approve", "POST", {"changes": {"title": "Sign releases"}})
        self.assertEqual((status, data["proposal"]["status"]), (200, "approved"))
        status, data, _ = self.request(f"/api/proposals/{proposal['id']}/approve", "POST", {})
        self.assertEqual(status, 409)

        _, data, _ = self.request(f"/api/routes/{route['id']}")
        item = data["items"][0]
        self.assertEqual(item["title"], "Sign releases")
        self.request(f"/api/items/{item['id']}", "PUT", {"pinned": True})
        _, data, _ = self.request(f"/api/routes/{route['id']}/events")
        self.assertEqual(data["events"][0]["summary"], "Pinned “Sign releases”")
        status, _, _ = self.request(f"/api/events/{data['events'][0]['id']}/undo", "POST", {})
        self.assertEqual(status, 200)

        # The pin was undone, so the item matches the approval again and the approval can be reverted.
        status, data, _ = self.request(f"/api/proposals/{proposal['id']}/reopen", "POST", {})
        self.assertEqual((status, data["proposal"]["status"]), (200, "pending"))
        self.assertEqual(self.request(f"/api/routes/{route['id']}")[1]["items"], [])
        self.request(f"/api/proposals/{proposal['id']}/approve", "POST", {})
        item = self.request(f"/api/routes/{route['id']}")[1]["items"][0]
        self.request(f"/api/items/{item['id']}", "PUT", {"body": "changed by hand"})
        status, data, _ = self.request(f"/api/proposals/{proposal['id']}/reopen", "POST", {})
        self.assertEqual(status, 409)  # it changed after approval, so reverting would lose the edit

        _, data, _ = self.request(f"/api/routes/{route['id']}/context")
        self.assertEqual(data["layers"][0]["items"][0]["body"], "changed by hand")

    def test_checks_skips_includes_and_runs_over_http(self):
        _, data, _ = self.request("/api/routes", "POST", {"name": "Global", "scope": "global"})
        glob = data["route"]
        _, data, _ = self.request("/api/routes", "POST", {"name": "site", "targets": [{"kind": "folder", "value": "~/code/site"}]})
        site = data["route"]
        _, data, _ = self.request("/api/routes", "POST", {"name": "Personal safety check"})
        suite = data["route"]
        _, data, _ = self.request(f"/api/routes/{glob['id']}/items", "POST", {"kind": "check", "title": "env list check"})
        envs = data["item"]
        _, data, _ = self.request(f"/api/routes/{site['id']}/items", "POST", {"kind": "check", "title": "preview check"})
        self.assertEqual([c["name"] for c in self.request("/api/checks")[1]["checks"]], ["env list check", "preview check"])

        status, data, _ = self.request(f"/api/routes/{site['id']}/skips", "POST", {"check_id": envs["id"], "reason": "no envs"})
        self.assertEqual(status, 201)
        skip = data["skip"]
        status, _, _ = self.request(f"/api/routes/{site['id']}/skips", "POST", {"check_id": envs["id"], "reason": "again"})
        self.assertEqual(status, 409)
        _, data, _ = self.request(f"/api/routes/{site['id']}/checks")
        self.assertEqual(([c["name"] for c in data["checks"]], data["skipped"][0]["name"]), (["preview check"], "env list check"))
        self.assertEqual(self.request(f"/api/routes/{site['id']}")[1]["route"]["skips"][0]["id"], skip["id"])
        self.assertEqual(self.request(f"/api/skips/{skip['id']}/remove", "POST", {})[0], 200)

        status, data, _ = self.request(f"/api/routes/{suite['id']}/includes", "POST", {"route_id": site["id"]})
        self.assertEqual((status, [r["name"] for r in data["route"]["includes"]]), (201, ["site"]))
        self.assertEqual(self.request(f"/api/routes/{site['id']}/includes", "POST", {"route_id": suite["id"]})[0], 400)
        _, data, _ = self.request(f"/api/routes/{suite['id']}/checks")
        self.assertEqual([c["name"] for c in data["checks"]], ["env list check", "preview check"])
        _, data, _ = self.request(f"/api/routes/{suite['id']}/includes/{site['id']}/remove", "POST", {})
        self.assertEqual(data["route"]["includes"], [])

        key = store.create_key({"name": "Claude Code"})["access_key"]
        run = store.record_check_run(key, [{"check": "env list check", "status": "fail", "evidence": "no stage"}], route_ref=suite["id"])
        _, data, _ = self.request("/api/check-runs")
        self.assertEqual([r["id"] for r in data["runs"]], [run["id"]])
        _, data, _ = self.request(f"/api/check-runs/{run['id']}")
        self.assertEqual(data["run"]["outcome"], "failed")
        self.assertEqual(self.request("/api/check-runs/nope")[0], 404)
        self.assertEqual(self.request("/checks")[0], 200)

    def test_items_targets_and_route_updates(self):
        _, data, _ = self.request("/api/routes", "POST", {"name": "a"})
        a = data["route"]["id"]
        _, data, _ = self.request("/api/routes", "POST", {"name": "b"})
        b = data["route"]["id"]
        status, data, _ = self.request(f"/api/routes/{a}/items", "POST", {"kind": "note", "title": "N", "body": "text"})
        self.assertEqual(status, 201)
        item = data["item"]["id"]
        self.assertEqual(self.request(f"/api/items/{item}/retire", "POST", {})[1]["item"]["status"], "retired")
        self.assertEqual(self.request(f"/api/items/{item}/restore", "POST", {})[1]["item"]["status"], "active")
        self.assertEqual(self.request(f"/api/items/{item}/move", "POST", {"route_id": b})[1]["item"]["route_id"], b)
        status, data, _ = self.request(f"/api/routes/{a}/targets", "POST", {"kind": "topic", "value": "Billing, Invoices"})
        self.assertEqual(data["target"]["value"], "billing, invoices")
        self.assertEqual(self.request(f"/api/targets/{data['target']['id']}/remove", "POST", {})[0], 200)
        _, data, _ = self.request(f"/api/routes/{a}", "PUT", {"policy": "read", "paused": True})
        self.assertEqual((data["route"]["policy"], data["route"]["paused"]), ("read", True))
        self.assertEqual(self.request(f"/api/routes/{a}", "PUT", {"policy": "yolo"})[0], 400)
        self.assertEqual(self.request(f"/api/routes/{a}/delete", "POST", {})[0], 200)
        self.assertEqual(self.request(f"/api/routes/{a}")[0], 404)
        _, data, _ = self.request("/api/events")
        self.assertIn("Deleted route “a”", [e["summary"] for e in data["events"]])

    def test_keys_and_export_import(self):
        status, data, _ = self.request("/api/keys", "POST", {"name": "Cursor", "can_propose": False})
        self.assertEqual(status, 201)
        self.assertTrue(data["token"].startswith("dtr_"))
        key_id = data["access_key"]["id"]
        _, listed, _ = self.request("/api/keys")
        self.assertNotIn("token", json.dumps(listed))
        self.assertTrue(self.request(f"/api/keys/{key_id}", "PUT", {"can_propose": True})[1]["access_key"]["can_propose"])
        self.assertIsNotNone(self.request(f"/api/keys/{key_id}/revoke", "POST", {})[1]["access_key"]["revoked_at"])

        self.request("/api/routes", "POST", {"name": "app"})
        status, exported, headers = self.request("/api/export.json")
        self.assertIn("attachment", headers["content-disposition"])
        self.assertEqual(self.request("/api/import", "POST", exported)[0], 201)
        self.assertEqual(len(self.request("/api/routes")[1]["routes"]), 2)

    def test_serves_the_app_shell_and_static_files(self):
        for path in ("/", "/review", "/routes", "/routes/abc123", "/activity", "/connections", "/settings"):
            status, body, headers = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIn('<div id="root">', body)
            self.assertIn("default-src 'self'", headers["content-security-policy"])
            self.assertEqual(headers["x-frame-options"], "DENY")
        status, body, headers = self.request("/assets/index-abc.js")
        self.assertEqual((status, headers["content-type"]), (200, "text/javascript; charset=utf-8"))
        self.assertIn("immutable", headers["cache-control"])
        self.assertEqual(self.request("/assets/../store.py")[0], 404)
        self.assertEqual(self.request("/routes/a/b")[0], 404)
        self.assertEqual(self.request("/api/nope")[0], 404)
        for path, (name, content_type) in server.BRAND_FILES.items():
            status, _, headers = self.request(path)
            self.assertEqual((status, headers["content-type"], headers["server"]), (200, content_type, "detour"), path)

    def test_missing_ui_build_is_explained(self):
        server.WEB_DIR = Path(self.tmp.name) / "missing"
        server.reset_web_files()
        status, body, _ = self.request("/")
        self.assertEqual(status, 503)
        self.assertIn("npm run build", body)

    def test_rejects_cross_origin_and_non_json_writes(self):
        status, data, _ = self.request("/api/routes", "POST", {"name": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, data["error"]), (403, "cross-origin write denied"))
        status, _, _ = self.request("/api/routes", "POST", {"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/routes", "POST", raw=b"name=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("/api/routes", "POST", raw=b"[1]")
        self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/routes")[1]["routes"], [])

    def test_rejects_rebound_host_and_large_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        conn.request("GET", "/api/routes", headers={"Host": f"attacker.example:{self.httpd.server_port}"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        conn.request("GET", "/health", headers={"Host": f"detour:{self.httpd.server_port}"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        conn.putrequest("POST", "/api/routes")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(server.MAX_BODY_BYTES + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()


class PackagingTests(unittest.TestCase):
    root = Path(__file__).parent

    def test_compose_runs_web_and_mcp_in_one_local_container(self):
        compose = (self.root.parent / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:3787:3787"', compose)
        self.assertIn('"127.0.0.1:3788:3788"', compose)
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "/app/run.py"]', dockerfile)
        self.assertIn("npm ci --ignore-scripts", dockerfile)

    def test_web_dependencies_are_pinned_and_install_scripts_are_off(self):
        package = json.loads((self.root / "web" / "package.json").read_text(encoding="utf-8"))
        for name, version in {**package["dependencies"], **package["devDependencies"]}.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", name)
        self.assertIn("ignore-scripts=true", (self.root / "web" / ".npmrc").read_text(encoding="utf-8"))

    def test_brand_files_exist_and_banner_is_generic(self):
        for name, _ in server.BRAND_FILES.values():
            self.assertTrue((server.BRAND_DIR / name).is_file(), name)
        self.assertEqual(server.DetourHandler.version_string(None), "detour")


if __name__ == "__main__":
    unittest.main()
