CREATE TABLE IF NOT EXISTS completed_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_task_id INTEGER,
    chat_id INTEGER NOT NULL,
    title TEXT,
    text TEXT,
    category TEXT,
    priority TEXT,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);