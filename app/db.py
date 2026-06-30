"""Thin SQLite data layer (stdlib only, no ORM).

Kept dependency-free on purpose so the app installs and runs on any Python
version without compiling native wheels.
"""
import os
import sqlite3
from datetime import datetime, timezone
from flask import g, current_app

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'admin'
    status        TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'active' | 'suspended'
    access_expires TEXT,                              -- ISO date; NULL = no expiry
    cv_text       TEXT NOT NULL DEFAULT '',
    cv_filename   TEXT NOT NULL DEFAULT '',
    -- per-user outbound email settings (so each user sends from their own inbox)
    smtp_host     TEXT NOT NULL DEFAULT '',
    smtp_port     INTEGER NOT NULL DEFAULT 587,
    smtp_user     TEXT NOT NULL DEFAULT '',
    smtp_password TEXT NOT NULL DEFAULT '',
    from_name     TEXT NOT NULL DEFAULT '',
    cv_uploads    INTEGER NOT NULL DEFAULT 0,        -- free-trial upload counter
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    company_name    TEXT NOT NULL DEFAULT '',
    company_email   TEXT NOT NULL,
    job_title       TEXT NOT NULL DEFAULT '',
    job_description TEXT NOT NULL DEFAULT '',
    tailored_cv     TEXT NOT NULL DEFAULT '',
    cover_letter    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',   -- 'draft' | 'tailored' | 'sent' | 'failed'
    error           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    sent_at         TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_apps_user ON applications(user_id);

-- AI assistants: each (user, assistant) pair is an isolated conversation
-- thread — its own context window, separate from every other assistant.
CREATE TABLE IF NOT EXISTS assistant_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    assistant  TEXT NOT NULL,                      -- 'cv_coach' | 'cover_letter' | 'interview'
    role       TEXT NOT NULL,                      -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_assistant_msgs ON assistant_messages(user_id, assistant);

-- Support chat: customers report payments / talk to the admin. 'bot' rows are
-- the scripted auto-replies; 'admin' rows are real replies from the owner.
CREATE TABLE IF NOT EXISTS support_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,                      -- 'user' | 'bot' | 'admin'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_support_user ON support_messages(user_id);

-- Password reset tokens. We store only the SHA-256 of the token (never the raw
-- value), so a database leak can't be used to reset anyone's password. Each
-- token expires quickly and is single-use (used=1 after a successful reset).
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id);

-- Card payments (Flutterwave hosted checkout). One row per attempt; tx_ref is
-- our own reference (sent to the provider) and is unique so a webhook + the
-- browser callback for the same payment settle it exactly once.
CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    tx_ref     TEXT UNIQUE NOT NULL,
    flw_tx_id  TEXT,                                -- provider transaction id (on success)
    amount     REAL NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'BWP',
    days       INTEGER NOT NULL,                    -- access days this payment grants
    status     TEXT NOT NULL DEFAULT 'pending',     -- 'pending' | 'successful' | 'failed'
    created_at TEXT NOT NULL,
    paid_at    TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_db():
    if "db" not in g:
        path = current_app.config["DATABASE_PATH"]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        g.db = sqlite3.connect(path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL lets readers proceed while a write is in flight — the main
        # concurrency ceiling for SQLite under multi-user load. busy_timeout
        # makes brief write contention wait instead of erroring.
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Columns added after the first release. Each is applied with ALTER TABLE on
# existing databases (CREATE TABLE IF NOT EXISTS never alters an existing table).
_MIGRATIONS = [
    ("users", "cv_uploads", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(db):
    for table, column, decl in _MIGRATIONS:
        cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    _migrate(db)
    db.commit()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last_id = cur.lastrowid
    cur.close()
    return last_id
