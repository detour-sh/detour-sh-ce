import { useState } from "react";
import { Button, Tab, TabList, Tabs, Tag } from "@carbon/react";
import { api } from "../api.js";
import { useSession, usePageTitle } from "../App.jsx";
import ProposalCard from "../components/ProposalCard.jsx";
import { ErrorNote, Loading } from "../components/common.jsx";
import { AppLink, useRouter } from "../router.jsx";
import { POLICY_LABELS, useResource } from "../util.js";

const FILTERS = ["pending", "decided", "all"];
const ASIDE_LIMIT = 6;

export default function Review() {
  usePageTitle("Review");
  const { refreshSession } = useSession();
  const { navigate } = useRouter();
  const [filter, setFilter] = useState("pending");
  // Decided cards stay where they were, with their result and Undo, until the list is loaded again.
  const [overrides, setOverrides] = useState({});
  const [counts, setCounts] = useState(null);
  const proposals = useResource(async () => {
    const data = await api.proposals(filter);
    setCounts(data.counts);
    setOverrides({});
    return data;
  }, [filter]);
  const routes = useResource(() => api.routes(), []);
  const keys = useResource(() => api.keys(), []);
  const decided = counts ? counts.approved + counts.rejected + counts.auto : 0;
  const list = proposals.data?.proposals.map((p) => overrides[p.id] || p) || [];

  const changed = async (updated) => {
    if (updated) setOverrides((current) => ({ ...current, [updated.id]: updated }));
    routes.reload();
    refreshSession();
    try {
      setCounts((await api.proposals(filter)).counts);
    } catch {
      // The counts refresh on the next load.
    }
  };

  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Review</h1>
          <p>Agents propose changes to your routes as they work. Nothing reaches a route until you approve it, and you can undo every decision.</p>
        </div>
        <Button kind="tertiary" onClick={() => navigate("/routes")}>
          Review policies
        </Button>
      </div>

      <Tabs selectedIndex={FILTERS.indexOf(filter)} onChange={({ selectedIndex }) => setFilter(FILTERS[selectedIndex])}>
        <TabList aria-label="Filter proposals">
          <Tab>Waiting for you{counts ? ` (${counts.pending})` : ""}</Tab>
          <Tab>Decided{counts ? ` (${decided})` : ""}</Tab>
          <Tab>All</Tab>
        </TabList>
      </Tabs>

      <div className="dt-split dt-gap-top">
        <section className="dt-main" aria-label="Proposals">
          <ErrorNote error={proposals.error} title="Couldn't load proposals" />
          {proposals.loading && !proposals.data ? <Loading /> : null}
          {proposals.data && list.length === 0 ? (
            <div className="dt-empty">
              <h2>{filter === "pending" ? "You're caught up" : "Nothing here yet"}</h2>
              <p className="dt-muted">
                When an agent proposes a change to a route, it shows up here. Connect an agent under{" "}
                <AppLink href="/connections" className="cds--link">
                  Agent connections
                </AppLink>
                .
              </p>
            </div>
          ) : null}
          {list.map((proposal) => (
            <ProposalCard key={proposal.id} proposal={proposal} onChanged={changed} />
          ))}
        </section>

        <aside className="dt-aside" aria-label="Review policies and agents">
          <section className="dt-tile">
            <h2>Review policy</h2>
            <p className="dt-muted">Each route decides how much it trusts agents: ask every time, approve notes on its own, or stay read-only.</p>
            {routes.data?.routes.length ? (
              <dl className="dt-dl">
                {routes.data.routes.slice(0, ASIDE_LIMIT).map((route) => (
                  <div key={route.id}>
                    <dt>
                      <AppLink href={`/routes/${route.id}`} className="cds--link">
                        {route.name}
                      </AppLink>
                    </dt>
                    <dd>
                      <Tag size="sm" type={route.paused ? "warm-gray" : route.policy === "read" ? "gray" : route.policy === "notes" ? "green" : "blue"}>
                        {route.paused ? "Paused" : POLICY_LABELS[route.policy]}
                      </Tag>
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="dt-muted">No routes yet.</p>
            )}
            {routes.data?.routes.length > ASIDE_LIMIT ? (
              <AppLink href="/routes" className="cds--link">
                All {routes.data.routes.length} routes
              </AppLink>
            ) : null}
          </section>
          <section className="dt-tile">
            <h2>Connected agents</h2>
            {keys.data?.access_keys.filter((k) => !k.revoked_at).length ? (
              <dl className="dt-dl">
                {keys.data.access_keys
                  .filter((k) => !k.revoked_at)
                  .slice(0, ASIDE_LIMIT)
                  .map((key) => (
                    <div key={key.id}>
                      <dt>{key.name}</dt>
                      <dd className="dt-muted">{key.can_propose ? "Read and propose" : "Read only"}</dd>
                    </div>
                  ))}
              </dl>
            ) : (
              <p className="dt-muted">No agents connected.</p>
            )}
            <AppLink href="/connections" className="cds--link">
              Manage connections
            </AppLink>
          </section>
        </aside>
      </div>
    </div>
  );
}
