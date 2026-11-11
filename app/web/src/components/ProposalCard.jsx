import { useState } from "react";
import { Button, InlineNotification, Modal, Select, SelectItem, Tag, TextArea, TextInput } from "@carbon/react";
import { api } from "../api.js";
import { AppLink } from "../router.jsx";
import { ACTION_LABELS, ACTION_TAGS, KIND_LABELS, SCOPE_LABELS, TARGET_LABELS, relativeTime } from "../util.js";
import { ErrorNote, KindTag, LineDiff } from "./common.jsx";

function proposalTitle(p) {
  return p.proposed.title || p.proposed.name || p.before?.title || "Untitled";
}

function proposalLines(p) {
  const before = p.before;
  const next = p.proposed;
  switch (p.action) {
    case "add":
      return [{ t: "add", text: next.title }, ...(next.body ? next.body.split("\n").map((text) => ({ t: "add", text })) : [])];
    case "edit": {
      const lines = [];
      if (next.title !== before.title) lines.push({ t: "del", text: before.title }, { t: "add", text: next.title });
      if (next.kind !== before.kind) lines.push({ t: "del", text: `Kind: ${KIND_LABELS[before.kind]}` }, { t: "add", text: `Kind: ${KIND_LABELS[next.kind]}` });
      return lines;
    }
    case "retire":
      return [{ t: "del", text: before.title }, ...(before.body ? before.body.split("\n").map((text) => ({ t: "del", text })) : [])];
    case "move":
      return [
        { t: "del", text: `Route: ${p.route?.name ?? "(deleted)"}` },
        { t: "add", text: `Route: ${p.to_route?.name ?? "(deleted)"}` },
      ];
    case "create_route":
      return [
        { t: "add", text: `Name: ${next.name}` },
        { t: "add", text: `Scope: ${SCOPE_LABELS[next.scope]}${p.parent ? ` of ${p.parent.name}` : ""}` },
        ...(next.description ? [{ t: "add", text: `Description: ${next.description}` }] : []),
        ...(next.targets || []).map((target) => ({ t: "add", text: `Applies to ${TARGET_LABELS[target.kind]}: ${target.value}` })),
      ];
    default:
      return [];
  }
}

function routeLabel(p) {
  if (p.action === "create_route") return p.parent ? `Under ${p.parent.name}` : "New Project route";
  if (p.action === "move") return `${p.route?.name ?? "?"} → ${p.to_route?.name ?? "?"}`;
  if (!p.route) return "Deleted route";
  const scope = SCOPE_LABELS[p.route.scope];
  return p.route.name.toLowerCase() === scope.toLowerCase() ? scope : `${scope} · ${p.route.name}`;
}

function resultText(p) {
  if (p.status === "rejected") {
    return p.decision_note ? `Note for the agent: “${p.decision_note}”` : "The agent sees it as declined and won't propose it again.";
  }
  const prefix = p.status === "auto" ? "The route's policy applied it. " : "";
  switch (p.action) {
    case "add":
      return `${prefix}Added to ${p.route?.name}.`;
    case "edit":
      return `${prefix}${p.route?.name} was updated.`;
    case "retire":
      return `${prefix}Retired. You can restore it from the route.`;
    case "move":
      return `${prefix}Moved to ${p.to_route?.name}.`;
    case "create_route":
      return "The route was created.";
    default:
      return prefix;
  }
}

