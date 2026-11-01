#!/usr/bin/env python3
"""Administrative CLI for the MK Graphy backend.

Examples:
    python backend/manage.py data-path
    python backend/manage.py init-db
    python backend/manage.py security-check
    python backend/manage.py list-admins
    python backend/manage.py create-admin --phone 09121234567
    python backend/manage.py promote --phone 09121234567
    python backend/manage.py reset-password --phone 09121234567
    python backend/manage.py revoke-sessions --phone 09121234567
"""

from __future__ import annotations

import argparse
import getpass
import os
import stat
import sys

import database
import paths
import security


def _password(value: str | None) -> str:
    if value:
        print(
            "WARNING: passing --password leaves the secret in your shell history. "
            "Prefer the interactive prompt.",
            file=sys.stderr,
        )
        return value
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    return first


def _validate_password_or_exit(password: str, phone: str | None = None) -> None:
    error = security.validate_password(password, phone=phone)
    if error:
        raise SystemExit(error)


def command_data_path(_args) -> None:
    width = max(len(label) for label, _ in paths.describe())
    for label, value in paths.describe():
        print(f"{label.ljust(width)} : {value}")
    print("\nEverything above must stay outside the Git repository and is never served by HTTP.")


def command_init_db(_args) -> None:
    paths.ensure_layout()
    path = database.init_db()
    report = database.ensure_admin_account()
    print(f"Database initialized: {path}")
    if report.get("created"):
        print(f"Admin created: {report['phone']}")
        print(f"Password file: {report['credentials_file']}")
    if report.get("rotated"):
        print("Admin password rotated because the previous one was publicly known.")
        print(f"Password file: {report['credentials_file']}")


def command_migrate(_args) -> None:
    paths.ensure_layout()
    notes = paths.migrate_legacy_data()
    paths.write_migration_note(notes)
    if not notes:
        print("Nothing to migrate: no legacy data found inside the repository.")
        return
    for note in notes:
        print(note)


def command_security_check(_args) -> None:
    problems: list[str] = []
    warnings: list[str] = []

    data_root = paths.data_dir()
    project_root = paths.PROJECT_ROOT
    try:
        inside_repo = data_root.is_relative_to(project_root)
    except ValueError:
        inside_repo = False
    if inside_repo:
        problems.append(
            f"The data directory is inside the repository ({data_root}). "
            "Move it out with MK_DATA_DIR; otherwise a commit can publish user data."
        )

    if paths.LEGACY_DATA_DIR.exists():
        problems.append(f"Legacy database folder still exists: {paths.LEGACY_DATA_DIR}")
    if paths.LEGACY_UPLOAD_DIR.exists():
        problems.append(f"Legacy uploads folder still exists: {paths.LEGACY_UPLOAD_DIR}")

    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        problems.append("Missing .gitignore — database and uploads could be committed.")
    else:
        content = gitignore.read_text(encoding="utf-8", errors="ignore").lower()
        for pattern in ("*.db", "__pycache__", ".env"):
            if pattern not in content:
                warnings.append(f".gitignore does not mention {pattern}")

    db_file = paths.database_path()
    if not db_file.exists():
        warnings.append("No database yet — run `python backend/manage.py init-db`.")
    elif os.name != "nt":
        mode = stat.S_IMODE(db_file.stat().st_mode)
        if mode & 0o077:
            problems.append(
                f"Database permissions are too open ({oct(mode)}). Run: chmod 600 {db_file}"
            )

    with database.connection() as conn:
        admins = conn.execute(
            "SELECT id, phone, password_hash FROM users WHERE role = 'admin'"
        ).fetchall()
    if not admins:
        warnings.append("No admin account exists yet.")
    for admin_row in admins:
        if database._password_is_publicly_known(admin_row["password_hash"]):
            problems.append(
                f"Admin {admin_row['phone']} still uses a password that was published in the docs. "
                "Run `python backend/manage.py reset-password --phone <phone>`."
            )

    credential_file = paths.bootstrap_credentials_path()
    if credential_file.exists():
        warnings.append(
            f"Bootstrap credentials file still exists: {credential_file} — delete it after the first login."
        )

    if not (
        os.environ.get("MK_COOKIE_SECURE", "").strip() or os.environ.get("MK_HTTPS", "").strip()
    ):
        warnings.append(
            "MK_COOKIE_SECURE / MK_HTTPS are not set. Enable both when serving over HTTPS."
        )

    for line in problems:
        print(f"[PROBLEM] {line}")
    for line in warnings:
        print(f"[WARN]    {line}")
    if not problems and not warnings:
        print("Security check passed: no issues found.")
    elif not problems:
        print("\nSecurity check finished with warnings only.")
    else:
        print(f"\n{len(problems)} problem(s) must be fixed before publishing the site.")
        raise SystemExit(1)


