CREATE TABLE IF NOT EXISTS session_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    workspace TEXT NOT NULL,
    memory_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_name, content)
);

CREATE INDEX IF NOT EXISTS idx_session_memories_workspace_updated
ON session_memories(workspace, updated_at DESC, id DESC);
