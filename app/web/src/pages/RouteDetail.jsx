import { useState } from "react";
import {
  Breadcrumb,
  BreadcrumbItem,
  Button,
  DataTable,
  InlineNotification,
  Modal,
  OverflowMenu,
  OverflowMenuItem,
  RadioTile,
  Select,
  SelectItem,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableExpandHeader,
  TableExpandRow,
  TableExpandedRow,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  Tag,
  TileGroup,
  Toggle,
} from "@carbon/react";
import { Add, Pause, Play } from "@carbon/icons-react";
import { api } from "../api.js";
import { useSession, usePageTitle } from "../App.jsx";
import CheckResults from "../components/CheckResults.jsx";
import HistoryList from "../components/HistoryList.jsx";
import TargetList from "../components/TargetList.jsx";
import { ErrorNote, KindTag, Loading } from "../components/common.jsx";
import { ConfirmModal, ItemModal, MoveModal, RouteModal, SkipModal } from "../components/modals.jsx";
import { AppLink, useLinkHandler, useRouter } from "../router.jsx";
import { SCOPE_LABELS, relativeTime, useResource } from "../util.js";

const ITEM_HEADERS = [
  { key: "title", header: "Item" },
  { key: "kind", header: "Kind" },
  { key: "by", header: "Added by" },
  { key: "used", header: "Last used" },
];
const POLICIES = [
  { value: "ask", label: "Ask me every time", help: "Every proposed change waits in Review." },
  { value: "notes", label: "Auto-approve notes, ask for everything else", help: "Notes go in at once and show in History. Rules, decisions, runbooks, prompts and references wait for you." },
  { value: "read", label: "Read only", help: "Agents can read this route but can't propose changes to it." },
];
const STALE_MS = 90 * 24 * 60 * 60 * 1000;

