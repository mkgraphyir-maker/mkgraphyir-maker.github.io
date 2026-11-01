#!/usr/bin/env python3
"""
MK Graphy — full Python backend (standard library only).

Run locally:
    python backend/app.py --host 127.0.0.1 --port 8000

The application serves the static frontend from the project root and exposes a
JSON API for accounts, call orders, projects and the admin panel. All private
data (SQLite database, uploads, logs, secrets) lives outside the repository in
the per-user data directory resolved by ``paths.py``. No third-party Python
package is required.

Security model (see README §12):
  * admin routes are denied by default and require role == "admin" plus CSRF
  * every state-changing request must be JSON, same-origin and carry a CSRF token
  * request bodies are always consumed, otherwise the connection is closed
  * connections, request sizes and login attempts are rate limited
"""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
from datetime import timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import database
import paths
import security

ROOT = paths.PROJECT_ROOT
BACKEND_DIR = paths.BACKEND_DIR
TEMPLATE_DIR = BACKEND_DIR / "templates"

MAX_JSON_BYTES = 512 * 1024
MAX_PROJECT_BYTES = 40 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
SESSION_COOKIE = "mk_session"
REQUEST_TIMEOUT_SECONDS = 25
MAX_CONCURRENT_CONNECTIONS = 128

#: Extensions that may ever be served from a static prefix. Anything else —
#: source maps, backups, dotfiles, ``.py`` or ``.db`` files — is a 404.
STATIC_EXTENSIONS = {
    "/css/": {".css"},
    "/js/": {".js"},
    "/assets/": {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".json", ".woff", ".woff2", ".ttf"},
    "/uploads/": {".png", ".jpg", ".jpeg", ".webp"},
}

MIME_OVERRIDES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".json": "application/json; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}

DATA_URL_RE = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,(.+)$", re.S | re.I)

URGENCY_VALUES = {"immediate", "today", "this_week", "normal"}
ORDER_STATUS_VALUES = {"pending", "contacted", "closed"}
PROJECT_TYPES = {"web", "video"}

ROBOTS_TXT = "User-agent: *\nDisallow: /admin\nDisallow: /api/\nAllow: /\n"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class APIError(Exception):
    def __init__(self, status: int, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "firstName": user["firstName"],
        "lastName": user["lastName"],
        "phone": user["phone"],
        "role": user["role"],
        "mustChangePassword": bool(user.get("mustChangePassword")),
        "createdAt": user.get("createdAt"),
    }


def _mask_phone(phone: str | None) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:3]}***{phone[-2:]}"


def _is_valid_text(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value.strip()) <= maximum


