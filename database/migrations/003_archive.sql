-- Личный архив (файлы, идеи, голосовые)
CREATE TABLE IF NOT EXISTS archive_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    type        TEXT    NOT NULL,
    title       TEXT,
    text        TEXT,
    file_id     TEXT,
    tags        TEXT    NOT NULL DEFAULT '[]',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO schema_version VALUES (3);
