import { Accordion, AccordionItem, Tag } from "@carbon/react";
import { api } from "../api.js";
import { usePageTitle } from "../App.jsx";
import CheckResults from "../components/CheckResults.jsx";
import { ErrorNote, Loading } from "../components/common.jsx";
import { OUTCOME_TAGS, relativeTime, useResource } from "../util.js";

function counts(run) {
  return [
    [run.passed, "passed"],
    [run.failed, "failed"],
    [run.not_run, "not run"],
    [run.skipped, "skipped"],
  ]
    .filter(([n]) => n)
    .map(([n, word]) => `${n} ${word}`)
    .join(", ");
}

export default function ChecksPage() {
  usePageTitle("Check runs");
  const runs = useResource(() => api.checkRuns(), []);
  const list = runs.data?.runs || [];
  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Check runs</h1>
          <p>
            Each time an agent verifies your checks, the result lands here: what passed, what failed, and what a route skipped. You don't have to read
            the code to know your requirements held.
          </p>
        </div>
      </div>
      <ErrorNote error={runs.error} title="Couldn't load check runs" />
      {!runs.data ? (
        runs.error ? null : <Loading />
      ) : list.length ? (
        <Accordion align="start" className="dt-tile dt-runs">
          {list.map((run, index) => {
            const [color, text] = OUTCOME_TAGS[run.outcome];
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
                      {counts(run) || "no checks"} · {run.agent} · {relativeTime(run.created_at)}
                    </span>
                  </span>
                }
              >
                <CheckResults results={run.results} label={`Results of ${run.label}`} />
              </AccordionItem>
            );
          })}
        </Accordion>
      ) : (
        <div className="dt-empty">
          <h2>No check runs yet</h2>
          <p className="dt-muted">
            Add a Check to a route, for example “env list check: this project defines dev, test, stage and prod”. Connected agents verify the checks
            that apply before they finish a task and report the results here.
          </p>
        </div>
      )}
    </div>
  );
}