def _clean_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_list(value: object, maximum_items: int, maximum_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value[:maximum_items]:
        if isinstance(item, str) and item.strip():
            output.append(item.strip()[:maximum_length])
    return output


def _clean_stats(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value[:6]:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        stat_value = _clean_text(item.get("value"))
        if label and stat_value:
            output.append({"label": label[:120], "value": stat_value[:120]})
    return output


class RateLimiter:
    """Sliding-window limiter with bounded memory."""

    def __init__(self, limit: int, window_seconds: int, max_keys: int = 5000):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        if len(self._hits) < self.max_keys:
            return
        for key in list(self._hits):
            stamps = [stamp for stamp in self._hits[key] if now - stamp < self.window_seconds]
            if stamps:
                self._hits[key] = stamps
            else:
                del self._hits[key]

    def check(self, key: str) -> int:
        """Return remaining seconds when over budget, else 0."""
        now = time.monotonic()
        with self._lock:
            stamps = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window_seconds]
            if stamps:
                self._hits[key] = stamps
            else:
                self._hits.pop(key, None)
            if len(stamps) >= self.limit:
                oldest = min(stamps)
                return max(1, int(self.window_seconds - (now - oldest)))
        return 0

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._hits.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# Per-IP / per-account budgets.
GLOBAL_API_LIMITER = RateLimiter(limit=300, window_seconds=60)
LOGIN_IP_LIMITER = RateLimiter(limit=25, window_seconds=15 * 60)
LOGIN_ACCOUNT_LIMITER = RateLimiter(limit=6, window_seconds=15 * 60)
SIGNUP_IP_LIMITER = RateLimiter(limit=10, window_seconds=60 * 60)
ORDER_USER_LIMITER = RateLimiter(limit=6, window_seconds=60 * 60)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "MKGraphy"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_SECONDS

    _auth_cache: dict | None = None
    _json_body: dict | None = None
    _unread_body = False

    # ------------------------------------------------------------------
    # Request entry points
    # ------------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self._client_ip(), fmt % args))

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _client_ip(self) -> str:
        if _env_flag("MK_TRUST_PROXY"):
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                candidate = forwarded.split(",")[0].strip()
                if candidate:
                    return candidate[:45]
        return (self.client_address[0] if self.client_address else "?")[:45]

    def _is_https(self) -> bool:
        if _env_flag("MK_HTTPS"):
            return True
        if _env_flag("MK_TRUST_PROXY"):
            return self.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"
        return False

    def _cookie_secure(self) -> bool:
        return _env_flag("MK_COOKIE_SECURE") or self._is_https()

    def _security_headers(self, noindex: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=()",
        )
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("X-DNS-Prefetch-Control", "off")
        policy = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "media-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'; manifest-src 'self'"
        )
        if self._is_https():
            policy += "; upgrade-insecure-requests"
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Content-Security-Policy", policy)
        if noindex:
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")

    def _body_expected(self) -> bool:
        if self.headers.get("Transfer-Encoding"):
            return True
        value = self.headers.get("Content-Length")
        if not value:
            return False
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return True

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
        cookies: list[str] | None = None,
        cache_control: str = "no-store",
        noindex: bool = True,
    ) -> None:
        # A declared but unread request body must never be parsed as the next
        # request on the same connection (HTTP request smuggling / desync).
        if self._unread_body:
            self.close_connection = True

        route = getattr(self, "route", "")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self._security_headers(noindex=noindex or route.startswith("/admin"))
        if self.close_connection:
            self.send_header("Connection", "close")
        if headers:
            for key, value in headers.items():
                if key.lower() == "location":
                    value = security.safe_location(value)
                self.send_header(key, value)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def _json(
        self,
        status: int,
        payload: object,
        cookies: list[str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        self._send(status, _json_bytes(payload), cookies=cookies, headers=extra)

    def _html(self, status: int, markup: str, headers: dict[str, str] | None = None) -> None:
        self._send(status, markup.encode("utf-8"), content_type="text/html; charset=utf-8", headers=headers)

    def _redirect(self, location: str, status: int = 302) -> None:
        self._send(status, b"", headers={"Location": location}, cache_control="no-store")

    def _not_found(self) -> None:
        if getattr(self, "route", "").startswith("/api/"):
            self._json(404, {"error": "مسیر مورد نظر پیدا نشد."})
        else:
            self._html(404, self._error_page("صفحه پیدا نشد", "صفحه‌ای که به دنبال آن هستید وجود ندارد."))

    def _forbidden_page(self, message: str = "دسترسی به این بخش فقط برای مدیران مجاز است.") -> None:
        self._html(
            403,
            self._error_page("دسترسی غیرمجاز", message),
            headers={"X-Robots-Tag": "noindex, nofollow, noarchive"},
        )

    def _error_page(self, title: str, message: str) -> str:
        safe_title = html.escape(title, quote=True)
        safe_message = html.escape(message, quote=True)
        return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{safe_title} | MK Graphy</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7fafc;
font-family:'Vazirmatn','Segoe UI',Tahoma,sans-serif;color:#0b1b33;padding:1.5rem}}
.card{{background:#fff;border:1px solid #e7edf4;border-radius:20px;padding:2.5rem;
max-width:34rem;width:100%;box-shadow:0 18px 45px -22px rgba(15,36,64,.24);text-align:center}}
h1{{font-size:1.6rem;margin:0 0 .75rem}}p{{color:#5b6f87;line-height:1.9}}
a{{display:inline-block;margin-top:1.25rem;background:#2563eb;color:#fff;text-decoration:none;
padding:.7rem 1.25rem;border-radius:12px;font-weight:700}}
</style></head><body><div class="card"><h1>{safe_title}</h1><p>{safe_message}</p>
<a href="/">بازگشت به صفحه اصلی</a></div></body></html>"""

    # ------------------------------------------------------------------
    # Dispatch and error handling
    # ------------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        self._auth_cache = None
        self._json_body = None
        self._unread_body = self._body_expected()

        parsed = urlparse(self.path)
        raw_path = parsed.path or "/"
        try:
            self.route = unquote(raw_path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            self.route = raw_path
        self.query = parse_qs(parsed.query, keep_blank_values=True)

        try:
            if "\x00" in self.route or any(ord(character) < 0x20 for character in self.route):
                raise APIError(400, "مسیر درخواست معتبر نیست.")
            if ".." in self.route.split("/"):
                raise APIError(404, "مسیر مورد نظر پیدا نشد.")
            if not self._host_allowed():
                return self._json(421, {"error": "میزبان درخواست مجاز نیست."})
            self.cookies = self._parse_cookies()

            if method == "OPTIONS":
                return self._send(
                    204,
                    b"",
                    headers={"Allow": "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"},
                )

            if self.route.startswith("/api/"):
                self._handle_api(method)
            elif method in {"GET", "HEAD"}:
                self._handle_get()
            else:
                raise APIError(405, "درخواست مجاز نیست.")
        except APIError as error:
            self._handle_api_error(error)
        except sqlite3.IntegrityError:
            traceback.print_exc()
            security.audit("db.integrity_error", route=getattr(self, "route", "?"))
            if getattr(self, "route", "").startswith("/api/"):
                self._json(409, {"error": "این اطلاعات قبلاً در سیستم ثبت شده است."})
            else:
                self._html(409, self._error_page("خطا", "این اطلاعات قبلاً در سیستم ثبت شده است."))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            security.audit("server.error", route=getattr(self, "route", "?"))
            if getattr(self, "route", "").startswith("/api/"):
                self._json(500, {"error": "خطای داخلی سرور. لطفاً دوباره تلاش کنید."})
            else:
                self._html(500, self._error_page("خطای داخلی", "مشکلی در پردازش درخواست رخ داد."))

    def _handle_api_error(self, error: APIError) -> None:
        route = getattr(self, "route", "")
        if route.startswith("/api/"):
            payload: dict[str, object] = {"error": error.message}
            if error.field:
                payload["field"] = error.field
            extra = {"Retry-After": "60"} if error.status == 429 else None
            self._json(error.status, payload, extra=extra)
        else:
            self._html(error.status, self._error_page("خطا", error.message))

    def _host_allowed(self) -> bool:
        allowed = [
            item.strip().lower()
            for item in os.environ.get("MK_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        ]
        if not allowed:
            return True
        host = self.headers.get("Host", "").strip().lower()
        return host in allowed

    def _parse_cookies(self) -> dict[str, str]:
        header = self.headers.get("Cookie", "")
        if not header or len(header) > 8192:
            return {}
        jar = SimpleCookie()
        try:
            jar.load(header)
        except Exception:
            return {}
        return {morsel.key: morsel.value for morsel in jar.values()}

    # ------------------------------------------------------------------
    # Auth / session helpers
    # ------------------------------------------------------------------

    def _auth(self) -> dict | None:
        if self._auth_cache is not None:
            return self._auth_cache
        token = self.cookies.get(SESSION_COOKIE)
        if not token or len(token) > 256:
            self._auth_cache = None
            return None
        try:
            self._auth_cache = database.get_session_user(security.hash_token(token))
        except Exception:
            traceback.print_exc()
            self._auth_cache = None
        return self._auth_cache

    def _require_user(self) -> dict:
        auth = self._auth()
        if not auth:
            raise APIError(401, "برای انجام این کار ابتدا وارد حساب کاربری خود شوید.")
        return auth

    def _require_admin(self) -> dict:
        auth = self._require_user()
        if auth["user"]["role"] != "admin":
            security.audit(
                "admin.access.denied",
                ip=self._client_ip(),
                user_id=auth["user"]["id"],
                route=self.route,
            )
            raise APIError(403, "دسترسی به پنل مدیریت مجاز نیست.")
        return auth

    def _verify_csrf(self, auth: dict) -> None:
        provided = self.headers.get("X-CSRF-Token", "")
        if not security.tokens_match(provided, auth["csrfToken"]):
            security.audit(
                "csrf.failed", ip=self._client_ip(), route=self.route, user_id=auth["user"]["id"]
            )
            raise APIError(403, "درخواست امنیتی معتبر نیست. صفحه را دوباره بارگذاری کنید.")

    def _verify_origin(self) -> None:
        """Reject cross-site state-changing requests (defence in depth over CSRF)."""
        host = self.headers.get("Host", "").strip().lower()
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if not parsed.netloc or parsed.netloc.lower() != host:
                security.audit(
                    "origin.blocked", ip=self._client_ip(), route=self.route, origin=origin[:120]
                )
                raise APIError(403, "منبع درخواست مجاز نیست.")
            return
        referer = self.headers.get("Referer")
        if referer:
            parsed = urlparse(referer)
            if parsed.netloc and parsed.netloc.lower() != host:
                security.audit("referer.blocked", ip=self._client_ip(), route=self.route)
                raise APIError(403, "منبع درخواست مجاز نیست.")

    def _session_cookie(self, token: str) -> str:
        parts = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={int(timedelta(days=security.SESSION_DAYS).total_seconds())}",
        ]
        if self._cookie_secure():
            parts.append("Secure")
        return "; ".join(parts)

    def _clear_cookie(self) -> str:
        parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self._cookie_secure():
            parts.append("Secure")
        return "; ".join(parts)

    def _start_session(self, user_id: int) -> tuple[str, str]:
        token = security.new_token()
        csrf = security.new_token(24)
        database.create_session(
            user_id,
            security.hash_token(token),
            csrf,
            security.iso_after(security.SESSION_DAYS),
        )
        return token, csrf

    def _session_payload(self, auth: dict | None) -> dict:
        if not auth:
            return {"user": None, "csrfToken": None, "isAdmin": False}
        return {
            "user": _public_user(auth["user"]),
            "csrfToken": auth["csrfToken"],
            "isAdmin": auth["user"]["role"] == "admin",
        }

    # ------------------------------------------------------------------
    # GET routing: pages and static assets
    # ------------------------------------------------------------------

    def _handle_get(self) -> None:
        route = self.route

        if route == "/":
            return self._serve_page("index.html")
        if route in {"/index.html", "/contact.html", "/project.html"}:
            return self._serve_page(route.lstrip("/"))
        if route == "/contact":
            return self._redirect("/contact.html", 301)
        if route == "/robots.txt":
            return self._send(
                200,
                ROBOTS_TXT.encode("utf-8"),
                content_type="text/plain; charset=utf-8",
                cache_control="public, max-age=3600",
                noindex=False,
            )

        if route in {"/admin", "/admin/"}:
            auth = self._auth()
            if auth and auth["user"]["role"] == "admin":
                return self._serve_template("admin.html")
            if auth:
                security.audit("admin.page.denied", ip=self._client_ip(), user_id=auth["user"]["id"])
                return self._forbidden_page()
            return self._redirect("/admin/login")
        if route in {"/admin/login", "/admin/login/"}:
            auth = self._auth()
            if auth and auth["user"]["role"] == "admin":
                return self._redirect("/admin")
            if auth:
                return self._forbidden_page(
                    "این حساب کاربری دسترسی مدیریت ندارد. برای ورود به پنل مدیریت از حساب مدیر استفاده کنید."
                )
            return self._serve_template("admin_login.html")

        if route == "/favicon.ico":
            return self._redirect("/assets/favicon.png", 302)

        for prefix, extensions in STATIC_EXTENSIONS.items():
            if route.startswith(prefix):
                if prefix == "/uploads/":
                    base_dir = paths.upload_dir()
                else:
                    base_dir = ROOT / prefix.strip("/")
                return self._serve_static(base_dir, route[len(prefix) :], extensions)

        return self._not_found()

    def _serve_page(self, filename: str) -> None:
        path = (ROOT / filename).resolve()
        try:
            markup = path.read_text(encoding="utf-8")
        except OSError:
            return self._not_found()
        # Files opened directly (or served by a plain static server) do not have
        # this marker. It tells the frontend that API/auth features are
        # available and that it must not use the static preview fallback.
        if 'name="mk-backend"' not in markup:
            markup = markup.replace(
                "</head>",
                '    <meta name="mk-backend" content="1" />\n  </head>',
                1,
            )
        self._send(
            200,
            markup.encode("utf-8"),
            content_type="text/html; charset=utf-8",
            cache_control="no-store",
        )

    def _serve_template(self, name: str) -> None:
        if name not in {"admin.html", "admin_login.html"}:
            return self._not_found()
        template_root = TEMPLATE_DIR.resolve()
        path = (template_root / name).resolve()
        if not path.is_file() or not path.is_relative_to(template_root):
            return self._not_found()
        try:
            markup = path.read_text(encoding="utf-8")
        except OSError:
            return self._not_found()
        self._html(
            200,
            markup,
            headers={
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "Referrer-Policy": "no-referrer",
            },
        )

    def _serve_static(self, base_dir: Path, relative: str, allowed_extensions: set[str]) -> None:
        if not relative or relative.startswith("/") or "\\" in relative:
            return self._not_found()
        if any(part.startswith(".") for part in relative.split("/")):
            return self._not_found()
        base = base_dir.resolve()
        try:
            target = (base / relative).resolve()
        except (OSError, ValueError):
            return self._not_found()
        if not target.is_relative_to(base) or not target.is_file():
            return self._not_found()
        suffix = target.suffix.lower()
        if suffix not in allowed_extensions:
            return self._not_found()
        cache = (
            "public, max-age=31536000, immutable"
            if suffix in {".webp", ".jpg", ".jpeg", ".png", ".svg", ".woff", ".woff2", ".ttf"}
            else "no-cache"
        )
        self._serve_file(target, cache_control=cache)

    def _serve_file(self, path: Path, cache_control: str = "no-cache") -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._not_found()
        suffix = path.suffix.lower()
        content_type = (
            MIME_OVERRIDES.get(suffix)
            or mimetypes.guess_type(str(path))[0]
            or "application/octet-stream"
        )
        if content_type.startswith("text/") and "charset=" not in content_type:
            content_type += "; charset=utf-8"
        if content_type == "application/javascript":
            content_type = "text/javascript; charset=utf-8"
        self._send(200, data, content_type=content_type, cache_control=cache_control, noindex=False)

    # ------------------------------------------------------------------
    # API routing
    # ------------------------------------------------------------------

    def _handle_api(self, method: str) -> None:
        route = self.route.rstrip("/") if self.route != "/" else self.route

        if GLOBAL_API_LIMITER.check(f"api:{self._client_ip()}"):
            raise APIError(429, "تعداد درخواست‌ها زیاد است. لطفاً کمی بعد دوباره تلاش کنید.")
        GLOBAL_API_LIMITER.hit(f"api:{self._client_ip()}")

        if route == "/api/health" and method in {"GET", "HEAD"}:
            return self._json(200, {"ok": True, "service": "MK Graphy"})

        if route == "/api/session" and method in {"GET", "HEAD"}:
            return self._json(200, self._session_payload(self._auth()))

        if route.startswith("/api/admin"):
            # Default-deny: every admin route needs an admin session and, for
            # writes, a valid CSRF token. Auth is checked before the body is
            # read so an anonymous 40 MB upload is never buffered.
            return self._handle_admin_api(method, route)

        if route == "/api/auth/signup" and method == "POST":
            return self._api_signup()
        if route == "/api/auth/login" and method == "POST":
            return self._api_login()
        if route == "/api/auth/logout" and method == "POST":
            return self._api_logout()
        if route == "/api/call-orders" and method == "POST":
            return self._api_create_call_order()

        if route == "/api/projects" and method in {"GET", "HEAD"}:
            projects = database.list_projects(include_unpublished=False)
            return self._json(200, {"projects": projects})

        match = re.fullmatch(r"/api/projects/([^/]+)", route)
        if match and method in {"GET", "HEAD"}:
            project = database.get_project_by_slug(match.group(1), include_unpublished=False)
            if not project:
                raise APIError(404, "پروژه مورد نظر پیدا نشد.")
            return self._json(200, {"project": project})

        raise APIError(404, "مسیر API پیدا نشد.")

    def _handle_admin_api(self, method: str, route: str) -> None:
        auth = self._require_admin()
        if method not in {"GET", "HEAD"}:
            self._verify_origin()
            body = self._body()
            self._verify_csrf(auth)
        else:
            body = {}

        if route == "/api/admin/overview" and method in {"GET", "HEAD"}:
            stats = database.admin_stats()
            recent_orders = database.list_call_orders(limit=5)
            return self._json(
                200,
                {
                    "stats": stats,
                    "recentOrders": recent_orders,
                    "admin": _public_user(auth["user"]),
                    "security": {
                        "mustChangePassword": bool(auth["user"].get("mustChangePassword")),
                    },
                },
            )

        if route == "/api/admin/projects":
            if method in {"GET", "HEAD"}:
                return self._json(200, {"projects": database.list_projects(include_unpublished=True)})
            if method == "POST":
                data = self._project_payload(None, body)
                project = database.create_project(data)
                security.audit(
                    "admin.project.create", user_id=auth["user"]["id"], project_id=project["id"]
                )
                return self._json(201, {"project": project, "message": "نمونه‌کار با موفقیت ایجاد شد."})
            raise APIError(405, "متد درخواست مجاز نیست.")

        match = re.fullmatch(r"/api/admin/projects/(\d+)", route)
        if match:
            project_id = int(match.group(1))
            if method in {"GET", "HEAD"}:
                project = database.get_project_by_id(project_id)
                if not project:
                    raise APIError(404, "پروژه مورد نظر پیدا نشد.")
                return self._json(200, {"project": project})
            if method in {"PUT", "PATCH"}:
                existing = database.get_project_by_id(project_id)
                if not existing:
                    raise APIError(404, "پروژه مورد نظر پیدا نشد.")
                data = self._project_payload(existing, body)
                project = database.update_project(project_id, data)
                security.audit(
                    "admin.project.update", user_id=auth["user"]["id"], project_id=project_id
                )
                return self._json(
                    200, {"project": project, "message": "نمونه‌کار با موفقیت به‌روزرسانی شد."}
                )
            if method == "DELETE":
                if not database.delete_project(project_id):
                    raise APIError(404, "پروژه مورد نظر پیدا نشد.")
                security.audit(
                    "admin.project.delete", user_id=auth["user"]["id"], project_id=project_id
                )
                return self._json(200, {"message": "نمونه‌کار حذف شد."})
            raise APIError(405, "متد درخواست مجاز نیست.")

        if route == "/api/admin/orders" and method in {"GET", "HEAD"}:
            status = (self.query.get("status") or [None])[0]
            return self._json(200, {"orders": database.list_call_orders(status)})

        match = re.fullmatch(r"/api/admin/orders/(\d+)", route)
        if match and method in {"PATCH", "PUT"}:
            order_id = int(match.group(1))
            status = body.get("status")
            note = body.get("adminNote", "")
            if status not in ORDER_STATUS_VALUES:
                raise APIError(400, "وضعیت سفارش معتبر نیست.", "status")
            if not isinstance(note, str) or len(note) > 1000:
                raise APIError(400, "یادداشت مدیر بیش از حد طولانی است.", "adminNote")
            order = database.update_call_order(order_id, status, note.strip())
            if not order:
                raise APIError(404, "سفارش مورد نظر پیدا نشد.")
            security.audit(
                "admin.order.update", user_id=auth["user"]["id"], order_id=order_id, status=status
            )
            return self._json(200, {"order": order, "message": "وضعیت سفارش به‌روزرسانی شد."})

        if route == "/api/admin/users" and method in {"GET", "HEAD"}:
            return self._json(200, {"users": database.list_users_with_counts()})

        match = re.fullmatch(r"/api/admin/users/(\d+)", route)
        if match and method in {"PATCH", "PUT"}:
            user_id = int(match.group(1))
            role = body.get("role")
            if role not in {"user", "admin"}:
                raise APIError(400, "نقش کاربر معتبر نیست.", "role")
            target = database.get_user_by_id(user_id)
            if not target:
                raise APIError(404, "کاربر مورد نظر پیدا نشد.")
            if target["id"] == auth["user"]["id"] and role != "admin":
                raise APIError(400, "نمی‌توانید نقش مدیریتی خودتان را حذف کنید.")
            if target["role"] == "admin" and role == "user" and database.count_admins() <= 1:
                raise APIError(400, "حداقل یک مدیر باید در سیستم باقی بماند.")
            database.set_user_role(user_id, role)
            security.audit(
                "admin.user.role", user_id=auth["user"]["id"], target_user=user_id, role=role
            )
            return self._json(
                200, {"user": database.get_user_by_id(user_id), "message": "نقش کاربر تغییر کرد."}
            )

        if route == "/api/admin/profile":
            if method in {"GET", "HEAD"}:
                return self._json(200, {"user": _public_user(auth["user"])})
            if method in {"PUT", "PATCH"}:
                return self._api_update_admin_profile(auth, body)
            raise APIError(405, "متد درخواست مجاز نیست.")

        raise APIError(404, "مسیر API پیدا نشد.")

    # ------------------------------------------------------------------
    # JSON body + validation helpers
    # ------------------------------------------------------------------

    def _max_body_bytes(self) -> int:
        route = self.route.rstrip("/")
        if route == "/api/admin/projects" or re.fullmatch(r"/api/admin/projects/\d+", route):
            return MAX_PROJECT_BYTES
        return MAX_JSON_BYTES

    def _body(self) -> dict:
        if self._json_body is not None:
            return self._json_body
        self._json_body = self._read_json_body()
        return self._json_body

    def _read_json_body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "کدگذاری انتقال درخواست پشتیبانی نمی‌شود.")

        lengths = self.headers.get_all("Content-Length") or []
        unique = {value.strip() for value in lengths}
        if len(unique) > 1:
            raise APIError(400, "طول محتوای درخواست معتبر نیست.")

        raw_length = unique.pop() if unique else ""
        if not raw_length:
            self._unread_body = False
            return {}
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise APIError(400, "درخواست نامعتبر است.")
        if length < 0:
            raise APIError(400, "درخواست نامعتبر است.")
        if length == 0:
            self._unread_body = False
            return {}

        if length > self._max_body_bytes():
            raise APIError(413, "حجم درخواست بیش از حد مجاز است.")

        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise APIError(415, "نوع محتوای درخواست باید JSON باشد.")

        try:
            raw = self.rfile.read(length)
        except (TimeoutError, OSError):
            raise APIError(400, "خواندن درخواست ناموفق بود.")
        self._unread_body = False
        if len(raw) != length:
            self.close_connection = True
            raise APIError(400, "درخواست ناقص دریافت شد.")

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError(400, "ساختار JSON درخواست معتبر نیست.")
        if not isinstance(data, dict):
            raise APIError(400, "ساختار درخواست معتبر نیست.")
        if len(data) > 80:
            raise APIError(400, "تعداد فیلدهای درخواست بیش از حد مجاز است.")
        return data

    def _api_signup(self):
        self._verify_origin()
        data = self._body()
        ip = self._client_ip()
        if SIGNUP_IP_LIMITER.check(f"signup:{ip}"):
            raise APIError(
                429, "تعداد ثبت‌نام‌های مجاز از این دستگاه زیاد است. بعداً تلاش کنید."
            )

        first_name = security.validate_name(data.get("firstName"))
        last_name = security.validate_name(data.get("lastName"))
        phone = security.normalize_phone(data.get("phone") or "")
        password = data.get("password") or ""
        confirm = data.get("passwordConfirm") or ""

        if not first_name:
            raise APIError(400, "نام باید بین ۲ تا ۴۰ کاراکتر و معتبر باشد.", "firstName")
        if not last_name:
            raise APIError(400, "نام خانوادگی باید بین ۲ تا ۴۰ کاراکتر و معتبر باشد.", "lastName")
        if not phone:
            raise APIError(400, "شماره موبایل را به‌صورت ۰۹xxxxxxxxx وارد کنید.", "phone")
        password_error = security.validate_password(password, phone=phone, names=(first_name, last_name))
        if password_error:
            raise APIError(400, password_error, "password")
        if password != confirm:
            raise APIError(400, "تکرار رمز عبور با رمز عبور یکسان نیست.", "passwordConfirm")
        if database.get_user_by_phone(phone):
            raise APIError(409, "این شماره موبایل قبلاً ثبت‌نام کرده است.", "phone")

        SIGNUP_IP_LIMITER.hit(f"signup:{ip}")
        try:
            user = database.create_user(first_name, last_name, phone, security.hash_password(password))
        except sqlite3.IntegrityError:
            raise APIError(409, "این شماره موبایل قبلاً ثبت‌نام کرده است.", "phone")

        security.audit("auth.signup", user_id=user["id"], ip=ip)
        token, csrf = self._start_session(user["id"])
        self._json(
            201,
            {
                "user": _public_user(user),
                "csrfToken": csrf,
                "message": "حساب کاربری با موفقیت ساخته شد.",
            },
            cookies=[self._session_cookie(token)],
        )

    def _api_login(self):
        ip = self._client_ip()
        self._verify_origin()

        if LOGIN_IP_LIMITER.check(f"login:{ip}"):
            security.audit("auth.login.blocked", ip=ip, scope="ip")
            raise APIError(
                429, "تعداد تلاش‌های ورود زیاد است. لطفاً چند دقیقه بعد دوباره تلاش کنید."
            )

        data = self._body()
        phone = security.normalize_phone(data.get("phone") or "")
        password = data.get("password") or ""
        if not phone or not isinstance(password, str) or not password or len(password) > 128:
            LOGIN_IP_LIMITER.hit(f"login:{ip}")
            raise APIError(400, "شماره موبایل و رمز عبور را وارد کنید.", "phone")

        account_key = f"acct:{phone}"
        if LOGIN_ACCOUNT_LIMITER.check(account_key):
            security.audit("auth.login.blocked", ip=ip, phone=_mask_phone(phone), scope="account")
            raise APIError(
                429, "تعداد تلاش‌های ناموفق زیاد است. لطفاً چند دقیقه بعد دوباره تلاش کنید."
            )

        row = database.get_user_with_hash(phone)
        if row is None:
            verified = security.dummy_verify(password)
        else:
            verified = security.verify_password(password, row["password_hash"])

        if not verified:
            LOGIN_IP_LIMITER.hit(f"login:{ip}")
            LOGIN_ACCOUNT_LIMITER.hit(account_key)
            security.audit("auth.login.failed", ip=ip, phone=_mask_phone(phone))
            raise APIError(401, "شماره موبایل یا رمز عبور اشتباه است.")

        LOGIN_ACCOUNT_LIMITER.reset(account_key)
        user = database.get_user_by_id(row["id"])
        database.purge_expired_sessions()
        token, csrf = self._start_session(user["id"])
        security.audit("auth.login.success", user_id=user["id"], ip=ip, role=user["role"])
        self._json(
            200,
            {
                "user": _public_user(user),
                "csrfToken": csrf,
                "message": "خوش آمدید.",
            },
            cookies=[self._session_cookie(token)],
        )

    def _api_logout(self):
        auth = self._auth()
        self._verify_origin()
        self._body()
        if auth:
            self._verify_csrf(auth)
            database.delete_session(security.hash_token(self.cookies.get(SESSION_COOKIE, "")))
            security.audit("auth.logout", user_id=auth["user"]["id"], ip=self._client_ip())
        self._json(200, {"message": "از حساب کاربری خارج شدید."}, cookies=[self._clear_cookie()])

    def _api_create_call_order(self):
        auth = self._require_user()
        self._verify_origin()
        data = self._body()
        self._verify_csrf(auth)

        user_id = auth["user"]["id"]
        if ORDER_USER_LIMITER.check(f"order:{user_id}"):
            raise APIError(
                429, "تعداد سفارش‌های ثبت‌شده در یک ساعت اخیر زیاد است. کمی بعد دوباره تلاش کنید."
            )
        if database.count_open_orders(user_id) >= 3:
            raise APIError(
                429, "شما چند سفارش در انتظار بررسی دارید. تا پیگیری آن‌ها سفارش جدید ثبت نکنید."
            )

        urgency = data.get("urgency")
        reason = data.get("reason")
        if urgency not in URGENCY_VALUES:
            raise APIError(400, "فوریت درخواست را انتخاب کنید.", "urgency")
        if not _is_valid_text(reason, 10, 1500):
            raise APIError(400, "دلیل تماس باید بین ۱۰ تا ۱۵۰۰ کاراکتر باشد.", "reason")

        order = database.create_call_order(user_id, urgency, reason.strip())
        ORDER_USER_LIMITER.hit(f"order:{user_id}")
        security.audit("order.create", user_id=user_id, order_id=order["id"], urgency=urgency)
        self._json(
            201,
            {
                "order": order,
                "message": "سفارش تماس شما ثبت شد. به‌زودی با شما تماس می‌گیریم یا پیام می‌فرستیم.",
            },
        )

    # ------------------------------------------------------------------
    # Admin: projects and profile
    # ------------------------------------------------------------------

    def _project_payload(self, existing: dict | None, data: dict) -> dict:
        slug = security.validate_slug(data.get("slug"))
        if not slug:
            raise APIError(
                400, "شناسه انگلیسی پروژه معتبر نیست (حروف کوچک، عدد و خط تیره).", "slug"
            )
        if database.slug_exists(slug, exclude_id=existing["id"] if existing else None):
            raise APIError(409, "شناسه پروژه قبلاً استفاده شده است.", "slug")

        project_type = data.get("type")
        if project_type not in PROJECT_TYPES:
            raise APIError(400, "نوع پروژه باید وب یا ویدیو باشد.", "type")

        fields = [
            ("categoryName", 2, 80, "دسته‌بندی"),
            ("title", 3, 180, "عنوان"),
            ("subtitle", 3, 240, "زیرعنوان"),
            ("shortDescription", 10, 400, "توضیح کوتاه"),
            ("fullDescription", 20, 6000, "توضیحات کامل"),
            ("client", 2, 160, "کارفرما"),
            ("year", 2, 20, "سال"),
            ("duration", 2, 80, "مدت زمان"),
            ("challenge", 10, 3000, "چالش"),
            ("solution", 10, 3000, "راهکار"),
        ]
        cleaned = {}
        for field, minimum, maximum, label in fields:
            value = _clean_text(data.get(field))
            if not (minimum <= len(value) <= maximum):
                raise APIError(400, f"{label} باید بین {minimum} تا {maximum} کاراکتر باشد.", field)
            cleaned[field] = value

        testimonial = _clean_text(data.get("testimonial"))
        if len(testimonial) > 3000:
            raise APIError(400, "متن نظر کارفرما بیش از حد طولانی است.", "testimonial")

        services = _clean_list(data.get("servicesProvided"), 30, 160)
        technologies = _clean_list(data.get("technologies"), 30, 160)
        stats = _clean_stats(data.get("stats"))
        if not services:
            raise APIError(400, "حداقل یک خدمت ارائه‌شده وارد کنید.", "servicesProvided")
        if not stats:
            raise APIError(400, "حداقل یک شاخص آماری وارد کنید.", "stats")

        image = self._resolve_image(data.get("image"), required=True)
        banner_image = self._resolve_image(data.get("bannerImage"), required=True)
        gallery_raw = data.get("gallery") or []
        if not isinstance(gallery_raw, list) or not (1 <= len(gallery_raw) <= 6):
            raise APIError(400, "گالری باید بین ۱ تا ۶ تصویر داشته باشد.", "gallery")
        gallery = [self._resolve_image(item, required=True) for item in gallery_raw]

        try:
            sort_order = int(data.get("sortOrder", database.next_sort_order()))
        except (TypeError, ValueError):
            raise APIError(400, "ترتیب نمایش باید عدد باشد.", "sortOrder")
        if not (0 <= sort_order <= 100000):
            raise APIError(400, "ترتیب نمایش معتبر نیست.", "sortOrder")

        return {
            "slug": slug,
            "type": project_type,
            "categoryName": cleaned["categoryName"],
            "title": cleaned["title"],
            "subtitle": cleaned["subtitle"],
            "shortDescription": cleaned["shortDescription"],
            "fullDescription": cleaned["fullDescription"],
            "client": cleaned["client"],
            "year": cleaned["year"],
            "duration": cleaned["duration"],
            "servicesProvided": services,
            "technologies": technologies,
            "image": image,
            "bannerImage": banner_image,
            "gallery": gallery,
            "stats": stats,
            "challenge": cleaned["challenge"],
            "solution": cleaned["solution"],
            "testimonial": testimonial,
            "sortOrder": sort_order,
            "isPublished": bool(data.get("isPublished", True)),
        }

    def _resolve_image(self, value: object, required: bool = True) -> str:
        upload_root = paths.upload_dir().resolve()
        if not isinstance(value, str) or not value.strip():
            if required:
                raise APIError(400, "تصویر مورد نیاز است.", "image")
            return ""
        value = value.strip()
        if value.startswith("data:image/"):
            return self._save_image(value)
        if value.startswith("/uploads/"):
            filename = value[len("/uploads/") :]
            if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
                raise APIError(400, "مسیر تصویر معتبر نیست.", "image")
            target = (upload_root / filename).resolve()
            if not target.is_relative_to(upload_root) or not target.is_file():
                raise APIError(400, "تصویر انتخاب‌شده پیدا نشد.", "image")
            if target.suffix.lower() not in STATIC_EXTENSIONS["/uploads/"]:
                raise APIError(400, "فرمت تصویر مجاز نیست.", "image")
            return value
        if value.startswith("/assets/images/"):
            relative = value[len("/assets/") :]
            target = (ROOT / "assets" / relative).resolve()
            assets_root = (ROOT / "assets").resolve()
            if not target.is_relative_to(assets_root) or not target.is_file():
                raise APIError(400, "تصویر انتخاب‌شده پیدا نشد.", "image")
            return value
        if value.startswith("assets/images/"):
            target = (ROOT / value).resolve()
            assets_root = (ROOT / "assets").resolve()
            if not target.is_relative_to(assets_root) or not target.is_file():
                raise APIError(400, "تصویر انتخاب‌شده پیدا نشد.", "image")
            return "/" + value
        raise APIError(400, "فرمت مسیر تصویر مجاز نیست.", "image")

    def _save_image(self, data_url: str) -> str:
        match = DATA_URL_RE.match(data_url)
        if not match:
            security.audit("upload.rejected", ip=self._client_ip(), reason="mime")
            raise APIError(
                400, "فرمت تصویر پشتیبانی نمی‌شود. JPEG، PNG یا WebP ارسال کنید.", "image"
            )
        extension = match.group(1).lower()
        if extension == "jpg":
            extension = "jpeg"
        encoded = re.sub(r"\s+", "", match.group(2))
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            security.audit("upload.rejected", ip=self._client_ip(), reason="base64")
            raise APIError(400, "دادهٔ تصویر معتبر نیست.", "image")
        if len(payload) > MAX_IMAGE_BYTES:
            security.audit("upload.rejected", ip=self._client_ip(), reason="size")
            raise APIError(413, "حجم هر تصویر باید کمتر از ۶ مگابایت باشد.", "image")
        magic_ok = (
            (extension == "jpeg" and payload.startswith(b"\xff\xd8\xff"))
            or (extension == "png" and payload.startswith(b"\x89PNG\r\n\x1a\n"))
            or (
                extension == "webp"
                and len(payload) > 12
                and payload[:4] == b"RIFF"
                and payload[8:12] == b"WEBP"
            )
        )
        if not magic_ok:
            security.audit("upload.rejected", ip=self._client_ip(), reason="magic")
            raise APIError(400, "محتوای فایل با فرمت تصویر مطابقت ندارد.", "image")
        filename = f"{security.new_token(12).replace('-', '').replace('_', '')}.{extension}"
        upload_root = paths.upload_dir().resolve()
        upload_root.mkdir(parents=True, exist_ok=True)
        paths.harden_dir(upload_root)
        target = (upload_root / filename).resolve()
        if not target.is_relative_to(upload_root):
            raise APIError(400, "مسیر ذخیره تصویر معتبر نیست.", "image")
        target.write_bytes(payload)
        paths.harden_file(target)
        security.audit("upload.saved", ip=self._client_ip(), file=filename, bytes=len(payload))
        return f"/uploads/{filename}"

    def _api_update_admin_profile(self, auth: dict, data: dict):
        row = database.get_user_with_hash(auth["user"]["phone"])
        current_password = data.get("currentPassword") or ""
        if not row or not security.verify_password(current_password, row["password_hash"]):
            security.audit("admin.profile.denied", user_id=auth["user"]["id"], ip=self._client_ip())
            raise APIError(400, "رمز عبور فعلی اشتباه است.", "currentPassword")

        first_name = security.validate_name(data.get("firstName"))
        last_name = security.validate_name(data.get("lastName"))
        phone = security.normalize_phone(data.get("phone") or "")
        if not first_name:
            raise APIError(400, "نام معتبر نیست.", "firstName")
        if not last_name:
            raise APIError(400, "نام خانوادگی معتبر نیست.", "lastName")
        if not phone:
            raise APIError(400, "شماره موبایل معتبر نیست.", "phone")
        existing = database.get_user_by_phone(phone)
        if existing and existing["id"] != auth["user"]["id"]:
            raise APIError(409, "این شماره موبایل قبلاً ثبت شده است.", "phone")

        new_password = data.get("newPassword") or ""
        confirm = data.get("newPasswordConfirm") or ""
        if new_password:
            password_error = security.validate_password(
                new_password, phone=phone, names=(first_name, last_name)
            )
            if password_error:
                raise APIError(400, password_error, "newPassword")
            if new_password != confirm:
                raise APIError(400, "تکرار رمز عبور جدید یکسان نیست.", "newPasswordConfirm")
            if security.verify_password(new_password, row["password_hash"]):
                raise APIError(400, "رمز عبور جدید باید با رمز فعلی متفاوت باشد.", "newPassword")

        database.update_user_profile(auth["user"]["id"], first_name, last_name, phone)
        if new_password:
            database.update_user_password(
                auth["user"]["id"],
                security.hash_password(new_password),
                must_change=False,
                keep_session_id=auth["sessionId"],
            )
            security.audit("admin.password.changed", user_id=auth["user"]["id"])
        updated = database.get_user_by_id(auth["user"]["id"])
        security.audit("admin.profile.update", user_id=auth["user"]["id"])
        self._json(
            200,
            {
                "user": _public_user(updated),
                "message": "اطلاعات مدیر با موفقیت به‌روزرسانی شد.",
            },
        )


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with a hard cap on concurrent connections."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, server_address, handler_class, max_connections: int = MAX_CONCURRENT_CONNECTIONS):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self._reject(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            try:
                self._slots.release()
            except ValueError:  # pragma: no cover - defensive
                pass

    @staticmethod
    def _reject(request) -> None:
        try:
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Retry-After: 5\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
            )
        except OSError:
            pass
        finally:
            try:
                request.close()
            except OSError:
                pass


def _print_security_banner(host: str, port: int, admin_report: dict) -> None:
    print(
        "\n".join(
            [
                "",
                "=" * 68,
                " MK Graphy is running",
                "=" * 68,
                f" Site        : http://{host}:{port}",
                f" Admin panel : http://{host}:{port}/admin/login",
                f" Data folder : {paths.data_dir()}",
                f" Database    : {paths.database_path()}",
                f" Uploads     : {paths.upload_dir()}",
                "=" * 68,
            ]
        )
    )
    if admin_report.get("created") or admin_report.get("rotated"):
        action = "created" if admin_report.get("created") else "rotated because the old password was public"
        print(
            "\n"
            "  ADMIN PASSWORD — read once, then delete the file\n"
            f"  The administrator account was {action}.\n"
            f"  Phone    : {admin_report.get('phone')}\n"
            f"  Password : {admin_report.get('password')}\n"
            f"  Saved to : {admin_report.get('credentials_file')}\n"
            "  Sign in once, change the password in «تنظیمات ادمین», then delete that file.\n"
        )
    if host not in {"127.0.0.1", "localhost", "::1"} and not (
        _env_flag("MK_COOKIE_SECURE") or _env_flag("MK_HTTPS")
    ):
        print(
            "\n  WARNING: listening on a non-loopback address without HTTPS.\n"
            "  Set MK_COOKIE_SECURE=1 and MK_HTTPS=1 behind a TLS reverse proxy before exposing it.\n"
        )
    sys.stdout.flush()


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    paths.load_secrets_file()
    paths.ensure_layout()
    notes = paths.migrate_legacy_data()
    paths.write_migration_note(notes)
    for note in notes:
        print(f"[data] {note}")

    database.init_db()
    admin_report = database.ensure_admin_account()
    security.audit("server.start", host=host, port=port)

    server = BoundedThreadingHTTPServer((host, port), AppHandler)
    _print_security_banner(host, port, admin_report)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MK Graphy standard-library backend")
    parser.add_argument("--host", default=os.environ.get("MK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MK_PORT", "8000")))
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
