CREATE TABLE IF NOT EXISTS session_active_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    agent_path TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    nickname TEXT NOT NULL,
    role TEXT NOT NULL,
    last_error TEXT NOT NULL,
    task_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(session_name, agent_path)
);

CREATE INDEX IF NOT EXISTS idx_session_active_agents_session
ON session_active_agents(session_name, agent_path);
