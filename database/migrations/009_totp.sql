-- Аккаунты для генератора кодов 2FA (TOTP).
-- secret_enc — секретный ключ, зашифрованный (см. services/totp.py),
-- в открытом виде он в базе не хранится.
CREATE TABLE IF NOT EXISTS totp_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    secret_enc  TEXT    NOT NULL,
    digits      INTEGER NOT NULL DEFAULT 6,
    period      INTEGER NOT NULL DEFAULT 30,
    algorithm   TEXT    NOT NULL DEFAULT 'SHA1',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (9);
