-- Момент последней успешной отправки напоминания по расписанию (UTC).
-- Нужен, чтобы при старте бота понять, было ли пропущено срабатывание,
-- пока бот был выключен.
ALTER TABLE schedules ADD COLUMN last_fired_at TEXT;

-- Для уже существующих расписаний считаем, что всё прошлое уже обработано,
-- иначе при первом запуске с этой миграцией бот «догонит» старые срабатывания.
UPDATE schedules SET last_fired_at = datetime('now') WHERE is_active = 1;

INSERT OR IGNORE INTO schema_version (version) VALUES (7);
