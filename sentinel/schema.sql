CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    auth_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS device (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    armed INTEGER NOT NULL DEFAULT 0,
    triggered INTEGER NOT NULL DEFAULT 0,
    online INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('control', 'motion', 'device', 'account')),
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_name TEXT NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT,
    acknowledged_by TEXT
);
CREATE INDEX IF NOT EXISTS events_recent ON events(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, created_at DESC);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    address TEXT NOT NULL,
    attempted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_recent ON login_attempts(attempted_at, address, username);
