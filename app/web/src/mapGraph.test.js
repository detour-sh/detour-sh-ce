import assert from "node:assert/strict";
import { test } from "node:test";
import { addStop, connect, emptyGraph, fromFlow, nextId, removeNode, roadOrder, toFlow } from "./mapGraph.js";

const ids = (graph) => roadOrder(graph).map((n) => n.id);

test("an empty map is Start joined to End", () => {
  const graph = emptyGraph();
  assert.deepEqual(ids(graph), []);
  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].kind, "road");
});

test("addStop puts the new stop on the road just before End and moves End along", () => {
  let graph = addStop(emptyGraph(), "stop", "Rules");
  graph = addStop(graph, "gate", "Safety");
  assert.deepEqual(ids(graph).map((id) => graph.nodes.find((n) => n.id === id).title), ["Rules", "Safety"]);
  const end = graph.nodes.find((n) => n.type === "end");
  const last = graph.nodes.find((n) => n.title === "Safety");
  assert.ok(end.x > last.x);
  assert.equal(graph.edges.filter((e) => e.kind === "road").length, 3);
});

test("nextId never reuses an id", () => {
  assert.equal(nextId("stop", []), "stop-1");
  assert.equal(nextId("stop", ["stop-1", "stop-2"]), "stop-3");
});

test("connecting a road rewires: a stop has one road in and one road out", () => {
  let graph = addStop(addStop(addStop(emptyGraph(), "stop", "A"), "stop", "B"), "stop", "C");
  const [a, b, c] = roadOrder(graph).map((n) => n.id);
  graph = connect(graph, { source: a, target: c, kind: "road" });
  assert.equal(graph.edges.filter((e) => e.kind === "road" && e.from === a).length, 1);
  assert.equal(graph.edges.filter((e) => e.kind === "road" && e.to === c).length, 1);
  assert.ok(!roadOrder(graph).some((n) => n.id === b), "B is now off the road until it is reconnected");
});

test("connect ignores a self-connection and a duplicate", () => {
  const graph = addStop(emptyGraph(), "stop", "A");
  const [a] = roadOrder(graph).map((n) => n.id);
  assert.equal(connect(graph, { source: a, target: a, kind: "road" }), graph);
  const once = connect(graph, { source: "start", target: "end", kind: "detour" });
  assert.equal(once.edges.filter((e) => e.kind === "detour").length, 1);
  assert.equal(connect(once, { source: "start", target: "end", kind: "detour" }), once);
});

test("a detour starts with a label so it can be saved", () => {
  const graph = connect(addStop(emptyGraph(), "stop", "A"), { source: "start", target: "end", kind: "detour" });
  const edge = graph.edges.find((e) => e.kind === "detour");
  assert.ok(edge.label.length > 0);
});

test("removeNode bridges the road and drops that stop's detours", () => {
  let graph = addStop(addStop(addStop(emptyGraph(), "stop", "A"), "stop", "B"), "stop", "C");
  const [a, b, c] = roadOrder(graph).map((n) => n.id);
  graph = connect(graph, { source: a, target: c, kind: "detour" });
  graph = removeNode(graph, b);
  assert.deepEqual(ids(graph), [a, c]);
  assert.equal(graph.edges.filter((e) => e.from === b || e.to === b).length, 0);
  graph = removeNode(graph, a);
  assert.equal(graph.edges.filter((e) => e.kind === "detour").length, 0);
  assert.equal(removeNode(graph, "start"), graph, "Start and End can't be removed");
});

test("toFlow and fromFlow round trip and use separate handles for roads and detours", () => {
  const graph = connect(addStop(addStop(addStop(emptyGraph(), "stop", "A"), "gate", "B"), "stop", "C"), { source: "stop-1", target: "stop-2", kind: "detour" });
  const flow = toFlow(graph);
  const road = flow.edges.find((e) => e.data.kind === "road");
  const bypass = flow.edges.find((e) => e.data.kind === "detour");
  assert.equal(road.sourceHandle, "road-out");
  assert.equal(bypass.sourceHandle, "detour-out");
  assert.deepEqual(fromFlow(flow.nodes, flow.edges), graph);
});
