import { useCallback, useEffect, useMemo, useState } from "react";
import { Accordion, AccordionItem, Button, Select, SelectItem, Tab, TabList, TabPanel, TabPanels, Tabs, Tag, TextArea, TextInput } from "@carbon/react";
import { Add, Reset, Save, TrashCan } from "@carbon/icons-react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "../api.js";
import { usePageTitle } from "../App.jsx";
import { ErrorNote, Loading } from "../components/common.jsx";
import { addStop, connect, emptyGraph, fromFlow, removeNode, roadOrder, toFlow } from "../mapGraph.js";
import { OUTCOME_TAGS, RESULT_TAGS, SCOPE_LABELS, relativeTime, useResource } from "../util.js";

const TYPE_LABELS = { start: "Start", stop: "Stop", gate: "Gate", end: "End" };
const TYPE_TAGS = { start: "green", stop: "blue", gate: "magenta", end: "gray" };

/** A box on the map. Roads enter on the left and leave on the right; detours leave and enter along the bottom. */
function StopNode({ data, selected }) {
  const { type, title } = data;
  return (
    <div className={`dt-node dt-node--${type}${selected ? " is-selected" : ""}`}>
      {type !== "start" ? <Handle id="road-in" type="target" position={Position.Left} /> : null}
      <Tag size="sm" type={TYPE_TAGS[type]}>
        {TYPE_LABELS[type]}
      </Tag>
      <strong className="dt-node-title">{title}</strong>
      {type !== "end" ? <Handle id="road-out" type="source" position={Position.Right} /> : null}
      {type !== "end" ? <Handle id="detour-out" className="dt-handle--detour" type="source" position={Position.Bottom} style={{ left: "30%" }} /> : null}
      {type !== "start" ? <Handle id="detour-in" className="dt-handle--detour" type="target" position={Position.Bottom} style={{ left: "70%" }} /> : null}
    </div>
  );
}
const NODE_TYPES = { stop: StopNode };

