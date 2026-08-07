"""
Настройки пользователей: город, время сводки, часовой пояс.
"""

from database.connection import get_db


async def get_user(chat_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_user(chat_id: int, city: str = None, digest_time: str = None, timezone: str = None):
    """Создаёт или обновляет настройки пользователя."""
    async with get_db() as db:
        # Проверяем, существует ли пользователь
        async with db.execute(
            "SELECT city, digest_time, timezone FROM users WHERE chat_id = ?", 
            (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        
        if row:
            # Обновляем только переданные поля
            updates = []
            params = []
            if city is not None:
                updates.append("city = ?")
                params.append(city)
            if digest_time is not None:
                updates.append("digest_time = ?")
                params.append(digest_time)
            if timezone is not None:
                updates.append("timezone = ?")
                params.append(timezone)
            
            if updates:
                params.append(chat_id)
                await db.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE chat_id = ?",
                    params
                )
        else:
            # Создаём нового пользователя с дефолтами
            await db.execute(
                "INSERT INTO users (chat_id, city, digest_time, timezone) VALUES (?, ?, ?, ?)",
                (chat_id, city, digest_time or "07:00", timezone or "Europe/Moscow")
            )
        
        await db.commit()


async def set_city(chat_id: int, city: str):
    await upsert_user(chat_id, city=city)


async def set_digest_time(chat_id: int, digest_time: str):
    await upsert_user(chat_id, digest_time=digest_time)


async def get_all_users_with_city() -> list[dict]:
    """Все пользователи у которых настроен город — для утренней сводки."""
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM users WHERE city IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_users() -> list[dict]:
    """Все пользователи — для рассылки сводки всем."""
    async with get_db() as db:
        async with db.execute("SELECT * FROM users") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