export default function RouteDetail({ routeId }) {
  const { session, refreshSession } = useSession();
  const { navigate } = useRouter();
  const onLink = useLinkHandler();
  const detail = useResource(() => api.route(routeId), [routeId]);
  const events = useResource(() => api.routeEvents(routeId), [routeId]);
  const routes = useResource(() => api.routes(), []);
  const keys = useResource(() => api.keys(), []);
  const checks = useResource(() => api.routeChecks(routeId), [routeId]);
  const [tab, setTab] = useState(0);
  const [showRetired, setShowRetired] = useState(false);
  const [modal, setModal] = useState(null);
  const [error, setError] = useState(null);
  const route = detail.data?.route;
  usePageTitle(route?.name || "Route");

  const reload = () => {
    detail.reload();
    events.reload();
    routes.reload();
    checks.reload();
    refreshSession();
  };
  async function run(fn) {
    setError(null);
    try {
      await fn();
      reload();
    } catch (err) {
      setError(err);
    }
  }

  if (detail.error) {
    return (
      <div className="dt-page">
        <ErrorNote error={detail.error} title={detail.error.status === 404 ? "This route doesn't exist" : "Couldn't load the route"} />
        <p className="dt-gap-top">
          <AppLink href="/routes" className="cds--link">
            Back to routes
          </AppLink>
        </p>
      </div>
    );
  }
  if (!route) return <Loading />;

  const items = detail.data.items.filter((item) => showRetired || item.status === "active");
  const byId = Object.fromEntries(items.map((item) => [item.id, item]));
  const rows = items.map((item) => ({ id: item.id, title: item.title, kind: item.kind, by: item.created_by, used: relativeTime(item.last_used_at) }));
  const activeCount = detail.data.items.filter((item) => item.status === "active").length;
  const retiredCount = detail.data.items.length - activeCount;
  const allRoutes = routes.data?.routes || [];

  return (
    <div className="dt-page">
      <Breadcrumb noTrailingSlash>
        <BreadcrumbItem href="/routes" onClick={onLink}>
          Routes
        </BreadcrumbItem>
        {route.parent ? (
          <BreadcrumbItem href={`/routes/${route.parent.id}`} onClick={onLink}>
            {route.parent.name}
          </BreadcrumbItem>
        ) : null}
        <BreadcrumbItem isCurrentPage>{route.name}</BreadcrumbItem>
      </Breadcrumb>

      <div className="dt-page-header dt-gap-top">
        <div>
          <div className="dt-title-row">
            <h1>{route.name}</h1>
            <Tag type="gray">{SCOPE_LABELS[route.scope]}</Tag>
            {route.paused ? <Tag type="warm-gray">Agent access paused</Tag> : null}
          </div>
          <p>
            {route.description || (route.scope === "global" ? "Applies to all of your work." : "No description.")}
            {route.scope !== "global" ? " Global routes outrank this one when they disagree." : ""}
          </p>
        </div>
        <div className="dt-actions">
          <Button kind="tertiary" renderIcon={route.paused ? Play : Pause} onClick={() => run(() => api.updateRoute(route.id, { paused: !route.paused }))}>
            {route.paused ? "Resume agent access" : "Pause agent access"}
          </Button>
          <Button renderIcon={Add} onClick={() => setModal({ type: "add" })}>
            Add context
          </Button>
          <OverflowMenu aria-label="More route actions" flipped size="lg">
            <OverflowMenuItem itemText="Edit route details" onClick={() => setModal({ type: "route" })} />
            <OverflowMenuItem itemText="Delete route" isDelete hasDivider onClick={() => setModal({ type: "delete" })} />
          </OverflowMenu>
        </div>
      </div>

      <ErrorNote error={error} title="Couldn't save that" onClose={() => setError(null)} />

      <div className="dt-split">
        <div className="dt-main">
          <Tabs selectedIndex={tab} onChange={({ selectedIndex }) => setTab(selectedIndex)}>
            <TabList aria-label="Route sections">
              <Tab>Context</Tab>
              <Tab>Checks</Tab>
              <Tab>Applies to</Tab>
              <Tab>Agent access</Tab>
              <Tab>History</Tab>
            </TabList>
            <TabPanels>
              <TabPanel className="dt-stack">
                {retiredCount ? <Toggle id="show-retired" size="sm" labelText={`Show retired (${retiredCount})`} hideLabel={false} toggled={showRetired} onToggle={setShowRetired} /> : null}
                {rows.length ? (
                  <DataTable rows={rows} headers={ITEM_HEADERS}>
                    {({ rows: tableRows, headers, getHeaderProps, getRowProps, getTableProps, getExpandedRowProps }) => (
                      <TableContainer>
                        <div className="dt-table-wrap">
                          <Table {...getTableProps()} aria-label="Context items">
                            <TableHead>
                              <TableRow>
                                <TableExpandHeader aria-label="Show text" />
                                {headers.map((header) => {
                                  const { key, ...props } = getHeaderProps({ header });
                                  return (
                                    <TableHeader key={key} {...props}>
                                      {header.header}
                                    </TableHeader>
                                  );
                                })}
                                <TableHeader>
                                  <span className="dt-sr-only">Actions</span>
                                </TableHeader>
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {tableRows.map((row) => {
                                const item = byId[row.id];
                                const stale = item.status === "active" && Date.now() - new Date(item.last_used_at || item.created_at).getTime() > STALE_MS;
                                const { key, ...rowProps } = getRowProps({ row });
                                const { key: expandedKey, ...expandedProps } = getExpandedRowProps ? getExpandedRowProps({ row }) : { key: `${row.id}-x` };
                                return [
                                  <TableExpandRow key={key} {...rowProps} aria-label={`Show text of ${item.title}`}>
                                    <TableCell>
                                      <strong>{item.title}</strong>
                                      <div className="dt-tags">
                                        {item.pinned ? <Tag size="sm" type="gray">Pinned</Tag> : null}
                                        {item.status === "retired" ? <Tag size="sm" type="red">Retired</Tag> : null}
                                        {stale ? <Tag size="sm" type="warm-gray">Not used in 90 days</Tag> : null}
                                      </div>
                                    </TableCell>
                                    <TableCell>
                                      <KindTag kind={item.kind} />
                                    </TableCell>
                                    <TableCell>{item.created_by}</TableCell>
                                    <TableCell>{relativeTime(item.last_used_at)}</TableCell>
                                    <TableCell className="cds--table-column-menu">
                                      <OverflowMenu aria-label={`Actions for ${item.title}`} flipped size="sm">
                                        <OverflowMenuItem itemText="Edit" onClick={() => setModal({ type: "edit", item })} />
                                        <OverflowMenuItem itemText={item.pinned ? "Unpin" : "Pin to top"} onClick={() => run(() => api.updateItem(item.id, { pinned: !item.pinned }))} />
                                        <OverflowMenuItem itemText="Move to another route" disabled={allRoutes.length < 2} onClick={() => setModal({ type: "move", item })} />
                                        {item.status === "active" ? (
                                          <OverflowMenuItem itemText="Retire" isDelete hasDivider onClick={() => run(() => api.retireItem(item.id))} />
                                        ) : (
                                          <OverflowMenuItem itemText="Restore" hasDivider onClick={() => run(() => api.restoreItem(item.id))} />
                                        )}
                                      </OverflowMenu>
                                    </TableCell>
                                  </TableExpandRow>,
                                  row.isExpanded ? (
                                    <TableExpandedRow key={expandedKey} {...expandedProps} colSpan={headers.length + 2}>
                                      <div className="dt-body">{item.body || "No text."}</div>
                                    </TableExpandedRow>
                                  ) : null,
                                ];
                              })}
                            </TableBody>
                          </Table>
                        </div>
                      </TableContainer>
                    )}
                  </DataTable>
                ) : (
                  <div className="dt-empty">
                    <h2>No context yet</h2>
                    <p className="dt-muted">Add a rule, decision or runbook, or let a connected agent propose some as it works.</p>
                    <Button kind="tertiary" renderIcon={Add} onClick={() => setModal({ type: "add" })}>
                      Add context
                    </Button>
                  </div>
                )}
              </TabPanel>

              <TabPanel className="dt-stack">
                <RouteChecks route={route} plan={checks} routes={allRoutes} run={run} onSkip={() => setModal({ type: "skip" })} />
              </TabPanel>

              <TabPanel className="dt-stack">
                <div>
                  <h2 className="dt-heading-03">Where this route applies</h2>
                  <p className="dt-muted">
                    An agent gets this route when any target matches what it's working on. A route can reach past one repository: add folders, files, topics or artifacts.
                  </p>
                </div>
                {route.scope === "global" ? (
                  <InlineNotification kind="info" lowContrast hideCloseButton title="Global routes apply everywhere" subtitle="They don't need targets." />
                ) : (
                  <TargetList route={route} onChanged={reload} />
                )}
              </TabPanel>

              <TabPanel className="dt-stack">
                <TileGroup
                  legend="When an agent proposes a change"
                  name="policy"
                  valueSelected={route.policy}
                  onChange={(value) => run(() => api.updateRoute(route.id, { policy: value }))}
                >
                  {POLICIES.map((policy) => (
                    <RadioTile key={policy.value} id={`policy-${policy.value}`} value={policy.value}>
                      <strong>{policy.label}</strong>
                      <p className="dt-muted">{policy.help}</p>
                    </RadioTile>
                  ))}
                </TileGroup>
                <div className="dt-tile">
                  <h2>Agents</h2>
                  <p className="dt-muted">Access keys apply to every route. Change what a key may do under Agent connections.</p>
                  {keys.data?.access_keys.filter((k) => !k.revoked_at).length ? (
                    <dl className="dt-dl">
                      {keys.data.access_keys
                        .filter((k) => !k.revoked_at)
                        .map((key) => (
                          <div key={key.id}>
                            <dt>{key.name}</dt>
                            <dd className="dt-muted">
                              {route.paused || route.policy === "read" || !key.can_propose ? "Read" : "Read, propose"} · last used {relativeTime(key.last_used_at).toLowerCase()}
                            </dd>
                          </div>
                        ))}
                    </dl>
                  ) : (
                    <p className="dt-muted">No agents connected.</p>
                  )}
                  <AppLink href="/connections" className="cds--link">
                    Agent connections
                  </AppLink>
                </div>
              </TabPanel>

              <TabPanel>
                {events.data ? <HistoryList events={events.data.events} onChanged={reload} /> : <Loading />}
              </TabPanel>
            </TabPanels>
          </Tabs>
        </div>

        <aside className="dt-aside" aria-label="Route summary">
          <section className="dt-tile">
            <h2>At a glance</h2>
            <dl className="dt-stats">
              <div>
                <dt>Items</dt>
                <dd>{activeCount}</dd>
              </div>
              <div>
                <dt>Waiting for you</dt>
                <dd>
                  {route.pending_count ? (
                    <AppLink href="/" className="cds--link">
                      {route.pending_count}
                    </AppLink>
                  ) : (
                    0
                  )}
                </dd>
              </div>
              {route.children.length ? (
                <div>
                  <dt>Sub-detours</dt>
                  <dd className="dt-body-01">
                    {route.children.map((child, index) => (
                      <span key={child.id}>
                        {index ? ", " : ""}
                        <AppLink href={`/routes/${child.id}`} className="cds--link">
                          {child.name}
                        </AppLink>
                      </span>
                    ))}
                  </dd>
                </div>
              ) : null}
              <div>
                <dt>Outranked by</dt>
                <dd className="dt-body-01">{route.scope === "global" ? "Nothing" : route.scope === "sub" ? "Global, its Project" : "Global"}</dd>
              </div>
            </dl>
          </section>
          <section className="dt-tile">
            <h2>What agents receive</h2>
            <p className="dt-muted">See the exact context an agent gets in this route, after higher routes are merged in.</p>
            <Button kind="tertiary" size="md" onClick={() => setModal({ type: "preview" })}>
              Preview context
            </Button>
          </section>
        </aside>
      </div>

      {modal?.type === "add" ? <ItemModal onClose={() => setModal(null)} onSubmit={async (payload) => { await api.createItem(route.id, payload); reload(); }} /> : null}
      {modal?.type === "edit" ? <ItemModal item={modal.item} onClose={() => setModal(null)} onSubmit={async (payload) => { await api.updateItem(modal.item.id, payload); reload(); }} /> : null}
      {modal?.type === "move" ? <MoveModal item={modal.item} routes={allRoutes} onClose={() => setModal(null)} onSubmit={async (to) => { await api.moveItem(modal.item.id, to); reload(); }} /> : null}
      {modal?.type === "route" ? (
        <RouteModal route={route} routes={allRoutes} nestedEnabled={session?.nested_enabled} onClose={() => setModal(null)} onSubmit={async (payload) => { await api.updateRoute(route.id, payload); reload(); }} />
      ) : null}
      {modal?.type === "delete" ? (
        <ConfirmModal
          heading={`Delete “${route.name}”?`}
          body={`This deletes the route, its ${activeCount} items${route.children.length ? ", its Sub-detours" : ""} and any proposals waiting for it. It can't be undone. Export your data first from Settings if you might want it back.`}
          confirmText="Delete route"
          onClose={() => setModal(null)}
          onConfirm={async () => {
            await api.deleteRoute(route.id);
            refreshSession();
            navigate("/routes");
          }}
        />
      ) : null}
      {modal?.type === "preview" ? <PreviewModal routeId={route.id} onClose={() => setModal(null)} /> : null}
      {modal?.type === "skip" ? <SkipModal route={route} onClose={() => setModal(null)} onSubmit={async (payload) => { await api.addSkip(route.id, payload); reload(); }} /> : null}
    </div>
  );
}

