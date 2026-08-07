-- Привычки
CREATE TABLE IF NOT EXISTS habits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'здоровье',
    reminder_time   TEXT    NOT NULL DEFAULT '21:00',
    target_per_week INTEGER NOT NULL DEFAULT 7,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Журнал выполнения привычек
CREATE TABLE IF NOT EXISTS habit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id   INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    date       TEXT    NOT NULL,
    completed  INTEGER NOT NULL DEFAULT 0,
    note       TEXT,
    UNIQUE(habit_id, date)
);

INSERT OR IGNORE INTO schema_version VALUES (2);