def command_list_admins(_args) -> None:
    users = database.list_users_with_counts()
    admins = [user for user in users if user["role"] == "admin"]
    if not admins:
        print("No admin users found.")
        return
    for admin in admins:
        print(f"{admin['id']:>4}  {admin['phone']}  {admin['firstName']} {admin['lastName']}")


def command_create_admin(args) -> None:
    paths.ensure_layout()
    database.init_db()
    phone = security.normalize_phone(args.phone)
    if not phone:
        raise SystemExit("Invalid Iranian mobile number.")
    first_name = security.validate_name(args.first_name)
    last_name = security.validate_name(args.last_name)
    if not first_name or not last_name:
        raise SystemExit("First and last name are required and must be valid.")
    password = _password(args.password)
    _validate_password_or_exit(password, phone)

    existing = database.get_user_by_phone(phone)
    if existing:
        database.update_user_profile(existing["id"], first_name, last_name, phone)
        database.update_user_password(existing["id"], security.hash_password(password))
        database.set_user_role(existing["id"], "admin")
        security.audit("cli.admin.updated", phone=phone)
        print(f"Updated existing account as admin: {phone}")
    else:
        database.create_user(
            first_name,
            last_name,
            phone,
            security.hash_password(password),
            role="admin",
        )
        security.audit("cli.admin.created", phone=phone)
        print(f"Created admin account: {phone}")


def command_promote(args) -> None:
    phone = security.normalize_phone(args.phone)
    if not phone:
        raise SystemExit("Invalid Iranian mobile number.")
    user = database.get_user_by_phone(phone)
    if not user:
        raise SystemExit("No user with that phone number was found.")
    database.set_user_role(user["id"], "admin")
    security.audit("cli.admin.promoted", phone=phone)
    print(f"Promoted user to admin: {phone}")


def command_reset_password(args) -> None:
    phone = security.normalize_phone(args.phone)
    if not phone:
        raise SystemExit("Invalid Iranian mobile number.")
    user = database.get_user_by_phone(phone)
    if not user:
        raise SystemExit("No user with that phone number was found.")
    password = _password(args.password)
    _validate_password_or_exit(password, phone)
    database.update_user_password(user["id"], security.hash_password(password), must_change=False)
    security.audit("cli.password.reset", phone=phone)
    print(f"Password updated for: {phone}")
    print("All active sessions for this account were signed out.")


def command_revoke_sessions(args) -> None:
    if args.phone == "all":
        with database.connection() as conn:
            removed = conn.execute("DELETE FROM sessions").rowcount
        security.audit("cli.sessions.revoked_all", count=removed)
        print(f"Signed out all {removed} session(s).")
        return
    phone = security.normalize_phone(args.phone)
    if not phone:
        raise SystemExit("Invalid Iranian mobile number.")
    user = database.get_user_by_phone(phone)
    if not user:
        raise SystemExit("No user with that phone number was found.")
    database.delete_user_sessions(user["id"])
    security.audit("cli.sessions.revoked", phone=phone)
    print(f"Signed out every session of: {phone}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MK Graphy management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data_parser = subparsers.add_parser("data-path", help="show where private data is stored")
    data_parser.set_defaults(func=command_data_path)

    init_parser = subparsers.add_parser("init-db", help="create tables, seed data and the first admin")
    init_parser.set_defaults(func=command_init_db)

    migrate_parser = subparsers.add_parser(
        "migrate-data", help="move a legacy backend/data + backend/uploads out of the repo"
    )
    migrate_parser.set_defaults(func=command_migrate)

    check_parser = subparsers.add_parser(
        "security-check", help="audit storage, permissions and default credentials"
    )
    check_parser.set_defaults(func=command_security_check)

    list_parser = subparsers.add_parser("list-admins", help="list admin accounts")
    list_parser.set_defaults(func=command_list_admins)

    create_parser = subparsers.add_parser("create-admin", help="create or update an admin account")
    create_parser.add_argument("--phone", required=True)
    create_parser.add_argument("--password")
    create_parser.add_argument("--first-name", default="مدیر")
    create_parser.add_argument("--last-name", default="استودیو")
    create_parser.set_defaults(func=command_create_admin)

    promote_parser = subparsers.add_parser("promote", help="promote an existing user to admin")
    promote_parser.add_argument("--phone", required=True)
    promote_parser.set_defaults(func=command_promote)

    reset_parser = subparsers.add_parser("reset-password", help="reset a user's password")
    reset_parser.add_argument("--phone", required=True)
    reset_parser.add_argument("--password")
    reset_parser.set_defaults(func=command_reset_password)

    revoke_parser = subparsers.add_parser("revoke-sessions", help="sign out active sessions")
    revoke_parser.add_argument("--phone", required=True, help="a phone number, or 'all'")
    revoke_parser.set_defaults(func=command_revoke_sessions)

    args = parser.parse_args()
    paths.load_secrets_file()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
