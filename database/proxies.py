"""
Хранение прокси, через которые бот подключается к Telegram API.
Таблица создаётся автоматически (CREATE TABLE IF NOT EXISTS).
"""
import logging
from datetime import datetime

from database.connection import get_db

logger = logging.getLogger(__name__)


async def ensure_proxies_table() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_proxies (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                proxy_type   TEXT    NOT NULL,
                host         TEXT    NOT NULL,
                port         INTEGER NOT NULL,
                username     TEXT,
                password     TEXT,
                country_code TEXT,
                country_name TEXT,
                city         TEXT,
                is_active    INTEGER NOT NULL DEFAULT 0,
                last_ok      INTEGER,
                last_ms      INTEGER,
                last_error   TEXT,
                activated_at TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        await db.commit()


async def add_or_update_proxy(
    user_id: int, proxy_type: str, host: str, port: int,
    username: str = None, password: str = None,
) -> dict:
    """Добавляет прокси или обновляет пароль, если такой уже есть."""
    async with get_db() as db:
        async with db.execute(
            """SELECT id FROM user_proxies
               WHERE user_id=? AND proxy_type=? AND host=? AND port=? AND username IS ?""",
            (user_id, proxy_type, host, port, username),
        ) as cur:
            row = await cur.fetchone()

        if row:
            proxy_id = row["id"]
            await db.execute(
                "UPDATE user_proxies SET password=? WHERE id=?",
                (password, proxy_id),
            )
        else:
            cur = await db.execute(
                """INSERT INTO user_proxies
                   (user_id, proxy_type, host, port, username, password)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, proxy_type, host, port, username, password),
            )
            proxy_id = cur.lastrowid
        await db.commit()
    return await get_proxy(proxy_id)


async def get_proxy(proxy_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM user_proxies WHERE id=?", (proxy_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_proxies(user_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM user_proxies WHERE user_id=? ORDER BY id", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_active_proxy() -> dict | None:
    """Последний активированный прокси (сессия бота одна на всех)."""
    async with get_db() as db:
        async with db.execute(
            """SELECT * FROM user_proxies WHERE is_active=1
               ORDER BY activated_at DESC LIMIT 1"""
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def set_active(proxy_id: int, active: bool) -> None:
    async with get_db() as db:
        if active:
            await db.execute("UPDATE user_proxies SET is_active=0")
            await db.execute(
                "UPDATE user_proxies SET is_active=1, activated_at=? WHERE id=?",
                (datetime.now().isoformat(), proxy_id),
            )
        else:
            await db.execute(
                "UPDATE user_proxies SET is_active=0 WHERE id=?", (proxy_id,)
            )
        await db.commit()


async def update_check(proxy_id: int, res: dict) -> None:
    async with get_db() as db:
        await db.execute(
            """UPDATE user_proxies
               SET last_ok=?, last_ms=?, last_error=?,
                   country_code=COALESCE(?, country_code),
                   country_name=COALESCE(?, country_name),
                   city=COALESCE(?, city)
               WHERE id=?""",
            (
                1 if res.get("ok") else 0,
                res.get("ms"),
                res.get("error"),
                res.get("country_code"),
                res.get("country_name"),
                res.get("city"),
                proxy_id,
            ),
        )
        await db.commit()