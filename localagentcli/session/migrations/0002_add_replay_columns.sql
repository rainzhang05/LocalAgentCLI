ALTER TABLE sessions
ADD COLUMN replay_last_record_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sessions
ADD COLUMN replay_last_replayed_at TEXT NOT NULL DEFAULT '';
