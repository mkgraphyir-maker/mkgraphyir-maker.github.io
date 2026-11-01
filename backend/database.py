"""
SQLite data layer for the MK Graphy backend.

The database lives **outside the Git checkout** (see ``paths.py``). Seeds come
from ``seed_projects.json``; the first admin account is created with a
cryptographically generated password that is written to a private file and
printed once, so no default password ever exists in the repository.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import paths
import security

BACKEND_DIR = paths.BACKEND_DIR
SEED_PROJECTS_PATH = BACKEND_DIR / "seed_projects.json"
SCHEMA_PATH = BACKEND_DIR / "schema.sql"

DEFAULT_ADMIN_PHONE = "09120000000"
DEFAULT_ADMIN_FIRST_NAME = "مدیر"
DEFAULT_ADMIN_LAST_NAME = "استودیو"


def db_path() -> Path:
    return paths.database_path()


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a short-lived SQLite connection and always close it.

    ``with sqlite3.connect(...)`` only commits — it does not close the file
    descriptor, which leaks handles under load and can be used to exhaust the
    server. Every query in this module goes through this context manager.
    """
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA busy_timeout = 8000")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Backwards-compatible raw helper. Prefer :func:`connection`."""
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA trusted_schema = OFF")
    conn.execute("PRAGMA busy_timeout = 8000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "type": row["type"],
        "categoryName": row["category_name"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "shortDescription": row["short_description"],
        "fullDescription": row["full_description"],
        "client": row["client"],
        "year": row["year"],
        "duration": row["duration"],
        "servicesProvided": _loads(row["services_provided"], []),
        "technologies": _loads(row["technologies"], []),
        "image": row["image"],
        "bannerImage": row["banner_image"],
        "gallery": _loads(row["gallery"], []),
        "stats": _loads(row["stats"], []),
        "challenge": row["challenge"],
        "solution": row["solution"],
        "testimonial": row["testimonial"],
        "sortOrder": row["sort_order"],
        "isPublished": bool(row["is_published"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _user_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "phone": row["phone"],
        "role": row["role"],
        "mustChangePassword": bool(row["must_change_password"]) if _has_column(row, "must_change_password") else False,
        "createdAt": row["created_at"],
    }


def _has_column(row: sqlite3.Row, column: str) -> bool:
    try:
        row[column]
    except (IndexError, KeyError):
        return False
    return True


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {row["name"] for row in rows}


def _ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """Additive migrations so an older database keeps working."""
    applied: list[str] = []
    user_columns = _table_columns(conn, "users")
    if user_columns and "must_change_password" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
        applied.append("users.must_change_password")
    session_columns = _table_columns(conn, "sessions")
    if session_columns and "expires_at" in session_columns:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
    conn.commit()
    return applied


def init_db(path: Path | None = None, seed: bool = True) -> Path:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.harden_dir(target.parent)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connection(target) as conn:
        conn.executescript(schema)
        _ensure_columns(conn)
        purge_expired_sessions(conn)
        if seed:
            _seed_projects(conn)
    if target.exists():
        paths.harden_file(target)
    return target


# ---------------------------------------------------------------------------
# First-run admin bootstrap
# ---------------------------------------------------------------------------


def _admin_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id ASC").fetchall()


def _write_bootstrap_credentials(phone: str, password: str, first_name: str, last_name: str) -> Path:
    content = "\n".join(
        [
            "MK Graphy — generated administrator credentials",
            "=" * 52,
            f"Generated at : {security.iso_now()}",
            f"Name         : {first_name} {last_name}",
            f"Phone        : {phone}",
            f"Password     : {password}",
            "",
            "Sign in at    : /admin/login",
            "",
            "This file is private (owner-only permissions) and lives outside the",
            "Git repository. Change the password from the admin panel",
            "(\"تنظیمات ادمین\") right after the first sign-in, then delete this file.",
            "",
        ]
    )
    target = paths.bootstrap_credentials_path()
    security.write_private_file(target, content)
    return target


def ensure_admin_account() -> dict[str, Any]:
    """Make sure at least one admin exists and its password is not public.

    * No admin yet -> create one with a generated (or ``MK_ADMIN_PASSWORD``) secret.
    * Admin still using a password that used to be shipped publicly -> rotate it.

    Returns a small report dict used by the console banner and the CLI.
    """
    report: dict[str, Any] = {
        "created": False,
        "rotated": False,
        "phone": None,
        "password": None,
        "credentials_file": None,
    }
    env_password = (os.environ.get("MK_ADMIN_PASSWORD") or "").strip()

    phone = security.normalize_phone(os.environ.get("MK_ADMIN_PHONE")) or DEFAULT_ADMIN_PHONE
    first_name = security.validate_name(os.environ.get("MK_ADMIN_FIRST_NAME")) or DEFAULT_ADMIN_FIRST_NAME
    last_name = security.validate_name(os.environ.get("MK_ADMIN_LAST_NAME")) or DEFAULT_ADMIN_LAST_NAME

    with connection() as conn:
        admins = _admin_rows(conn)
        if not admins:
            password = env_password or security.generate_password()
            problem = security.password_problem(password)
            if problem:
                password = security.generate_password()
            now = security.iso_now()
            existing = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET role = 'admin', first_name = ?, last_name = ?, password_hash = ?, "
                    "must_change_password = 1, updated_at = ? WHERE id = ?",
                    (first_name, last_name, security.hash_password(password), now, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO users (first_name, last_name, phone, password_hash, role, "
                    "must_change_password, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'admin', 1, ?, ?)",
                    (first_name, last_name, phone, security.hash_password(password), now, now),
                )
            conn.commit()
            report.update(created=True, phone=phone, password=password)
            security.audit("admin.bootstrap.created", phone=phone)
        else:
            primary = admins[0]
            if _password_is_publicly_known(primary["password_hash"]):
                password = env_password or security.generate_password()
                problem = security.password_problem(password)
                if problem:
                    password = security.generate_password()
                conn.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE id = ?",
                    (security.hash_password(password), security.iso_now(), primary["id"]),
                )
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (primary["id"],))
                conn.commit()
                report.update(rotated=True, phone=primary["phone"], password=password)
                security.audit("admin.bootstrap.rotated", phone=primary["phone"])

    if report["password"]:
        report["credentials_file"] = str(
            _write_bootstrap_credentials(
                report["phone"] or phone,
                report["password"],
                first_name if report["created"] else (admins[0]["first_name"] if admins else first_name),
                last_name if report["created"] else (admins[0]["last_name"] if admins else last_name),
            )
        )
    return report


def _password_is_publicly_known(stored_hash: str) -> bool:
    for candidate in security.PROJECT_DEFAULT_PASSWORDS:
        if security.verify_password(candidate, stored_hash):
            return True
    return False


def admin_uses_compromised_password(user_id: int) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False
        return _password_is_publicly_known(row["password_hash"])


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def _seed_projects(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]
    if count:
        return
    if not SEED_PROJECTS_PATH.exists():
        return
    projects = json.loads(SEED_PROJECTS_PATH.read_text(encoding="utf-8"))
    now = security.iso_now()
    for project in projects:
        conn.execute(
            """
            INSERT INTO projects (
                slug, type, category_name, title, subtitle, short_description,
                full_description, client, year, duration, services_provided,
                technologies, image, banner_image, gallery, stats, challenge,
                solution, testimonial, sort_order, is_published, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["slug"],
                project["type"],
                project["categoryName"],
                project["title"],
                project["subtitle"],
                project["shortDescription"],
                project["fullDescription"],
                project["client"],
                project["year"],
                project["duration"],
                json.dumps(project.get("servicesProvided", []), ensure_ascii=False),
                json.dumps(project.get("technologies", []), ensure_ascii=False),
                project["image"],
                project["bannerImage"],
                json.dumps(project.get("gallery", []), ensure_ascii=False),
                json.dumps(project.get("stats", []), ensure_ascii=False),
                project["challenge"],
                project["solution"],
                project.get("testimonial", ""),
                int(project.get("sortOrder", 1000)),
                1 if project.get("isPublished", True) else 0,
                now,
                now,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user_by_phone(phone: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return _user_from_row(row) if row else None


def get_user_with_hash(phone: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()


def get_user_row_by_id(user_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row else None


def create_user(
    first_name: str,
    last_name: str,
    phone: str,
    password_hash: str,
    role: str = "user",
) -> dict[str, Any]:
    now = security.iso_now()
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (first_name, last_name, phone, password_hash, role, "
            "must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (first_name, last_name, phone, password_hash, role, now, now),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _user_from_row(row)


def update_user_profile(user_id: int, first_name: str, last_name: str, phone: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE users SET first_name = ?, last_name = ?, phone = ?, updated_at = ? WHERE id = ?",
            (first_name, last_name, phone, security.iso_now(), user_id),
        )


def update_user_password(
    user_id: int,
    password_hash: str,
    *,
    must_change: bool = False,
    keep_session_id: int | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
            (password_hash, 1 if must_change else 0, security.iso_now(), user_id),
        )
        if keep_session_id is None:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        else:
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id != ?", (user_id, keep_session_id)
            )


def set_user_role(user_id: int, role: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
            (role, security.iso_now(), user_id),
        )
        if role != "admin":
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def count_admins() -> int:
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()["count"]


def list_users_with_counts() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT u.*, COUNT(o.id) AS order_count
            FROM users u
            LEFT JOIN call_orders o ON o.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        ).fetchall()
        return [{**_user_from_row(row), "orderCount": row["order_count"]} for row in rows]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(user_id: int, token_hash: str, csrf_token: str, expires_at: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_hash, user_id, csrf_token, security.iso_now(), expires_at),
        )


def get_session_user(token_hash: str) -> dict[str, Any] | None:
    if not token_hash:
        return None
    with connection() as conn:
        row = conn.execute(
            """
            SELECT s.id AS session_id, s.csrf_token, s.expires_at, u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= security.iso_now():
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
            return None
        return {
            "sessionId": row["session_id"],
            "csrfToken": row["csrf_token"],
            "expiresAt": row["expires_at"],
            "user": _user_from_row(row),
        }


def delete_session(token_hash: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_user_sessions(user_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired_sessions(conn: sqlite3.Connection | None = None) -> int:
    """Remove expired sessions. Called at startup and opportunistically."""
    if conn is not None:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (security.iso_now(),))
        conn.commit()
        return cursor.rowcount or 0
    with connection() as own:
        cursor = own.execute("DELETE FROM sessions WHERE expires_at <= ?", (security.iso_now(),))
        return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def list_projects(include_unpublished: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM projects"
    if not include_unpublished:
        query += " WHERE is_published = 1"
    query += " ORDER BY sort_order ASC, id ASC"
    with connection() as conn:
        rows = conn.execute(query).fetchall()
        return [_project_from_row(row) for row in rows]


def get_project_by_slug(slug: str, include_unpublished: bool = False) -> dict[str, Any] | None:
    query = "SELECT * FROM projects WHERE slug = ?"
    if not include_unpublished:
        query += " AND is_published = 1"
    with connection() as conn:
        row = conn.execute(query, (slug,)).fetchone()
        return _project_from_row(row) if row else None


def get_project_by_id(project_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project_from_row(row) if row else None


def slug_exists(slug: str, exclude_id: int | None = None) -> bool:
    with connection() as conn:
        if exclude_id is None:
            row = conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE slug = ? AND id != ?", (slug, exclude_id)
            ).fetchone()
        return row is not None


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    now = security.iso_now()
    with connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO projects (
                slug, type, category_name, title, subtitle, short_description,
                full_description, client, year, duration, services_provided,
                technologies, image, banner_image, gallery, stats, challenge,
                solution, testimonial, sort_order, is_published, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["slug"],
                data["type"],
                data["categoryName"],
                data["title"],
                data["subtitle"],
                data["shortDescription"],
                data["fullDescription"],
                data["client"],
                data["year"],
                data["duration"],
                json.dumps(data.get("servicesProvided", []), ensure_ascii=False),
                json.dumps(data.get("technologies", []), ensure_ascii=False),
                data["image"],
                data["bannerImage"],
                json.dumps(data.get("gallery", []), ensure_ascii=False),
                json.dumps(data.get("stats", []), ensure_ascii=False),
                data["challenge"],
                data["solution"],
                data.get("testimonial", ""),
                int(data.get("sortOrder", 1000)),
                1 if data.get("isPublished", True) else 0,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _project_from_row(row)


def update_project(project_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE projects SET
                slug = ?, type = ?, category_name = ?, title = ?, subtitle = ?,
                short_description = ?, full_description = ?, client = ?, year = ?,
                duration = ?, services_provided = ?, technologies = ?, image = ?,
                banner_image = ?, gallery = ?, stats = ?, challenge = ?, solution = ?,
                testimonial = ?, sort_order = ?, is_published = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data["slug"],
                data["type"],
                data["categoryName"],
                data["title"],
                data["subtitle"],
                data["shortDescription"],
                data["fullDescription"],
                data["client"],
                data["year"],
                data["duration"],
                json.dumps(data.get("servicesProvided", []), ensure_ascii=False),
                json.dumps(data.get("technologies", []), ensure_ascii=False),
                data["image"],
                data["bannerImage"],
                json.dumps(data.get("gallery", []), ensure_ascii=False),
                json.dumps(data.get("stats", []), ensure_ascii=False),
                data["challenge"],
                data["solution"],
                data.get("testimonial", ""),
                int(data.get("sortOrder", 1000)),
                1 if data.get("isPublished", True) else 0,
                security.iso_now(),
                project_id,
            ),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project_from_row(row) if row else None


def delete_project(project_id: int) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0


def next_sort_order() -> int:
    with connection() as conn:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM projects").fetchone()
        return int(row["max_sort"]) + 10


# ---------------------------------------------------------------------------
# Call orders
# ---------------------------------------------------------------------------


def create_call_order(user_id: int, urgency: str, reason: str) -> dict[str, Any]:
    now = security.iso_now()
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO call_orders (user_id, urgency, reason, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (user_id, urgency, reason, now, now),
        )
        row = conn.execute(
            """
            SELECT o.*, u.first_name, u.last_name, u.phone
            FROM call_orders o JOIN users u ON u.id = o.user_id
            WHERE o.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return _order_from_row(row)


def _order_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "phone": row["phone"],
        "urgency": row["urgency"],
        "reason": row["reason"],
        "status": row["status"],
        "adminNote": row["admin_note"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_call_orders(status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT o.*, u.first_name, u.last_name, u.phone
        FROM call_orders o JOIN users u ON u.id = o.user_id
    """
    params: list[Any] = []
    if status in {"pending", "contacted", "closed"}:
        query += " WHERE o.status = ?"
        params.append(status)
    query += " ORDER BY o.created_at DESC, o.id DESC"
    if isinstance(limit, int) and 0 < limit <= 5000:
        query += " LIMIT ?"
        params.append(limit)
    with connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [_order_from_row(row) for row in rows]


def count_recent_orders(user_id: int, minutes: int) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM call_orders WHERE user_id = ? AND created_at >= ?",
            (user_id, security.iso_before(minutes)),
        ).fetchone()
        return int(row["count"])


def count_open_orders(user_id: int) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM call_orders WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
        return int(row["count"])


def update_call_order(order_id: int, status: str, admin_note: str) -> dict[str, Any] | None:
    with connection() as conn:
        conn.execute(
            "UPDATE call_orders SET status = ?, admin_note = ?, updated_at = ? WHERE id = ?",
            (status, admin_note, security.iso_now(), order_id),
        )
        row = conn.execute(
            """
            SELECT o.*, u.first_name, u.last_name, u.phone
            FROM call_orders o JOIN users u ON u.id = o.user_id
            WHERE o.id = ?
            """,
            (order_id,),
        ).fetchone()
        return _order_from_row(row) if row else None


# ---------------------------------------------------------------------------
# Admin statistics
# ---------------------------------------------------------------------------


def admin_stats() -> dict[str, int]:
    with connection() as conn:
        users = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        admins = conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()[
            "count"
        ]
        projects = conn.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]
        published = conn.execute(
            "SELECT COUNT(*) AS count FROM projects WHERE is_published = 1"
        ).fetchone()["count"]
        orders = conn.execute("SELECT COUNT(*) AS count FROM call_orders").fetchone()["count"]
        pending = conn.execute(
            "SELECT COUNT(*) AS count FROM call_orders WHERE status = 'pending'"
        ).fetchone()["count"]
        return {
            "users": users,
            "admins": admins,
            "projects": projects,
            "publishedProjects": published,
            "orders": orders,
            "pendingOrders": pending,
        }
