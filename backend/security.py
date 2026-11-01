"""
Security helpers for MK Graphy's standard-library backend.

Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a random salt.
Session tokens and CSRF tokens are generated with ``secrets`` and only token
hashes are persisted in SQLite. This module also owns the password policy, the
constant-time helpers and the append-only security audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paths

PBKDF2_ITERATIONS = 260_000
PBKDF2_DKLEN = 32
SESSION_DAYS = 7
TOKEN_MAX_LENGTH = 512

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_NAME_RE = re.compile(r"^[\u0600-\u06FF\u200c\u200dA-Za-z\s\-']{2,40}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Passwords that are publicly known (previously shipped as the project
#: default, or extremely common). They are rejected everywhere and any existing
#: account still using one is rotated automatically at startup.
COMPROMISED_PASSWORDS = frozenset(
    {
        "mkgraphy@1404",
        "mkgraphy1404",
        "mkgraphy@1405",
        "password",
        "password1",
        "password123",
        "passw0rd",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty123",
        "qwertyuiop",
        "admin123",
        "admin1234",
        "administrator",
        "letmein1",
        "welcome1",
        "welcome123",
        "iloveyou1",
        "abc12345",
        "test1234",
        "changeme1",
        "changeme123",
        "1qaz2wsx",
        "zaq12wsx",
        "asdf1234",
        "zxcvbnm1",
        "qazwsx123",
        "iran1234",
    }
)

#: Passwords that earlier versions of this project shipped in public docs. Any
#: account still using one of these is rotated automatically on startup.
PROJECT_DEFAULT_PASSWORDS = ("MkGraphy@1404", "MkGraphy1404", "MkGraphy@1405")

_COMMON_WORDS = (
    "password",
    "passwort",
    "qwerty",
    "azerty",
    "letmein",
    "welcome",
    "admin",
    "root",
    "login",
    "master",
    "monkey",
    "dragon",
    "football",
    "baseball",
    "sunshine",
    "princess",
    "iloveyou",
    "trustno",
    "changeme",
    "secret",
)

#: Lazily built PBKDF2 hash used to keep failed logins for unknown accounts as
#: slow as failed logins for real accounts (prevents user enumeration by timing).
_DUMMY_HASH_LOCK = threading.Lock()
_DUMMY_HASH: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def iso_after(days: int = SESSION_DAYS) -> str:
    return (utc_now() + timedelta(days=days)).isoformat(timespec="seconds")


def iso_before(minutes: int) -> str:
    return (utc_now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=PBKDF2_DKLEN
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations_int = int(iterations)
        if not (100_000 <= iterations_int <= 2_000_000):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations_int, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def dummy_verify(password: str) -> bool:
    """Burn the same CPU time as a real verification, always returning False."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        with _DUMMY_HASH_LOCK:
            if _DUMMY_HASH is None:
                _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    verify_password(password if isinstance(password, str) else "", _DUMMY_HASH)
    return False


