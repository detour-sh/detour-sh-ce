"""Detour's data model.

A route is a named set of context. Items are the context itself (rules,
decisions, runbooks, prompts, notes, references). Targets say where a route
applies (a repo, a folder, a file, a topic, an artifact or a URL). Agents never
write directly: they file proposals, and the route's policy decides whether a
proposal waits for the user or applies at once. Every change to an item or a
target is recorded as an event that can be undone.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DETOUR_DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("DETOUR_DB_PATH", str(DATA_DIR / "detour.sqlite")))
NESTED_DETOURS_ENABLED = os.environ.get("DETOUR_ENABLE_NESTED_DETOURS", "false").lower() == "true"

# Bump when init_db gains a migration. Databases at this version skip migrations.
SCHEMA_VERSION = 2
BACKUPS_TO_KEEP = 5
BUSY_TIMEOUT_SECONDS = 10
STALE_AFTER_DAYS = 90

SCOPES = ("global", "project", "sub")
POLICIES = ("ask", "notes", "read")
ITEM_KINDS = ("rule", "decision", "runbook", "prompt", "note", "reference")
TARGET_KINDS = ("repo", "folder", "file", "topic", "artifact", "url")
PROPOSAL_ACTIONS = ("add", "edit", "retire", "move", "create_route")
PROPOSAL_STATUSES = ("pending", "approved", "rejected", "auto")
YOU = "You"

MAX_TITLE = 200
MAX_BODY = 20_000
MAX_TEXT = 2_000
MAX_TARGET = 500

ITEM_FIELDS = ("id", "route_id", "kind", "title", "body", "pinned", "status", "created_by", "created_at", "updated_at")
TARGET_FIELDS = ("id", "route_id", "kind", "value", "created_at")
ROUTE_FIELDS = ("id", "name", "scope", "parent_id", "description", "policy", "paused", "sort_order", "created_at", "updated_at")


class Conflict(Exception):
    """The request is valid but the data changed underneath it."""


class Forbidden(Exception):
    """The caller is not allowed to do this."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- database


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('global','project','sub')),
  parent_id TEXT REFERENCES routes(id) ON DELETE CASCADE,
  description TEXT NOT NULL DEFAULT '',
  policy TEXT NOT NULL DEFAULT 'ask' CHECK (policy IN ('ask','notes','read')),
  paused INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('rule','decision','runbook','prompt','note','reference')),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_route ON items(route_id, status);

CREATE TABLE IF NOT EXISTS targets (
  id TEXT PRIMARY KEY,
  route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('repo','folder','file','topic','artifact','url')),
  value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (route_id, kind, value)
);

CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  key_id TEXT,
  agent TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('add','edit','retire','move','create_route')),
  route_id TEXT REFERENCES routes(id) ON DELETE CASCADE,
  item_id TEXT,
  payload_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','auto')),
  decision_note TEXT NOT NULL DEFAULT '',
  event_id TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  route_id TEXT,
  route_name TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  summary TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  proposal_id TEXT,
  created_at TEXT NOT NULL,
  undone_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_route ON events(route_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);