function Inspector({ graph, selected, items, onChange, onRemove, onClear }) {
  const node = selected?.kind === "node" ? graph.nodes.find((n) => n.id === selected.id) : null;
  const edge = selected?.kind === "edge" ? graph.edges.find((e) => e.id === selected.id) : null;
  const order = roadOrder(graph);
  const loose = graph.nodes.filter((n) => n.type !== "start" && n.type !== "end" && !order.some((o) => o.id === n.id));
  if (node) {
    const fixed = node.type === "start" || node.type === "end";
    return (
      <div className="dt-tile" aria-label="Selected stop">
        <h2>
          {TYPE_LABELS[node.type]}
          {fixed ? "" : `: ${node.title}`}
        </h2>
        {fixed ? (
          <p className="dt-muted">{node.type === "start" ? "Every agent starts here." : "The road ends here. Everything on the road must be done, or detoured around, to get here."}</p>
        ) : (
          <>
            <TextInput id="node-title" labelText="Name" value={node.title} maxLength={200} onChange={(e) => onChange({ title: e.target.value })} />
            <TextArea
              id="node-note"
              labelText="What the agent must do here"
              helperText={node.type === "gate" ? "A gate needs evidence: say what the agent should look at." : "Instructions the agent reads at this stop."}
              rows={4}
              value={node.note}
              maxLength={2000}
              onChange={(e) => onChange({ note: e.target.value })}
            />
            <Select id="node-item" labelText="Item to follow" helperText="Optional. Its text is sent to the agent with this stop." value={node.item_id} onChange={(e) => onChange({ item_id: e.target.value })}>
              <SelectItem value="" text="None" />
              {items.map((item) => (
                <SelectItem key={item.id} value={item.id} text={`${item.title} (${item.kind})`} />
              ))}
            </Select>
            <div className="dt-actions">
              <Button kind="danger--tertiary" size="sm" renderIcon={TrashCan} onClick={onRemove}>
                Remove stop
              </Button>
            </div>
          </>
        )}
      </div>
    );
  }
  if (edge) {
    const isDetour = edge.kind === "detour";
    return (
      <div className="dt-tile" aria-label="Selected connection">
        <h2>{isDetour ? "Detour" : "Road"}</h2>
        {isDetour ? (
          <>
            <p className="dt-muted">
              Agents may skip every stop between these two points, but only when the label below really applies. Each detour they take is logged with their reason.
            </p>
            <TextInput id="edge-label" labelText="When it applies" helperText="For example: mvp deploy" value={edge.label} maxLength={200} onChange={(e) => onChange({ label: e.target.value })} />
            <TextArea id="edge-reason" labelText="Why this detour is allowed" rows={3} value={edge.reason} maxLength={2000} onChange={(e) => onChange({ reason: e.target.value })} />
          </>
        ) : (
          <p className="dt-muted">The road from one stop to the next. Agents follow it in order.</p>
        )}
        <div className="dt-actions">
          <Button kind="danger--tertiary" size="sm" renderIcon={TrashCan} onClick={onRemove}>
            Remove {isDetour ? "detour" : "road"}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="dt-tile" aria-label="Road summary">
      <h2>The road</h2>
      {order.length ? (
        <ol className="dt-road-list">
          {order.map((n) => (
            <li key={n.id}>
              <Tag size="sm" type={TYPE_TAGS[n.type]}>
                {TYPE_LABELS[n.type]}
              </Tag>{" "}
              {n.title}
            </li>
          ))}
        </ol>
      ) : (
        <p className="dt-muted">No stops yet. Add a stop or a gate, then drag from the dot on its right edge to the dot on the left edge of the next box.</p>
      )}
      {loose.length ? (
        <p className="dt-warning" role="status">
          Not on the road yet: {loose.map((n) => n.title).join(", ")}. Connect {loose.length === 1 ? "it" : "them"} before saving.
        </p>
      ) : null}
      <p className="dt-muted">Drag from a box's right dot to build the road. Drag from a bottom dot to the right-hand bottom dot of a later box to draw a detour.</p>
      <Button kind="ghost" size="sm" onClick={onClear}>
        Clear selection
      </Button>
    </div>
  );
}

function Editor({ routeId, onDirty }) {
  const loaded = useResource(() => Promise.all([api.routeMap(routeId), api.route(routeId)]), [routeId]);
  const [graph, setGraph] = useState(null);
  const [baseline, setBaseline] = useState("");
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!loaded.data) return;
    const next = loaded.data[0].graph || emptyGraph();
    setGraph(next);
    setBaseline(JSON.stringify(next));
    setSelected(null);
    setError(null);
  }, [loaded.data]);

  const reactFlow = useReactFlow();
  const nodeCount = graph?.nodes.length;
  useEffect(() => {
    // Fit once the canvas has its final size, and again when a stop is added or removed.
    const timer = setTimeout(() => reactFlow.fitView({ padding: 0.2, maxZoom: 1, duration: 150 }), 60);
    return () => clearTimeout(timer);
  }, [reactFlow, nodeCount, loaded.data]);

  const dirty = graph !== null && JSON.stringify(graph) !== baseline;
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const items = useMemo(() => (loaded.data ? loaded.data[1].items.filter((i) => i.status === "active") : []), [loaded.data]);
  const flow = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const base = toFlow(graph);
    return {
      nodes: base.nodes.map((n) => ({ ...n, selected: selected?.kind === "node" && selected.id === n.id })),
      edges: base.edges.map((e) => ({
        ...e,
        selected: selected?.kind === "edge" && selected.id === e.id,
        markerEnd: { type: MarkerType.ArrowClosed },
        interactionWidth: 24,
      })),
    };
  }, [graph, selected]);

  const onNodesChange = useCallback((changes) => {
    setGraph((g) => {
      let next = g;
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          next = { ...next, nodes: next.nodes.map((n) => (n.id === change.id ? { ...n, x: change.position.x, y: change.position.y } : n)) };
        }
      }
      return next;
    });
  }, []);
  const onConnect = useCallback((c) => {
    const kind = c.sourceHandle === "detour-out" ? "detour" : "road";
    setGraph((g) => connect(g, { source: c.source, target: c.target, kind }));
    setSelected(null);
  }, []);
  const patch = (fields) => {
    if (selected.kind === "node") setGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === selected.id ? { ...n, ...fields } : n)) }));
    else setGraph((g) => ({ ...g, edges: g.edges.map((e) => (e.id === selected.id ? { ...e, ...fields } : e)) }));
  };
  const remove = () => {
    if (selected.kind === "node") setGraph((g) => removeNode(g, selected.id));
    else setGraph((g) => ({ ...g, edges: g.edges.filter((e) => e.id !== selected.id) }));
    setSelected(null);
  };
  const add = (type) => {
    const title = type === "gate" ? "New gate" : "New stop";
    setGraph((g) => {
      const next = addStop(g, type, title);
      setSelected({ kind: "node", id: next.nodes.at(-1).id });
      return next;
    });
  };
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveRouteMap(routeId, graph);
      setGraph(saved.graph);
      setBaseline(JSON.stringify(saved.graph));
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };
  const discard = () => {
    setGraph(JSON.parse(baseline));
    setSelected(null);
    setError(null);
  };

  if (loaded.error) return <ErrorNote error={loaded.error} title="Couldn't load this map" />;
  if (!graph) return <Loading />;
  return (
    <>
      <div className="dt-actions dt-map-toolbar">
        <Button kind="tertiary" size="md" renderIcon={Add} onClick={() => add("stop")}>
          Add stop
        </Button>
        <Button kind="tertiary" size="md" renderIcon={Add} onClick={() => add("gate")}>
          Add gate
        </Button>
        <Button kind="primary" size="md" renderIcon={Save} disabled={!dirty || saving} onClick={save}>
          {saving ? "Saving" : "Save map"}
        </Button>
        <Button kind="ghost" size="md" renderIcon={Reset} disabled={!dirty || saving} onClick={discard}>
          Discard changes
        </Button>
        {dirty ? <Tag type="warm-gray">Unsaved changes</Tag> : loaded.data[0].saved ? <Tag type="green">Saved {relativeTime(loaded.data[0].updated_at)}</Tag> : <Tag type="gray">Not saved yet</Tag>}
      </div>
      <ErrorNote error={error} title="Couldn't save this map" onClose={() => setError(null)} />
      <div className="dt-split dt-map-split">
        <div className="dt-main">
          <div className="dt-map-canvas" role="application" aria-label="Route map canvas">
            <ReactFlow
              nodes={flow.nodes}
              edges={flow.edges}
              nodeTypes={NODE_TYPES}
              onNodesChange={onNodesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => setSelected({ kind: "node", id: node.id })}
              onEdgeClick={(_, edge) => setSelected({ kind: "edge", id: edge.id })}
              onPaneClick={() => setSelected(null)}
              deleteKeyCode={null}
              fitView
              fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
              minZoom={0.3}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
              nodesConnectable
              elementsSelectable
            >
              <Background gap={24} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </div>
        <div className="dt-aside">
          <Inspector graph={graph} selected={selected} items={items} onChange={patch} onRemove={remove} onClear={() => setSelected(null)} />
        </div>
      </div>
    </>
  );
}

