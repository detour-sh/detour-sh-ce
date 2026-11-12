import { Tag } from "@carbon/react";
import { RESULT_TAGS } from "../util.js";

/** One row per check: its result, what the agent saw, and which route it came from. */
export default function CheckResults({ results, label = "Check results" }) {
  if (!results.length) return <p className="dt-muted">No checks apply here.</p>;
  return (
    <ol className="dt-rows" aria-label={label}>
      {results.map((result) => {
        const [color, text] = RESULT_TAGS[result.status] || ["gray", result.status];
        return (
          <li key={`${result.check_id || result.id}-${result.status}`} className="dt-row">
            <span className="dt-row-kind">
              <Tag size="sm" type={color}>
                {text}
              </Tag>
            </span>
            <span className="dt-row-main">
              <strong>{result.name}</strong>
              <span className="dt-muted"> · {result.route}</span>
              {result.instead_of ? <span className="dt-muted"> · runs instead of “{result.instead_of}”</span> : null}
              {result.evidence ? <div className="dt-body-01">{result.evidence}</div> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
