-- ID последнего закреплённого сообщения утренней сводки,
-- чтобы при новой сводке откреплять предыдущую
ALTER TABLE users ADD COLUMN last_digest_msg_id INTEGER;

-- фиксируем применённую версию (как в миграциях 001–004)
INSERT OR IGNORE INTO schema_version (version) VALUES (5);