CREATE TABLE IF NOT EXISTS mcp_access_keys (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  can_propose INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);
"""


def backup_database(conn: sqlite3.Connection, from_version: int) -> Path:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"detour-schema{from_version}-{stamp}.sqlite"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    backups = sorted(backup_dir.glob("detour-schema*.sqlite"), key=lambda path: path.stat().st_mtime)
    for old in backups[:-BACKUPS_TO_KEEP]:
        old.unlink(missing_ok=True)
    return target


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {current} is newer than this Detour build supports ({SCHEMA_VERSION}). "
                "Upgrade Detour, or restore a backup from the backups folder in the data volume."
            )
        has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone()
        if has_tables:
            backup_database(conn, current)
    finally:
        conn.close()
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        legacy = _rename_legacy_tables(conn)
        conn.executescript(SCHEMA)
        if legacy:
            _migrate_legacy(conn, legacy)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with db() as conn:
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError("Database migration left broken references. Restore the backup and report a bug.")


# ---------------------------------------------------------------- migration from 0.1


def _tables(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _rename_legacy_tables(conn) -> dict[str, str]:
    """Move 0.1 tables aside so the new schema can use the same names."""
    tables = _tables(conn)
    legacy: dict[str, str] = {}
    for old, key in (("routes", "routes"), ("stacks", "routes"), ("route_items", "route_items"), ("stack_items", "route_items"),
                     ("artifacts", "artifacts"), ("records", "artifacts")):
        if old not in tables or key in legacy:
            continue
        if old == "routes" and "name" in _columns(conn, "routes"):
            continue  # already the new shape
        conn.execute(f"ALTER TABLE {old} RENAME TO legacy_{key}")
        legacy[key] = f"legacy_{key}"
    for old in ("environment_flags", "tag_sets", "bundles"):
        if old in tables:
            conn.execute(f"ALTER TABLE {old} RENAME TO legacy_{old}")
            legacy[old] = f"legacy_{old}"
    if "mcp_access_keys" in tables and "can_propose" not in _columns(conn, "mcp_access_keys"):
        # Keys made before 0.2 were read-only. Keep them that way until the user says otherwise.
        conn.execute("ALTER TABLE mcp_access_keys ADD COLUMN can_propose INTEGER NOT NULL DEFAULT 0")
        legacy["keys"] = "mcp_access_keys"
    return legacy


LEGACY_TYPE_TO_KIND = {
    "note": "note", "doc": "reference", "skill": "runbook", "prompt": "prompt", "decision": "decision",
    "runbook": "runbook", "tool": "reference", "product_info": "reference", "soul_file": "rule", "file": "reference",
}


def _migrate_legacy(conn, legacy: dict[str, str]) -> None:
    stamp = now_iso()
    route_ids: set[str] = set()
    if "routes" in legacy:
        table = legacy["routes"]
        cols = _columns(conn, table)
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        for index, row in enumerate(rows):
            scope = row["scope"] if "scope" in cols and row["scope"] in SCOPES else "project"
            conn.execute(
                "INSERT INTO routes (id, name, scope, parent_id, description, policy, paused, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, NULL, ?, 'ask', 0, ?, ?, ?)",
                (row["id"], (row["title"] or "Untitled route")[:MAX_TITLE], scope, row["description"] if "description" in cols else "",
                 row["sort_order"] if "sort_order" in cols else index, row["created_at"], row["updated_at"]),
            )
            route_ids.add(row["id"])
        if "parent_id" in cols:
            for row in rows:
                if row["parent_id"] and row["parent_id"] in route_ids and row["parent_id"] != row["id"]:
                    conn.execute("UPDATE routes SET parent_id = ? WHERE id = ?", (row["parent_id"], row["id"]))
        conn.execute("UPDATE routes SET scope = 'project' WHERE scope = 'sub' AND parent_id IS NULL")

    artifacts: dict[str, sqlite3.Row] = {}
    if "artifacts" in legacy:
        artifacts = {row["id"]: row for row in conn.execute(f"SELECT * FROM {legacy['artifacts']}")}

    def artifact_body(row) -> str:
        keys = row.keys()
        parts = [row["body_markdown"] or ""]
        if "notes_markdown" in keys and row["notes_markdown"]:
            parts.append(row["notes_markdown"])
        for label, column in (("Local path", "local_path"), ("Link", "cloud_url"), ("File", "file_name")):
            if column in keys and row[column]:
                parts.append(f"{label}: {row[column]}")
        return "\n\n".join(part for part in parts if part).strip()[:MAX_BODY]

    def add_item(route_id, kind, title, body, status="active", created_at=stamp):
        conn.execute(
            "INSERT INTO items (id, route_id, kind, title, body, pinned, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (make_id(), route_id, kind, (title or "Untitled")[:MAX_TITLE], body, status, YOU, created_at, stamp),
        )

    def add_target(route_id, kind, value):
        value = normalize_target(kind, (value or "").strip()[:MAX_TARGET])
        if value:
            conn.execute("INSERT OR IGNORE INTO targets (id, route_id, kind, value, created_at) VALUES (?, ?, ?, ?, ?)",
                         (make_id(), route_id, kind, value, stamp))

    attached: set[str] = set()
    if "route_items" in legacy:
        table = legacy["route_items"]
        cols = _columns(conn, table)
        route_col = "route_id" if "route_id" in cols else "stack_id"
        artifact_col = "artifact_id" if "artifact_id" in cols else "record_id"
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY created_at"):
            route_id = row[route_col]
            if route_id not in route_ids:
                continue
            kind = row["kind"]
            body = row["body_markdown"] if "body_markdown" in cols else ""
            if kind in ("artifact", "record"):
                artifact = artifacts.get(row[artifact_col])
                if artifact is None:
                    continue
                attached.add(artifact["id"])
                _migrate_artifact(artifact, route_id, add_item, add_target, artifact_body)
            elif kind == "note":
                add_item(route_id, "note", row["label"] or "Note", body or "", created_at=row["created_at"])
            elif kind == "link":
                add_item(route_id, "reference", row["label"] or row["url"], row["url"] or "", created_at=row["created_at"])
            elif kind == "repo":
                add_target(route_id, "repo", row["url"] or row["label"])

    leftovers = [row for row in artifacts.values() if row["id"] not in attached and row["status"] == "active"]
    if leftovers:
        holder = make_id()
        conn.execute(
            "INSERT INTO routes (id, name, scope, description, policy, paused, sort_order, created_at, updated_at) "
            "VALUES (?, 'Imported from Detour 0.1', 'project', ?, 'ask', 0, 9999, ?, ?)",
            (holder, "Artifacts that were not attached to a Detour. This route has no targets, so agents only see it when they ask for it by name.", stamp, stamp),
        )
        for artifact in leftovers:
            _migrate_artifact(artifact, holder, add_item, add_target, artifact_body)

    if "environment_flags" in legacy:
        global_row = conn.execute("SELECT id FROM routes WHERE scope = 'global' ORDER BY sort_order LIMIT 1").fetchone()
        for flag in conn.execute(f"SELECT * FROM {legacy['environment_flags']}"):
            route_id = flag["scope_id"]
            if route_id == "workspace" or route_id not in route_ids:
                if global_row is None:
                    global_id = make_id()
                    conn.execute(
                        "INSERT INTO routes (id, name, scope, description, policy, paused, sort_order, created_at, updated_at) "
                        "VALUES (?, 'Global', 'global', 'Applies to all of your work.', 'ask', 0, -1, ?, ?)",
                        (global_id, stamp, stamp),
                    )
                    global_row = {"id": global_id}
                route_id = global_row["id"]
            body = f"{flag['name']}={flag['value']}"
            if flag["description"]:
                body += f"\n\n{flag['description']}"
            add_item(route_id, "rule", flag["name"], body)

    for key, table in legacy.items():
        if table.startswith("legacy_"):
            conn.execute(f"DROP TABLE {table}")


def _migrate_artifact(artifact, route_id, add_item, add_target, artifact_body) -> None:
    status = "retired" if artifact["status"] == "archived" else "active"
    keys = artifact.keys()
    if artifact["type"] == "repo":
        if "cloud_url" in keys:
            add_target(route_id, "repo", artifact["cloud_url"])
        if "local_path" in keys:
            add_target(route_id, "folder", artifact["local_path"])
        body = (artifact["body_markdown"] or "").strip()
        if body:
            add_item(route_id, "reference", artifact["title"], body, status, artifact["created_at"])
        return
    kind = LEGACY_TYPE_TO_KIND.get(artifact["type"], "reference")
    add_item(route_id, kind, artifact["title"], artifact_body(artifact), status, artifact["created_at"])


# ---------------------------------------------------------------- validation helpers


def _text(payload: dict, name: str, limit: int, required: bool = False, default: str = "") -> str:
    value = payload.get(name, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} must be {limit} characters or fewer")
    return value


def _choice(value, options, name: str) -> str:
    if value not in options:
        raise ValueError(f"{name} must be one of: {', '.join(options)}")
    return value


def _snapshot(row, fields) -> dict | None:
    if row is None:
        return None
    return {field: row[field] for field in fields}


def _load(conn, entity: str, entity_id: str) -> dict | None:
    if entity == "item":
        return _snapshot(conn.execute("SELECT * FROM items WHERE id = ?", (entity_id,)).fetchone(), ITEM_FIELDS)
    if entity == "target":
        return _snapshot(conn.execute("SELECT * FROM targets WHERE id = ?", (entity_id,)).fetchone(), TARGET_FIELDS)
    if entity == "route":
        return _snapshot(conn.execute("SELECT * FROM routes WHERE id = ?", (entity_id,)).fetchone(), ROUTE_FIELDS)
    raise ValueError("unknown entity")


def _write(conn, entity: str, state: dict) -> None:
    table, fields = {"item": ("items", ITEM_FIELDS), "target": ("targets", TARGET_FIELDS)}[entity]
    columns = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{field} = excluded.{field}" for field in fields if field != "id")
    conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}",
        tuple(state[field] for field in fields),
    )


def _delete(conn, entity: str, entity_id: str) -> None:
    table = {"item": "items", "target": "targets"}[entity]
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))


def _route_row(conn, ref: str):
    ref = (ref or "").strip()
    if not ref:
        return None
    row = conn.execute("SELECT * FROM routes WHERE id = ?", (ref,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM routes WHERE name = ? COLLATE NOCASE ORDER BY sort_order LIMIT 1", (ref,)).fetchone()
    return row


def _require_route(conn, ref: str):
    row = _route_row(conn, ref)
    if row is None:
        raise KeyError("route not found")
    return row


def _record(conn, entity, entity_id, route_id, action, actor, summary, before=None, after=None, proposal_id=None) -> str:
    event_id = make_id()
    route = conn.execute("SELECT name FROM routes WHERE id = ?", (route_id,)).fetchone() if route_id else None
    conn.execute(
        "INSERT INTO events (id, entity, entity_id, route_id, route_name, action, actor, summary, before_json, after_json, proposal_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, entity, entity_id, route_id, route["name"] if route else "", action, actor, summary,
         json.dumps(before) if before is not None else None, json.dumps(after) if after is not None else None,
         proposal_id, now_iso()),
    )
    return event_id


def _change(conn, entity, before, after, action, actor, summary, proposal_id=None) -> str:
    if after is None:
        _delete(conn, entity, before["id"])
    else:
        _write(conn, entity, after)
    state = after or before
    return _record(conn, entity, state["id"], state["route_id"], action, actor, summary, before, after, proposal_id)


# ---------------------------------------------------------------- routes


def _route_dict(conn, row) -> dict:
    route = _snapshot(row, ROUTE_FIELDS)
    route["paused"] = bool(route["paused"])
    stale_before = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=STALE_AFTER_DAYS)).isoformat().replace("+00:00", "Z")
    counts = conn.execute(
        "SELECT COUNT(*) AS items, SUM(CASE WHEN COALESCE(last_used_at, created_at) < ? THEN 1 ELSE 0 END) AS stale "
        "FROM items WHERE route_id = ? AND status = 'active'",
        (stale_before, row["id"]),
    ).fetchone()
    route["item_count"] = counts["items"] or 0
    route["stale_count"] = counts["stale"] or 0
    route["pending_count"] = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE status = 'pending' AND (route_id = ? OR json_extract(payload_json, '$.to_route_id') = ?)",
        (row["id"], row["id"]),
    ).fetchone()[0]
    route["targets"] = [dict(t) for t in conn.execute(
        "SELECT id, kind, value, created_at FROM targets WHERE route_id = ? ORDER BY kind, value", (row["id"],))]
    last = conn.execute("SELECT actor, created_at FROM events WHERE route_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (row["id"],)).fetchone()
    route["last_change"] = dict(last) if last else None
    parent = conn.execute("SELECT id, name FROM routes WHERE id = ?", (row["parent_id"],)).fetchone() if row["parent_id"] else None
    route["parent"] = dict(parent) if parent else None
    route["children"] = [dict(c) for c in conn.execute("SELECT id, name FROM routes WHERE parent_id = ? ORDER BY sort_order, name", (row["id"],))]
    return route


def list_routes() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM routes ORDER BY CASE scope WHEN 'global' THEN 0 WHEN 'project' THEN 1 ELSE 2 END, sort_order, name COLLATE NOCASE"
        ).fetchall()
        return [_route_dict(conn, row) for row in rows]


def get_route(ref: str) -> dict | None:
    with db() as conn:
        row = _route_row(conn, ref)
        return _route_dict(conn, row) if row else None


def _validate_route_fields(conn, payload: dict, existing=None) -> dict:
    name = _text(payload, "name", MAX_TITLE, required=existing is None, default=existing["name"] if existing else "")
    if not name:
        raise ValueError("name is required")
    scope = payload.get("scope", existing["scope"] if existing else "project")
    _choice(scope, SCOPES, "scope")
    parent_id = payload.get("parent_id", existing["parent_id"] if existing else None) or None
    if scope == "sub":
        if not NESTED_DETOURS_ENABLED and (existing is None or existing["scope"] != "sub"):
            raise ValueError("Sub-detours are turned off. Set DETOUR_ENABLE_NESTED_DETOURS=true to use them.")
        parent = conn.execute("SELECT * FROM routes WHERE id = ?", (parent_id,)).fetchone() if parent_id else None
        if parent is None or parent["scope"] != "project":
            raise ValueError("a Sub-detour needs a Project route as its parent")
        if existing is not None and parent["id"] == existing["id"]:
            raise ValueError("a route can't be its own parent")
    else:
        parent_id = None
    description = _text(payload, "description", MAX_TEXT, default=existing["description"] if existing else "")
    policy = payload.get("policy", existing["policy"] if existing else "ask")
    _choice(policy, POLICIES, "policy")
    paused = payload.get("paused", bool(existing["paused"]) if existing else False)
    if not isinstance(paused, bool):
        raise ValueError("paused must be true or false")
    return {"name": name, "scope": scope, "parent_id": parent_id, "description": description, "policy": policy, "paused": int(paused)}


def create_route(payload: dict, actor: str = YOU, conn=None) -> dict:
    if conn is None:
        with db() as conn:
            return create_route(payload, actor, conn)
    fields = _validate_route_fields(conn, payload)
    route_id = make_id()
    stamp = now_iso()
    order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM routes").fetchone()[0]
    conn.execute(
        "INSERT INTO routes (id, name, scope, parent_id, description, policy, paused, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (route_id, fields["name"], fields["scope"], fields["parent_id"], fields["description"], fields["policy"],
         fields["paused"], order, stamp, stamp),
    )
    _record(conn, "route", route_id, route_id, "create", actor, f"Created route “{fields['name']}”")
    for target in payload.get("targets") or []:
        add_target(route_id, target, actor, conn)
    return _route_dict(conn, conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone())


def update_route(ref: str, payload: dict, actor: str = YOU) -> dict:
    with db() as conn:
        row = _require_route(conn, ref)
        fields = _validate_route_fields(conn, payload, row)
        if fields["scope"] != row["scope"] and conn.execute("SELECT 1 FROM routes WHERE parent_id = ?", (row["id"],)).fetchone():
            raise ValueError("move or delete this route's Sub-detours before changing its scope")
        changes = [label for key, label in (("name", "name"), ("description", "description"), ("policy", "review policy"),
                                             ("paused", "agent access"), ("scope", "scope")) if fields[key] != row[key]]
        conn.execute(
            "UPDATE routes SET name = ?, scope = ?, parent_id = ?, description = ?, policy = ?, paused = ?, updated_at = ? WHERE id = ?",
            (fields["name"], fields["scope"], fields["parent_id"], fields["description"], fields["policy"], fields["paused"], now_iso(), row["id"]),
        )
        if changes:
            summary = "Paused agent access" if changes == ["agent access"] and fields["paused"] else \
                "Resumed agent access" if changes == ["agent access"] else f"Changed {', '.join(changes)}"
            _record(conn, "route", row["id"], row["id"], "update", actor, summary)
        return _route_dict(conn, conn.execute("SELECT * FROM routes WHERE id = ?", (row["id"],)).fetchone())


def delete_route(ref: str, actor: str = YOU) -> None:
    with db() as conn:
        row = _require_route(conn, ref)
        _record(conn, "route", row["id"], row["id"], "delete", actor, f"Deleted route “{row['name']}”")
        conn.execute("DELETE FROM routes WHERE id = ?", (row["id"],))


def reorder_routes(ids: list) -> list[dict]:
    with db() as conn:
        existing = {row["id"] for row in conn.execute("SELECT id FROM routes")}
        if not isinstance(ids, list) or set(ids) != existing or len(ids) != len(existing):
            raise ValueError("order must list every route exactly once")
        for index, route_id in enumerate(ids):
            conn.execute("UPDATE routes SET sort_order = ? WHERE id = ?", (index, route_id))
    return list_routes()


# ---------------------------------------------------------------- items


def _item_dict(row) -> dict:
    item = {field: row[field] for field in ITEM_FIELDS}
    item["pinned"] = bool(item["pinned"])
    item["last_used_at"] = row["last_used_at"]
    return item


def list_items(route_ref: str, include_retired: bool = True) -> list[dict]:
    with db() as conn:
        route = _require_route(conn, route_ref)
        query = "SELECT * FROM items WHERE route_id = ?" + ("" if include_retired else " AND status = 'active'")
        query += " ORDER BY status, pinned DESC, CASE kind WHEN 'rule' THEN 0 WHEN 'decision' THEN 1 WHEN 'runbook' THEN 2 WHEN 'prompt' THEN 3 WHEN 'note' THEN 4 ELSE 5 END, title COLLATE NOCASE"
        return [_item_dict(row) for row in conn.execute(query, (route["id"],))]


def _require_item(conn, item_id: str):
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KeyError("item not found")
    return row


def _new_item_state(route_id: str, payload: dict, actor: str) -> dict:
    stamp = now_iso()
    kind = _choice(payload.get("kind", "note"), ITEM_KINDS, "kind")
    return {
        "id": make_id(), "route_id": route_id, "kind": kind,
        "title": _text(payload, "title", MAX_TITLE, required=True),
        "body": _text(payload, "body", MAX_BODY),
        "pinned": int(bool(payload.get("pinned", False))), "status": "active",
        "created_by": actor, "created_at": stamp, "updated_at": stamp,
    }


def _edited_item_state(before: dict, payload: dict) -> dict:
    after = dict(before)
    if "kind" in payload:
        after["kind"] = _choice(payload["kind"], ITEM_KINDS, "kind")
    if "title" in payload:
        after["title"] = _text(payload, "title", MAX_TITLE, required=True)
    if "body" in payload:
        after["body"] = _text(payload, "body", MAX_BODY)
    if "pinned" in payload:
        if not isinstance(payload["pinned"], bool):
            raise ValueError("pinned must be true or false")
        after["pinned"] = int(payload["pinned"])
    if after == before:
        return before
    after["updated_at"] = now_iso()
    return after


def create_item(route_ref: str, payload: dict, actor: str = YOU) -> dict:
    with db() as conn:
        route = _require_route(conn, route_ref)
        state = _new_item_state(route["id"], payload, actor)
        _change(conn, "item", None, state, "add", actor, f"Added {state['kind']} “{state['title']}”")
        return _item_dict(_require_item(conn, state["id"]))


def update_item(item_id: str, payload: dict, actor: str = YOU) -> dict:
    with db() as conn:
        before = _snapshot(_require_item(conn, item_id), ITEM_FIELDS)
        after = _edited_item_state(before, payload)
        if after is not before:
            if set(payload) == {"pinned"}:
                summary = f"{'Pinned' if after['pinned'] else 'Unpinned'} “{after['title']}”"
            else:
                summary = f"Edited {after['kind']} “{after['title']}”"
            _change(conn, "item", before, after, "edit", actor, summary)
        return _item_dict(_require_item(conn, item_id))


def set_item_status(item_id: str, status: str, actor: str = YOU) -> dict:
    _choice(status, ("active", "retired"), "status")
    with db() as conn:
        before = _snapshot(_require_item(conn, item_id), ITEM_FIELDS)
        if before["status"] != status:
            after = dict(before, status=status, updated_at=now_iso())
            verb = "Retired" if status == "retired" else "Restored"
            _change(conn, "item", before, after, "retire" if status == "retired" else "restore", actor, f"{verb} {after['kind']} “{after['title']}”")
        return _item_dict(_require_item(conn, item_id))


def move_item(item_id: str, route_ref: str, actor: str = YOU) -> dict:
    with db() as conn:
        before = _snapshot(_require_item(conn, item_id), ITEM_FIELDS)
        target = _require_route(conn, route_ref)
        if target["id"] != before["route_id"]:
            after = dict(before, route_id=target["id"], updated_at=now_iso())
            _change(conn, "item", before, after, "move", actor, f"Moved “{after['title']}” to {target['name']}")
        return _item_dict(_require_item(conn, item_id))


def delete_item(item_id: str, actor: str = YOU) -> None:
    with db() as conn:
        before = _snapshot(_require_item(conn, item_id), ITEM_FIELDS)
        _change(conn, "item", before, None, "delete", actor, f"Deleted {before['kind']} “{before['title']}”")


# ---------------------------------------------------------------- targets


def normalize_target(kind: str, value: str) -> str:
    value = value.strip()
    if kind in ("folder", "file"):
        value = value.replace("\\", "/")
        if len(value) > 1:
            value = value.rstrip("/")
    if kind == "repo":
        value = normalize_repo(value)
    if kind == "topic":
        value = ", ".join(dict.fromkeys(part.strip().casefold() for part in value.split(",") if part.strip()))
    return value


def normalize_repo(value: str) -> str:
    value = value.strip().casefold()
    value = re.sub(r"^[a-z+]+://", "", value)
    value = re.sub(r"^[^@/]+@([^:/]+):", r"\1/", value)
    value = re.sub(r"^[^@/]+@", "", value)
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def add_target(route_ref: str, payload: dict, actor: str = YOU, conn=None) -> dict:
    if conn is None:
        with db() as conn:
            return add_target(route_ref, payload, actor, conn)
    route = _require_route(conn, route_ref)
    if route["scope"] == "global":
        raise ValueError("Global routes apply everywhere and don't take targets")
    kind = _choice(payload.get("kind"), TARGET_KINDS, "kind")
    value = normalize_target(kind, _text(payload, "value", MAX_TARGET, required=True))
    if not value:
        raise ValueError("value is required")
    if kind in ("url", "artifact") and not re.match(r"^https?://", value):
        raise ValueError("an artifact or URL target must start with http:// or https://")
    existing = conn.execute("SELECT * FROM targets WHERE route_id = ? AND kind = ? AND value = ?", (route["id"], kind, value)).fetchone()
    if existing:
        return dict(_snapshot(existing, TARGET_FIELDS))
    state = {"id": make_id(), "route_id": route["id"], "kind": kind, "value": value, "created_at": now_iso()}
    _change(conn, "target", None, state, "add", actor, f"Applied to {kind} {value}")
    return state


def remove_target(target_id: str, actor: str = YOU) -> None:
    with db() as conn:
        before = _load(conn, "target", target_id)
        if before is None:
            raise KeyError("target not found")
        _change(conn, "target", before, None, "remove", actor, f"Stopped applying to {before['kind']} {before['value']}")


# ---------------------------------------------------------------- history


def list_events(route_ref: str | None = None, limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with db() as conn:
        if route_ref:
            route = _require_route(conn, route_ref)
            rows = conn.execute("SELECT * FROM events WHERE route_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (route["id"], limit))
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
        events = []
        for row in rows.fetchall():
            event = {key: row[key] for key in ("id", "entity", "entity_id", "route_id", "route_name", "action", "actor", "summary", "proposal_id", "created_at", "undone_at")}
            event["undoable"] = row["entity"] in ("item", "target") and row["undone_at"] is None
            events.append(event)
        return events


def undo_event(event_id: str, actor: str = YOU) -> dict:
    with db() as conn:
        return _undo(conn, event_id, actor)


def _undo(conn, event_id: str, actor: str) -> dict:
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        raise KeyError("event not found")
    if event["undone_at"]:
        raise Conflict("That change was already undone.")
    if event["entity"] not in ("item", "target"):
        raise Conflict("Route changes can't be undone here. Change the route instead.")
    before = json.loads(event["before_json"]) if event["before_json"] else None
    after = json.loads(event["after_json"]) if event["after_json"] else None
    current = _load(conn, event["entity"], event["entity_id"])
    if current != after:
        raise Conflict("It has changed since then, so this can't be undone. Edit it instead.")
    if before is not None and conn.execute("SELECT 1 FROM routes WHERE id = ?", (before["route_id"],)).fetchone() is None:
        raise Conflict("The route it belonged to was deleted.")
    conn.execute("UPDATE events SET undone_at = ? WHERE id = ?", (now_iso(), event_id))
    undo_id = _change(conn, event["entity"], current, before, "undo", actor, f"Undid: {event['summary']}")
    if event["proposal_id"]:
        conn.execute("UPDATE proposals SET status = 'pending', decided_at = NULL, event_id = NULL, decision_note = '' WHERE id = ?", (event["proposal_id"],))
    return {"undone": event_id, "event_id": undo_id}


# ---------------------------------------------------------------- proposals


def _proposal_target_routes(conn, action: str, payload: dict):
    """Return (route row, destination row or None) a proposal acts on, checking they exist."""
    if action == "create_route":
        return None, None
    if action == "add":
        return _require_route(conn, payload.get("route", "")), None
    item = _require_item(conn, _text(payload, "item_id", 64, required=True))
    route = conn.execute("SELECT * FROM routes WHERE id = ?", (item["route_id"],)).fetchone()
    dest = _require_route(conn, payload.get("to_route", "")) if action == "move" else None
    return route, dest


def propose(key: dict, payload: dict) -> dict:
    """File a proposal from an agent. Returns the proposal and what happened to it."""
    if not key.get("can_propose"):
        raise Forbidden("This access key is read-only. The user can allow proposals for it under Agent connections.")
    action = _choice(payload.get("action"), PROPOSAL_ACTIONS, "action")
    reason = _text(payload, "reason", MAX_TEXT, required=True)
    source = _text(payload, "source", MAX_TEXT)
    with db() as conn:
        route, dest = _proposal_target_routes(conn, action, payload)
        for row in (route, dest):
            if row is None:
                continue
            if row["paused"]:
                raise Forbidden(f"Agent access to “{row['name']}” is paused.")
            if row["policy"] == "read":
                raise Forbidden(f"“{row['name']}” is read-only for agents.")
        normalized: dict = {}
        before = None
        if action == "add":
            normalized = {"kind": _choice(payload.get("kind", "note"), ITEM_KINDS, "kind"),
                          "title": _text(payload, "title", MAX_TITLE, required=True), "body": _text(payload, "body", MAX_BODY)}
        elif action == "edit":
            before = _snapshot(_require_item(conn, payload["item_id"]), ITEM_FIELDS)
            if before["status"] != "active":
                raise ValueError("that item is retired")
            changes = {name: payload[name] for name in ("kind", "title", "body") if name in payload}
            if not changes:
                raise ValueError("an edit needs a new kind, title or body")
            proposed = _edited_item_state(before, changes)
            if proposed is before:
                raise ValueError("the edit doesn't change anything")
            normalized = {name: proposed[name] for name in ("kind", "title", "body")}
        elif action in ("retire", "move"):
            before = _snapshot(_require_item(conn, payload["item_id"]), ITEM_FIELDS)
            if action == "retire" and before["status"] != "active":
                raise ValueError("that item is already retired")
            if action == "move":
                if dest["id"] == before["route_id"]:
                    raise ValueError("the item is already in that route")
                normalized = {"to_route_id": dest["id"]}
        elif action == "create_route":
            scope = payload.get("scope", "project")
            if scope not in ("project", "sub"):
                raise ValueError("agents can propose Project routes or Sub-detours")
            normalized = {"name": _text(payload, "name", MAX_TITLE, required=True), "scope": scope,
                          "parent_id": None, "description": _text(payload, "description", MAX_TEXT), "targets": []}
            if scope == "sub":
                parent = _require_route(conn, payload.get("parent", ""))
                normalized["parent_id"] = parent["id"]
            for target in payload.get("targets") or []:
                if not isinstance(target, dict):
                    raise ValueError("targets must be a list of {kind, value}")
                kind = _choice(target.get("kind"), TARGET_KINDS, "target kind")
                normalized["targets"].append({"kind": kind, "value": normalize_target(kind, _text(target, "value", MAX_TARGET, required=True))})
        if before is not None:
            normalized["before"] = before

        route_id = route["id"] if route is not None else None
        item_id = before["id"] if before else None
        fingerprint = (normalized.get("title") or normalized.get("name") or "").casefold()
        for row in conn.execute(
            "SELECT * FROM proposals WHERE action = ? AND COALESCE(route_id, '') = COALESCE(?, '') AND COALESCE(item_id, '') = COALESCE(?, '') "
            "AND status IN ('pending', 'rejected') ORDER BY created_at DESC",
            (action, route_id, item_id),
        ):
            old = json.loads(row["payload_json"])
            if (old.get("title") or old.get("name") or "").casefold() != fingerprint:
                continue
            if row["status"] == "pending":
                return _proposal_dict(conn, row) | {"outcome": "already_pending"}
            return _proposal_dict(conn, row) | {"outcome": "previously_rejected"}

        proposal_id = make_id()
        conn.execute(
            "INSERT INTO proposals (id, key_id, agent, action, route_id, item_id, payload_json, reason, source, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (proposal_id, key.get("id"), key.get("name") or "Agent", action, route_id, item_id, json.dumps(normalized), reason, source, now_iso()),
        )
        auto = (
            route is not None and route["policy"] == "notes" and action in ("add", "edit")
            and normalized.get("kind") == "note" and (before is None or before["kind"] == "note")
        )
        if auto:
            _apply_proposal(conn, proposal_id, {}, key.get("name") or "Agent", status="auto")
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return _proposal_dict(conn, row) | {"outcome": "applied" if auto else "waiting_for_review"}


def _proposal_dict(conn, row) -> dict:
    payload = json.loads(row["payload_json"])
    route = conn.execute("SELECT id, name, scope FROM routes WHERE id = ?", (row["route_id"],)).fetchone() if row["route_id"] else None
    to_route = None
    if payload.get("to_route_id"):
        found = conn.execute("SELECT id, name, scope FROM routes WHERE id = ?", (payload["to_route_id"],)).fetchone()
        to_route = dict(found) if found else {"id": payload["to_route_id"], "name": "(deleted route)", "scope": "project"}
    parent = None
    if payload.get("parent_id"):
        found = conn.execute("SELECT id, name FROM routes WHERE id = ?", (payload["parent_id"],)).fetchone()
        parent = dict(found) if found else None
    return {
        "id": row["id"], "agent": row["agent"], "key_id": row["key_id"], "action": row["action"], "status": row["status"],
        "reason": row["reason"], "source": row["source"], "decision_note": row["decision_note"],
        "created_at": row["created_at"], "decided_at": row["decided_at"], "event_id": row["event_id"],
        "route": dict(route) if route else None, "to_route": to_route, "parent": parent,
        "item_id": row["item_id"], "before": payload.get("before"),
        "proposed": {key: payload[key] for key in ("kind", "title", "body", "name", "scope", "description", "targets") if key in payload},
    }


def list_proposals(status: str = "pending", key_id: str | None = None, limit: int = 200) -> list[dict]:
    with db() as conn:
        clauses, args = [], []
        if status == "decided":
            clauses.append("status IN ('approved','rejected','auto')")
        elif status != "all":
            _choice(status, PROPOSAL_STATUSES, "status")
            clauses.append("status = ?")
            args.append(status)
        if key_id:
            clauses.append("key_id = ?")
            args.append(key_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(f"SELECT * FROM proposals {where} ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
        return [_proposal_dict(conn, row) for row in rows]


def get_proposal(proposal_id: str, key_id: str | None = None) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None or (key_id is not None and row["key_id"] != key_id):
            raise KeyError("proposal not found")
        return _proposal_dict(conn, row)


def proposal_counts() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM proposals GROUP BY status").fetchall()
    counts = {status: 0 for status in PROPOSAL_STATUSES}
    counts.update({row["status"]: row["n"] for row in rows})
    return counts


def _apply_proposal(conn, proposal_id: str, overrides: dict, actor: str, status: str = "approved") -> str | None:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise KeyError("proposal not found")
    if row["status"] != "pending":
        raise Conflict("That proposal was already decided.")
    payload = json.loads(row["payload_json"])
    action = row["action"]
    allowed = {"add": ("kind", "title", "body"), "edit": ("kind", "title", "body"), "create_route": ("name", "description")}.get(action, ())
    extra = set(overrides) - set(allowed)
    if extra:
        raise ValueError(f"can't change {', '.join(sorted(extra))} on a {action} proposal")
    payload.update({key: overrides[key] for key in allowed if key in overrides})
    who = f"{row['agent']}, approved by {actor}" if status == "approved" else f"{row['agent']}, auto-approved"
    event_id = None
    if action in ("edit", "retire", "move"):
        current = _load(conn, "item", row["item_id"])
        if current is None:
            raise Conflict("The item was deleted after this was proposed.")
        if current != payload["before"]:
            raise Conflict("The item changed after this was proposed. Reject it, or ask the agent to propose again.")
    if action == "add":
        if row["route_id"] is None:
            raise Conflict("The route was deleted.")
        state = _new_item_state(row["route_id"], payload, row["agent"])
        event_id = _change(conn, "item", None, state, "add", who, f"Added {state['kind']} “{state['title']}”", proposal_id)
    elif action == "edit":
        after = _edited_item_state(current, {key: payload[key] for key in ("kind", "title", "body")})
        event_id = _change(conn, "item", current, after, "edit", who, f"Edited {after['kind']} “{after['title']}”", proposal_id)
    elif action == "retire":
        after = dict(current, status="retired", updated_at=now_iso())
        event_id = _change(conn, "item", current, after, "retire", who, f"Retired {after['kind']} “{after['title']}”", proposal_id)
    elif action == "move":
        dest = conn.execute("SELECT * FROM routes WHERE id = ?", (payload["to_route_id"],)).fetchone()
        if dest is None:
            raise Conflict("The destination route was deleted.")
        after = dict(current, route_id=dest["id"], updated_at=now_iso())
        event_id = _change(conn, "item", current, after, "move", who, f"Moved “{after['title']}” to {dest['name']}", proposal_id)
    elif action == "create_route":
        route = create_route(
            {"name": payload["name"], "scope": payload["scope"], "parent_id": payload["parent_id"],
             "description": payload["description"], "targets": payload["targets"]}, who, conn)
        conn.execute("UPDATE proposals SET route_id = ? WHERE id = ?", (route["id"], proposal_id))
    conn.execute("UPDATE proposals SET status = ?, decided_at = ?, event_id = ? WHERE id = ?", (status, now_iso(), event_id, proposal_id))
    return event_id


def approve_proposal(proposal_id: str, overrides: dict | None = None, actor: str = YOU) -> dict:
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError("changes must be an object")
    with db() as conn:
        _apply_proposal(conn, proposal_id, overrides, actor)
        return _proposal_dict(conn, conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone())


def reject_proposal(proposal_id: str, note: str = "", actor: str = YOU) -> dict:
    note = _text({"note": note}, "note", MAX_TEXT)
    with db() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError("proposal not found")
        if row["status"] != "pending":
            raise Conflict("That proposal was already decided.")
        conn.execute("UPDATE proposals SET status = 'rejected', decided_at = ?, decision_note = ? WHERE id = ?", (now_iso(), note, proposal_id))
        return _proposal_dict(conn, conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone())


def reopen_proposal(proposal_id: str, actor: str = YOU) -> dict:
    """Undo a decision: revert what an approval changed, or take back a rejection."""
    with db() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError("proposal not found")
        if row["status"] == "pending":
            raise Conflict("That proposal is still waiting for review.")
        if row["status"] == "rejected":
            conn.execute("UPDATE proposals SET status = 'pending', decided_at = NULL, decision_note = '' WHERE id = ?", (proposal_id,))
        elif row["action"] == "create_route":
            raise Conflict("Delete the route instead. Creating a route can't be undone from here.")
        else:
            _undo(conn, row["event_id"], actor)
        return _proposal_dict(conn, conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone())


# ---------------------------------------------------------------- context for agents


def _norm_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").casefold()


def _path_matches(target: str, path: str) -> bool:
    target, path = _norm_path(target), _norm_path(path)
    if not target or not path:
        return False
    if target.startswith("~/"):
        rest = "/" + target[2:]
        return path.endswith(rest) or (rest + "/") in (path + "/")
    return path == target or path.startswith(target + "/")


def _target_matches(target, folder: str, repo: str, topic: str, file: str, url: str) -> bool:
    kind, value = target["kind"], target["value"]
    if kind == "folder" and folder:
        return _path_matches(value, folder)
    if kind == "file" and file:
        f = _norm_path(file)
        return f == _norm_path(value) or f.endswith("/" + _norm_path(value).lstrip("~/"))
    if kind == "repo" and repo:
        r = normalize_repo(repo)
        return r == value or r.endswith("/" + value) or value.endswith("/" + r)
    if kind == "topic" and topic:
        words = topic.casefold()
        return any(part and part in words for part in value.split(", "))
    if kind in ("artifact", "url") and url:
        return url.strip().rstrip("/").casefold() == value.rstrip("/").casefold()
    return False


def find_routes(folder: str = "", repo: str = "", topic: str = "", file: str = "", url: str = "") -> list[dict]:
    """Routes that apply to what an agent is working on, Global first. Paused routes are left out."""
    with db() as conn:
        routes = conn.execute("SELECT * FROM routes WHERE paused = 0 ORDER BY sort_order, name").fetchall()
        matched = []
        for route in routes:
            if route["scope"] == "global":
                matched.append((route, ["applies everywhere"]))
                continue
            reasons = [f"{t['kind']} {t['value']}" for t in conn.execute("SELECT * FROM targets WHERE route_id = ?", (route["id"],))
                       if _target_matches(t, folder, repo, topic, file, url)]
            if reasons:
                matched.append((route, reasons))
        order = {"global": 0, "project": 1, "sub": 2}
        matched.sort(key=lambda pair: (order[pair[0]["scope"]], pair[0]["sort_order"]))
        return [{"id": r["id"], "name": r["name"], "scope": r["scope"], "description": r["description"],
                 "parent_id": r["parent_id"], "matched_by": reasons} for r, reasons in matched]


def get_context(route_ref: str = "", folder: str = "", repo: str = "", topic: str = "", file: str = "", url: str = "",
                mark_used: bool = True) -> dict:
    """Merge Global, Project and Sub-detour context in precedence order."""
    with db() as conn:
        if route_ref:
            selected = _require_route(conn, route_ref)
            chain = []
            row = selected
            while row is not None:
                chain.append(row)
                row = conn.execute("SELECT * FROM routes WHERE id = ?", (row["parent_id"],)).fetchone() if row["parent_id"] else None
            globals_ = [] if selected["scope"] == "global" else conn.execute("SELECT * FROM routes WHERE scope = 'global' ORDER BY sort_order").fetchall()
            layers = list(globals_) + list(reversed(chain))
        else:
            matched_ids = [r["id"] for r in find_routes(folder, repo, topic, file, url)]
            ids = list(matched_ids)
            for route_id in matched_ids:  # a matching Sub-detour brings its parent
                parent = conn.execute("SELECT parent_id FROM routes WHERE id = ?", (route_id,)).fetchone()["parent_id"]
                if parent and parent not in ids:
                    ids.append(parent)
            rows = [conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone() for route_id in ids]
            order = {"global": 0, "project": 1, "sub": 2}
            layers = sorted(rows, key=lambda r: (order[r["scope"]], r["sort_order"]))
        result_layers, used = [], []
        for rank, route in enumerate(layers, start=1):
            if route["paused"]:
                continue
            items = conn.execute(
                "SELECT * FROM items WHERE route_id = ? AND status = 'active' ORDER BY pinned DESC, kind, title COLLATE NOCASE", (route["id"],)
            ).fetchall()
            used.extend(item["id"] for item in items)
            result_layers.append({
                "authority": rank, "route": {"id": route["id"], "name": route["name"], "scope": route["scope"], "description": route["description"]},
                "items": [{"id": i["id"], "kind": i["kind"], "title": i["title"], "body": i["body"], "pinned": bool(i["pinned"])} for i in items],
            })
        if mark_used and used:
            stamp = now_iso()
            conn.executemany("UPDATE items SET last_used_at = ? WHERE id = ?", [(stamp, item_id) for item_id in used])
        return {
            "precedence": "Layers are listed from highest to lowest authority. When two items disagree, follow the one with the lower authority number: Global outranks Project, and Project outranks Sub-detour.",
            "layers": result_layers,
        }


def search_items(query: str, limit: int = 20) -> list[dict]:
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db() as conn:
        rows = conn.execute(
            "SELECT items.*, routes.name AS route_name FROM items JOIN routes ON routes.id = items.route_id "
            "WHERE items.status = 'active' AND routes.paused = 0 AND (items.title LIKE ? ESCAPE '\\' OR items.body LIKE ? ESCAPE '\\') "
            "ORDER BY items.pinned DESC, items.updated_at DESC LIMIT ?",
            (pattern, pattern, max(1, min(limit, 50))),
        ).fetchall()
        return [{"id": r["id"], "route": r["route_name"], "route_id": r["route_id"], "kind": r["kind"], "title": r["title"], "preview": r["body"][:240]} for r in rows]


# ---------------------------------------------------------------- access keys


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _key_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"], "can_propose": bool(row["can_propose"]), "created_at": row["created_at"],
            "last_used_at": row["last_used_at"], "revoked_at": row["revoked_at"]}


def list_keys() -> list[dict]:
    with db() as conn:
        return [_key_dict(row) for row in conn.execute("SELECT * FROM mcp_access_keys ORDER BY revoked_at IS NOT NULL, created_at DESC")]


def create_key(payload: dict) -> dict:
    name = _text(payload, "name", 80, required=True)
    can_propose = payload.get("can_propose", True)
    if not isinstance(can_propose, bool):
        raise ValueError("can_propose must be true or false")
    token = f"dtr_{secrets.token_urlsafe(32)}"
    key_id = make_id()
    with db() as conn:
        conn.execute("INSERT INTO mcp_access_keys (id, name, token_hash, can_propose, created_at) VALUES (?, ?, ?, ?, ?)",
                     (key_id, name, hash_key(token), int(can_propose), now_iso()))
        row = conn.execute("SELECT * FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone()
    return {"access_key": _key_dict(row), "token": token}


def update_key(key_id: str, payload: dict) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            raise KeyError("access key not found")
        if "can_propose" in payload:
            if not isinstance(payload["can_propose"], bool):
                raise ValueError("can_propose must be true or false")
            conn.execute("UPDATE mcp_access_keys SET can_propose = ? WHERE id = ?", (int(payload["can_propose"]), key_id))
        if "name" in payload:
            conn.execute("UPDATE mcp_access_keys SET name = ? WHERE id = ?", (_text(payload, "name", 80, required=True), key_id))
        return _key_dict(conn.execute("SELECT * FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone())


def revoke_key(key_id: str) -> dict:
    with db() as conn:
        if conn.execute("SELECT 1 FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone() is None:
            raise KeyError("access key not found")
        conn.execute("UPDATE mcp_access_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?", (now_iso(), key_id))
        return _key_dict(conn.execute("SELECT * FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone())


def verify_key(token: str) -> dict | None:
    if not token:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM mcp_access_keys WHERE token_hash = ? AND revoked_at IS NULL", (hash_key(token),)).fetchone()
        if row is None:
            return None
        stamp = now_iso()
        conn.execute("UPDATE mcp_access_keys SET last_used_at = ? WHERE id = ?", (stamp, row["id"]))
        key = _key_dict(row)
    key["last_used_at"] = stamp
    return key


def get_key(key_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM mcp_access_keys WHERE id = ? AND revoked_at IS NULL", (key_id,)).fetchone()
        return _key_dict(row) if row else None


# ---------------------------------------------------------------- export and import

EXPORT_FORMAT = "detour-export/2"


def export_all() -> dict:
    with db() as conn:
        routes = [dict(_snapshot(r, ROUTE_FIELDS), paused=bool(r["paused"])) for r in conn.execute("SELECT * FROM routes ORDER BY sort_order")]
        items = [dict(_snapshot(r, ITEM_FIELDS), pinned=bool(r["pinned"])) for r in conn.execute("SELECT * FROM items ORDER BY created_at")]
        targets = [_snapshot(r, TARGET_FIELDS) for r in conn.execute("SELECT * FROM targets ORDER BY created_at")]
    return {"format": EXPORT_FORMAT, "exported_at": now_iso(), "routes": routes, "items": items, "targets": targets}


def import_all(payload: dict, actor: str = YOU) -> dict:
    """Add every route in an export as new routes. Nothing existing is changed."""
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError(f"expected an export with format {EXPORT_FORMAT}")
    routes, items, targets = payload.get("routes"), payload.get("items"), payload.get("targets")
    if not all(isinstance(value, list) for value in (routes, items, targets)):
        raise ValueError("routes, items and targets must be lists")
    with db() as conn:
        id_map: dict[str, str] = {}
        ordered = sorted(routes, key=lambda r: 0 if not r.get("parent_id") else 1)
        for route in ordered:
            if not isinstance(route, dict):
                raise ValueError("each route must be an object")
            data = {key: route.get(key) for key in ("name", "scope", "description", "policy", "paused")}
            data["parent_id"] = id_map.get(route.get("parent_id")) if route.get("parent_id") else None
            if data["scope"] == "sub" and not data["parent_id"]:
                data["scope"] = "project"
            if data["scope"] == "sub" and not NESTED_DETOURS_ENABLED:
                data["scope"], data["parent_id"] = "project", None
            if data.get("paused") is None:
                data["paused"] = False
            created = create_route({k: v for k, v in data.items() if v is not None}, actor, conn)
            id_map[route.get("id")] = created["id"]
        for item in items:
            if not isinstance(item, dict) or item.get("route_id") not in id_map:
                raise ValueError("each item must belong to a route in the export")
            state = _new_item_state(id_map[item["route_id"]], item, actor)
            state["status"] = "retired" if item.get("status") == "retired" else "active"
            _change(conn, "item", None, state, "add", actor, f"Imported {state['kind']} “{state['title']}”")
        for target in targets:
            if not isinstance(target, dict) or target.get("route_id") not in id_map:
                raise ValueError("each target must belong to a route in the export")
            add_target(id_map[target["route_id"]], target, actor, conn)
    return {"routes": len(id_map), "items": len(items), "targets": len(targets)}
