import { InlineLoading, InlineNotification, Tag } from "@carbon/react";
import { KIND_LABELS, KIND_TAGS, TARGET_LABELS, TARGET_TAGS, lineDiff } from "../util.js";

export function ErrorNote({ error, title = "Something went wrong", onClose }) {
  if (!error) return null;
  return (
    <InlineNotification
      kind="error"
      lowContrast
      title={title}
      subtitle={error.message || String(error)}
      hideCloseButton={!onClose}
      onCloseButtonClick={onClose}
    />
  );
}

export function Loading({ label = "Loading" }) {
  return <InlineLoading description={label} />;
}

export function KindTag({ kind }) {
  return (
    <Tag size="sm" type={KIND_TAGS[kind] || "gray"}>
      {KIND_LABELS[kind] || kind}
    </Tag>
  );
}

export function TargetTag({ kind }) {
  return (
    <Tag size="sm" type={TARGET_TAGS[kind] || "gray"}>
      {TARGET_LABELS[kind] || kind}
    </Tag>
  );
}

const SIGNS = { add: "+", del: "−", ctx: " " };
const SPOKEN = { add: "Added: ", del: "Removed: ", ctx: "Unchanged: " };

/** Line-by-line before and after, as in a code review. */
export function LineDiff({ lines, before, after, label = "Proposed change" }) {
  const rows = lines || lineDiff(before || "", after || "");
  if (!rows.length) return null;
  return (
    <div className="dt-diff" role="group" aria-label={label}>
      {rows.map((line, index) => (
        <div key={index} className={`dt-diff-line dt-diff-line--${line.t}`}>
          <span className="dt-diff-sign" aria-hidden="true">
            {SIGNS[line.t]}
          </span>
          <span className="dt-sr-only">{SPOKEN[line.t]}</span>
          <span>{line.text || " "}</span>
        </div>
      ))}
    </div>
  );
}
