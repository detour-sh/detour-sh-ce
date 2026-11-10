import { useState } from "react";
import {
  Button,
  ContentSwitcher,
  DataTable,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TableToolbar,
  TableToolbarContent,
  TableToolbarSearch,
  Tag,
  Theme,
} from "@carbon/react";
import { Add } from "@carbon/icons-react";
import { api } from "../api.js";
import { useSession, usePageTitle } from "../App.jsx";
import { ErrorNote, Loading, TargetTag } from "../components/common.jsx";
import { RouteModal } from "../components/modals.jsx";
import { AppLink, useRouter } from "../router.jsx";
import { SCOPE_LABELS, policyText, relativeTime, useResource } from "../util.js";

const HEADERS = [
  { key: "name", header: "Route" },
  { key: "targets", header: "Applies to" },
  { key: "items", header: "Items" },
  { key: "policy", header: "Agents can" },
  { key: "attention", header: "Needs attention" },
  { key: "changed", header: "Last change" },
];
const SCOPE_FILTERS = ["all", "global", "project", "sub"];

function attention(route) {
  if (route.pending_count) return { text: `${route.pending_count} waiting for you`, type: "blue" };
  if (route.stale_count) return { text: `${route.stale_count} not used in 90 days`, type: "warm-gray" };
  if (route.scope !== "global" && !route.targets.length) return { text: "No targets", type: "warm-gray" };
  return { text: "Nothing", type: "green" };
}

export default function RoutesPage() {
  usePageTitle("Routes");
  const { session } = useSession();
  const { navigate } = useRouter();
  const routes = useResource(() => api.routes(), []);
  const [scope, setScope] = useState("all");
  const [creating, setCreating] = useState(false);

  const all = routes.data?.routes || [];
  const shown = scope === "all" ? all : all.filter((r) => r.scope === scope);
  const byId = Object.fromEntries(all.map((r) => [r.id, r]));
  const rows = shown.map((r) => ({
    id: r.id,
    name: `${r.name} ${SCOPE_LABELS[r.scope]}`,
    targets: r.scope === "global" ? "Everywhere" : r.targets.map((t) => `${t.kind} ${t.value}`).join(" "),
    items: String(r.item_count),
    policy: policyText(r),
    attention: attention(r).text,
    changed: r.last_change ? relativeTime(r.last_change.created_at) : "",
  }));

  function cell(routeId, key) {
    const r = byId[routeId];
    switch (key) {
      case "name":
        return (
          <>
            <AppLink href={`/routes/${r.id}`} className="cds--link">
              {r.name}
            </AppLink>
            <div className="dt-label">
              {SCOPE_LABELS[r.scope]}
              {r.parent ? ` of ${r.parent.name}` : ""}
            </div>
          </>
        );
      case "targets":
        if (r.scope === "global") return <span className="dt-muted">Everywhere</span>;
        if (!r.targets.length) return <span className="dt-muted">Only when asked by name</span>;
        return (
          <div className="dt-tags">
            {r.targets.slice(0, 3).map((t) => (
              <span key={t.id} className="dt-tags">
                <TargetTag kind={t.kind} />
                <code className="dt-code">{t.value}</code>
              </span>
            ))}
            {r.targets.length > 3 ? <span className="dt-label">+{r.targets.length - 3} more</span> : null}
          </div>
        );
      case "items":
        return r.item_count;
      case "policy":
        return <span className="dt-muted">{policyText(r)}</span>;
      case "attention": {
        const a = attention(r);
        return (
          <Tag size="sm" type={a.type}>
            {a.text}
          </Tag>
        );
      }
      case "changed":
        return r.last_change ? (
          <span className="dt-muted">
            {relativeTime(r.last_change.created_at)} · {r.last_change.actor}
          </span>
        ) : null;
      default:
        return null;
    }
  }

  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Routes</h1>
          <p>
            A route is a named set of context: rules, decisions, runbooks, prompts and notes. Attach it to whatever it's about, such as a repo, a folder, a topic or an
            artifact, and agents pick it up when they work there.
          </p>
        </div>
        <Button renderIcon={Add} onClick={() => setCreating(true)}>
          New route
        </Button>
      </div>

      <ErrorNote error={routes.error} title="Couldn't load routes" />
      {routes.loading && !routes.data ? <Loading /> : null}

      {routes.data ? (
        <div className="dt-stack">
          <ContentSwitcher selectedIndex={SCOPE_FILTERS.indexOf(scope)} onChange={({ index }) => setScope(SCOPE_FILTERS[index])} size="md" aria-label="Filter by scope">
            <Switch name="all" text="All" />
            <Switch name="global" text="Global" />
            <Switch name="project" text="Project" />
            <Switch name="sub" text="Sub-detour" />
          </ContentSwitcher>
          <DataTable rows={rows} headers={HEADERS}>
            {({ rows: tableRows, headers, getHeaderProps, getRowProps, getTableProps, getToolbarProps, onInputChange }) => (
              <TableContainer>
                <TableToolbar {...getToolbarProps()} aria-label="Routes table toolbar">
                  <TableToolbarContent>
                    <TableToolbarSearch onChange={onInputChange} placeholder="Filter routes" persistent labelText="Filter routes" />
                  </TableToolbarContent>
                </TableToolbar>
                <div className="dt-table-wrap">
                  <Table {...getTableProps()} aria-label="Routes">
                    <TableHead>
                      <TableRow>
                        {headers.map((header) => {
                          const { key, ...props } = getHeaderProps({ header });
                          return (
                            <TableHeader key={key} {...props}>
                              {header.header}
                            </TableHeader>
                          );
                        })}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {tableRows.map((row) => {
                        const { key, ...props } = getRowProps({ row });
                        return (
                          <TableRow key={key} {...props}>
                            {row.cells.map((c) => (
                              <TableCell key={c.id}>{cell(row.id, c.info.header)}</TableCell>
                            ))}
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                {!tableRows.length ? <p className="dt-muted dt-gap-top">No routes match.</p> : null}
              </TableContainer>
            )}
          </DataTable>

          <Theme theme="g100">
            <section className="dt-ask" aria-labelledby="ask-heading">
              <div>
                <h2 id="ask-heading">You don't have to set routes up by hand</h2>
                <p className="dt-muted">Ask any connected agent. It drafts the route and its context over MCP, and the draft comes to Review for you to approve.</p>
              </div>
              <Theme theme="g90" className="dt-prompt">
                “Make a route for my tax paperwork. Have it apply to ~/Documents/taxes and keep it read-only.”
              </Theme>
            </section>
          </Theme>
        </div>
      ) : null}

      {creating ? (
        <RouteModal
          routes={all}
          nestedEnabled={session?.nested_enabled}
          onClose={() => setCreating(false)}
          onSubmit={async (payload) => {
            const { route } = await api.createRoute(payload);
            navigate(`/routes/${route.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
