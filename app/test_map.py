import json
import unittest

import store
from test_store import StoreCase


def node(node_id, kind="stop", title=None, **extra):
    return {"id": node_id, "type": kind, "title": title if title is not None else node_id.title(), "x": 0, "y": 0, **extra}


def road(a, b):
    return {"id": f"{a}-{b}", "from": a, "to": b, "kind": "road"}


def detour(a, b, label="mvp deploy", reason="Prototype with no real users."):
    return {"id": f"d-{a}-{b}", "from": a, "to": b, "kind": "detour", "label": label, "reason": reason}


def chain(*mids, detours=()):
    """start -> each middle node -> end, as (id, type) pairs, plus any detour edges."""
    nodes = [node("start", "start", "Start"), *[node(i, t) for i, t in mids], node("end", "end", "End")]
    ids = ["start", *[i for i, _ in mids], "end"]
    return {"nodes": nodes, "edges": [road(a, b) for a, b in zip(ids, ids[1:])] + list(detours)}


class MapStoreTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.glob = store.create_route({"name": "Global", "scope": "global"})
        self.app = self.project("openrouter", targets=[{"kind": "folder", "value": "~/code/openrouter"}])
        self.where = {"folder": "/home/me/code/openrouter"}

    def test_a_route_without_a_saved_map_has_an_empty_road(self):
        found = store.get_map(self.app["id"])
        self.assertFalse(found["saved"])
        self.assertEqual([n["type"] for n in found["graph"]["nodes"]], ["start", "end"])
        self.assertEqual(found["order"], [])
        self.assertEqual(found["route"]["name"], "openrouter")
        with self.assertRaises(KeyError):
            store.get_map("missing")

    def test_save_map_keeps_the_road_order_and_detours(self):
        graph = chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop"), detours=[detour("rules", "ship")])
        saved = store.save_map(self.app["id"], graph)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["order"], ["rules", "safety", "ship"])
        self.assertEqual(store.get_map("openrouter")["graph"]["edges"][-1]["label"], "mvp deploy")

    def test_a_map_must_be_one_road_from_start_to_end(self):
        bad = {
            "no start": {"nodes": [node("end", "end")], "edges": []},
            "two ends": {"nodes": [node("start", "start"), node("end", "end"), node("e2", "end")], "edges": [road("start", "end")]},
            "fork": {"nodes": [node("start", "start"), node("a"), node("b"), node("end", "end")],
                     "edges": [road("start", "a"), road("start", "b"), road("a", "end")]},
            "island": {"nodes": [node("start", "start"), node("a"), node("end", "end")], "edges": [road("start", "end")]},
            "cycle": {"nodes": [node("start", "start"), node("a"), node("b"), node("end", "end")],
                      "edges": [road("start", "a"), road("a", "b"), road("b", "a")]},
            "into start": {"nodes": [node("start", "start"), node("end", "end")], "edges": [road("start", "end"), road("end", "start")]},
            "self loop": {"nodes": [node("start", "start"), node("end", "end")], "edges": [road("start", "end"), road("end", "end")]},
        }
        for name, graph in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                store.save_map(self.app["id"], graph)
        with self.assertRaisesRegex(ValueError, "isn't on the road"):
            store.save_map(self.app["id"], bad["island"])

    def test_detours_must_skip_ahead_and_say_why(self):
        mids = (("a", "stop"), ("b", "gate"), ("c", "stop"))
        cases = {
            "backwards": detour("c", "a"),
            "skips nothing": detour("a", "b"),
            "no label": {**detour("a", "c"), "label": ""},
            "unknown stop": detour("a", "zzz"),
            "into start": detour("b", "start"),
        }
        for name, edge in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                store.save_map(self.app["id"], chain(*mids, detours=[edge]))
        store.save_map(self.app["id"], chain(*mids, detours=[detour("start", "end", "skip everything")]))

    def test_nodes_and_edges_are_validated(self):
        item = store.create_item(self.app["id"], {"kind": "check", "title": "env check", "body": "dev, test, stage, prod"})
        store.save_map(self.app["id"], chain(("a", "stop")))
        bad_nodes = [
            node("a", "stop", title=""),  # a stop needs a title
            node("a", "wall"),
            node("a", "stop", item_id="nope"),
            node("has space", "stop"),
            node("a", "stop", x="far"),
        ]
        for bad in bad_nodes:
            graph = chain(("a", "stop"))
            graph["nodes"][1] = bad
            with self.subTest(bad), self.assertRaises(ValueError):
                store.save_map(self.app["id"], graph)
        graph = chain(("a", "gate"))
        graph["nodes"][1]["item_id"] = item["id"]
        self.assertEqual(store.save_map(self.app["id"], graph)["graph"]["nodes"][1]["item_id"], item["id"])
        with self.assertRaises(ValueError):
            store.save_map(self.app["id"], {"nodes": [node(f"n{i}") for i in range(store.MAX_MAP_NODES + 1)], "edges": []})
        with self.assertRaises(ValueError):
            store.save_map(self.app["id"], "nope")

    def test_plan_walks_every_layer_global_first(self):
        store.save_map(self.glob["id"], chain(("secrets", "gate")))
        store.save_map(self.app["id"], chain(("rules", "stop"), ("tests", "gate")))
        plan = store.plan_route(**self.where)
        self.assertEqual([s["id"].split(":")[1] for s in plan["stops"]], ["secrets", "rules", "tests"])
        self.assertEqual([s["route"] for s in plan["stops"]], ["Global", "openrouter", "openrouter"])
        self.assertEqual([s["type"] for s in plan["stops"]], ["gate", "stop", "gate"])
        self.assertEqual(plan["routes"], ["Global", "openrouter"])

    def test_a_layer_without_a_map_is_one_implicit_stop_and_paused_layers_are_left_out(self):
        store.save_map(self.app["id"], chain(("rules", "stop")))
        plan = store.plan_route(**self.where)
        self.assertEqual([s["route"] for s in plan["stops"]], ["Global", "openrouter"])
        self.assertEqual(plan["stops"][0]["id"], f"{self.glob['id']}:context")
        store.update_route(self.glob["id"], {"paused": True})
        self.assertEqual([s["route"] for s in store.plan_route(**self.where)["stops"]], ["openrouter"])

    def test_a_stop_brings_its_item_and_a_stale_item_is_tolerated(self):
        rule = store.create_item(self.app["id"], {"kind": "rule", "title": "Sign releases", "body": "Always sign."})
        graph = chain(("rules", "stop"))
        graph["nodes"][1].update(item_id=rule["id"], note="Read this first.")
        store.save_map(self.app["id"], graph)
        stop = store.plan_route(**self.where)["stops"][-1]
        self.assertEqual((stop["item"]["title"], stop["note"]), ("Sign releases", "Read this first."))
        store.delete_item(rule["id"])
        self.assertIsNone(store.plan_route(**self.where)["stops"][-1]["item"])

    def test_plan_lists_detours_with_the_stops_they_skip(self):
        store.save_map(self.app["id"], chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop"),
                                             detours=[detour("rules", "ship", "mvp deploy", "No real users yet.")]))
        found = store.plan_route(**self.where)["detours"]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["label"], found[0]["reason"]), ("mvp deploy", "No real users yet."))
        self.assertEqual(found[0]["skips"], [f"{self.app['id']}:safety"])
        self.assertEqual((found[0]["from"], found[0]["to"]), (f"{self.app['id']}:rules", f"{self.app['id']}:ship"))

    def report(self, results, **where):
        return store.record_route_run(self.agent_key(), results, **(where or self.where))

    def agent_key(self):
        return {"id": None, "name": "Claude Code"}

    def stop_id(self, name, route=None):
        return f"{(route or self.app)['id']}:{name}"

    def test_a_run_passes_when_every_stop_is_reported(self):
        store.save_map(self.app["id"], chain(("rules", "stop"), ("safety", "gate")))
        run = self.report([
            {"stop": self.stop_id("rules"), "status": "pass"},
            {"stop": self.stop_id("safety"), "status": "pass", "evidence": "Tests ran green."},
            {"stop": f"{self.glob['id']}:context", "status": "pass"},
        ])
        self.assertEqual((run["outcome"], run["passed"], run["agent"]), ("passed", 3, "Claude Code"))
        self.assertEqual([r["stop"] for r in run["results"]], [f"{self.glob['id']}:context", self.stop_id("rules"), self.stop_id("safety")])
        self.assertIn("✓", run["summary"])
        self.assertEqual(store.list_route_runs()[0]["id"], run["id"])

    def test_a_gate_needs_evidence_and_unreported_stops_are_not_run(self):
        store.save_map(self.app["id"], chain(("safety", "gate")))
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.report([{"stop": self.stop_id("safety"), "status": "pass"}])
        run = self.report([{"stop": self.stop_id("safety"), "status": "pass", "evidence": "Looked at CI."}])
        self.assertEqual((run["outcome"], run["not_run"]), ("incomplete", 1))

    def test_a_failed_stop_blocks_everything_after_it(self):
        store.save_map(self.app["id"], chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop")))
        run = self.report([
            {"stop": f"{self.glob['id']}:context", "status": "pass"},
            {"stop": self.stop_id("rules"), "status": "fail", "evidence": "Found an unsigned release."},
            {"stop": self.stop_id("safety"), "status": "pass", "evidence": "Looks fine."},
        ])
        by_stop = {r["stop"]: r["status"] for r in run["results"]}
        self.assertEqual(by_stop[self.stop_id("rules")], "fail")
        self.assertEqual(by_stop[self.stop_id("safety")], "blocked")
        self.assertEqual(by_stop[self.stop_id("ship")], "blocked")
        self.assertEqual((run["outcome"], run["failed"], run["blocked"]), ("failed", 1, 2))

    def test_a_taken_detour_marks_the_skipped_stops_detoured(self):
        store.save_map(self.app["id"], chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop"),
                                             detours=[detour("rules", "ship")]))
        detour_id = store.plan_route(**self.where)["detours"][0]["id"]
        run = self.report([
            {"stop": f"{self.glob['id']}:context", "status": "pass"},
            {"stop": self.stop_id("rules"), "status": "pass"},
            {"detour": detour_id, "evidence": "This is an MVP deploy with no users."},
            {"stop": self.stop_id("ship"), "status": "pass"},
        ])
        by_stop = {r["stop"]: r for r in run["results"]}
        self.assertEqual(by_stop[self.stop_id("safety")]["status"], "detoured")
        self.assertIn("mvp deploy", by_stop[self.stop_id("safety")]["evidence"])
        self.assertIn("MVP deploy with no users", by_stop[self.stop_id("safety")]["evidence"])
        self.assertEqual((run["outcome"], run["detoured"]), ("passed", 1))

    def test_detour_reports_are_checked(self):
        store.save_map(self.app["id"], chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop"),
                                             detours=[detour("rules", "ship")]))
        detour_id = store.plan_route(**self.where)["detours"][0]["id"]
        with self.assertRaisesRegex(ValueError, "why"):
            self.report([{"detour": detour_id}])
        with self.assertRaisesRegex(ValueError, "isn't a detour"):
            self.report([{"detour": "nope", "evidence": "x"}])
        with self.assertRaisesRegex(ValueError, "detoured around"):
            self.report([{"detour": detour_id, "evidence": "mvp"}, {"stop": self.stop_id("safety"), "status": "pass", "evidence": "x"}])

    def test_report_errors_are_plain(self):
        store.save_map(self.app["id"], chain(("rules", "stop")))
        stop = self.stop_id("rules")
        for results, pattern in (
            ([], "list"),
            ("nope", "list"),
            ([{"stop": "ghost", "status": "pass"}], "isn't a stop"),
            ([{"stop": stop, "status": "maybe"}], "status"),
            ([{"stop": stop, "status": "pass"}, {"stop": stop, "status": "pass"}], "twice"),
            (["x"], "object"),
        ):
            with self.subTest(results), self.assertRaisesRegex(ValueError, pattern):
                self.report(results)
        with self.assertRaisesRegex(ValueError, "Pass the same"):
            store.record_route_run(self.agent_key(), [{"stop": stop, "status": "pass"}])

    def test_maps_follow_their_route_through_export_and_delete(self):
        rule = store.create_item(self.app["id"], {"kind": "rule", "title": "Sign releases"})
        graph = chain(("rules", "stop"), ("safety", "gate"), ("ship", "stop"), detours=[detour("rules", "ship")])
        graph["nodes"][1]["item_id"] = rule["id"]
        store.save_map(self.app["id"], graph)
        exported = json.loads(json.dumps(store.export_all()))
        self.assertEqual(exported["maps"][0]["route_id"], self.app["id"])
        counts = store.import_all(exported)
        self.assertEqual(counts["maps"], 1)
        copies = [r for r in store.list_routes() if r["name"] == "openrouter" and r["id"] != self.app["id"]]
        copied = store.get_map(copies[0]["id"])
        self.assertEqual(copied["order"], ["rules", "safety", "ship"])
        copied_item = next(i for i in store.list_items(copies[0]["id"]) if i["title"] == "Sign releases")
        self.assertEqual(copied["graph"]["nodes"][1]["item_id"], copied_item["id"])
        store.delete_route(self.app["id"])
        with self.assertRaises(KeyError):
            store.get_map(self.app["id"])
        with self.assertRaises(ValueError):  # old exports have no maps and still import
            store.import_all({**exported, "maps": "nope"})
        old = {k: v for k, v in exported.items() if k != "maps"}
        self.assertEqual(store.import_all(old)["maps"], 0)


if __name__ == "__main__":
    unittest.main()
