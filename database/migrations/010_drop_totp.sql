-- Генератор 2FA больше не хранит аккаунты (ключ присылается каждый раз),
-- таблица из миграции 009 не нужна.
DROP TABLE IF EXISTS totp_accounts;

INSERT OR IGNORE INTO schema_version (version) VALUES (10);
