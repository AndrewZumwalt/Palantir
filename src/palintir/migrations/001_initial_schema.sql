-- Palintir initial database schema

-- Enrolled people (students, teachers, admins)
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',  -- teacher, student, admin, guest
    face_embedding BLOB,                   -- 512-D float32 mean embedding
    voice_embedding BLOB,                  -- 192-D float32 mean embedding
    enrolled_at TEXT NOT NULL DEFAULT (datetime('now')),
    consent_given_at TEXT,
    consent_text TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

-- Class sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    metadata TEXT  -- JSON blob for extra info
);

-- Attendance records
CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    person_id TEXT NOT NULL REFERENCES persons(id),
    entered_at TEXT NOT NULL DEFAULT (datetime('now')),
    exited_at TEXT,
    duration_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance_records(session_id);
CREATE INDEX IF NOT EXISTS idx_attendance_person ON attendance_records(person_id);

-- Event log
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    person_id TEXT REFERENCES persons(id),
    session_id TEXT REFERENCES sessions(id),
    data TEXT,  -- JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- Engagement samples (10-second intervals)
CREATE TABLE IF NOT EXISTS engagement_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    person_id TEXT NOT NULL REFERENCES persons(id),
    state TEXT NOT NULL,  -- working, collaborating, phone, sleeping, disengaged
    confidence REAL NOT NULL DEFAULT 0.0,
    sampled_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_engagement_session ON engagement_samples(session_id);
CREATE INDEX IF NOT EXISTS idx_engagement_person ON engagement_samples(person_id);

-- Conversation history
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT REFERENCES persons(id),
    session_id TEXT REFERENCES sessions(id),
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    context TEXT,  -- JSON snapshot of context used
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_person ON conversations(person_id);

-- Persistent memory (key facts extracted from conversations)
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT REFERENCES persons(id),
    fact TEXT NOT NULL,
    source TEXT,  -- "conversation", "enrollment", "manual"
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT  -- optional expiry
);

CREATE INDEX IF NOT EXISTS idx_memory_person ON memory(person_id);

-- Automation rules
CREATE TABLE IF NOT EXISTS automation_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    trigger_type TEXT NOT NULL,  -- "person_enters", "person_exits", "schedule", "voice_command"
    trigger_config TEXT NOT NULL,  -- JSON: {"person_id": "...", "role": "teacher", ...}
    action_type TEXT NOT NULL,    -- "gpio", "command", "notification", "tts"
    action_config TEXT NOT NULL,  -- JSON: {"pin": 17, "state": "high", ...}
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
