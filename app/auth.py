"""Owner sign-in for hosted mode.

Detour has exactly one owner. The first person to present the one-time setup code
printed in the server log claims it and chooses a password. A signed-in browser
holds a random session token in an HttpOnly, SameSite=Strict cookie. The password is
stored as a salted scrypt hash, and setup codes and session tokens as SHA-256
hashes, so a copy of the database can't be replayed as a sign-in.

Local mode never uses this module.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from http import HTTPStatus

import store

SESSION_COOKIE = "detour_session"
SESSION_MAX_AGE = dt.timedelta(days=30)
SESSION_IDLE_TIMEOUT = dt.timedelta(days=7)
SESSION_TOUCH_INTERVAL = dt.timedelta(minutes=5)
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
DISPLAY_NAME_MAX_LENGTH = 80
SETUP_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SETUP_CODE_LENGTH = 12
SETUP_CODE_MAX_ATTEMPTS = 10
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2**15, 8, 1, 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
SIGN_IN_FREE_ATTEMPTS = 5
SIGN_IN_FIRST_LOCK_SECONDS = 30
SIGN_IN_MAX_LOCK_SECONDS = 15 * 60
SETUP_CODE_COMMAND = "docker compose exec detour python /app/server.py setup-code"
RESET_OWNER_COMMAND = "docker compose exec detour python /app/server.py reset-owner --yes"


class AuthError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.UNAUTHORIZED, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class SignInThrottle:
    """Lock out password guessing after a few failures, doubling the wait each time.

    Detour has one owner, so one global counter is enough. Attempts are also
    serialized, which caps guessing at the speed of one scrypt check. The cost is
    that someone hammering the page can keep the owner waiting; the wait never
    exceeds SIGN_IN_MAX_LOCK_SECONDS.
    """

    def __init__(self) -> None:
        self.attempt_lock = threading.Lock()
        self.failures = 0
        self.locked_until = 0.0

    def wait_seconds(self) -> int:
        if self.failures < SIGN_IN_FREE_ATTEMPTS:
            return 0
        return min(SIGN_IN_FIRST_LOCK_SECONDS * 2 ** min(self.failures - SIGN_IN_FREE_ATTEMPTS, 30), SIGN_IN_MAX_LOCK_SECONDS)

    def check(self) -> None:
        remaining = self.locked_until - time.monotonic()
        if remaining > 0:
            seconds = math.ceil(remaining)
            raise AuthError(f"Too many attempts. Try again in {seconds} seconds.", HTTPStatus.TOO_MANY_REQUESTS, seconds)

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= SIGN_IN_FREE_ATTEMPTS:
            self.locked_until = time.monotonic() + self.wait_seconds()

    def reset(self) -> None:
        self.failures = 0
        self.locked_until = 0.0


THROTTLE = SignInThrottle()


# ---------------------------------------------------------------- helpers


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _from_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return "$".join(("scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), _b64(salt), _b64(digest)))


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_digest = base64.urlsafe_b64decode(expected)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.urlsafe_b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(expected_digest), maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(digest, expected_digest)


def validate_new_password(password, confirmation) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password must be at most {PASSWORD_MAX_LENGTH} characters")
    if password != confirmation:
        raise ValueError("passwords do not match")
    return password


def clean_display_name(value) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not name:
        raise ValueError("display name is required")
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"display name must be at most {DISPLAY_NAME_MAX_LENGTH} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("display name contains control characters")
    return name


def normalize_setup_code(value) -> str:
    cleaned = str(value or "").upper().translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    return re.sub(r"[^0-9A-Z]", "", cleaned)


def describe_user_agent(value: str) -> str:
    agent = value or ""
    browser = next((name for token, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if token in agent), "Browser")
    system = next((name for token, name in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iPadOS"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if token in agent), "")
    return f"{browser} on {system}" if system else browser


def read_cookie(header: str | None, name: str) -> str | None:
    for part in (header or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return None


def session_cookie(token: str, secure: bool) -> str:
    flags = f"Path=/; HttpOnly; SameSite=Strict; Max-Age={int(SESSION_MAX_AGE.total_seconds())}"
    return f"{SESSION_COOKIE}={token}; {flags}{'; Secure' if secure else ''}"


def cleared_cookie(secure: bool) -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{'; Secure' if secure else ''}"


# ---------------------------------------------------------------- owner and sessions


def owner_exists() -> bool:
    with store.db() as conn:
        return conn.execute("SELECT 1 FROM owner WHERE id = 1").fetchone() is not None


def owner_display_name() -> str | None:
    with store.db() as conn:
        row = conn.execute("SELECT display_name FROM owner WHERE id = 1").fetchone()
    return row["display_name"] if row else None


def issue_setup_code() -> str:
    """Replace any earlier setup code with a new one and return it. Only its hash is stored."""
    code = "".join(secrets.choice(SETUP_CODE_ALPHABET) for _ in range(SETUP_CODE_LENGTH))
    with store.db() as conn:
        if conn.execute("SELECT 1 FROM owner WHERE id = 1").fetchone():
            raise ValueError("Detour is already set up")
        conn.execute(
            """INSERT INTO setup_codes (id, code_hash, failed_attempts, created_at) VALUES (1, ?, 0, ?)
               ON CONFLICT(id) DO UPDATE SET code_hash = excluded.code_hash, failed_attempts = 0, created_at = excluded.created_at""",
            (hash_secret(code), store.now_iso()),
        )
    return "-".join(code[index:index + 4] for index in range(0, SETUP_CODE_LENGTH, 4))


def _insert_session(conn, label: str) -> str:
    now = _utc_now()
    conn.execute("DELETE FROM owner_sessions WHERE revoked_at IS NOT NULL OR expires_at <= ?", (_iso(now),))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO owner_sessions (id, token_hash, label, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (store.make_id(), hash_secret(token), label[:120], _iso(now), _iso(now), _iso(now + SESSION_MAX_AGE)),
    )
    return token


def claim_owner(payload: dict, label: str) -> str:
    """Create the owner from a valid setup code and return a new session token."""
    code = normalize_setup_code(payload.get("setup_code"))
    failure = None
    with store.db(write=True) as conn:
        if conn.execute("SELECT 1 FROM owner WHERE id = 1").fetchone():
            raise AuthError("Detour is already set up. Sign in instead.", HTTPStatus.CONFLICT)
        row = conn.execute("SELECT code_hash, failed_attempts FROM setup_codes WHERE id = 1").fetchone()
        if not row:
            raise AuthError(f"There is no active setup code. Restart Detour or run: {SETUP_CODE_COMMAND}", HTTPStatus.FORBIDDEN)
        if not code or not hmac.compare_digest(hash_secret(code), row["code_hash"]):
            attempts = row["failed_attempts"] + 1
            if attempts >= SETUP_CODE_MAX_ATTEMPTS:
                conn.execute("DELETE FROM setup_codes WHERE id = 1")
                failure = AuthError(f"Too many incorrect setup codes. Get a new code with: {SETUP_CODE_COMMAND}", HTTPStatus.FORBIDDEN)
            else:
                conn.execute("UPDATE setup_codes SET failed_attempts = ? WHERE id = 1", (attempts,))
                failure = AuthError("That setup code is not valid.", HTTPStatus.FORBIDDEN)
        else:
            display_name = clean_display_name(payload.get("display_name"))
            password = validate_new_password(payload.get("password"), payload.get("password_confirm"))
            created = store.now_iso()
            conn.execute(
                "INSERT INTO owner (id, display_name, password_hash, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
                (display_name, hash_password(password), created, created),
            )
            conn.execute("DELETE FROM setup_codes")
            conn.execute("DELETE FROM owner_sessions")
            token = _insert_session(conn, label)
    if failure:
        raise failure
    return token


def sign_in(payload: dict, label: str) -> str:
    password = payload.get("password")
    with THROTTLE.attempt_lock:
        THROTTLE.check()
        with store.db() as conn:
            row = conn.execute("SELECT password_hash FROM owner WHERE id = 1").fetchone()
        if not row:
            raise AuthError("Detour is not set up yet.", HTTPStatus.CONFLICT)
        if not isinstance(password, str) or len(password) > PASSWORD_MAX_LENGTH or not verify_password(password, row["password_hash"]):
            THROTTLE.failed()
            raise AuthError("Incorrect password.")
        THROTTLE.reset()
        with store.db() as conn:
            return _insert_session(conn, label)


def session_for_token(token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    now = _utc_now()
    with store.db() as conn:
        row = conn.execute(
            "SELECT id, label, created_at, last_seen_at, expires_at FROM owner_sessions WHERE token_hash = ? AND revoked_at IS NULL",
            (hash_secret(token),),
        ).fetchone()
        if not row:
            return None
        last_seen = _from_iso(row["last_seen_at"])
        if _from_iso(row["expires_at"]) <= now or last_seen + SESSION_IDLE_TIMEOUT <= now:
            return None
        if now - last_seen >= SESSION_TOUCH_INTERVAL:
            conn.execute("UPDATE owner_sessions SET last_seen_at = ? WHERE id = ?", (_iso(now), row["id"]))
    return dict(row)


def sign_out(token: str | None) -> None:
    if not token or len(token) > 200:
        return
    with store.db() as conn:
        conn.execute("UPDATE owner_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (store.now_iso(), hash_secret(token)))


def change_password(payload: dict, current_session_id: str) -> None:
    """Replace the password after checking the current one, then sign out every other browser.

    Wrong current passwords count toward the same lockout as sign-in, so this can't be used to guess.
    """
    current = payload.get("current_password")
    with THROTTLE.attempt_lock:
        THROTTLE.check()
        with store.db() as conn:
            row = conn.execute("SELECT password_hash FROM owner WHERE id = 1").fetchone()
        if not row or not isinstance(current, str) or len(current) > PASSWORD_MAX_LENGTH or not verify_password(current, row["password_hash"]):
            THROTTLE.failed()
            raise AuthError("Current password is incorrect.", HTTPStatus.FORBIDDEN)
        THROTTLE.reset()
    password = validate_new_password(payload.get("new_password"), payload.get("new_password_confirm"))
    if hmac.compare_digest(password, current):
        raise ValueError("new password must be different from the current one")
    with store.db() as conn:
        conn.execute("UPDATE owner SET password_hash = ?, updated_at = ? WHERE id = 1", (hash_password(password), store.now_iso()))
        conn.execute("UPDATE owner_sessions SET revoked_at = ? WHERE id != ? AND revoked_at IS NULL", (store.now_iso(), current_session_id))


def reset_owner() -> str:
    """Remove the owner and every session, keep all saved context, and return a new setup code."""
    with store.db() as conn:
        conn.execute("DELETE FROM owner")
        conn.execute("DELETE FROM owner_sessions")
        conn.execute("DELETE FROM setup_codes")
    THROTTLE.reset()
    return issue_setup_code()


def setup_code_banner(code: str, public_url: str | None) -> str:
    rule = "=" * 68
    where = f"{public_url}/" if public_url else "the portal"
    return (
        f"{rule}\n"
        f"Detour is not set up yet. Open {where}\n"
        f"and enter this one-time setup code:\n\n"
        f"    {code}\n\n"
        f"A new code is printed each time Detour starts until setup is done.\n"
        f"{rule}"
    )