def generate_password(length: int = 20) -> str:
    """Cryptographically strong password with mixed character classes."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    symbols = "!@#$%*-_=+?"
    while True:
        core = "".join(secrets.choice(alphabet) for _ in range(length - 4))
        tail = "".join(secrets.choice(symbols) for _ in range(2))
        digits = "".join(secrets.choice("23456789") for _ in range(2))
        candidate = core + tail + digits
        if password_problem(candidate) is None:
            return candidate


def password_problem(password: str) -> str | None:
    """Return a Persian error message, or ``None`` when the password is strong."""
    if not isinstance(password, str):
        return "رمز عبور معتبر نیست."
    if len(password) < 10:
        return "رمز عبور باید حداقل ۱۰ کاراکتر باشد."
    if len(password) > 128:
        return "رمز عبور بیش از حد طولانی است."
    if not re.search(r"[A-Za-z]", password):
        return "رمز عبور باید شامل حروف انگلیسی باشد."
    if not re.search(r"\d", password):
        return "رمز عبور باید شامل عدد باشد."
    if password.strip() != password:
        return "رمز عبور نباید با فاصله شروع یا تمام شود."
    lowered = password.lower()
    if lowered in COMPROMISED_PASSWORDS:
        return "این رمز عبور عمومی و لو رفته است. رمز دیگری انتخاب کنید."
    if any(word in lowered for word in _COMMON_WORDS) and len(set(lowered)) < 12:
        return "رمز عبور بیش از حد قابل حدس است. ترکیب پیچیده‌تری انتخاب کنید."
    if len(set(password)) <= 3:
        return "رمز عبور باید تنوع کاراکتری بیشتری داشته باشد."
    if re.fullmatch(r"[\dA-Za-z]+", password) and not re.search(r"[A-Z]", password):
        return "رمز عبور باید حداقل یک حرف بزرگ داشته باشد."
    return None


def validate_password(
    password: str,
    *,
    phone: str | None = None,
    names: tuple[str, ...] = (),
) -> str | None:
    """Return a Persian error message, or ``None`` when the password is acceptable."""
    problem = password_problem(password)
    if problem:
        return problem
    lowered = password.lower()
    compact_phone = (phone or "").lstrip("0")
    if compact_phone and len(compact_phone) >= 6 and compact_phone in re.sub(r"\D", "", password):
        return "رمز عبور نباید شامل شماره موبایل باشد."
    for name in names:
        candidate = (name or "").strip().lower()
        if len(candidate) >= 3 and candidate in lowered:
            return "رمز عبور نباید شامل نام یا نام خانوادگی باشد."
    return None


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def new_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(provided: object, expected: object) -> bool:
    """Constant-time comparison that never raises on odd input."""
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    if not provided or not expected:
        return False
    if len(provided) > TOKEN_MAX_LENGTH or len(expected) > TOKEN_MAX_LENGTH:
        return False
    try:
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def normalize_phone(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    for index, digit in enumerate(PERSIAN_DIGITS):
        value = value.replace(digit, str(index))
    for index, digit in enumerate(ARABIC_DIGITS):
        value = value.replace(digit, str(index))
    value = re.sub(r"[\s\-().]", "", value)
    if value.startswith("+98"):
        value = "0" + value[3:]
    elif value.startswith("0098"):
        value = "0" + value[4:]
    elif value.startswith("98") and len(value) == 12:
        value = "0" + value[2:]
    if not re.fullmatch(r"09\d{9}", value or ""):
        return None
    return value


def validate_name(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not _NAME_RE.fullmatch(value):
        return None
    return value


def validate_slug(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not (3 <= len(value) <= 90) or not _SLUG_RE.fullmatch(value):
        return None
    return value


def safe_next_path(raw: str | None, default: str = "/") -> str:
    if not raw or not isinstance(raw, str):
        return default
    if not raw.startswith("/") or raw.startswith("//"):
        return default
    if any(character in raw for character in ("\n", "\r", "\t", "\\")):
        return default
    return raw


def safe_location(value: str) -> str:
    """Sanitise a ``Location`` header value against response splitting."""
    cleaned = value.replace("\r", "").replace("\n", "").replace("\t", "")
    if not cleaned.startswith("/") or cleaned.startswith("//"):
        return "/"
    return cleaned


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_AUDIT_LOCK = threading.Lock()
_AUDIT_MAX_BYTES = 5 * 1024 * 1024
_SENSITIVE_KEYS = {
    "password",
    "currentPassword",
    "newPassword",
    "passwordConfirm",
    "newPasswordConfirm",
    "token",
    "csrfToken",
    "password_hash",
}


def audit(event: str, **fields: object) -> None:
    """Append one JSON line to the private security log (never raises).

    Passwords and tokens must never be passed in ``fields``; the helper also
    strips well-known key names as a second line of defence.
    """
    payload: dict[str, object] = {"ts": iso_now(), "event": event}
    for key, value in fields.items():
        if key in _SENSITIVE_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)
    line = json.dumps(payload, ensure_ascii=False)
    try:
        log_path: Path = paths.audit_log_path()
        with _AUDIT_LOCK:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            paths.harden_dir(log_path.parent)
            if log_path.is_file() and log_path.stat().st_size > _AUDIT_MAX_BYTES:
                backup = log_path.with_suffix(".log.1")
                try:
                    if backup.exists():
                        backup.unlink()
                    log_path.replace(backup)
                except OSError:
                    pass
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            paths.harden_file(log_path)
    except OSError:
        pass


def write_private_file(path: Path, content: str) -> None:
    """Write a file readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.harden_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)
    paths.harden_file(path)
