CREATE TABLE IF NOT EXISTS sessions (
    name TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    format_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
ON sessions(updated_at DESC, name DESC);
