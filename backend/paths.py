"""
Storage layout for MK Graphy.

Everything that is private — the SQLite database with user accounts and call
orders, the password hashes, admin bootstrap credentials, uploaded images,
audit logs and optional API keys — lives **outside** the Git checkout, in a
per-user application-data directory. The repository only contains code and
public assets, so cloning or publishing it can never leak user data.

Resolution order for the data directory:

1. ``MK_DATA_DIR`` environment variable (absolute path recommended)
2. Platform default:
   - Windows: ``%LOCALAPPDATA%\\MKGraphy``
   - macOS:   ``~/Library/Application Support/MKGraphy``
   - Linux:   ``$XDG_DATA_HOME/mk-graphy`` or ``~/.local/share/mk-graphy``

Individual paths can still be overridden with ``MK_DB_PATH`` and
``MK_UPLOAD_DIR`` for advanced deployments (for example a systemd ``StateDirectory``).

The legacy in-repository location (``backend/data`` and ``backend/uploads``) is
detected and migrated automatically on first run, then removed from the
checkout so it cannot be committed by accident.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

#: Legacy location that used to hold the database and uploads inside the repo.
LEGACY_DATA_DIR = BACKEND_DIR / "data"
LEGACY_UPLOAD_DIR = BACKEND_DIR / "uploads"

APP_DIR_NAME = "MKGraphy"
POSIX_DIR_NAME = "mk-graphy"

MIGRATION_NOTE = "data-migration.txt"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return (Path(base) / APP_DIR_NAME).resolve()
        return (Path.home() / "AppData" / "Local" / APP_DIR_NAME).resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / APP_DIR_NAME).resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return (base / POSIX_DIR_NAME).resolve()


def data_dir() -> Path:
    return _env_path("MK_DATA_DIR") or default_data_dir()


def database_path() -> Path:
    return _env_path("MK_DB_PATH") or (data_dir() / "mk_graphy.db")


def upload_dir() -> Path:
    return _env_path("MK_UPLOAD_DIR") or (data_dir() / "uploads")


def log_dir() -> Path:
    return data_dir() / "logs"


def backup_dir() -> Path:
    return data_dir() / "backups"


def secrets_path() -> Path:
    """Optional ``.env`` file holding API keys and deployment secrets."""
    return _env_path("MK_SECRETS_FILE") or (data_dir() / ".env")


def bootstrap_credentials_path() -> Path:
    """One-time generated admin password. Delete it after the first login."""
    return data_dir() / "admin-credentials.txt"


def audit_log_path() -> Path:
    return log_dir() / "security.log"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def harden_dir(path: Path) -> None:
    """Restrict a directory to the current user (best effort)."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, stat.S_IRWXU)  # 700
    except OSError:
        pass


def harden_file(path: Path) -> None:
    """Restrict a file to the current user (best effort)."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass


def ensure_layout() -> Path:
    """Create and lock down every private directory. Returns the data dir."""
    directories = [data_dir(), upload_dir(), log_dir(), backup_dir()]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        harden_dir(directory)
    # The parent of an overridden DB path may live elsewhere.
    database_path().parent.mkdir(parents=True, exist_ok=True)
    return data_dir()


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


def _pending_legacy_files() -> list[Path]:
    candidates: list[Path] = []
    legacy_db = LEGACY_DATA_DIR / "mk_graphy.db"
    if legacy_db.is_file() and not database_path().exists():
        candidates.append(legacy_db)
        for suffix in ("-wal", "-shm"):
            sidecar = legacy_db.with_name(legacy_db.name + suffix)
            if sidecar.is_file():
                candidates.append(sidecar)
    return candidates


def legacy_uploads_present() -> bool:
    if not LEGACY_UPLOAD_DIR.is_dir():
        return False
    return any(child.is_file() for child in LEGACY_UPLOAD_DIR.rglob("*"))


def migrate_legacy_data() -> list[str]:
    """Move a pre-hardening database/uploads into the private data directory.

    Returns a list of human-readable notes for the console output. The legacy
    files are *moved*, never copied, so no copy of user data is left inside the
    repository.
    """
    notes: list[str] = []
    legacy_files = _pending_legacy_files()
    if legacy_files:
        target_db = database_path()
        target_db.parent.mkdir(parents=True, exist_ok=True)
        harden_dir(target_db.parent)
        for source in legacy_files:
            destination = target_db.with_name(target_db.name + source.name[len("mk_graphy.db") :])
            try:
                shutil.move(str(source), str(destination))
            except OSError as error:  # pragma: no cover - defensive
                notes.append(f"Legacy file could not be moved ({source}): {error}")
        if target_db.exists():
            harden_file(target_db)
            notes.append(f"Database moved out of the repository to: {target_db}")

    if legacy_uploads_present():
        target_uploads = upload_dir()
        target_uploads.mkdir(parents=True, exist_ok=True)
        moved = 0
        for source in sorted(LEGACY_UPLOAD_DIR.rglob("*")):
            if not source.is_file():
                continue
            destination = target_uploads / source.name
            if destination.exists():
                continue
            try:
                shutil.move(str(source), str(destination))
                moved += 1
            except OSError:  # pragma: no cover - defensive
                continue
        if moved:
            notes.append(f"Moved {moved} uploaded file(s) to: {target_uploads}")

    _cleanup_legacy_dirs(notes)
    return notes


def _cleanup_legacy_dirs(notes: list[str]) -> None:
    for directory in (LEGACY_DATA_DIR, LEGACY_UPLOAD_DIR):
        if not directory.exists():
            continue
        leftovers = [child for child in directory.rglob("*") if child.is_file()]
        if leftovers:
            notes.append(
                f"Legacy directory still contains {len(leftovers)} file(s) and was left in place: "
                f"{directory} — delete it manually and keep it out of Git."
            )
            continue
        # Sync clients (OneDrive/Dropbox) and Windows indexers can briefly lock
        # a folder, so try both removal strategies before giving up.
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            try:
                directory.rmdir()
            except OSError:
                notes.append(
                    f"Empty legacy directory could not be removed: {directory} — delete it manually."
                )
                continue
        notes.append(f"Removed empty legacy directory: {directory}")


def write_migration_note(notes: list[str]) -> None:
    if not notes:
        return
    try:
        target = data_dir() / MIGRATION_NOTE
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [f"[{stamp}]", *notes, ""]
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        harden_file(target)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# .env loading (optional, no third-party dependency)
# ---------------------------------------------------------------------------


def load_secrets_file() -> Path | None:
    """Load ``KEY=VALUE`` pairs from the private .env file into ``os.environ``.

    Existing environment variables always win, so a service manager can
    override anything. API keys and similar secrets belong in this file, never
    in the repository.
    """
    path = secrets_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
    harden_file(path)
    return path


def describe() -> list[tuple[str, str]]:
    """Rows for the CLI/console summary."""
    return [
        ("Data directory", str(data_dir())),
        ("Database", str(database_path())),
        ("Uploads", str(upload_dir())),
        ("Logs", str(log_dir())),
        ("Backups", str(backup_dir())),
        ("Secrets (.env)", str(secrets_path())),
    ]
