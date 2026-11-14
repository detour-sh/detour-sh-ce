// Pure helpers for the route map: the shape the portal API stores, and the shape React Flow draws.
// The server (store.py) is the judge of whether a map is valid. These helpers only make editing it pleasant.

export const NODE_GAP = 220;

export function emptyGraph() {
  return {
    nodes: [
      { id: "start", type: "start", title: "Start", note: "", item_id: "", x: 0, y: 0 },
      { id: "end", type: "end", title: "End", note: "", item_id: "", x: NODE_GAP * 2, y: 0 },
    ],
    edges: [{ id: "start-end", from: "start", to: "end", kind: "road", label: "", reason: "" }],
  };
}

export function nextId(prefix, taken) {
  const used = new Set(taken);
  let n = 1;
  while (used.has(`${prefix}-${n}`)) n += 1;
  return `${prefix}-${n}`;
}

/** The nodes on the road between Start and End, in order. */
export function roadOrder(graph) {
  const ahead = new Map(graph.edges.filter((e) => e.kind === "road").map((e) => [e.from, e.to]));
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const start = graph.nodes.find((n) => n.type === "start");
  const order = [];
  const seen = new Set();
  let current = start?.id;
  while (ahead.has(current) && !seen.has(current)) {
    seen.add(current);
    current = ahead.get(current);
    if (byId.get(current)?.type !== "end") order.push(byId.get(current));
  }
  return order;
}

/** Add a stop or gate to the end of the road, just before End. */
export function addStop(graph, type, title) {
  const end = graph.nodes.find((n) => n.type === "end");
  const last = roadOrder(graph).at(-1) || graph.nodes.find((n) => n.type === "start");
  const id = nextId(type, graph.nodes.map((n) => n.id));
  const node = { id, type, title, note: "", item_id: "", x: end.x, y: end.y };
  const nodes = graph.nodes.map((n) => (n.id === end.id ? { ...n, x: n.x + NODE_GAP } : n)).concat(node);
  const edges = graph.edges.filter((e) => !(e.kind === "road" && e.from === last.id && e.to === end.id));
  const edgeId = (a, b) => nextId(`${a}-${b}`, edges.map((e) => e.id)).replace(/-1$/, "");
  edges.push({ id: edgeId(last.id, id), from: last.id, to: id, kind: "road", label: "", reason: "" });
  edges.push({ id: edgeId(id, end.id), from: id, to: end.id, kind: "road", label: "", reason: "" });
  return { nodes, edges };
}

/** Draw a connection. A road replaces the road already leaving the source and the road already entering the target. */
export function connect(graph, { source, target, kind }) {
  if (!source || !target || source === target) return graph;
  if (graph.edges.some((e) => e.kind === kind && e.from === source && e.to === target)) return graph;
  const taken = graph.edges.map((e) => e.id);
  let edges = graph.edges;
  if (kind === "road") edges = edges.filter((e) => !(e.kind === "road" && (e.from === source || e.to === target)));
  const id = nextId(`${kind}-${source}-${target}`, taken).replace(/-1$/, "");
  const edge = { id, from: source, to: target, kind, label: kind === "detour" ? "New detour" : "", reason: "" };
  return { ...graph, edges: [...edges, edge] };
}

/** Remove a stop. The road closes up over the gap, and detours that used it go too. Start and End stay. */
export function removeNode(graph, id) {
  const node = graph.nodes.find((n) => n.id === id);
  if (!node || node.type === "start" || node.type === "end") return graph;
  const before = graph.edges.find((e) => e.kind === "road" && e.to === id);
  const after = graph.edges.find((e) => e.kind === "road" && e.from === id);
  const edges = graph.edges.filter((e) => e.from !== id && e.to !== id);
  if (before && after) edges.push({ id: nextId(`${before.from}-${after.to}`, edges.map((e) => e.id)).replace(/-1$/, ""), from: before.from, to: after.to, kind: "road", label: "", reason: "" });
  return { nodes: graph.nodes.filter((n) => n.id !== id), edges };
}

const HANDLES = { road: ["road-out", "road-in"], detour: ["detour-out", "detour-in"] };

export function toFlow(graph) {
  return {
    nodes: graph.nodes.map((n) => ({ id: n.id, type: "stop", position: { x: n.x, y: n.y }, data: { type: n.type, title: n.title, note: n.note, item_id: n.item_id } })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      source: e.from,
      target: e.to,
      sourceHandle: HANDLES[e.kind][0],
      targetHandle: HANDLES[e.kind][1],
      label: e.kind === "detour" ? e.label : undefined,
      className: `dt-edge dt-edge--${e.kind}`,
      data: { kind: e.kind, label: e.label, reason: e.reason },
    })),
  };
}

export function fromFlow(nodes, edges) {
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.type, title: n.data.title, note: n.data.note, item_id: n.data.item_id, x: Math.round(n.position.x * 10) / 10, y: Math.round(n.position.y * 10) / 10 })),
    edges: edges.map((e) => ({ id: e.id, from: e.source, to: e.target, kind: e.data.kind, label: e.data.label, reason: e.data.reason })),
  };
}
