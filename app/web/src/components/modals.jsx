import { useState } from "react";
import { Modal, RadioButton, RadioButtonGroup, Select, SelectItem, TextArea, TextInput } from "@carbon/react";
import { KIND_LABELS, POLICY_LABELS, SCOPE_LABELS } from "../util.js";
import { ErrorNote } from "./common.jsx";

/** Shared submit plumbing: busy state and an error shown inside the modal. */
function useSubmit(onSubmit, onClose) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const submit = async (value) => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(value);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, submit };
}

export function RouteModal({ route, routes, nestedEnabled, onSubmit, onClose }) {
  const editing = Boolean(route);
  const [name, setName] = useState(route?.name || "");
  const [scope, setScope] = useState(route?.scope || "project");
  const [parentId, setParentId] = useState(route?.parent_id || "");
  const [description, setDescription] = useState(route?.description || "");
  const [policy, setPolicy] = useState(route?.policy || "ask");
  const { busy, error, submit } = useSubmit(onSubmit, onClose);
  const parents = (routes || []).filter((r) => r.scope === "project" && r.id !== route?.id);
  const scopes = nestedEnabled || route?.scope === "sub" ? ["global", "project", "sub"] : ["global", "project"];
  const payload = { name, scope, description, policy, ...(scope === "sub" ? { parent_id: parentId } : {}) };
  return (
    <Modal
      open
      modalHeading={editing ? "Edit route" : "New route"}
      primaryButtonText={editing ? "Save" : "Create route"}
      secondaryButtonText="Cancel"
      primaryButtonDisabled={busy || !name.trim() || (scope === "sub" && !parentId)}
      onRequestClose={onClose}
      onRequestSubmit={() => submit(payload)}
      size="md"
    >
      <div className="dt-stack">
        <TextInput id="route-name" labelText="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} data-modal-primary-focus />
        <RadioButtonGroup legendText="Scope" name="route-scope" valueSelected={scope} onChange={(value) => setScope(value)} orientation="vertical">
          {scopes.map((value) => (
            <RadioButton
              key={value}
              id={`scope-${value}`}
              value={value}
              labelText={
                value === "global" ? "Global: applies to all of your work" : value === "project" ? "Project: applies where its targets match" : "Sub-detour: part of a Project route"
              }
            />
          ))}
        </RadioButtonGroup>
        {scope === "sub" ? (
          <Select id="route-parent" labelText="Parent route" value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <SelectItem value="" text="Choose a Project route" />
            {parents.map((r) => (
              <SelectItem key={r.id} value={r.id} text={r.name} />
            ))}
          </Select>
        ) : null}
        <TextArea id="route-description" labelText="Description (optional)" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
        <Select id="route-policy" labelText="When an agent proposes a change" value={policy} onChange={(e) => setPolicy(e.target.value)}>
          {Object.entries(POLICY_LABELS).map(([value, label]) => (
            <SelectItem key={value} value={value} text={label} />
          ))}
        </Select>
        <ErrorNote error={error} title="Couldn't save the route" />
      </div>
    </Modal>
  );
}

export function ItemModal({ item, onSubmit, onClose }) {
  const [kind, setKind] = useState(item?.kind || "rule");
  const [title, setTitle] = useState(item?.title || "");
  const [body, setBody] = useState(item?.body || "");
  const { busy, error, submit } = useSubmit(onSubmit, onClose);
  return (
    <Modal
      open
      modalHeading={item ? "Edit context" : "Add context"}
      primaryButtonText={item ? "Save" : "Add"}
      secondaryButtonText="Cancel"
      primaryButtonDisabled={busy || !title.trim()}
      onRequestClose={onClose}
      onRequestSubmit={() => submit({ kind, title, body })}
      size="md"
    >
      <div className="dt-stack">
        <Select id="item-kind" labelText="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(KIND_LABELS).map(([value, label]) => (
            <SelectItem key={value} value={value} text={label} />
          ))}
        </Select>
        <TextInput id="item-title" labelText="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        <TextArea id="item-body" labelText="Text" helperText="Markdown is fine. Agents receive it as written." rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
        <ErrorNote error={error} title="Couldn't save" />
      </div>
    </Modal>
  );
}

export function MoveModal({ item, routes, onSubmit, onClose }) {
  const options = routes.filter((r) => r.id !== item.route_id);
  const [routeId, setRouteId] = useState(options[0]?.id || "");
  const { busy, error, submit } = useSubmit(onSubmit, onClose);
  return (
    <Modal
      open
      modalHeading={`Move “${item.title}”`}
      primaryButtonText="Move"
      secondaryButtonText="Cancel"
      primaryButtonDisabled={busy || !routeId}
      onRequestClose={onClose}
      onRequestSubmit={() => submit(routeId)}
      size="sm"
    >
      <div className="dt-stack">
        <Select id="move-route" labelText="Move to" value={routeId} onChange={(e) => setRouteId(e.target.value)}>
          {options.map((r) => (
            <SelectItem key={r.id} value={r.id} text={`${r.name} (${SCOPE_LABELS[r.scope]})`} />
          ))}
        </Select>
        <ErrorNote error={error} title="Couldn't move it" />
      </div>
    </Modal>
  );
}

export function ConfirmModal({ heading, body, confirmText, onConfirm, onClose }) {
  const { busy, error, submit } = useSubmit(onConfirm, onClose);
  return (
    <Modal open danger size="sm" modalHeading={heading} primaryButtonText={confirmText} secondaryButtonText="Cancel" primaryButtonDisabled={busy} onRequestClose={onClose} onRequestSubmit={() => submit()}>
      <p>{body}</p>
      <ErrorNote error={error} title="Couldn't do that" />
    </Modal>
  );
}