export default function ProposalCard({ proposal, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const p = proposal;
  const pending = p.status === "pending";
  const editable = ["add", "edit", "create_route"].includes(p.action);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      const result = await fn();
      setEditing(false);
      setRejecting(false);
      onChanged?.(result?.proposal);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const titleId = `proposal-${p.id}-title`;
  return (
    <article className="dt-tile dt-proposal" aria-labelledby={titleId}>
      <div className="dt-tags">
        <Tag size="sm" type={ACTION_TAGS[p.action]}>
          {ACTION_LABELS[p.action]}
        </Tag>
        {p.proposed.kind || p.before?.kind ? <KindTag kind={p.proposed.kind || p.before.kind} /> : null}
        <Tag size="sm" type="gray">
          {routeLabel(p)}
        </Tag>
        <span className="dt-spacer" />
        <span className="dt-label">
          {p.agent} · {relativeTime(p.created_at)}
        </span>
      </div>
      <h2 id={titleId} className="dt-proposal-title">
        {proposalTitle(p)}
      </h2>
      <div>
        <p className="dt-label">Why the agent proposed this</p>
        <p>{p.reason}</p>
      </div>
      {p.action === "edit" && p.proposed.body !== p.before.body ? (
        <>
          {proposalLines(p).length ? <LineDiff lines={proposalLines(p)} label="Title and kind changes" /> : null}
          <LineDiff before={p.before.body} after={p.proposed.body} label="Text changes" />
        </>
      ) : (
        <LineDiff lines={proposalLines(p)} />
      )}
      {p.source ? <p className="dt-label">Source: {p.source}</p> : null}
      <ErrorNote error={error} title="Couldn't save that" onClose={() => setError(null)} />
      {pending ? (
        <div className="dt-actions">
          <Button size="md" disabled={busy} onClick={() => act(() => api.approve(p.id, {}))}>
            Approve
          </Button>
          {editable ? (
            <Button size="md" kind="tertiary" disabled={busy} onClick={() => setEditing(true)}>
              Edit, then approve
            </Button>
          ) : null}
          <Button size="md" kind="ghost" disabled={busy} onClick={() => setRejecting(true)}>
            Reject
          </Button>
        </div>
      ) : (
        <div className="dt-result">
          <InlineNotification
            kind={p.status === "rejected" ? "warning" : "success"}
            lowContrast
            hideCloseButton
            title={p.status === "rejected" ? "Rejected" : p.status === "auto" ? "Applied" : "Approved"}
            subtitle={resultText(p)}
            role="status"
          />
          {p.action === "create_route" && p.route ? (
            <AppLink href={`/routes/${p.route.id}`} className="cds--link">
              Open route
            </AppLink>
          ) : null}
          {/* An approved new route is undone by deleting the route; a rejection can always be taken back. */}
          {p.status === "rejected" || p.action !== "create_route" ? (
            <Button size="md" kind="ghost" disabled={busy} onClick={() => act(() => api.reopen(p.id))}>
              Undo
            </Button>
          ) : null}
        </div>
      )}
      {editing ? <EditModal proposal={p} busy={busy} error={error} onClose={() => setEditing(false)} onSubmit={(changes) => act(() => api.approve(p.id, changes))} /> : null}
      {rejecting ? <RejectModal busy={busy} onClose={() => setRejecting(false)} onSubmit={(note) => act(() => api.reject(p.id, note))} /> : null}
    </article>
  );
}

function EditModal({ proposal, busy, error, onClose, onSubmit }) {
  const isRoute = proposal.action === "create_route";
  const [title, setTitle] = useState(isRoute ? proposal.proposed.name : proposal.proposed.title);
  const [kind, setKind] = useState(proposal.proposed.kind || "note");
  const [body, setBody] = useState(isRoute ? proposal.proposed.description || "" : proposal.proposed.body || "");
  const submit = () => onSubmit(isRoute ? { name: title, description: body } : { title, kind, body });
  return (
    <Modal
      open
      modalHeading="Edit, then approve"
      modalLabel={`${ACTION_LABELS[proposal.action]} · ${proposal.agent}`}
      primaryButtonText="Approve"
      secondaryButtonText="Cancel"
      primaryButtonDisabled={busy || !title.trim()}
      onRequestClose={onClose}
      onRequestSubmit={submit}
      size="md"
    >
      <div className="dt-stack">
        <TextInput id="edit-title" labelText={isRoute ? "Route name" : "Title"} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        {!isRoute ? (
          <Select id="edit-kind" labelText="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND_LABELS).map(([value, label]) => (
              <SelectItem key={value} value={value} text={label} />
            ))}
          </Select>
        ) : null}
        <TextArea id="edit-body" labelText={isRoute ? "Description" : "Text"} rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
        <ErrorNote error={error} title="Couldn't approve" />
      </div>
    </Modal>
  );
}

function RejectModal({ busy, onClose, onSubmit }) {
  const [note, setNote] = useState("");
  return (
    <Modal
      open
      danger
      modalHeading="Reject this proposal?"
      primaryButtonText="Reject"
      secondaryButtonText="Cancel"
      primaryButtonDisabled={busy}
      onRequestClose={onClose}
      onRequestSubmit={() => onSubmit(note)}
      size="sm"
    >
      <TextArea
        id="reject-note"
        labelText="Note for the agent (optional)"
        helperText="Agents can read this, and won't propose the same change again."
        rows={3}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
    </Modal>
  );
}
