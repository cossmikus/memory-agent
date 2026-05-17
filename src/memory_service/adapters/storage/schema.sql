-- Idempotent schema. Re-run on every startup; CREATE IF NOT EXISTS guards.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    user_id       TEXT,
    timestamp     TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id       TEXT PRIMARY KEY,
    turn_id  TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    role     TEXT NOT NULL,
    name     TEXT,
    content  TEXT NOT NULL,
    position INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_turn ON messages(turn_id);

CREATE TABLE IF NOT EXISTS memories (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    type               TEXT NOT NULL,
    key                TEXT NOT NULL,
    value              TEXT NOT NULL,
    value_normalized   TEXT NOT NULL,
    confidence         REAL NOT NULL,
    salience           REAL NOT NULL,
    source_turn_id     TEXT NOT NULL,
    source_session_id  TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    supersedes         TEXT,
    active             INTEGER NOT NULL DEFAULT 1,
    history_json       TEXT NOT NULL DEFAULT '[]',
    metadata_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_user_key ON memories(user_id, key, active);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(user_id, active);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(source_session_id);

CREATE TABLE IF NOT EXISTS triples (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    subject           TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object            TEXT NOT NULL,
    source_memory_id  TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_triples_user_subject ON triples(user_id, subject);
CREATE INDEX IF NOT EXISTS idx_triples_user_object ON triples(user_id, object);
CREATE INDEX IF NOT EXISTS idx_triples_user_predicate ON triples(user_id, predicate);

-- FTS5 for BM25 lexical search. Stores user_id + memory id as unindexed cols
-- so we can filter by user without a separate join.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED,
    user_id   UNINDEXED,
    key,
    value_normalized,
    tokenize = 'porter unicode61'
);
