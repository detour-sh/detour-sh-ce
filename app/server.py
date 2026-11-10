from __future__ import annotations

import datetime as dt
import base64
import binascii
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_NAME = "detour"
PORT = int(os.environ.get("PORT", "3787"))
DATA_DIR = Path(os.environ.get("DETOUR_DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("DETOUR_DB_PATH", str(DATA_DIR / "detour.sqlite")))
EXPORT_DIR = Path(os.environ.get("DETOUR_EXPORT_DIR", str(DATA_DIR / "exports")))

ARTIFACT_TYPE_DEFINITIONS = [
    ("file", "File", "A local or uploaded file that an AI can reference."),
    ("repo", "Repository", "A codebase with local and/or cloud locations."),
    ("note", "Note", "Quick context, observations, and working notes."),
    ("doc", "Doc", "Durable documentation, guides, specs, and references."),
    ("skill", "Skill", "Reusable human or agent procedures for doing work."),
    ("prompt", "Prompt", "Prompt templates, agent instructions, and task frames."),
    ("decision", "Decision", "Product, engineering, or operating decisions with rationale."),
    ("runbook", "Runbook", "Step-by-step operational procedures and checklists."),
    ("tool", "Tool", "Commands, MCP servers, APIs, setup notes, and owners."),
    ("product_info", "Product info", "Positioning, customers, support facts, roadmap, and FAQ."),
    ("soul_file", "Soul file", "Voice, principles, taste, and durable behavior notes."),
]
ARTIFACT_TYPES = {artifact_type for artifact_type, _label, _description in ARTIFACT_TYPE_DEFINITIONS}
ARTIFACT_TYPE_LABELS = {artifact_type: label for artifact_type, label, _description in ARTIFACT_TYPE_DEFINITIONS}
ARTIFACT_STATUSES = {"active", "archived"}
VISIBILITIES = {"private", "team", "exportable"}
BUNDLE_AUDIENCES = {"teammate", "contractor", "agent", "repo", "public"}
ROUTE_ITEM_KINDS = {"artifact", "note", "link", "repo"}
DETOUR_SCOPES = {"global", "project", "sub"}
NESTED_DETOURS_ENABLED = os.environ.get("DETOUR_ENABLE_NESTED_DETOURS", "false").lower() == "true"
MCP_AUTH_MODE = os.environ.get("DETOUR_MCP_AUTH_MODE", "key").strip().lower()
USER_NAME = os.environ.get("DETOUR_USER_NAME", "Local user").strip() or "Local user"
if MCP_AUTH_MODE not in {"key", "off"}:
    raise RuntimeError("DETOUR_MCP_AUTH_MODE must be 'key' or 'off'")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_FILE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".txt"}


class RequestTooLarge(Exception):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_id() -> str:
    return uuid.uuid4().hex


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or make_id()[:8]


def parse_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def normalize_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    tags = []
    for item in raw:
        tag = slugify(str(item))
        if tag and tag not in tags:
            tags.append(tag)
    return tags


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        migrate_legacy_records_table(conn)
        migrate_to_routes_and_artifacts(conn)
        migrate_route_item_schema(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              title TEXT NOT NULL,
              slug TEXT NOT NULL,
              body_markdown TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
              visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','team','exportable')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bundles (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              slug TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              audience TEXT NOT NULL CHECK (audience IN ('teammate','contractor','agent','repo','public')),
              artifact_ids_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_artifacts_type_status ON artifacts(type, status);
            CREATE INDEX IF NOT EXISTS idx_artifacts_updated_at ON artifacts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_bundles_updated_at ON bundles(updated_at DESC);

            CREATE TABLE IF NOT EXISTS routes (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS route_items (
              id TEXT PRIMARY KEY,
              route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
              kind TEXT NOT NULL CHECK (kind IN ('artifact','note','link','repo')),
              artifact_id TEXT,
              label TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              body_markdown TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_routes_updated_at ON routes(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_route_items_route ON route_items(route_id, created_at);

            CREATE TABLE IF NOT EXISTS mcp_access_keys (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              last_used_at TEXT,
              revoked_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_mcp_access_keys_active ON mcp_access_keys(revoked_at);

            CREATE TABLE IF NOT EXISTS environment_flags (
              scope_id TEXT NOT NULL,
              name TEXT NOT NULL,
              value TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (scope_id, name)
            );

            CREATE TABLE IF NOT EXISTS tag_sets (
              id TEXT PRIMARY KEY,
              scope_id TEXT NOT NULL,
              name TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (scope_id, name)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(artifacts)")}
        if "local_path" not in columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN local_path TEXT NOT NULL DEFAULT ''")
        if "cloud_url" not in columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN cloud_url TEXT NOT NULL DEFAULT ''")
        if "notes_markdown" not in columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN notes_markdown TEXT NOT NULL DEFAULT ''")
        for column, definition in (("file_name", "TEXT NOT NULL DEFAULT ''"), ("file_size", "INTEGER NOT NULL DEFAULT 0"), ("file_sha256", "TEXT NOT NULL DEFAULT ''"), ("blob_path", "TEXT NOT NULL DEFAULT ''")):
            if column not in columns:
                conn.execute(f"ALTER TABLE artifacts ADD COLUMN {column} {definition}")
        route_columns = {row["name"] for row in conn.execute("PRAGMA table_info(routes)")}
        if "scope" not in route_columns:
            conn.execute("ALTER TABLE routes ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'")
        if "parent_id" not in route_columns:
            conn.execute("ALTER TABLE routes ADD COLUMN parent_id TEXT")
        if "sort_order" not in route_columns:
            conn.execute("ALTER TABLE routes ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            route_rows = conn.execute(
                "SELECT id FROM routes ORDER BY CASE scope WHEN 'global' THEN 0 WHEN 'project' THEN 1 ELSE 2 END, title COLLATE NOCASE ASC"
            ).fetchall()
            for index, route_row in enumerate(route_rows):
                conn.execute("UPDATE routes SET sort_order = ? WHERE id = ?", (index, route_row["id"]))


def hash_mcp_access_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def list_environment_flags(scope_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT scope_id, name, value, description, created_at, updated_at FROM environment_flags WHERE scope_id = ? ORDER BY name",
            (scope_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def normalize_environment_flag(payload: dict) -> tuple[str, str, str]:
    name = clean_required(payload.get("name"), "name").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name):
        raise ValueError("name must use uppercase letters, numbers, and underscores")
    if any(word in name for word in ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "PRIVATE_KEY")):
        raise ValueError("Detour environment flags cannot store secrets or access keys")
    value = clean_required(payload.get("value"), "value")
    description = str(payload.get("description") or "").strip()
    if len(value) > 500 or len(description) > 500:
        raise ValueError("value and description must be at most 500 characters")
    return name, value, description


def save_environment_flag(scope_id: str, payload: dict) -> dict:
    name, value, description = normalize_environment_flag(payload)
    if scope_id != "workspace" and not get_route(scope_id):
        raise KeyError("route not found")
    ts = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO environment_flags (scope_id, name, value, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(scope_id, name) DO UPDATE SET value = excluded.value, description = excluded.description, updated_at = excluded.updated_at""",
            (scope_id, name, value, description, ts, ts),
        )
        row = conn.execute("SELECT * FROM environment_flags WHERE scope_id = ? AND name = ?", (scope_id, name)).fetchone()
    return dict(row)


def remove_environment_flag(scope_id: str, name: str) -> None:
    with db() as conn:
        result = conn.execute("DELETE FROM environment_flags WHERE scope_id = ? AND name = ?", (scope_id, name))
        if not result.rowcount:
            raise KeyError("environment flag not found")


def list_tag_sets(scope_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tag_sets WHERE scope_id = ? ORDER BY name COLLATE NOCASE", (scope_id,)).fetchall()
    return [{**dict(row), "tags": parse_json_list(row["tags_json"])} for row in rows]


def applicable_tag_sets(route_id: str) -> list[dict]:
    sets = list_tag_sets("workspace")
    route = get_route(route_id)
    if not route:
        return sets
    for level in detour_ancestry(route):
        sets.extend(list_tag_sets(level["id"]))
    return sets


def normalize_tag_set(payload: dict) -> tuple[str, list[str]]:
    name = clean_required(payload.get("name"), "name")
    tags = normalize_tags(payload.get("tags"))
    if len(name) > 80:
        raise ValueError("tag set name must be at most 80 characters")
    if not tags:
        raise ValueError("a tag set needs at least one tag")
    if len(tags) > 50:
        raise ValueError("a tag set can contain at most 50 tags")
    return name, tags


def save_tag_set(scope_id: str, payload: dict) -> dict:
    name, tags = normalize_tag_set(payload)
    if scope_id != "workspace" and not get_route(scope_id):
        raise KeyError("route not found")
    tag_set_id, ts = make_id(), now_iso()
    with db() as conn:
        existing = conn.execute("SELECT id FROM tag_sets WHERE scope_id = ? AND name = ?", (scope_id, name)).fetchone()
        if existing:
            tag_set_id = existing["id"]
            conn.execute("UPDATE tag_sets SET tags_json = ?, updated_at = ? WHERE id = ?", (json.dumps(tags), ts, tag_set_id))
        else:
            conn.execute("INSERT INTO tag_sets (id, scope_id, name, tags_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (tag_set_id, scope_id, name, json.dumps(tags), ts, ts))
    return next(item for item in list_tag_sets(scope_id) if item["id"] == tag_set_id)


def remove_tag_set(scope_id: str, tag_set_id: str) -> None:
    with db() as conn:
        result = conn.execute("DELETE FROM tag_sets WHERE id = ? AND scope_id = ?", (tag_set_id, scope_id))
        if not result.rowcount:
            raise KeyError("tag set not found")


def list_mcp_access_keys() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at FROM mcp_access_keys ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def create_mcp_access_key(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("key name must be between 1 and 80 characters")
    token = f"dtr_{secrets.token_urlsafe(32)}"
    access_key = {"id": make_id(), "name": name, "created_at": now_iso(), "last_used_at": None, "revoked_at": None}
    with db() as conn:
        conn.execute(
            "INSERT INTO mcp_access_keys (id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (access_key["id"], name, hash_mcp_access_key(token), access_key["created_at"]),
        )
    return {"access_key": access_key, "token": token}


def verify_mcp_access_key(token: str) -> dict | None:
    if not token:
        return None
    token_hash = hash_mcp_access_key(token)
    with db() as conn:
        row = conn.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at FROM mcp_access_keys WHERE token_hash = ? AND revoked_at IS NULL",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        used_at = now_iso()
        conn.execute("UPDATE mcp_access_keys SET last_used_at = ? WHERE id = ?", (used_at, row["id"]))
    result = dict(row)
    result["last_used_at"] = used_at
    return result


def revoke_mcp_access_key(key_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT id FROM mcp_access_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise KeyError("access key not found")
        conn.execute("UPDATE mcp_access_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?", (now_iso(), key_id))
        result = conn.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at FROM mcp_access_keys WHERE id = ?", (key_id,)
        ).fetchone()
    return dict(result)


def migrate_to_routes_and_artifacts(conn: sqlite3.Connection) -> None:
    old_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "records" in old_tables:
        conn.execute("ALTER TABLE records RENAME TO artifacts")
        conn.execute("DROP INDEX IF EXISTS idx_records_type_status")
        conn.execute("DROP INDEX IF EXISTS idx_records_updated_at")
    if "stacks" in old_tables:
        conn.execute("ALTER TABLE stacks RENAME TO routes")
        conn.execute("DROP INDEX IF EXISTS idx_stacks_updated_at")
    if "stack_items" in old_tables:
        conn.execute("""CREATE TABLE route_items (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('artifact','note','link','repo')),
            artifact_id TEXT,
            label TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            body_markdown TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        conn.execute("""INSERT INTO route_items (id, route_id, kind, artifact_id, label, url, created_at)
            SELECT id, stack_id, CASE kind WHEN 'record' THEN 'artifact' ELSE kind END,
                   record_id, label, url, created_at FROM stack_items ORDER BY rowid""")
        conn.execute("DROP TABLE stack_items")
    if "bundles" in old_tables:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(bundles)")}
        if "record_ids_json" in columns:
            conn.execute("ALTER TABLE bundles RENAME COLUMN record_ids_json TO artifact_ids_json")


def migrate_route_item_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'route_items'").fetchone()
    if not row:
        return
    columns = {item["name"] for item in conn.execute("PRAGMA table_info(route_items)")}
    if "note" not in row["sql"]:
        conn.executescript(
            """
            ALTER TABLE route_items RENAME TO route_items_legacy;
            CREATE TABLE route_items (
              id TEXT PRIMARY KEY,
              route_id TEXT NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
              kind TEXT NOT NULL CHECK (kind IN ('artifact','note','link','repo')),
              artifact_id TEXT,
              label TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              body_markdown TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            INSERT INTO route_items (id, route_id, kind, artifact_id, label, url, created_at)
              SELECT id, route_id, kind, artifact_id, label, url, created_at FROM route_items_legacy;
            DROP TABLE route_items_legacy;
            """
        )
    elif "body_markdown" not in columns:
        conn.execute("ALTER TABLE route_items ADD COLUMN body_markdown TEXT NOT NULL DEFAULT ''")


def migrate_legacy_records_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'records'"
    ).fetchone()
    create_sql = row["sql"] if row else ""
    if "CHECK (type IN ('note','soul_file','tool','product_info'))" not in create_sql:
        return
    conn.executescript(
        """
        ALTER TABLE records RENAME TO records_legacy_type_check;

        CREATE TABLE records (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          title TEXT NOT NULL,
          slug TEXT NOT NULL,
          body_markdown TEXT NOT NULL,
          tags_json TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
          visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','team','exportable')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        INSERT INTO records
          (id, type, title, slug, body_markdown, tags_json, status, visibility, created_at, updated_at)
        SELECT id, type, title, slug, body_markdown, tags_json, status, visibility, created_at, updated_at
        FROM records_legacy_type_check;

        DROP TABLE records_legacy_type_check;
        CREATE INDEX IF NOT EXISTS idx_records_type_status ON records(type, status);
        CREATE INDEX IF NOT EXISTS idx_records_updated_at ON records(updated_at DESC);
        """
    )


def row_to_artifact(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "slug": row["slug"],
        "body_markdown": row["body_markdown"],
        "tags": parse_json_list(row["tags_json"]),
        "status": row["status"],
        "visibility": row["visibility"],
        "local_path": row["local_path"],
        "cloud_url": row["cloud_url"],
        "notes_markdown": row["notes_markdown"],
        "file_name": row["file_name"],
        "file_size": row["file_size"],
        "file_sha256": row["file_sha256"],
        "blob_path": row["blob_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_bundle(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "slug": row["slug"],
        "description": row["description"],
        "audience": row["audience"],
        "artifact_ids": parse_json_list(row["artifact_ids_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_artifact(artifact_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    return row_to_artifact(row) if row else None


def list_artifacts(params: dict[str, list[str]]) -> list[dict]:
    clauses = []
    args: list[str] = []
    artifact_type = first(params, "type")
    status = first(params, "status") or "active"
    query = first(params, "q")
    tag = first(params, "tag")

    if artifact_type:
        clauses.append("type = ?")
        args.append(artifact_type)
    if status != "all":
        clauses.append("status = ?")
        args.append(status)
    if query:
        clauses.append("(lower(title) LIKE ? OR lower(body_markdown) LIKE ? OR lower(tags_json) LIKE ?)")
        like = f"%{query.lower()}%"
        args.extend([like, like, like])
    if tag:
        clauses.append("lower(tags_json) LIKE ?")
        args.append(f"%{slugify(tag)}%")

    sql = "SELECT * FROM artifacts"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC, title ASC LIMIT 200"

    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [row_to_artifact(row) for row in rows]


def create_artifact(payload: dict, route_id: str | None = None) -> dict:
    title = clean_required(payload.get("title"), "title")
    artifact_type = clean_required(payload.get("type"), "type")
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(ARTIFACT_TYPES))}")
    visibility = str(payload.get("visibility") or "private")
    if visibility not in VISIBILITIES:
        raise ValueError("visibility must be private, team, or exportable")
    body = str(payload.get("body_markdown") or payload.get("body") or "")
    notes = str(payload.get("notes_markdown") or "")
    tags = normalize_tags(payload.get("tags"))
    if route_id:
        configured_sets = applicable_tag_sets(route_id)
        allowed_tags = {tag for tag_set in configured_sets for tag in tag_set["tags"]}
        invalid_tags = [tag for tag in tags if allowed_tags and tag not in allowed_tags]
        if invalid_tags:
            raise ValueError(f"tags are not in an applicable tag set: {', '.join(invalid_tags)}")
    local_path, cloud_url = normalize_locations(payload)
    ts = now_iso()
    artifact_id = make_id()
    with db() as conn:
        if route_id:
            if not conn.execute("SELECT 1 FROM routes WHERE id = ?", (route_id,)).fetchone():
                raise KeyError("route not found")
            if conn.execute("SELECT COUNT(*) FROM route_items WHERE route_id = ?", (route_id,)).fetchone()[0] >= 100:
                raise ValueError("a route can hold at most 100 artifacts")
        conn.execute(
            """
            INSERT INTO artifacts
              (id, type, title, slug, body_markdown, notes_markdown, tags_json, status, visibility, local_path, cloud_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (artifact_id, artifact_type, title, slugify(title), body, notes, json.dumps(tags), visibility, local_path, cloud_url, ts, ts),
        )
        if route_id:
            conn.execute(
                "INSERT INTO route_items (id, route_id, kind, artifact_id, created_at) VALUES (?, ?, 'artifact', ?, ?)",
                (make_id(), route_id, artifact_id, ts),
            )
            conn.execute("UPDATE routes SET updated_at = ? WHERE id = ?", (ts, route_id))
    artifact = get_artifact(artifact_id)
    assert artifact is not None
    return artifact


def create_file_artifact(payload: dict, route_id: str) -> dict:
    filename = clean_required(payload.get("filename"), "filename")
    if Path(filename).name != filename or len(filename) > 255:
        raise ValueError("filename must be a simple file name")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError("file type must be PDF, DOCX, XLSX, CSV, Markdown, or text")
    encoded = clean_required(payload.get("content_base64"), "content_base64")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("file content must be valid base64") from exc
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("file must be between 1 byte and 8 MB")
    artifact = create_artifact({
        "type": "file",
        "title": str(payload.get("title") or filename),
        "body_markdown": str(payload.get("body_markdown") or f"Uploaded file: {filename}"),
        "notes_markdown": str(payload.get("notes_markdown") or ""),
        "tags": payload.get("tags"),
        "visibility": payload.get("visibility") or "private",
    }, route_id)
    blob_dir = DATA_DIR / "files"
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob_path = blob_dir / f"{artifact['id']}{extension}"
    try:
        blob_path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        with db() as conn:
            conn.execute("UPDATE artifacts SET file_name = ?, file_size = ?, file_sha256 = ?, blob_path = ?, updated_at = ? WHERE id = ?",
                         (filename, len(content), digest, str(blob_path), now_iso(), artifact["id"]))
    except Exception:
        blob_path.unlink(missing_ok=True)
        archive_artifact(artifact["id"])
        raise
    return get_artifact(artifact["id"])


def update_artifact(artifact_id: str, payload: dict) -> dict:
    existing = get_artifact(artifact_id)
    if not existing:
        raise KeyError("artifact not found")
    title = str(payload.get("title", existing["title"])).strip()
    if not title:
        raise ValueError("title is required")
    artifact_type = str(payload.get("type", existing["type"]))
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(ARTIFACT_TYPES))}")
    status = str(payload.get("status", existing["status"]))
    if status not in ARTIFACT_STATUSES:
        raise ValueError("status must be active or archived")
    visibility = str(payload.get("visibility", existing["visibility"]))
    if visibility not in VISIBILITIES:
        raise ValueError("visibility must be private, team, or exportable")
    body = str(payload.get("body_markdown", payload.get("body", existing["body_markdown"])))
    notes = str(payload.get("notes_markdown", existing["notes_markdown"]) or "")
    tags = normalize_tags(payload.get("tags", existing["tags"]))
    local_path, cloud_url = normalize_locations(payload, existing)
    ts = now_iso()
    with db() as conn:
        conn.execute(
            """
            UPDATE artifacts
            SET type = ?, title = ?, slug = ?, body_markdown = ?, notes_markdown = ?, tags_json = ?,
                status = ?, visibility = ?, local_path = ?, cloud_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (artifact_type, title, slugify(title), body, notes, json.dumps(tags), status, visibility, local_path, cloud_url, ts, artifact_id),
        )
    updated = get_artifact(artifact_id)
    assert updated is not None
    return updated


def archive_artifact(artifact_id: str) -> dict:
    return update_artifact(artifact_id, {"status": "archived"})


def list_bundles() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM bundles ORDER BY updated_at DESC, title ASC LIMIT 200").fetchall()
    return [row_to_bundle(row) for row in rows]


def get_bundle(bundle_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
    return row_to_bundle(row) if row else None


def bundle_artifacts(bundle: dict) -> list[dict]:
    ids = [str(item) for item in bundle["artifact_ids"]]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM artifacts WHERE id IN ({placeholders}) ORDER BY type ASC, title ASC",
            ids,
        ).fetchall()
    by_id = {row["id"]: row_to_artifact(row) for row in rows}
    return [by_id[item] for item in ids if item in by_id]


def create_bundle(payload: dict) -> dict:
    title = clean_required(payload.get("title"), "title")
    audience = str(payload.get("audience") or "agent")
    if audience not in BUNDLE_AUDIENCES:
        raise ValueError("audience must be teammate, contractor, agent, repo, or public")
    artifact_ids = normalize_id_list(payload.get("artifact_ids"))
    ts = now_iso()
    bundle_id = make_id()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO bundles
              (id, title, slug, description, audience, artifact_ids_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle_id,
                title,
                slugify(title),
                str(payload.get("description") or ""),
                audience,
                json.dumps(artifact_ids),
                ts,
                ts,
            ),
        )
    bundle = get_bundle(bundle_id)
    assert bundle is not None
    return bundle


def update_bundle(bundle_id: str, payload: dict) -> dict:
    existing = get_bundle(bundle_id)
    if not existing:
        raise KeyError("bundle not found")
    title = str(payload.get("title", existing["title"])).strip()
    if not title:
        raise ValueError("title is required")
    audience = str(payload.get("audience", existing["audience"]))
    if audience not in BUNDLE_AUDIENCES:
        raise ValueError("audience must be teammate, contractor, agent, repo, or public")
    artifact_ids = normalize_id_list(payload.get("artifact_ids", existing["artifact_ids"]))
    ts = now_iso()
    with db() as conn:
        conn.execute(
            """
            UPDATE bundles
            SET title = ?, slug = ?, description = ?, audience = ?, artifact_ids_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                slugify(title),
                str(payload.get("description", existing["description"]) or ""),
                audience,
                json.dumps(artifact_ids),
                ts,
                bundle_id,
            ),
        )
    updated = get_bundle(bundle_id)
    assert updated is not None
    return updated


def route_items(route_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM route_items WHERE route_id = ? ORDER BY created_at, rowid", (route_id,)
        ).fetchall()
    items = []
    for row in rows:
        item = {key: row[key] for key in ("id", "kind", "artifact_id", "label", "url", "body_markdown", "created_at")}
        if row["kind"] == "artifact":
            artifact = get_artifact(row["artifact_id"])
            item["artifact"] = artifact
            item["label"] = artifact["title"] if artifact else "Missing artifact"
            item["type"] = artifact["type"] if artifact else "missing"
        items.append(item)
    return items


def row_to_route(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "scope": row["scope"],
        "parent_id": row["parent_id"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "items": route_items(row["id"]),
    }


def list_routes() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM routes ORDER BY sort_order ASC, created_at ASC").fetchall()
    return [row_to_route(row) for row in rows]


def reorder_routes(payload: dict) -> list[dict]:
    route_ids = payload.get("route_ids")
    if not isinstance(route_ids, list) or any(not isinstance(route_id, str) for route_id in route_ids):
        raise ValueError("route_ids must be a list of Detour IDs")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("route_ids cannot contain duplicates")
    with db() as conn:
        existing = {row["id"] for row in conn.execute("SELECT id FROM routes")}
        if set(route_ids) != existing:
            raise ValueError("route_ids must include every Detour exactly once")
        for index, route_id in enumerate(route_ids):
            conn.execute("UPDATE routes SET sort_order = ? WHERE id = ?", (index, route_id))
    return list_routes()


def get_route(route_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    return row_to_route(row) if row else None


def route_memberships() -> dict[str, list[dict]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT ri.artifact_id, r.id AS route_id, r.title AS route_title
            FROM route_items ri
            JOIN routes r ON r.id = ri.route_id
            WHERE ri.kind = 'artifact' AND ri.artifact_id IS NOT NULL
            ORDER BY r.title COLLATE NOCASE ASC
            """
        ).fetchall()
    memberships: dict[str, list[dict]] = {}
    for row in rows:
        memberships.setdefault(row["artifact_id"], []).append(
            {"id": row["route_id"], "title": row["route_title"]}
        )
    return memberships


def create_route(payload: dict) -> dict:
    title = clean_required(payload.get("title"), "title")
    if len(title) > 200:
        raise ValueError("title must be at most 200 characters")
    description = str(payload.get("description") or "").strip()
    if len(description) > 4000:
        raise ValueError("description must be at most 4000 characters")
    scope = str(payload.get("scope") or "project").strip().lower()
    if scope not in DETOUR_SCOPES:
        raise ValueError("scope must be global, project, or sub")
    parent_id = str(payload.get("parent_id") or "").strip() or None
    if scope == "sub":
        if not NESTED_DETOURS_ENABLED:
            raise ValueError("nested Detours are disabled; enable DETOUR_ENABLE_NESTED_DETOURS first")
        parent = get_route(parent_id or "")
        if not parent or parent["scope"] not in {"project", "sub"}:
            raise ValueError("a Sub-detour requires a Project or Sub-detour parent")
    elif parent_id:
        raise ValueError("only a Sub-detour can have a parent")
    route_id, ts = make_id(), now_iso()
    with db() as conn:
        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM routes").fetchone()[0]
        conn.execute(
            "INSERT INTO routes (id, title, description, scope, parent_id, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (route_id, title, description, scope, parent_id, sort_order, ts, ts),
        )
    route = get_route(route_id)
    assert route is not None
    return route


def add_route_item(route_id: str, payload: dict) -> dict:
    if not get_route(route_id):
        raise KeyError("route not found")
    kind = str(payload.get("kind") or "")
    if kind not in ROUTE_ITEM_KINDS:
        raise ValueError("artifact kind must be an artifact, link, or repo")
    artifact_id, label, url, body = "", "", "", ""
    if kind == "artifact":
        artifact_id = clean_required(payload.get("artifact_id"), "artifact_id")
        artifact = get_artifact(artifact_id)
        if not artifact or artifact["status"] != "active":
            raise ValueError("select an active artifact")
    elif kind == "note":
        label = clean_required(payload.get("label"), "label")
        body = str(payload.get("body_markdown") or "")
        if len(label) > 200 or len(body) > 8000:
            raise ValueError("note is too long")
    else:
        label = clean_required(payload.get("label"), "label")
        if len(label) > 200:
            raise ValueError("label must be at most 200 characters")
        url = clean_required(payload.get("url"), "url")
        parsed = urlparse(url)
        if len(url) > 2048 or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in url):
            raise ValueError("enter a valid HTTP(S) URL without credentials")
    ts, item_id = now_iso(), make_id()
    with db() as conn:
        if kind == "artifact" and conn.execute(
            "SELECT 1 FROM route_items WHERE route_id = ? AND kind = 'artifact' AND artifact_id = ?", (route_id, artifact_id)
        ).fetchone():
            raise ValueError("artifact is already in this route")
        if conn.execute("SELECT COUNT(*) FROM route_items WHERE route_id = ?", (route_id,)).fetchone()[0] >= 100:
            raise ValueError("a route can hold at most 100 artifacts")
        conn.execute(
            "INSERT INTO route_items (id, route_id, kind, artifact_id, label, url, body_markdown, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, route_id, kind, artifact_id or None, label, url, body, ts),
        )
        conn.execute("UPDATE routes SET updated_at = ? WHERE id = ?", (ts, route_id))
    return next(item for item in route_items(route_id) if item["id"] == item_id)


def remove_route_item(route_id: str, item_id: str) -> None:
    with db() as conn:
        result = conn.execute("DELETE FROM route_items WHERE id = ? AND route_id = ?", (item_id, route_id))
        if not result.rowcount:
            raise KeyError("artifact not found in route")
        conn.execute("UPDATE routes SET updated_at = ? WHERE id = ?", (now_iso(), route_id))


def detour_ancestry(route: dict) -> list[dict]:
    ancestry = []
    current = route
    seen = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        ancestry.append(current)
        current = get_route(current["parent_id"]) if current.get("parent_id") else None
    if current:
        raise ValueError("Detour hierarchy contains a cycle")
    ancestry.reverse()
    return ancestry


def single_route_context(route: dict, authority: int) -> dict:
    items = []
    for item in route["items"]:
        if item["kind"] == "artifact" and item["artifact"] and item["artifact"]["status"] == "active":
            artifact = item["artifact"]
            items.append({"kind": "artifact", "id": artifact["id"], "type": artifact["type"], "title": artifact["title"],
                          "body_markdown": artifact["body_markdown"][:8000], "notes_markdown": artifact["notes_markdown"][:8000],
                          "local_path": artifact["local_path"], "cloud_url": artifact["cloud_url"],
                          "file_name": artifact["file_name"], "file_size": artifact["file_size"], "file_sha256": artifact["file_sha256"],
                          "truncated": len(artifact["body_markdown"]) > 8000 or len(artifact["notes_markdown"]) > 8000})
        elif item["kind"] == "note":
            items.append({"kind": "note", "label": item["label"], "body_markdown": item["body_markdown"][:8000], "truncated": len(item["body_markdown"]) > 8000})
        elif item["kind"] in {"link", "repo"}:
            items.append({"kind": item["kind"], "label": item["label"], "url": item["url"]})
    return {"detour": {"id": route["id"], "title": route["title"], "description": route["description"], "scope": route["scope"], "authority": authority}, "environment": list_environment_flags(route["id"]), "items": items}


def route_context(route: dict) -> dict:
    globals_ = [item for item in list_routes() if item["scope"] == "global"]
    selected = [] if route["scope"] == "global" else detour_ancestry(route)
    stack = []
    seen = set()
    for item in globals_ + selected:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        authority = 3 if item["scope"] == "global" else 2 if item["scope"] == "project" else 1
        stack.append(single_route_context(item, authority))
    effective_environment: dict[str, dict] = {item["name"]: item for item in list_environment_flags("workspace")}
    for level in sorted(stack, key=lambda item: item["detour"]["authority"]):
        for flag in level["environment"]:
            effective_environment[flag["name"]] = flag
    return {
        "format": "detour.context.v2",
        "requested_detour": {"id": route["id"], "title": route["title"], "scope": route["scope"]},
        "precedence": "global > project > sub",
        "context_stack": stack,
        "workspace_environment": list_environment_flags("workspace"),
        "effective_environment": list(effective_environment.values()),
        "tag_sets": applicable_tag_sets(route["id"]),
        "items": [item for level in stack for item in level["items"]],
        "max_artifact_characters": 8000,
    }


def export_route(route: dict) -> dict:
    artifacts = []
    seen_artifacts: set[str] = set()
    for item in route["items"]:
        if item["kind"] != "artifact" or not item["artifact"] or item["artifact"]["id"] in seen_artifacts:
            continue
        artifact = item["artifact"]
        seen_artifacts.add(artifact["id"])
        artifacts.append({
            "id": artifact["id"],
            "type": artifact["type"],
            "title": artifact["title"],
            "body_markdown": artifact["body_markdown"],
            "notes_markdown": artifact["notes_markdown"],
            "tags": artifact["tags"],
            "visibility": artifact["visibility"],
            "local_path": artifact["local_path"],
            "cloud_url": artifact["cloud_url"],
        })
    return {
        "format": "detour.route.export.v1",
        "exported_at": now_iso(),
        "route": {"title": route["title"], "description": route["description"]},
        "environment": [
            {"name": flag["name"], "value": flag["value"], "description": flag["description"]}
            for flag in list_environment_flags(route["id"])
        ],
        "tag_sets": [{"name": tag_set["name"], "tags": tag_set["tags"]} for tag_set in list_tag_sets(route["id"])],
        "artifacts": artifacts,
        "items": [
            ({"kind": "artifact", "artifact_id": item["artifact_id"]}
             if item["kind"] == "artifact" else
             ({"kind": "note", "label": item["label"], "body_markdown": item["body_markdown"]}
              if item["kind"] == "note" else
              {"kind": item["kind"], "label": item["label"], "url": item["url"]}))
            for item in route["items"]
        ],
    }


def import_route(payload: dict) -> dict:
    if payload.get("format") != "detour.route.export.v1":
        raise ValueError("unsupported route export format")
    source_route = payload.get("route")
    source_artifacts = payload.get("artifacts")
    source_items = payload.get("items")
    source_environment = payload.get("environment", [])
    source_tag_sets = payload.get("tag_sets", [])
    if not isinstance(source_route, dict) or not isinstance(source_artifacts, list) or not isinstance(source_items, list) or not isinstance(source_environment, list) or not isinstance(source_tag_sets, list):
        raise ValueError("route export must include route, artifacts, and items")
    title = clean_required(source_route.get("title"), "route.title")
    description = str(source_route.get("description") or "").strip()
    if len(title) > 200 or len(description) > 4000 or len(source_items) > 100:
        raise ValueError("route export exceeds size limits")
    if len(source_environment) > 100:
        raise ValueError("route export contains too many environment flags")
    normalized_environment = [normalize_environment_flag(flag) for flag in source_environment if isinstance(flag, dict)]
    if len(normalized_environment) != len(source_environment):
        raise ValueError("each environment flag must be an object")
    if len({flag[0] for flag in normalized_environment}) != len(normalized_environment):
        raise ValueError("route export contains duplicate environment flag names")
    if len(source_tag_sets) > 25 or any(not isinstance(tag_set, dict) for tag_set in source_tag_sets):
        raise ValueError("route export contains invalid tag sets")
    normalized_tag_sets = [normalize_tag_set(tag_set) for tag_set in source_tag_sets]
    if len({tag_set[0].casefold() for tag_set in normalized_tag_sets}) != len(normalized_tag_sets):
        raise ValueError("route export contains duplicate tag set names")
    artifact_by_source_id = {}
    for source in source_artifacts:
        if not isinstance(source, dict):
            raise ValueError("each artifact must be an object")
        source_id = clean_required(source.get("id"), "artifact.id")
        if source_id in artifact_by_source_id:
            raise ValueError("route export contains duplicate artifact IDs")
        artifact_type = clean_required(source.get("type"), "artifact.type")
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(ARTIFACT_TYPES))}")
        visibility = str(source.get("visibility") or "private")
        if visibility not in VISIBILITIES:
            raise ValueError("artifact visibility must be private, team, or exportable")
        artifact_by_source_id[source_id] = {
            "type": artifact_type,
            "title": clean_required(source.get("title"), "artifact.title"),
            "body_markdown": str(source.get("body_markdown") or ""),
            "notes_markdown": str(source.get("notes_markdown") or ""),
            "tags": normalize_tags(source.get("tags")),
            "visibility": visibility,
            "local_path": str(source.get("local_path") or "").strip(),
            "cloud_url": str(source.get("cloud_url") or "").strip(),
        }
        normalize_locations(artifact_by_source_id[source_id])
    route_id, ts = make_id(), now_iso()
    new_artifact_ids: dict[str, str] = {}
    with db() as conn:
        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM routes").fetchone()[0]
        conn.execute(
            "INSERT INTO routes (id, title, description, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (route_id, title, description, sort_order, ts, ts),
        )
        for name, value, flag_description in normalized_environment:
            conn.execute(
                "INSERT INTO environment_flags (scope_id, name, value, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (route_id, name, value, flag_description, ts, ts),
            )
        for set_name, set_tags in normalized_tag_sets:
            conn.execute(
                "INSERT INTO tag_sets (id, scope_id, name, tags_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (make_id(), route_id, set_name, json.dumps(set_tags), ts, ts),
            )
        for source_id, artifact in artifact_by_source_id.items():
            artifact_id = make_id()
            new_artifact_ids[source_id] = artifact_id
            conn.execute(
                """
                INSERT INTO artifacts
                  (id, type, title, slug, body_markdown, notes_markdown, tags_json, status, visibility, local_path, cloud_url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (artifact_id, artifact["type"], artifact["title"], slugify(artifact["title"]),
                 artifact["body_markdown"], artifact["notes_markdown"], json.dumps(artifact["tags"]), artifact["visibility"], artifact["local_path"], artifact["cloud_url"], ts, ts),
            )
        for item in source_items:
            if not isinstance(item, dict):
                raise ValueError("each route item must be an object")
            kind = item.get("kind")
            if kind == "artifact":
                artifact_id = new_artifact_ids.get(str(item.get("artifact_id")))
                if not artifact_id:
                    raise ValueError("route item references an unknown artifact")
                conn.execute(
                    "INSERT INTO route_items (id, route_id, kind, artifact_id, created_at) VALUES (?, ?, 'artifact', ?, ?)",
                    (make_id(), route_id, artifact_id, ts),
                )
            elif kind == "note":
                label = clean_required(item.get("label"), "item.label")
                body = str(item.get("body_markdown") or "")
                if len(label) > 200 or len(body) > 8000:
                    raise ValueError("note is too long")
                conn.execute(
                    "INSERT INTO route_items (id, route_id, kind, label, body_markdown, created_at) VALUES (?, ?, 'note', ?, ?, ?)",
                    (make_id(), route_id, label, body, ts),
                )
            elif kind in {"link", "repo"}:
                label = clean_required(item.get("label"), "item.label")
                url = clean_required(item.get("url"), "item.url")
                parsed = urlparse(url)
                if len(label) > 200 or len(url) > 2048 or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("route item URL must be a valid HTTP(S) URL without credentials")
                conn.execute(
                    "INSERT INTO route_items (id, route_id, kind, label, url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (make_id(), route_id, kind, label, url, ts),
                )
            else:
                raise ValueError("route item kind must be artifact, link, or repo")
    route = get_route(route_id)
    assert route is not None
    return route


def export_bundle_json(bundle: dict) -> dict:
    artifacts = bundle_artifacts(bundle)
    return {
        "format": "detour.bundle.v1",
        "exported_at": now_iso(),
        "bundle": bundle,
        "artifacts": artifacts,
    }


def export_bundle_markdown(bundle: dict) -> str:
    artifacts = bundle_artifacts(bundle)
    lines = [
        f"# {bundle['title']}",
        "",
        f"Audience: {bundle['audience']}",
        f"Exported: {now_iso()}",
        "",
    ]
    if bundle["description"]:
        lines.extend([bundle["description"], ""])
    for artifact in artifacts:
        lines.extend(
            [
                f"## {artifact['title']}",
                "",
                f"- Type: `{artifact['type']}`",
                f"- Visibility: `{artifact['visibility']}`",
                f"- Tags: {', '.join(artifact['tags']) if artifact['tags'] else 'none'}",
                "",
                artifact["body_markdown"].strip() or "_No body._",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def clean_required(value, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def normalize_id_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw = [str(item).strip() for item in value]
    else:
        raw = []
    ids = []
    for item in raw:
        if item and item not in ids:
            ids.append(item)
    return ids


def normalize_locations(payload: dict, existing: dict | None = None) -> tuple[str, str]:
    local_path = str(payload.get("local_path", existing.get("local_path", "") if existing else "") or "").strip()
    cloud_url = str(payload.get("cloud_url", existing.get("cloud_url", "") if existing else "") or "").strip()
    if len(local_path) > 2048 or any(ord(char) < 32 for char in local_path):
        raise ValueError("local path must be at most 2048 characters")
    if cloud_url:
        parsed = urlparse(cloud_url)
        if len(cloud_url) > 2048 or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or any(ord(char) < 32 for char in cloud_url):
            raise ValueError("cloud URL must be a valid HTTP(S) URL without credentials")
    return local_path, cloud_url


def first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0].strip() if values else ""


def artifact_type_options() -> str:
    return "\n".join(
        f'<option value="{html.escape(value)}">{html.escape(label)}</option>'
        for value, label, _description in ARTIFACT_TYPE_DEFINITIONS
    )


def artifact_type_label(value: str) -> str:
    return ARTIFACT_TYPE_LABELS.get(value, value.replace("_", " ").title())


def render_shell(title: str, active: str, content: str) -> str:
    def nav_item(label: str, href: str) -> str:
        selected = label == active
        current = ' aria-current="page"' if selected else ""
        return f'<a class="nav-item{" active" if selected else ""}" href="{href}"{current}>{label}</a>'

    nav_items = (("Detours", "/"), ("Context", "/artifacts"), ("Bundles", "/bundles"), ("Connections", "/connections"))
    navigation = "".join(nav_item(label, href) for label, href in nav_items)
    mobile_navigation = "".join(f'<a href="{href}">{label}</a>' for label, href in (*nav_items, ("Settings", "/settings")))
    user_name = html.escape(USER_NAME)
    user_initial = html.escape(USER_NAME[0].upper())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Detour</title>
  <script>
    document.documentElement.dataset.theme = localStorage.getItem("detour-theme") || "system";
    document.documentElement.dataset.accent = localStorage.getItem("detour-accent") || "green";
    document.documentElement.dataset.textSize = localStorage.getItem("detour-text-size") || "standard";
  </script>
  <link rel="stylesheet" href="/assets/portal.css">
</head>
<body>
<div class="app-shell">
  <aside class="sidebar" aria-label="Workspace navigation">
    <div class="rail-heading">
      <a class="brand" href="/">detour<span>.sh</span></a>
    </div>
    <p class="rail-label">YOUR WORKSPACE</p>
    <nav aria-label="Primary">{navigation}</nav>
    <div class="rail-bottom"><span class="local-dot" aria-hidden="true"></span> Local workspace<br><small>Only an export leaves this workspace.</small></div>
  </aside>
  <div class="workspace">
    <header class="topbar">
      <button class="menu-button desktop-menu-button" type="button" aria-label="Collapse navigation" aria-expanded="true" data-toggle-navigation>
        <svg class="menu-lines" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
        <svg class="collapse-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
      </button>
      <details class="mobile-nav"><summary><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg><span>Menu</span></summary><nav aria-label="Mobile navigation">{mobile_navigation}</nav></details>
      <a class="profile-link" href="/settings#profile" aria-label="{user_name} profile">
        <span class="profile-avatar" aria-hidden="true">{user_initial}</span>
        <span class="profile-copy"><strong>{user_name}</strong><small>Detours</small></span>
      </a>
      <a class="settings-button{' active' if active == 'Settings' else ''}" href="/settings" aria-label="Settings" title="Settings">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.25A3.25 3.25 0 1 0 12 8.75a3.25 3.25 0 0 0 0 6.5Z"/><path d="M19.2 13.4a7.8 7.8 0 0 0 .05-1.4 7.8 7.8 0 0 0-.05-1.4l2-1.55-2-3.46-2.47 1a8 8 0 0 0-2.43-1.4L14 2.55h-4l-.3 2.64a8 8 0 0 0-2.43 1.4l-2.47-1-2 3.46 2 1.55a7.8 7.8 0 0 0-.05 1.4c0 .48.02.95.08 1.4l-2.03 1.55 2 3.46 2.47-1a8 8 0 0 0 2.43 1.4l.3 2.64h4l.3-2.64a8 8 0 0 0 2.43-1.4l2.47 1 2-3.46-2-1.55Z"/></svg>
      </a>
    </header>
    <main>
      {content}
    </main>
  </div>
</div>
<script>
  const navigationButton = document.querySelector("[data-toggle-navigation]");
  const navigationCollapsed = localStorage.getItem("detour-navigation-collapsed") === "true";
  document.body.classList.toggle("navigation-collapsed", navigationCollapsed);
  navigationButton?.setAttribute("aria-expanded", String(!navigationCollapsed));
  navigationButton?.setAttribute("aria-label", navigationCollapsed ? "Expand navigation" : "Collapse navigation");
  navigationButton?.addEventListener("click", () => {{
    const collapsed = document.body.classList.toggle("navigation-collapsed");
    navigationButton.setAttribute("aria-expanded", String(!collapsed));
    navigationButton.setAttribute("aria-label", collapsed ? "Expand navigation" : "Collapse navigation");
    localStorage.setItem("detour-navigation-collapsed", String(collapsed));
  }});
  document.querySelectorAll("[data-theme-picker]").forEach((themePicker) => {{
    themePicker.value = document.documentElement.dataset.theme;
    themePicker.addEventListener("change", () => {{
      document.documentElement.dataset.theme = themePicker.value;
      localStorage.setItem("detour-theme", themePicker.value);
      document.querySelectorAll("[data-theme-picker]").forEach((picker) => picker.value = themePicker.value);
    }});
  }});
  const bindLocalPreference = (selector, datasetKey, storageKey, fallback) => {{
    document.querySelectorAll(selector).forEach((picker) => {{
      picker.value = document.documentElement.dataset[datasetKey] || fallback;
      picker.addEventListener("change", () => {{
        document.documentElement.dataset[datasetKey] = picker.value;
        localStorage.setItem(storageKey, picker.value);
        document.querySelectorAll(selector).forEach((other) => other.value = picker.value);
      }});
    }});
  }};
  bindLocalPreference("[data-accent-picker]", "accent", "detour-accent", "green");
  bindLocalPreference("[data-text-size-picker]", "textSize", "detour-text-size", "standard");
  document.querySelector("[data-pattern-toggle]")?.addEventListener("click", (event) => {{
    document.querySelector("[data-pattern-message]").textContent = "Interaction passed";
    event.currentTarget.textContent = "Activated";
  }});
  const patternDialog = document.querySelector("#pattern-dialog");
  document.querySelector("[data-open-pattern]")?.addEventListener("click", () => patternDialog.showModal());
  document.querySelector("[data-close-pattern]")?.addEventListener("click", () => patternDialog.close());
  document.querySelectorAll("[data-json-form]").forEach((form) => {{
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const message = form.querySelector(".form-message");
      const button = form.querySelector('button[type="submit"]');
      message.textContent = "";
      button.disabled = true;
      const data = new FormData(form);
      const body = Object.fromEntries(data.entries());
      const multi = data.getAll("artifact_ids");
      if (multi.length) body.artifact_ids = multi;
      const tagValues = data.getAll("tags").filter(Boolean);
      if (tagValues.length) body.tags = form.querySelector('[name="tags"][multiple]') ? tagValues : String(tagValues[0]).split(",").map((item) => item.trim()).filter(Boolean);
      try {{
        const res = await fetch(form.action, {{
          method: (form.dataset.method || form.method).toUpperCase(),
          headers: {{ "content-type": "application/json" }},
          body: JSON.stringify(body)
        }});
        if (!res.ok) {{
          const error = await res.json().catch(() => ({{ error: res.statusText }}));
          message.textContent = error.error || "Request failed";
          return;
        }}
        if (form.dataset.openCreatedRoute) {{
          const result = await res.json();
          location.assign(`/routes/${{encodeURIComponent(result.route.id)}}`);
          return;
        }}
        location.reload();
      }} catch {{
        message.textContent = "The local app is unavailable. Try again.";
      }} finally {{
        button.disabled = false;
      }}
    }});
  }});
  document.querySelectorAll("[data-route-attach]").forEach((form) => form.querySelector("select").addEventListener("change", async (event) => {{
    const routeId = event.target.value;
    if (!routeId) return;
    const message = form.querySelector(".form-message");
    try {{
      const body = {{ kind: "artifact", artifact_id: form.querySelector('[name="artifact_id"]').value }};
      const res = await fetch(`/api/routes/${{encodeURIComponent(routeId)}}/items`, {{ method: "POST", headers: {{ "content-type": "application/json" }}, body: JSON.stringify(body) }});
      if (!res.ok) {{ const error = await res.json().catch(() => ({{ error: res.statusText }})); message.textContent = error.error || "Could not add artifact"; return; }}
      location.reload();
    }} catch {{ message.textContent = "The local app is unavailable. Try again."; }}
  }}));
  document.querySelectorAll("[data-toggle-edit]").forEach((button) => button.addEventListener("click", () => {{
    const panel = document.getElementById(button.dataset.toggleEdit);
    panel.hidden = !panel.hidden;
    if (!panel.hidden) panel.querySelector("input, textarea, select")?.focus();
  }}));
  document.querySelectorAll("[data-copy-target]").forEach((button) => button.addEventListener("click", async () => {{
    const source = document.getElementById(button.dataset.copyTarget);
    try {{
      await navigator.clipboard.writeText(source.textContent.trim());
      button.textContent = "Copied";
      window.setTimeout(() => {{ button.textContent = "Copy"; }}, 1800);
    }} catch {{ button.textContent = "Copy failed"; }}
  }}));
  document.querySelector("[data-create-mcp-key]")?.addEventListener("submit", async (event) => {{
    event.preventDefault();
    const form = event.currentTarget;
    const message = form.querySelector(".form-message");
    const button = form.querySelector('button[type="submit"]');
    message.textContent = "";
    button.disabled = true;
    try {{
      const res = await fetch(form.action, {{ method: "POST", headers: {{ "content-type": "application/json" }}, body: JSON.stringify(Object.fromEntries(new FormData(form).entries())) }});
      const result = await res.json();
      if (!res.ok) {{ message.textContent = result.error || "Could not create key"; return; }}
      const resultBox = document.getElementById("mcp-key-result");
      document.getElementById("mcp-key-value").textContent = result.token;
      resultBox.hidden = false;
      resultBox.scrollIntoView({{ behavior: "smooth", block: "nearest" }});
      form.reset();
    }} catch {{ message.textContent = "The local app is unavailable. Try again."; }}
    finally {{ button.disabled = false; }}
  }});
  document.querySelector("[data-import-route]")?.addEventListener("submit", async (event) => {{
    event.preventDefault();
    const form = event.currentTarget;
    const message = form.querySelector(".form-message");
    const button = form.querySelector('button[type="submit"]');
    const file = form.querySelector('input[type="file"]').files[0];
    message.textContent = "";
    if (!file) {{ message.textContent = "Choose a Detour JSON file first."; return; }}
    button.disabled = true;
    try {{
      const res = await fetch(form.action, {{ method: "POST", headers: {{ "content-type": "application/json" }}, body: await file.text() }});
      if (!res.ok) {{ const error = await res.json().catch(() => ({{ error: res.statusText }})); message.textContent = error.error || "Import failed"; return; }}
      const result = await res.json();
      location.assign(`/routes/${{encodeURIComponent(result.route.id)}}`);
    }} catch {{ message.textContent = "The local app is unavailable. Try again."; }}
    finally {{ button.disabled = false; }}
  }});
  document.querySelectorAll("[data-file-upload]").forEach((form) => form.addEventListener("submit", async (event) => {{
    event.preventDefault();
    const file = form.querySelector('input[type="file"]').files[0];
    const message = form.querySelector(".form-message");
    const button = form.querySelector('button[type="submit"]');
    if (!file) {{ message.textContent = "Choose a file first."; return; }}
    button.disabled = true;
    try {{
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
      const data = {{ filename: file.name, content_base64: btoa(binary) }};
      const formData = new FormData(form);
      for (const [key, value] of formData.entries()) if (key !== "file") data[key] = value;
      const res = await fetch(form.action, {{ method: "POST", headers: {{ "content-type": "application/json" }}, body: JSON.stringify(data) }});
      if (!res.ok) {{ const error = await res.json().catch(() => ({{ error: res.statusText }})); message.textContent = error.error || "Upload failed"; return; }}
      location.reload();
    }} catch {{ message.textContent = "The local app is unavailable. Try again."; }}
    finally {{ button.disabled = false; }}
  }}));
  const routeDialog = document.querySelector("#create-route-dialog");
  document.querySelector("[data-create-route]")?.addEventListener("click", () => routeDialog.showModal());
  document.querySelector("[data-close-route]")?.addEventListener("click", () => routeDialog.close());
  const importRouteDialog = document.querySelector("#import-route-dialog");
  document.querySelector("[data-import-route-dialog]")?.addEventListener("click", () => importRouteDialog.showModal());
  document.querySelector("[data-close-import-route]")?.addEventListener("click", () => importRouteDialog.close());
  document.querySelectorAll("[data-open-dialog]").forEach((button) => button.addEventListener("click", () => {{
    document.getElementById(button.dataset.openDialog)?.showModal();
  }}));
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog")?.close()));
  const closeActionMenus = (except = null) => document.querySelectorAll("[data-action-menu]").forEach((menu) => {{
    if (menu === except) return;
    menu.querySelector("[data-action-menu-popover]").hidden = true;
    menu.querySelector("[data-action-menu-trigger]").setAttribute("aria-expanded", "false");
  }});
  document.querySelectorAll("[data-action-menu]").forEach((menu) => {{
    const trigger = menu.querySelector("[data-action-menu-trigger]");
    const popover = menu.querySelector("[data-action-menu-popover]");
    trigger.addEventListener("click", (event) => {{
      event.stopPropagation();
      const opening = popover.hidden;
      closeActionMenus(menu);
      popover.hidden = !opening;
      trigger.setAttribute("aria-expanded", String(opening));
    }});
    menu.addEventListener("click", (event) => {{
      if (event.target.closest('[role="menuitem"]')) closeActionMenus();
    }});
  }});
  document.addEventListener("click", () => closeActionMenus());
  document.addEventListener("keydown", (event) => {{ if (event.key === "Escape") closeActionMenus(); }});
  const routeList = document.querySelector("#route-list");
  if (routeList) {{
    const savedView = localStorage.getItem("detour-route-view") === "rows" ? "rows" : "grid";
    const setView = (view) => {{
      routeList.dataset.view = view;
      document.querySelectorAll("[data-route-view]").forEach((button) => {{
        button.setAttribute("aria-pressed", String(button.dataset.routeView === view));
      }});
      localStorage.setItem("detour-route-view", view);
    }};
    setView(savedView);
    document.querySelectorAll("[data-route-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.routeView)));
    const orderStatus = document.querySelector("[data-route-order-status]");
    const routeCards = () => Array.from(routeList.querySelectorAll("[data-route-id]"));
    const updateMoveButtons = () => {{
      const cards = routeCards();
      cards.forEach((card, index) => {{
        const up = card.querySelector('[data-move-route="up"]');
        const down = card.querySelector('[data-move-route="down"]');
        if (up) up.disabled = index === 0;
        if (down) down.disabled = index === cards.length - 1;
      }});
    }};
    const saveRouteOrder = async () => {{
      if (orderStatus) orderStatus.textContent = "Saving order…";
      try {{
        const response = await fetch("/api/routes/reorder", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ route_ids: routeCards().map((card) => card.dataset.routeId) }}),
        }});
        if (!response.ok) throw new Error("Could not save Detour order");
        if (orderStatus) orderStatus.textContent = "Order saved";
      }} catch (error) {{
        if (orderStatus) orderStatus.textContent = "Order was not saved. Reload to restore it.";
      }}
    }};
    routeList.addEventListener("click", (event) => {{
      const button = event.target.closest("[data-move-route]");
      if (!button) return;
      const card = button.closest("[data-route-id]");
      const cards = routeCards();
      const index = cards.indexOf(card);
      const nextIndex = button.dataset.moveRoute === "up" ? index - 1 : index + 1;
      if (nextIndex < 0 || nextIndex >= cards.length) return;
      if (nextIndex < index) routeList.insertBefore(card, cards[nextIndex]);
      else routeList.insertBefore(card, cards[nextIndex].nextSibling);
      updateMoveButtons();
      card.focus({{ preventScroll: true }});
      saveRouteOrder();
    }});
    let draggedCard = null;
    routeList.addEventListener("dragstart", (event) => {{
      draggedCard = event.target.closest("[data-route-id]");
      if (!draggedCard) return;
      draggedCard.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedCard.dataset.routeId);
    }});
    routeList.addEventListener("dragover", (event) => {{
      const target = event.target.closest("[data-route-id]");
      if (!draggedCard || !target || target === draggedCard) return;
      event.preventDefault();
      const rect = target.getBoundingClientRect();
      const sameRow = event.clientY >= rect.top && event.clientY <= rect.bottom;
      const placeAfter = sameRow ? event.clientX > rect.left + rect.width / 2 : event.clientY > rect.top + rect.height / 2;
      routeList.insertBefore(draggedCard, placeAfter ? target.nextSibling : target);
    }});
    routeList.addEventListener("drop", (event) => {{
      if (!draggedCard) return;
      event.preventDefault();
      updateMoveButtons();
      saveRouteOrder();
    }});
    routeList.addEventListener("dragend", () => {{
      if (draggedCard) draggedCard.classList.remove("is-dragging");
      draggedCard = null;
    }});
    updateMoveButtons();
  }}
</script>
</body>
</html>"""


def render_home() -> str:
    routes = list_routes()
    tiles = "\n".join(render_route_card(route) for route in routes) or '<p class="empty">No Detours yet. Create a Global or Project Detour.</p>'
    nesting_note = "Advanced nesting is enabled." if NESTED_DETOURS_ENABLED else "Advanced nesting is off. Enable DETOUR_ENABLE_NESTED_DETOURS=true to create Sub-detours."
    parent_options = "".join(f'<option value="{html.escape(route["id"])}">{html.escape(route["title"])}</option>' for route in routes if route["scope"] in {"project", "sub"})
    return render_shell("Detours", "Detours", f"""
      <div class="page-heading routes-heading">
        <div><p class="eyebrow">YOUR WORKSPACE</p><h1>Detours</h1><p>Global context applies everywhere. Project context applies when that Detour is called.</p></div>
        <div class="action-menu" data-action-menu>
          <button class="action-menu-trigger" type="button" aria-label="Add Detour" aria-expanded="false" aria-controls="detour-actions" data-action-menu-trigger><svg class="action-menu-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M10 4v12M4 10h12"/></svg></button>
          <div class="action-menu-popover" id="detour-actions" role="menu" data-action-menu-popover hidden>
            <button type="button" role="menuitem" data-create-route><strong>Create Detour</strong><small>Start a new context bucket</small></button>
            <button type="button" role="menuitem" data-import-route-dialog><strong>Import Detour</strong><small>Restore from a JSON export</small></button>
          </div>
        </div>
      </div>
      <div class="route-toolbar">
        <div><p class="eyebrow">CONTEXT BUCKETS</p><h2>My Detours <span class="result-count">{len(routes)}</span></h2><small>{nesting_note}</small></div>
        <div class="view-switch" role="group" aria-label="Detour view">
          <button type="button" data-route-view="grid" aria-pressed="true">Grid</button>
          <button type="button" data-route-view="rows" aria-pressed="false">Rows</button>
        </div>
      </div>
      <p class="route-order-help">Drag Detours to reorder, or use the arrow buttons. The same order is used in Grid and Rows. <span data-route-order-status role="status" aria-live="polite"></span></p>
      <section id="route-list" class="route-list" data-view="grid" aria-label="Detours">
        {tiles}
      </section>
      <dialog id="create-route-dialog" aria-labelledby="create-route-title">
        <form class="form-stack" method="post" action="/api/routes" data-json-form data-open-created-route="true">
          <div class="dialog-heading"><h2 id="create-route-title">Create Detour</h2><button type="button" class="text-button" data-close-route>Close</button></div>
          <label>Name <input name="title" required maxlength="200" autofocus></label>
          <label>Description <textarea name="description" maxlength="4000" placeholder="What work does this bring together?"></textarea></label>
          <label>Scope <select name="scope"><option value="project">Project</option><option value="global">Global</option>{'<option value="sub">Sub-detour</option>' if NESTED_DETOURS_ENABLED else ''}</select></label>
          {f'<label>Parent for Sub-detour <select name="parent_id"><option value="">None</option>{parent_options}</select></label>' if NESTED_DETOURS_ENABLED else ''}
          <p class="form-message" role="alert" aria-live="polite"></p>
          <button type="submit">Create Detour</button>
        </form>
      </dialog>
      <dialog id="import-route-dialog" aria-labelledby="import-route-title">
        <form class="form-stack" method="post" action="/api/routes/import" data-import-route>
          <div class="dialog-heading"><h2 id="import-route-title">Import Detour</h2><button type="button" class="text-button" data-close-import-route>Close</button></div>
          <p class="muted">Choose a Detour JSON export. Imported artifacts receive new local IDs.</p>
          <label>Detour JSON <input type="file" accept="application/json,.json" required></label>
          <p class="form-message" role="alert" aria-live="polite"></p>
          <button type="submit">Import Detour</button>
        </form>
      </dialog>
    """)


def render_connections() -> str:
    endpoint = "http://127.0.0.1:3788/mcp"
    key_placeholder = "<YOUR_DETOUR_KEY>"

    def snippet(snippet_id: str, value: str) -> str:
        return f'<div class="setup-snippet"><pre id="{snippet_id}"><code>{html.escape(value)}</code></pre><button type="button" class="secondary-button" data-copy-target="{snippet_id}" aria-label="Copy setup instructions">Copy</button></div>'

    authenticated_server = {"type": "http", "url": endpoint, "headers": {"Authorization": f"Bearer {key_placeholder}"}}
    vscode = json.dumps({"servers": {"detour": authenticated_server}}, indent=2)
    cursor = json.dumps({"mcpServers": {"detour": {"url": endpoint, "headers": authenticated_server["headers"]}}}, indent=2)
    claude_desktop = json.dumps({"mcpServers": {"detour": authenticated_server}}, indent=2)
    key_rows = "".join(
        f'''<div class="access-key-row"><div><strong>{html.escape(item["name"])}</strong><small>Created {html.escape(item["created_at"][:10])}{f' · Last used {html.escape(item["last_used_at"][:10])}' if item["last_used_at"] else ''}</small></div>
        <span class="key-status">{"Revoked" if item["revoked_at"] else "Active"}</span>
        {"" if item["revoked_at"] else f'<form method="post" action="/api/mcp-keys/{html.escape(item["id"])}/revoke" data-json-form><p class="form-message" role="alert"></p><button type="submit" class="secondary-button">Revoke</button></form>'}</div>'''
        for item in list_mcp_access_keys()
    ) or '<p class="muted">No harness keys yet.</p>'
    if MCP_AUTH_MODE == "key":
        auth_section = f'''
        <section class="access-key-section" aria-labelledby="access-key-title">
          <div class="access-key-heading"><div><p class="eyebrow">ACCESS CONTROL</p><h2 id="access-key-title">Harness keys</h2><p>Give each local AI harness its own revocable key. Detour stores only a hash.</p></div>
          <form class="new-key-form" method="post" action="/api/mcp-keys" data-create-mcp-key><label>Key name <input name="name" required maxlength="80" placeholder="Codex on this PC"></label><p class="form-message" role="alert"></p><button type="submit">Create key</button></form></div>
          <div id="mcp-key-result" class="one-time-key" hidden><div><strong>Copy this key now</strong><small>It will not be shown again.</small></div><code id="mcp-key-value"></code><button type="button" class="secondary-button" data-copy-target="mcp-key-value">Copy</button></div>
          <div class="access-key-list">{key_rows}</div>
        </section>'''
        auth_note = f'Add <code>Authorization: Bearer {html.escape(key_placeholder)}</code> to every client and replace the placeholder with a dedicated harness key.'
    else:
        auth_section = '<section class="auth-warning"><strong>MCP authentication is disabled.</strong><p>This is intended only for a trusted offline machine. Restart with <code>DETOUR_MCP_AUTH_MODE=key</code> to require harness keys.</p></section>'
        auth_note = "Authentication is disabled by the container startup configuration."
    return render_shell("Connections", "Connections", f"""
      <div class="page-heading"><p class="eyebrow">LOCAL CONNECTION</p><h1>Connect Detour</h1><p>Retrieve Detours and saved context from a desktop AI tool on this computer.</p></div>
      <section class="connection-endpoint" aria-label="MCP endpoint">
        <div><p class="eyebrow">MCP ENDPOINT</p><strong>{endpoint}</strong></div>
        <button type="button" class="secondary-button" data-copy-target="mcp-endpoint">Copy</button>
        <span id="mcp-endpoint" hidden>{endpoint}</span>
      </section>
      {auth_section}
      <div class="connection-layout">
        <nav class="connection-index" aria-label="Client setup">
          <a href="#vscode">VS Code</a><a href="#codex">Codex / ChatGPT desktop</a><a href="#claude-code">Claude Code</a><a href="#claude-desktop">Claude Desktop</a><a href="#cursor">Cursor</a><a href="#other-clients">Other clients</a>
        </nav>
        <div class="connection-guides">
          <section id="vscode" class="connection-guide"><h2>VS Code</h2><p>Run <strong>MCP: Add Server</strong> and choose HTTP, or add this to your user MCP configuration. {auth_note}</p>
            {snippet("vscode-config", vscode)}
            <a href="https://code.visualstudio.com/docs/agent-customization/mcp-servers" target="_blank" rel="noopener noreferrer">VS Code setup docs</a>
          </section>
          <section id="codex" class="connection-guide"><h2>Codex / ChatGPT desktop</h2><p>Add a Streamable HTTP server using the endpoint and an Authorization header:</p>
            {snippet("codex-config", json.dumps({"mcpServers": {"detour": authenticated_server}}, indent=2))}
            <a href="https://developers.openai.com/docs/extend/mcp" target="_blank" rel="noopener noreferrer">OpenAI MCP setup docs</a>
          </section>
          <section id="claude-code" class="connection-guide"><h2>Claude Code</h2><p>Add a remote HTTP server with the endpoint and <code>Authorization: Bearer {html.escape(key_placeholder)}</code>, then use <code>/mcp</code> to check it:</p>
            {snippet("claude-command", f"Endpoint: {endpoint}\nHeader: Authorization: Bearer {key_placeholder}")}
            <a href="https://code.claude.com/docs/en/mcp" target="_blank" rel="noopener noreferrer">Claude Code setup docs</a>
          </section>
          <section id="claude-desktop" class="connection-guide"><h2>Claude Desktop</h2><p>Open Settings &rarr; Developer &rarr; Edit Config and add this authenticated HTTP server, then restart Claude Desktop.</p>
            {snippet("claude-desktop-config", claude_desktop)}
            <a href="https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop" target="_blank" rel="noopener noreferrer">Claude Desktop local MCP docs</a>
          </section>
          <section id="cursor" class="connection-guide"><h2>Cursor</h2><p>Add this to your personal <code>~/.cursor/mcp.json</code>, then restart Cursor:</p>
            {snippet("cursor-config", cursor)}
            <a href="https://prod.cursor.com/help/customization/mcp" target="_blank" rel="noopener noreferrer">Cursor setup docs</a>
          </section>
          <section id="other-clients" class="connection-guide"><h2>Other clients</h2><p>Use Streamable HTTP with the endpoint and an <code>Authorization: Bearer</code> header. Detour offers four read-only tools: list Detours, retrieve Detour context, search saved context, and retrieve one context item.</p><p>Cloud-hosted clients cannot reach this localhost address. Cloud identity can be added later without changing the local key flow.</p></section>
        </div>
      </div>
    """)


def render_environment_section(scope_id: str, heading: str, intro: str, inherited: bool = False) -> str:
    flags = list_environment_flags(scope_id)
    base_action = "/api/environment" if scope_id == "workspace" else f"/api/routes/{html.escape(scope_id)}/environment"
    rows = "".join(
        f'''<div class="environment-row"><div><code>{html.escape(flag["name"])}</code><strong>{html.escape(flag["value"])}</strong><small>{html.escape(flag["description"]) or "No description"}</small></div><form method="post" action="{base_action}/{html.escape(flag["name"])}/remove" data-json-form><p class="form-message" role="alert"></p><button type="submit" class="text-button">Remove</button></form></div>'''
        for flag in flags
    ) or '<p class="empty compact-empty">No flags set here.</p>'
    inherited_copy = '<p class="muted">Workspace defaults also apply; matching Detour values follow context precedence.</p>' if inherited else ""
    return f'''<section class="environment-section" aria-labelledby="environment-{html.escape(scope_id)}">
      <div class="section-title"><div><p class="eyebrow">AI ENVIRONMENT</p><h2 id="environment-{html.escape(scope_id)}">{html.escape(heading)}</h2><p>{html.escape(intro)}</p>{inherited_copy}</div></div>
      <div class="environment-layout"><div class="environment-list">{rows}</div>
        <form class="environment-form form-stack" method="post" action="{base_action}" data-json-form>
          <div class="environment-fields"><label>Name <input name="name" required maxlength="64" placeholder="NEVER_DELETE"></label><label>Value <input name="value" required maxlength="500" placeholder="true"></label></div>
          <label>Description <input name="description" maxlength="500" placeholder="Tell the AI when this flag matters"></label>
          <p class="muted">Non-secret guidance only. Passwords, tokens, and private keys are rejected.</p>
          <p class="form-message" role="alert" aria-live="polite"></p><button type="submit">Save flag</button>
        </form>
      </div>
    </section>'''


def render_tag_sets_section(scope_id: str, heading: str, intro: str, inherited: bool = False) -> str:
    tag_sets = list_tag_sets(scope_id)
    base_action = "/api/tag-sets" if scope_id == "workspace" else f"/api/routes/{html.escape(scope_id)}/tag-sets"
    rows = "".join(
        f'''<div class="tag-set-row"><div><strong>{html.escape(tag_set["name"])}</strong><div class="tag-set-values">{"".join(f'<code>{html.escape(tag)}</code>' for tag in tag_set["tags"])}</div></div><form method="post" action="{base_action}/{html.escape(tag_set["id"])}/remove" data-json-form><p class="form-message" role="alert"></p><button type="submit" class="text-button">Remove</button></form></div>'''
        for tag_set in tag_sets
    ) or '<p class="empty compact-empty">No tag sets defined here.</p>'
    inherited_copy = '<p class="muted">Workspace and parent Detour tag sets are also available here.</p>' if inherited else ""
    return f'''<section class="tag-sets-section" aria-labelledby="tag-sets-{html.escape(scope_id)}">
      <div class="section-title"><div><p class="eyebrow">CONTROLLED TAGS</p><h2 id="tag-sets-{html.escape(scope_id)}">{html.escape(heading)}</h2><p>{html.escape(intro)}</p>{inherited_copy}</div></div>
      <div class="environment-layout"><div class="tag-set-list">{rows}</div>
        <form class="environment-form form-stack" method="post" action="{base_action}" data-json-form>
          <label>Set name <input name="name" required maxlength="80" placeholder="Deployment stage"></label>
          <label>Allowed tags <input name="tags" required placeholder="development, staging, production"></label>
          <p class="muted">Comma-separated canonical tags. Saving the same set name updates it.</p>
          <p class="form-message" role="alert" aria-live="polite"></p><button type="submit">Save tag set</button>
        </form>
      </div>
    </section>'''


def render_tag_picker(route_id: str) -> str:
    tag_sets = applicable_tag_sets(route_id)
    if not tag_sets:
        return '<label>Tags <input name="tags" placeholder="onboarding, testing"></label>'
    groups = "".join(
        f'<optgroup label="{html.escape(tag_set["name"], quote=True)}">' + "".join(
            f'<option value="{html.escape(tag, quote=True)}">{html.escape(tag)}</option>' for tag in tag_set["tags"]
        ) + '</optgroup>'
        for tag_set in tag_sets
    )
    return f'<label>Tags <select name="tags" multiple size="6">{groups}</select><small>Choose from workspace and Detour tag sets.</small></label>'


def render_settings() -> str:
    return render_shell("Settings", "Settings", f"""
      <div class="page-heading"><p class="eyebrow">WORKSPACE</p><h1>Settings</h1><p>Local presentation and advanced diagnostics.</p></div>
      <section class="settings-section" id="profile">
        <div><h2>Profile</h2><p>The local name shown in this Detour workspace.</p></div>
        <div class="settings-value"><strong>{html.escape(USER_NAME)}</strong><small>Set with <code>DETOUR_USER_NAME</code> when starting the container.</small></div>
      </section>
      <section class="settings-section">
        <div><h2>Appearance</h2><p>Choose contrast, accent color, and text size. These preferences stay in this browser.</p></div>
        <div class="appearance-controls">
          <label>Theme <select data-theme-picker aria-label="Settings color scheme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
          <label>Accent <select data-accent-picker aria-label="Interface accent color"><option value="green">Detour green</option><option value="blue">Blue</option><option value="purple">Purple</option><option value="amber">Amber</option></select></label>
          <label>Text size <select data-text-size-picker aria-label="Interface text size"><option value="compact">Compact</option><option value="standard">Standard</option><option value="large">Large</option></select></label>
        </div>
      </section>
      {render_environment_section("workspace", "Workspace environment", "Defaults supplied to AI context for every Detour.")}
      {render_tag_sets_section("workspace", "Global tag sets", "Canonical tags available across every Detour.")}
      <section class="settings-section">
        <div><h2>Interface test pattern</h2><p>Review typography, colors, controls, states, and responsive behavior after a build.</p></div>
        <a class="button-link" href="/test-pattern">Open test pattern</a>
      </section>
    """)


def render_test_pattern() -> str:
    return render_shell("Interface test pattern", "Settings", """
      <div class="page-heading"><p class="eyebrow">BUILD DIAGNOSTIC</p><h1>Interface test pattern</h1><p data-testid="pattern-ready">A deterministic surface for visual and interaction checks.</p></div>
      <section class="test-pattern-grid">
        <article class="pattern-panel"><p class="eyebrow">TYPE</p><h2>Context stays legible</h2><p>Primary text explains the task. <span class="muted">Muted metadata remains readable.</span></p><code>C:\\work\\detour</code><p><a href="#pattern-controls">Visible link treatment</a></p></article>
        <article class="pattern-panel"><p class="eyebrow">COLOR</p><div class="swatch-grid"><span class="swatch swatch-accent">Accent</span><span class="swatch swatch-surface">Surface</span><span class="swatch swatch-success">Success</span><span class="swatch swatch-danger">Danger</span></div></article>
        <article class="pattern-panel" id="pattern-controls"><p class="eyebrow">CONTROLS</p><div class="pattern-controls"><button type="button" data-pattern-toggle>Primary</button><button type="button" class="secondary-button">Secondary</button><button type="button" disabled>Disabled</button><label>Text input <input value="Detour context"></label><label>Select <select><option>Project</option></select></label></div><p class="form-message pattern-message" role="status" data-pattern-message>Ready</p></article>
        <article class="pattern-panel"><p class="eyebrow">STATES</p><div class="pattern-states"><span class="status-sample status-success">Connected</span><span class="status-sample status-warning">Needs review</span><span class="status-sample status-danger">Unavailable</span></div><div class="empty">Empty state: no context saved yet.</div></article>
      </section>
      <dialog id="pattern-dialog"><div class="dialog-heading"><h2>Interaction visible</h2><button type="button" class="text-button" data-close-pattern>Close</button></div><p>The test runner confirms this dialog opens, receives focus, and closes.</p></dialog>
      <button type="button" class="secondary-button" data-open-pattern>Test dialog</button>
    """)


def render_route_card(route: dict) -> str:
    counts: dict[str, int] = {}
    for item in route["items"]:
        label = artifact_type_label(item["type"]) if item["kind"] == "artifact" and item["type"] != "missing" else item["kind"].title()
        counts[label] = counts.get(label, 0) + 1
    types = "".join(f'<span class="route-type">{html.escape(label)} <strong>{count}</strong></span>' for label, count in counts.items()) or '<span class="route-type">Empty</span>'
    route_id = html.escape(route["id"])
    title = html.escape(route["title"])
    return f"""<article class="route-card" data-route-id="{route_id}" draggable="true" tabindex="-1">
      <div class="route-order-actions">
        <span class="route-drag-handle" aria-hidden="true"><svg viewBox="0 0 16 16"><circle cx="5" cy="4" r="1"/><circle cx="11" cy="4" r="1"/><circle cx="5" cy="8" r="1"/><circle cx="11" cy="8" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="11" cy="12" r="1"/></svg></span>
        <button type="button" class="route-move-button" data-move-route="up" aria-label="Move {title} earlier"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 10 4-4 4 4"/></svg></button>
        <button type="button" class="route-move-button" data-move-route="down" aria-label="Move {title} later"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg></button>
      </div>
      <a class="route-card-link" href="/routes/{route_id}">
        <div class="route-card-heading"><strong>{title}</strong><span class="type-label">{html.escape(route['scope'].replace('_', ' ').title())}</span></div>
        <p>{html.escape(route['description']) or 'No description yet.'}</p>
        <div class="route-types">{types}</div>
        <small>{len(route['items'])} context items &middot; Updated {html.escape(route['updated_at'][:10])}</small>
      </a>
    </article>"""


def render_artifact_edit_controls(artifact: dict, control_id: str) -> tuple[str, str]:
    type_options = "".join(
        f'<option value="{html.escape(value)}"{" selected" if value == artifact["type"] else ""}>{html.escape(label)}</option>'
        for value, label, _description in ARTIFACT_TYPE_DEFINITIONS
    )
    button = f'<button type="button" class="secondary-button edit-button action-icon-button" data-toggle-edit="{html.escape(control_id)}"><svg class="row-action-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 11.8V13h1.2l7.1-7.1-1.2-1.2L3 11.8Z"/><path d="m9.6 5.2 1.2 1.2"/></svg><span>Edit</span></button>'
    panel = f"""<div class="edit-panel" id="{html.escape(control_id)}" hidden>
        <form class="quick-edit-form" method="post" data-method="PUT" action="/api/artifacts/{html.escape(artifact['id'])}" data-json-form>
          <label>Title <input name="title" value="{html.escape(artifact['title'], quote=True)}" required maxlength="200"></label>
          <label>Type <select name="type">{type_options}</select></label>
          <label>Tags <input name="tags" value="{html.escape(', '.join(artifact['tags']), quote=True)}"></label>
          <label>Local location <input name="local_path" value="{html.escape(artifact['local_path'], quote=True)}" placeholder="C:\\work\\repo"></label>
          <label>Cloud location <input name="cloud_url" value="{html.escape(artifact['cloud_url'], quote=True)}" placeholder="https://github.com/org/repo"></label>
          <label>Body <textarea name="body_markdown">{html.escape(artifact['body_markdown'])}</textarea></label>
          <label>Repository notes <textarea name="notes_markdown">{html.escape(artifact['notes_markdown'])}</textarea></label>
          <p class="form-message" role="alert" aria-live="polite"></p>
          <button type="submit">Save changes</button>
        </form>
      </div>"""
    return button, panel


def render_route_detail(route: dict) -> str:
    artifacts = list_artifacts({"status": ["active"]})
    artifact_options = "\n".join(f'<option value="{html.escape(artifact["id"])}">{html.escape(artifact["title"])} ({html.escape(artifact_type_label(artifact["type"]))})</option>' for artifact in artifacts)
    rows = "\n".join(render_route_item(route["id"], item) for item in route["items"]) or '<p class="empty">No context yet. Save the first item below.</p>'
    inheritance = "Global context applies first." if route["scope"] == "project" else ("Global and parent Project context apply first." if route["scope"] == "sub" else "Highest-authority context across every project.")
    return render_shell(route["title"], "Detours", f"""
      <a class="back-link" href="/">&larr; Detours</a>
      <div class="page-heading route-detail-heading">
        <div><p class="eyebrow">{html.escape(route['scope'].upper())} DETOUR</p><h1>{html.escape(route['title'])}</h1><p>{html.escape(route['description']) or 'Your private working context.'} {inheritance}</p></div>
        <div class="action-menu" data-action-menu>
          <button class="action-menu-trigger" type="button" aria-label="Add context" aria-expanded="false" aria-controls="context-actions" data-action-menu-trigger><svg class="action-menu-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M10 4v12M4 10h12"/></svg></button>
          <div class="action-menu-popover" id="context-actions" role="menu" data-action-menu-popover hidden>
            <a href="#gather-context" role="menuitem"><strong>Write context</strong><small>Save a rule, decision, or note</small></a>
            <button type="button" role="menuitem" data-open-dialog="add-link-dialog"><strong>Add a link or note</strong><small>Reference another source</small></button>
            <button type="button" role="menuitem" data-open-dialog="upload-file-dialog"><strong>Upload a file</strong><small>Add a local document</small></button>
            <button type="button" role="menuitem" data-open-dialog="attach-artifact-dialog"><strong>Attach existing</strong><small>Reuse saved context</small></button>
            <a href="/api/routes/{html.escape(route['id'])}/export.json" download="{html.escape(slugify(route['title']))}.json" role="menuitem"><strong>Export Detour</strong><small>Download a JSON backup</small></a>
          </div>
        </div>
      </div>
      {render_environment_section(route['id'], "Environment and flags", "Detour-specific operating constraints and tool settings.", inherited=True)}
      {render_tag_sets_section(route['id'], "Tag sets", "Create one or more controlled tag groups for this Detour.", inherited=True)}
      <section class="list-section page-results" aria-labelledby="route-items-title">
        <div class="section-title"><div><p class="eyebrow">SAVED CONTEXT</p><h2 id="route-items-title">Context items <span class="result-count">{len(route['items'])}</span></h2></div></div>
        <div class="artifacts">{rows}</div>
      </section>
      <section class="create-section" id="gather-context" aria-labelledby="add-item-title">
        <div class="capture-heading"><p class="eyebrow">GATHER CONTEXT</p><h2 id="add-item-title">Save something useful</h2><p>Capture the part an AI should remember next time.</p></div>
        <div class="capture-layout">
          <form class="capture-composer form-stack" method="post" action="/api/routes/{html.escape(route['id'])}/artifacts" data-json-form>
            <div class="capture-fields-row">
              <label>Kind <select name="type">{artifact_type_options()}</select></label>
              <label>Title <input name="title" required maxlength="200" placeholder="What should this be called?"></label>
            </div>
            <label>Context <textarea class="capture-body" name="body_markdown" required placeholder="Paste the rule, decision, workflow, or useful detail here..."></textarea></label>
            <details class="advanced-fields">
              <summary>Details and source</summary>
              <div class="advanced-fields-grid">
                {render_tag_picker(route['id'])}
                <label>Local location <input name="local_path" placeholder="C:\\work\\router"></label>
                <label>Cloud location <input name="cloud_url" type="url" placeholder="https://github.com/org/repo"></label>
                <label>Notes <textarea name="notes_markdown" placeholder="Ownership, setup, conventions, or handoff notes"></textarea></label>
              </div>
            </details>
            <p class="form-message" role="alert" aria-live="polite"></p>
            <div class="capture-submit"><span>Saved privately to {html.escape(route['title'])}.</span><button type="submit">Save context</button></div>
          </form>
          <aside class="capture-support" aria-label="Other ways to gather context">
            <h3>Bring something in</h3>
            <p>Use another source without crowding the main capture flow.</p>
            <button class="source-action" type="button" data-open-dialog="add-link-dialog"><span><strong>Add a link or note</strong><small>Reference a page or write a short note</small></span><b aria-hidden="true">&rarr;</b></button>
            <button class="source-action" type="button" data-open-dialog="upload-file-dialog"><span><strong>Upload a file</strong><small>PDF, document, sheet, Markdown, or text</small></span><b aria-hidden="true">&rarr;</b></button>
            <button class="source-action" type="button" data-open-dialog="attach-artifact-dialog"><span><strong>Attach existing</strong><small>Reuse context already saved in Detour</small></span><b aria-hidden="true">&rarr;</b></button>
            <p class="capture-privacy">Private and local by default.</p>
          </aside>
        </div>
      </section>
      <dialog id="add-link-dialog"><div class="dialog-heading"><h2>Add a link or note</h2><button class="text-button" type="button" data-close-dialog>Close</button></div><form class="form-stack" method="post" action="/api/routes/{html.escape(route['id'])}/items" data-json-form><label>Kind <select name="kind"><option value="note">Note</option><option value="link">Link</option></select></label><label>Label <input name="label" required maxlength="200"></label><label>URL <input name="url" type="url" maxlength="2048" placeholder="https://"></label><label>Note <textarea name="body_markdown" placeholder="Context specific to this Detour"></textarea></label><p class="form-message" role="alert" aria-live="polite"></p><button type="submit">Add context</button></form></dialog>
      <dialog id="upload-file-dialog"><div class="dialog-heading"><h2>Upload a file</h2><button class="text-button" type="button" data-close-dialog>Close</button></div><form class="form-stack" method="post" action="/api/routes/{html.escape(route['id'])}/files" data-file-upload><label>File <input name="file" type="file" accept=".pdf,.docx,.xlsx,.csv,.md,.txt" required></label><label>Notes <textarea name="notes_markdown" placeholder="What should the AI know about this file?"></textarea></label><p class="muted">Up to 8 MB. Stored locally in Detour.</p><p class="form-message" role="alert" aria-live="polite"></p><button type="submit">Upload file</button></form></dialog>
      <dialog id="attach-artifact-dialog"><div class="dialog-heading"><h2>Attach existing context</h2><button class="text-button" type="button" data-close-dialog>Close</button></div><form class="form-stack" method="post" action="/api/routes/{html.escape(route['id'])}/items" data-json-form><input type="hidden" name="kind" value="artifact"><label>Context item <select name="artifact_id" required><option value="">Select saved context</option>{artifact_options}</select></label><p class="form-message" role="alert" aria-live="polite"></p><button type="submit">Attach context</button></form></dialog>
    """)


def render_route_item(route_id: str, item: dict) -> str:
    if item["kind"] == "artifact":
        kind = artifact_type_label(item["type"]) if item["type"] != "missing" else "Missing"
        detail = html.escape(item["artifact"]["body_markdown"][:160]) if item["artifact"] else "The source artifact is unavailable."
        if item["artifact"] and item["artifact"]["type"] == "repo" and item["artifact"]["notes_markdown"]:
            detail += f'<br><small>Notes: {html.escape(item["artifact"]["notes_markdown"][:160])}</small>'
        edit_button, edit_panel = render_artifact_edit_controls(item["artifact"], f'edit-route-artifact-{item["artifact_id"]}') if item["artifact"] else ("", "")
    elif item["kind"] == "note":
        kind = "Note"
        detail = html.escape(item["body_markdown"][:160]) or "No note body yet."
        edit_button, edit_panel = "", ""
    else:
        kind = "Repository" if item["kind"] == "repo" else "Link"
        detail = f'<a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(item["url"])}</a>'
        edit_button, edit_panel = "", ""
    return f"""<article class="artifact-row route-item">
      <div class="route-item-content"><div class="row-heading"><strong>{html.escape(item['label'])}</strong><span class="type-label">{kind}</span></div><p>{detail}</p></div>
      <div class="route-item-actions">
        {edit_button}
        <form method="post" action="/api/routes/{html.escape(route_id)}/items/{html.escape(item['id'])}/remove" data-json-form>
          <p class="form-message" role="alert" aria-live="polite"></p><button type="submit" class="text-button action-icon-button"><svg class="row-action-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3.5 4.5h9M6 4.5V3h4v1.5M5 6.5l.5 6h5l.5-6M7 7v4M9 7v4"/></svg><span>Remove</span></button>
        </form>
      </div>
      {edit_panel}
    </article>"""


def render_artifacts(params: dict[str, list[str]]) -> str:
    artifacts = list_artifacts(params)
    routes = list_routes()
    memberships = route_memberships()
    query = html.escape(first(params, "q"), quote=True)
    selected_type = first(params, "type")
    type_options = '<option value="">All types</option>' + "".join(
        f'<option value="{html.escape(value)}"{" selected" if value == selected_type else ""}>{html.escape(label)}</option>'
        for value, label, _ in ARTIFACT_TYPE_DEFINITIONS
    )
    status = first(params, "status") or "active"
    status_options = "".join(
        f'<option value="{value}"{" selected" if value == status else ""}>{label}</option>'
        for value, label in (("active", "Active"), ("archived", "Archived"), ("all", "All statuses"))
    )
    rows = "\n".join(render_artifact_row(artifact, memberships.get(artifact["id"], []), routes) for artifact in artifacts) or '<p class="empty">No matching context. Try another filter or save something to a Detour.</p>'
    return render_shell("Context", "Context", f"""
      <div class="page-heading"><p class="eyebrow">YOUR CONTEXT</p><h1>Context</h1><p>Find your notes, skills, docs, and other saved material.</p></div>
      <form class="filter-bar" method="get" action="/artifacts" role="search">
        <label>Search <input type="search" name="q" value="{query}" placeholder="Title, body, or tag"></label>
        <label>Type <select name="type">{type_options}</select></label>
        <label>Status <select name="status">{status_options}</select></label>
        <button type="submit">Filter</button>
      </form>
      <section class="list-section" aria-labelledby="results-title">
        <div class="section-title"><div><p class="eyebrow">RESULTS</p><h2 id="results-title">Context items <span class="result-count">{len(artifacts)}</span></h2></div><a href="/">Open Detours</a></div>
        <div class="artifacts">{rows}</div>
      </section>
    """)


def render_bundles() -> str:
    bundles = list_bundles()
    artifacts = list_artifacts({"status": ["active"]})
    artifact_options = "\n".join(
        f'<option value="{html.escape(artifact["id"])}">{html.escape(artifact["title"])} ({html.escape(artifact_type_label(artifact["type"]))})</option>'
        for artifact in artifacts
    )
    rows = "\n".join(render_bundle_card(bundle) for bundle in bundles) or '<p class="empty">No bundles yet. Create one when you need a deliberate export.</p>'
    return render_shell("Bundles", "Bundles", f"""
      <div class="page-heading"><p class="eyebrow">DELIBERATE EXPORTS</p><h1>Bundles</h1><p>Choose which context items to package for another person or tool.</p></div>
      <section class="list-section page-results" aria-labelledby="results-title">
        <div class="section-title"><div><p class="eyebrow">RESULTS</p><h2 id="results-title">Bundles <span class="result-count">{len(bundles)}</span></h2></div><a href="#new-bundle">New bundle</a></div>
        <div class="artifacts">{rows}</div>
      </section>
      <section class="create-section" aria-labelledby="create-title">
        <div class="section-title"><div><p class="eyebrow">SELECTED CONTEXT</p><h2 id="create-title">New bundle</h2></div></div>
        <form class="form-panel form-stack editor-form" id="new-bundle" method="post" action="/api/bundles" data-json-form>
          <label>Title <input name="title" required maxlength="200"></label>
          <label>Audience <select name="audience"><option value="agent">Agent</option><option value="teammate">Teammate</option><option value="contractor">Contractor</option><option value="repo">Repo</option><option value="public">Public</option></select></label>
          <label>Description <textarea name="description"></textarea></label>
          <label>Active context <select name="artifact_ids" multiple size="7">{artifact_options}</select></label>
          <p class="muted">Select context with Ctrl/Cmd-click. Export is a separate action. Showing up to 200 active items.</p>
          <p class="form-message" role="alert" aria-live="polite"></p>
          <button type="submit">Create bundle</button>
        </form>
      </section>
    """)


def render_artifact_row(artifact: dict, attached_routes: list[dict], routes: list[dict]) -> str:
    tags = ", ".join(html.escape(tag) for tag in artifact["tags"])
    body = html.escape(artifact["body_markdown"][:100])
    route_links = " ".join(
        f'<a class="route-chip" href="/routes/{html.escape(route["id"])}">{html.escape(route["title"])}</a>'
        for route in attached_routes
    ) or '<span class="muted">Unattached</span>'
    attached_ids = {route["id"] for route in attached_routes}
    route_options = "".join(
        f'<option value="{html.escape(route["id"])}">{html.escape(route["title"])}</option>'
        for route in routes if route["id"] not in attached_ids
    )
    edit_button, edit_panel = render_artifact_edit_controls(artifact, f'edit-artifact-{artifact["id"]}')
    return f"""
    <article class="artifact-row artifact-index-row">
      <div class="row-heading"><strong>{html.escape(artifact["title"])}</strong><span class="type-label">{html.escape(artifact_type_label(artifact["type"]))}</span></div>
      <p>{body or 'No body yet.'}</p>
      <small>{'Tags: ' + tags + ' · ' if tags else ''}{'Local: ' + html.escape(artifact['local_path']) + ' · ' if artifact['local_path'] else ''}{'Cloud: ' + html.escape(artifact['cloud_url']) + ' · ' if artifact['cloud_url'] else ''}{'Has repository notes · ' if artifact['type'] == 'repo' and artifact['notes_markdown'] else ''}Updated {html.escape(artifact["updated_at"][:10])}</small>
      <div class="artifact-routes"><span class="meta-label">DETOURS</span>{route_links}</div>
      <div class="artifact-actions">
        {edit_button}
        {f'''<form class="route-attach-form" method="post" action="/api/routes/{html.escape(routes[0]["id"])}/items" data-route-attach>
          <input type="hidden" name="kind" value="artifact"><input type="hidden" name="artifact_id" value="{html.escape(artifact["id"])}">
          <label class="sr-only" for="route-for-{html.escape(artifact["id"])}">Add to Detour</label><select id="route-for-{html.escape(artifact["id"])}" name="route_id"><option value="">Add to Detour</option>{route_options}</select>
          <p class="form-message" role="alert" aria-live="polite"></p>
        </form>''' if route_options else ''}
      </div>
      {edit_panel}
    </article>"""


def render_bundle_card(bundle: dict) -> str:
    return f"""
    <article class="artifact-row">
      <div class="row-heading"><strong>{html.escape(bundle["title"])}</strong><span class="type-label">{html.escape(bundle["audience"])}</span></div>
      <p>{html.escape(bundle["description"]) or 'No description yet.'}</p>
      <small>{len(bundle["artifact_ids"])} context items · <a href="/api/bundles/{html.escape(bundle["id"])}/export.md">Markdown</a> · <a href="/api/bundles/{html.escape(bundle["id"])}/export.json">JSON</a></small>
    </article>"""


class DetourHandler(BaseHTTPRequestHandler):
    server_version = "detour-local/0.1"

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def do_PUT(self) -> None:
        self.route("PUT")

    def route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if not self.valid_host():
                return self.json({"error": "invalid host"}, HTTPStatus.BAD_REQUEST)
            if method in {"POST", "PUT"}:
                if not self.valid_write_origin():
                    return self.json({"error": "cross-origin write denied"}, HTTPStatus.FORBIDDEN)
                if self.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return self.json({"error": "application/json required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            if method == "GET" and path == "/":
                return self.html(render_home())
            if method == "GET" and path == "/artifacts":
                return self.html(render_artifacts(params))
            if method == "GET" and path == "/bundles":
                return self.html(render_bundles())
            if method == "GET" and path == "/connections":
                return self.html(render_connections())
            if method == "GET" and path == "/settings":
                return self.html(render_settings())
            if method == "GET" and path == "/test-pattern":
                return self.html(render_test_pattern())
            if method == "GET" and path.startswith("/routes/"):
                route = get_route(path.removeprefix("/routes/"))
                if not route:
                    raise KeyError("route not found")
                return self.html(render_route_detail(route))
            if method == "GET" and path == "/assets/portal.css":
                return self.text(Path(__file__).with_name("portal.css").read_text(encoding="utf-8"), "text/css; charset=utf-8")
            if method == "GET" and path == "/health":
                return self.json({"ok": True, "service": APP_NAME, "db_path": str(DB_PATH)})
            if path == "/api/search" and method == "GET":
                return self.json({"artifacts": list_artifacts(params)})
            if path == "/api/mcp-keys" and method == "GET":
                return self.json({"access_keys": list_mcp_access_keys()})
            if path == "/api/mcp-keys" and method == "POST":
                return self.json(create_mcp_access_key(self.body_json()), HTTPStatus.CREATED)
            if path.startswith("/api/mcp-keys/") and path.endswith("/revoke") and method == "POST":
                key_id = path.removeprefix("/api/mcp-keys/").removesuffix("/revoke")
                return self.json({"access_key": revoke_mcp_access_key(key_id)})
            if path == "/api/artifacts" and method == "GET":
                return self.json({"artifacts": list_artifacts(params)})
            if path == "/api/artifacts" and method == "POST":
                return self.json({"artifact": create_artifact(self.body_json())}, HTTPStatus.CREATED)
            if path.startswith("/api/artifacts/"):
                return self.artifact_route(method, path)
            if path == "/api/bundles" and method == "GET":
                return self.json({"bundles": list_bundles()})
            if path == "/api/bundles" and method == "POST":
                return self.json({"bundle": create_bundle(self.body_json())}, HTTPStatus.CREATED)
            if path.startswith("/api/bundles/"):
                return self.bundle_route(method, path)
            if path == "/api/environment" and method == "GET":
                return self.json({"environment": list_environment_flags("workspace")})
            if path == "/api/environment" and method == "POST":
                return self.json({"flag": save_environment_flag("workspace", self.body_json())}, HTTPStatus.CREATED)
            if path.startswith("/api/environment/") and path.endswith("/remove") and method == "POST":
                remove_environment_flag("workspace", path.removeprefix("/api/environment/").removesuffix("/remove"))
                return self.json({"ok": True})
            if path == "/api/tag-sets" and method == "GET":
                return self.json({"tag_sets": list_tag_sets("workspace")})
            if path == "/api/tag-sets" and method == "POST":
                return self.json({"tag_set": save_tag_set("workspace", self.body_json())}, HTTPStatus.CREATED)
            if path.startswith("/api/tag-sets/") and path.endswith("/remove") and method == "POST":
                remove_tag_set("workspace", path.removeprefix("/api/tag-sets/").removesuffix("/remove"))
                return self.json({"ok": True})
            if path == "/api/routes" and method == "GET":
                return self.json({"routes": list_routes()})
            if path == "/api/routes" and method == "POST":
                return self.json({"route": create_route(self.body_json())}, HTTPStatus.CREATED)
            if path == "/api/routes/reorder" and method == "POST":
                return self.json({"routes": reorder_routes(self.body_json())})
            if path == "/api/routes/import" and method == "POST":
                return self.json({"route": import_route(self.body_json())}, HTTPStatus.CREATED)
            if path.startswith("/api/routes/"):
                return self.route_detail_api(method, path)
            self.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RequestTooLarge:
            self.json({"error": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except KeyError as exc:
            self.json({"error": str(exc).strip("'")}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            print(f"request failed: {type(exc).__name__}: {exc}", flush=True)
            self.json({"error": "internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def valid_host(self) -> bool:
        host = self.headers.get("host", "")
        if not host or "@" in host or "," in host:
            return False
        try:
            parsed = urlparse(f"//{host}")
            return parsed.hostname in {"127.0.0.1", "localhost", "detour"} and parsed.port == self.server.server_port
        except ValueError:
            return False

    def valid_write_origin(self) -> bool:
        if self.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        origin = self.headers.get("origin")
        if not origin:
            return True
        return origin == f"http://{self.headers['host']}"

    def artifact_route(self, method: str, path: str) -> None:
        rest = path.removeprefix("/api/artifacts/")
        if rest.endswith("/archive") and method == "POST":
            artifact_id = rest.removesuffix("/archive")
            return self.json({"artifact": archive_artifact(artifact_id)})
        artifact_id = rest
        if method == "GET":
            artifact = get_artifact(artifact_id)
            if not artifact:
                raise KeyError("artifact not found")
            return self.json({"artifact": artifact})
        if method == "PUT":
            return self.json({"artifact": update_artifact(artifact_id, self.body_json())})
        self.json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def bundle_route(self, method: str, path: str) -> None:
        rest = path.removeprefix("/api/bundles/")
        if rest.endswith("/export.md") and method == "GET":
            bundle_id = rest.removesuffix("/export.md")
            bundle = get_bundle(bundle_id)
            if not bundle:
                raise KeyError("bundle not found")
            return self.text(export_bundle_markdown(bundle), "text/markdown; charset=utf-8")
        if rest.endswith("/export.json") and method == "GET":
            bundle_id = rest.removesuffix("/export.json")
            bundle = get_bundle(bundle_id)
            if not bundle:
                raise KeyError("bundle not found")
            return self.json(export_bundle_json(bundle))
        bundle_id = rest
        if method == "GET":
            bundle = get_bundle(bundle_id)
            if not bundle:
                raise KeyError("bundle not found")
            return self.json({"bundle": bundle, "artifacts": bundle_artifacts(bundle)})
        if method == "PUT":
            return self.json({"bundle": update_bundle(bundle_id, self.body_json())})
        self.json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def route_detail_api(self, method: str, path: str) -> None:
        parts = path.removeprefix("/api/routes/").split("/")
        route_id = parts[0]
        if len(parts) == 2 and parts[1] == "export.json" and method == "GET":
            route = get_route(route_id)
            if not route:
                raise KeyError("route not found")
            filename = f"{slugify(route['title']) or 'route'}.json"
            return self.json_file(export_route(route), filename)
        if len(parts) == 2 and parts[1] == "artifacts" and method == "POST":
            return self.json({"artifact": create_artifact(self.body_json(), route_id)}, HTTPStatus.CREATED)
        if len(parts) == 2 and parts[1] == "files" and method == "POST":
            return self.json({"artifact": create_file_artifact(self.body_json(MAX_UPLOAD_BYTES * 2), route_id)}, HTTPStatus.CREATED)
        if len(parts) == 4 and parts[1] == "items" and parts[3] == "remove" and method == "POST":
            remove_route_item(route_id, parts[2])
            return self.json({"ok": True})
        if len(parts) == 2 and parts[1] == "items" and method == "POST":
            return self.json({"item": add_route_item(route_id, self.body_json())}, HTTPStatus.CREATED)
        if len(parts) == 2 and parts[1] == "environment" and method == "GET":
            return self.json({"environment": list_environment_flags(route_id)})
        if len(parts) == 2 and parts[1] == "environment" and method == "POST":
            return self.json({"flag": save_environment_flag(route_id, self.body_json())}, HTTPStatus.CREATED)
        if len(parts) == 4 and parts[1] == "environment" and parts[3] == "remove" and method == "POST":
            remove_environment_flag(route_id, parts[2])
            return self.json({"ok": True})
        if len(parts) == 2 and parts[1] == "tag-sets" and method == "GET":
            return self.json({"tag_sets": list_tag_sets(route_id)})
        if len(parts) == 2 and parts[1] == "tag-sets" and method == "POST":
            return self.json({"tag_set": save_tag_set(route_id, self.body_json())}, HTTPStatus.CREATED)
        if len(parts) == 4 and parts[1] == "tag-sets" and parts[3] == "remove" and method == "POST":
            remove_tag_set(route_id, parts[2])
            return self.json({"ok": True})
        route = get_route(route_id)
        if not route:
            raise KeyError("route not found")
        if len(parts) == 2 and parts[1] == "context" and method == "GET":
            return self.json(route_context(route))
        if len(parts) == 1 and method == "GET":
            return self.json({"route": route})
        self.json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def body_json(self, max_length: int = 1_000_000) -> dict:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0:
            return {}
        if length > max_length:
            raise RequestTooLarge()
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def json_file(self, payload: dict, filename: str) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-disposition", f'attachment; filename="{filename}"')
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def html(self, payload: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.text(payload, "text/html; charset=utf-8", status)

    def text(self, payload: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DetourHandler)
    print(f"detour local running on http://127.0.0.1:{PORT} with data at {DB_PATH}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