function RunResults({ results }) {
  return (
    <ol className="dt-rows" aria-label="Stops in this run">
      {results.map((result) => {
        const [color, text] = RESULT_TAGS[result.status] || ["gray", result.status];
        return (
          <li key={result.stop} className="dt-row">
            <span className="dt-row-kind">
              <Tag size="sm" type={color}>
                {text}
              </Tag>
            </span>
            <span className="dt-row-main">
              <strong>{result.title}</strong>
              <span className="dt-muted">
                {" "}
                · {result.type === "gate" ? "Gate" : "Stop"} · {result.route}
              </span>
              {result.evidence ? <div className="dt-body-01">{result.evidence}</div> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Runs() {
  const runs = useResource(() => api.mapRuns(), []);
  const list = runs.data?.runs || [];
  if (runs.error) return <ErrorNote error={runs.error} title="Couldn't load runs" />;
  if (!runs.data) return <Loading />;
  if (!list.length) {
    return (
      <div className="dt-empty">
        <h2>No route runs yet</h2>
        <p className="dt-muted">When an agent works through a route's map, it reports each stop here: what it checked, what failed, and every detour it took.</p>
      </div>
    );
  }
  return (
    <Accordion align="start" className="dt-tile dt-runs">
      {list.map((run, index) => {
        const [color, text] = OUTCOME_TAGS[run.outcome];
        const counts = [[run.passed, "passed"], [run.failed, "failed"], [run.blocked, "blocked"], [run.not_run, "not run"], [run.detoured, "detoured"]].filter(([n]) => n).map(([n, w]) => `${n} ${w}`).join(", ");
        return (
          <AccordionItem
            key={run.id}
            open={index === 0}
            title={
              <span className="dt-tags">
                <Tag size="sm" type={color}>
                  {text}
                </Tag>
                <strong>{run.label}</strong>
                <span className="dt-muted">
                  {counts || "no stops"} · {run.agent} · {relativeTime(run.created_at)}
                </span>
              </span>
            }
          >
            <RunResults results={run.results} />
          </AccordionItem>
        );
      })}
    </Accordion>
  );
}

export default function MapPage() {
  usePageTitle("Route map");
  const routes = useResource(() => api.routes(), []);
  const [routeId, setRouteId] = useState("");
  const [dirty, setDirty] = useState(false);
  const list = routes.data?.routes || [];
  const chosen = routeId || list.find((r) => r.scope === "project")?.id || list[0]?.id || "";
  const route = list.find((r) => r.id === chosen);
  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Route map</h1>
          <p>
            Lay out the road an agent must travel for a route: the stops it must follow, the gates it must prove, and the detours you allow. Global, Project and
            Sub-detour roads are joined in that order, so nothing is skipped by accident.
          </p>
        </div>
      </div>
      <ErrorNote error={routes.error} title="Couldn't load routes" />
      <Tabs>
        <TabList aria-label="Route map views" contained>
          <Tab>Map</Tab>
          <Tab>Runs</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            {!routes.data ? (
              routes.error ? null : <Loading />
            ) : !list.length ? (
              <div className="dt-empty">
                <h2>No routes yet</h2>
                <p className="dt-muted">Create a route first. Each route gets its own map.</p>
              </div>
            ) : (
              <>
                <Select
                  id="map-route"
                  className="dt-map-select"
                  labelText="Route"
                  helperText={dirty ? "Save or discard your changes before switching routes." : route ? `${SCOPE_LABELS[route.scope]} route` : ""}
                  value={chosen}
                  disabled={dirty}
                  onChange={(e) => setRouteId(e.target.value)}
                >
                  {list.map((r) => (
                    <SelectItem key={r.id} value={r.id} text={`${r.name} (${SCOPE_LABELS[r.scope]})`} />
                  ))}
                </Select>
                <ReactFlowProvider>
                  <Editor key={chosen} routeId={chosen} onDirty={setDirty} />
                </ReactFlowProvider>
              </>
            )}
          </TabPanel>
          <TabPanel>
            <Runs />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}
