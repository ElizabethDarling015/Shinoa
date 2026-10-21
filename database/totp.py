"""
CRUD для аккаунтов 2FA (totp_accounts). Секреты шифруются/расшифровываются
в services/totp.py — сюда попадает уже зашифрованная строка.
"""

from database.connection import get_db


async def add_totp_account(chat_id: int, name: str, secret_enc: str,
                           digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> int:
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO totp_accounts (chat_id, name, secret_enc, digits, period, algorithm) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, name, secret_enc, digits, period, algorithm),
        )
        await db.commit()
        return cur.lastrowid


async def get_totp_accounts(chat_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM totp_accounts WHERE chat_id = ? ORDER BY name COLLATE NOCASE, id",
            (chat_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_totp_account(account_id: int, chat_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM totp_accounts WHERE id = ? AND chat_id = ?", (account_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def delete_totp_account(account_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM totp_accounts WHERE id = ? AND chat_id = ?", (account_id, chat_id)
        )
        await db.commit()
        return cur.rowcount > 0
