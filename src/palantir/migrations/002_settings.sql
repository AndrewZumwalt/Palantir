-- Persistent key/value settings store.
--
-- Lets the operator configure secrets (Anthropic / Groq API keys) from
-- the web UI without editing .env on the host.  Reads merge with
-- environment variables: a row here wins over a PALANTIR_* env var.
-- Cleared values delete the row.

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
