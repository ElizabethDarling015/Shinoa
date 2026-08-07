-- Пользователи и их настройки
CREATE TABLE IF NOT EXISTS users (
    chat_id      INTEGER PRIMARY KEY,
    timezone     TEXT    NOT NULL DEFAULT 'Europe/Moscow',
    digest_time  TEXT    NOT NULL DEFAULT '07:00',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Главная сущность: задачи и напоминания
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    text         TEXT    NOT NULL,
    category     TEXT    NOT NULL DEFAULT 'личное',
    priority     TEXT    NOT NULL DEFAULT 'medium',
    type         TEXT    NOT NULL DEFAULT 'weekly',
    status       TEXT    NOT NULL DEFAULT 'active',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- Расписания срабатывания
CREATE TABLE IF NOT EXISTS schedules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    time           TEXT    NOT NULL,
    days_of_week   TEXT,
    day_of_month   INTEGER,
    month          INTEGER,
    interval_days  INTEGER,
    one_shot       INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 1
);

-- Версия схемы для системы миграций
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO schema_version VALUES (1);
