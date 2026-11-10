import { useState } from "react";
import { Button, Tag } from "@carbon/react";
import { api } from "../api.js";
import { AppLink } from "../router.jsx";
import { relativeTime } from "../util.js";
import { ErrorNote } from "./common.jsx";

const ACTION_TAGS = {
  add: ["green", "Added"], edit: ["blue", "Edited"], retire: ["red", "Retired"], restore: ["green", "Restored"],
  move: ["purple", "Moved"], remove: ["red", "Removed"], delete: ["red", "Deleted"], undo: ["gray", "Undone"],
  create: ["teal", "Created"], update: ["blue", "Changed"],
};

/** A list of changes with Undo where Detour can reverse them. */
export default function HistoryList({ events, onChanged, showRoute = false }) {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  async function undo(id) {
    setBusy(id);
    setError(null);
    try {
      await api.undo(id);
      onChanged?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  if (!events.length) return <p className="dt-muted">No changes yet.</p>;
  return (
    <>
      <ErrorNote error={error} title="Couldn't undo" onClose={() => setError(null)} />
      <ol className="dt-rows" aria-label="Changes, newest first">
        {events.map((event) => {
          const [color, label] = ACTION_TAGS[event.action] || ["gray", event.action];
          return (
            <li key={event.id} className="dt-row">
              <span className="dt-row-kind">
                <Tag size="sm" type={event.undone_at ? "gray" : color}>
                  {event.undone_at ? "Undone" : label}
                </Tag>
              </span>
              <span className="dt-row-main">
                {event.summary}
                {showRoute && event.route_name ? (
                  <>
                    {" · "}
                    {event.route_id && event.action !== "delete" ? (
                      <AppLink href={`/routes/${event.route_id}`} className="cds--link">
                        {event.route_name}
                      </AppLink>
                    ) : (
                      event.route_name
                    )}
                  </>
                ) : null}
              </span>
              <span className="dt-row-meta">
                {event.actor} · {relativeTime(event.created_at)}
              </span>
              {event.undoable ? (
                <Button size="sm" kind="ghost" disabled={busy === event.id} onClick={() => undo(event.id)}>
                  Undo
                </Button>
              ) : null}
            </li>
          );
        })}
      </ol>
    </>
  );
}
