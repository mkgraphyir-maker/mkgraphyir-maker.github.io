PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    phone         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    -- 1 right after a generated bootstrap password: the admin panel keeps
    -- reminding the owner to pick a personal password.
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL CHECK (type IN ('web', 'video')),
    category_name       TEXT NOT NULL,
    title               TEXT NOT NULL,
    subtitle            TEXT NOT NULL,
    short_description   TEXT NOT NULL,
    full_description    TEXT NOT NULL,
    client              TEXT NOT NULL,
    year                TEXT NOT NULL,
    duration            TEXT NOT NULL,
    services_provided   TEXT NOT NULL DEFAULT '[]',
    technologies        TEXT NOT NULL DEFAULT '[]',
    image               TEXT NOT NULL,
    banner_image        TEXT NOT NULL,
    gallery             TEXT NOT NULL DEFAULT '[]',
    stats               TEXT NOT NULL DEFAULT '[]',
    challenge           TEXT NOT NULL,
    solution            TEXT NOT NULL,
    testimonial         TEXT NOT NULL DEFAULT '',
    sort_order          INTEGER NOT NULL DEFAULT 1000,
    is_published        INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    urgency     TEXT NOT NULL CHECK (urgency IN ('immediate', 'today', 'this_week', 'normal')),
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'contacted', 'closed')),
    admin_note  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_sort ON projects(sort_order, id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON call_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON call_orders(created_at DESC);
