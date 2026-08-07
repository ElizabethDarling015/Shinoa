-- Добавляем колонку city в существующую таблицу users
ALTER TABLE users ADD COLUMN city TEXT;

-- Фиксируем применение миграции
INSERT OR IGNORE INTO schema_version VALUES (4);