function RouteChecks({ route, plan, routes, run, onSkip }) {
  const [includeId, setIncludeId] = useState("");
  const included = new Set(route.includes.map((r) => r.id));
  const candidates = routes.filter((r) => r.id !== route.id && !included.has(r.id) && r.scope !== "global");
  const planned = plan.data
    ? [
        ...plan.data.checks.map((check) => ({ ...check, status: "runs", evidence: check.requirement })),
        ...plan.data.skipped.map((skip) => ({
          ...skip,
          status: "skipped",
          evidence: `Skipped by ${skip.skipped_by}: ${skip.reason}${skip.instead ? ` Runs “${skip.instead}” instead.` : ""}`,
        })),
      ]
    : [];
  return (
    <>
      <section>
        <h2 className="dt-heading-03">Checks that run here</h2>
        <p className="dt-muted">
          When an agent asks for this route's checks, it verifies each one and reports pass or fail. Global checks always run. Included routes add
          their checks, and skips take checks out.
        </p>
        <ErrorNote error={plan.error} title="Couldn't load the checks" />
        {plan.data ? <CheckResults results={planned} label="Checks that run in this route" /> : plan.error ? null : <Loading />}
      </section>

      <section>
        <h2 className="dt-heading-03">Also run checks from</h2>
        <p className="dt-muted">Combine routes into one suite, such as a “Personal safety check” that runs your environment and release-gate checks together.</p>
        {route.includes.length ? (
          <ul className="dt-rows" aria-label="Included routes">
            {route.includes.map((other) => (
              <li key={other.id} className="dt-row">
                <span className="dt-row-main">
                  <AppLink href={`/routes/${other.id}`} className="cds--link">
                    {other.name}
                  </AppLink>
                </span>
                <Button kind="ghost" size="sm" onClick={() => run(() => api.removeInclude(route.id, other.id))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="dt-form-row dt-gap-top">
          <Select id="include-route" labelText="Route to include" value={includeId} onChange={(e) => setIncludeId(e.target.value)}>
            <SelectItem value="" text={candidates.length ? "Choose a route" : "No other routes"} />
            {candidates.map((other) => (
              <SelectItem key={other.id} value={other.id} text={other.name} />
            ))}
          </Select>
          <Button
            kind="tertiary"
            size="md"
            disabled={!includeId}
            onClick={() =>
              run(async () => {
                await api.addInclude(route.id, includeId);
                setIncludeId("");
              })
            }
          >
            Include
          </Button>
        </div>
      </section>

      <section>
        <h2 className="dt-heading-03">Skipped here</h2>
        <p className="dt-muted">Skip a check that doesn't fit this route, and optionally run a different check in its place. Only you can add skips; agents can't.</p>
        {route.skips.length ? (
          <ul className="dt-rows" aria-label="Skipped checks">
            {route.skips.map((skip) => (
              <li key={skip.id} className="dt-row">
                <span className="dt-row-main">
                  <strong>{skip.check?.name || "Deleted check"}</strong>
                  {skip.instead ? <span className="dt-muted"> → runs “{skip.instead.name}” instead</span> : null}
                  <div className="dt-body-01">{skip.reason}</div>
                </span>
                <Button kind="ghost" size="sm" onClick={() => run(() => api.removeSkip(skip.id))}>
                  Stop skipping
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        <Button kind="tertiary" size="md" className="dt-gap-top" renderIcon={Add} onClick={onSkip}>
          Skip a check
        </Button>
      </section>
    </>
  );
}

function PreviewModal({ routeId, onClose }) {
  const context = useResource(() => api.routeContext(routeId), [routeId]);
  return (
    <Modal open passiveModal modalHeading="What agents receive" onRequestClose={onClose} size="lg">
      <ErrorNote error={context.error} />
      {context.data ? (
        <>
          <p className="dt-muted">{context.data.precedence}</p>
          {context.data.layers.map((layer) => (
            <section key={layer.route.id} className="dt-context-layer">
              <h3>
                {layer.authority}. {layer.route.name} <span className="dt-label">({SCOPE_LABELS[layer.route.scope]})</span>
              </h3>
              {layer.items.length ? (
                layer.items.map((item) => (
                  <div key={item.id} className="dt-gap-top">
                    <div className="dt-tags">
                      <KindTag kind={item.kind} />
                      <strong>{item.title}</strong>
                    </div>
                    {item.body ? <div className="dt-body">{item.body}</div> : null}
                  </div>
                ))
              ) : (
                <p className="dt-muted">No items.</p>
              )}
            </section>
          ))}
        </>
      ) : (
        <Loading />
      )}
    </Modal>
  );
}
