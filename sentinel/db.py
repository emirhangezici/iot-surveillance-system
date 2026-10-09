"""Request-scoped SQLite connections and first-run demo initialization."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=5)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def record_event(conn, label, kind, source="Entrance sensor", review=False):
    user = g.get("user")
    conn.execute(
        "INSERT INTO events (created_at, kind, label, source, actor_id, actor_name, needs_review) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            now(),
            kind,
            label,
            source,
            user["id"] if user else None,
            user["username"] if user else "Simulator",
            int(review),
        ),
    )


def initialize():
    path = Path(current_app.config["DATABASE"])
    if path.suffix.lower() in {".mdb", ".accdb"}:
        raise RuntimeError("SQLITE_DATABASE must point to a SQLite file, not an Access database.")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    with conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO device (id, updated_at) VALUES (1, ?)", (now(),)
        ).rowcount
        if inserted:
            record_event(conn, "Simulator ready", "device")
        if (
            current_app.config["SEED_DEMO"]
            and not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        ):
            for username, password, role in (
                ("admin", "DemoAdmin!2026", "admin"),
                ("operator", "DemoUser!2026", "operator"),
            ):
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), role, now()),
                )
