"""SQLite connection handling and schema.

We use the stdlib sqlite3 module with parameterized queries only: no string
formatting of SQL with user data, ever.
"""

import os
import sqlite3

from flask import Flask, current_app, g

# CHECK constraints mirror the enums in schemas.py, so even a bug that
# bypasses validation can't write an out-of-range value.
SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_email  TEXT NOT NULL,
    subject         TEXT NOT NULL,
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (analysis_status IN ('pending', 'completed', 'failed')),
    analysis_error  TEXT,
    analysis_started_at TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    ticket_id           INTEGER PRIMARY KEY
        REFERENCES tickets(id) ON DELETE CASCADE,
    category            TEXT NOT NULL CHECK (category IN (
        'billing', 'technical', 'account', 'shipping',
        'product_question', 'refund', 'feedback', 'other')),
    sentiment           TEXT NOT NULL
        CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    urgency             TEXT NOT NULL
        CHECK (urgency IN ('low', 'medium', 'high', 'critical')),
    suggested_response  TEXT NOT NULL,
    escalate_to_human   INTEGER NOT NULL CHECK (escalate_to_human IN (0, 1)),
    explanation         TEXT NOT NULL,
    model               TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by earlier versions (no migration framework:
    just add what's missing)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "analysis_started_at" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN analysis_started_at TEXT")
        conn.execute("UPDATE tickets SET analysis_started_at = created_at")


def _resolve_path(app: Flask) -> str:
    path = app.config["DATABASE_PATH"]
    if not os.path.isabs(path):
        path = os.path.join(app.instance_path, path)
    return path


def get_db() -> sqlite3.Connection:
    """One connection per request, closed automatically at teardown."""
    if "db" not in g:
        conn = sqlite3.connect(_resolve_path(current_app))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app: Flask) -> None:
    path = _resolve_path(app)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def init_app(app: Flask) -> None:
    app.config.setdefault("DATABASE_PATH", "triage.db")
    init_db(app)
    app.teardown_appcontext(close_db)
