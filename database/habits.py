"""
CRUD-операции для привычек и журнала их выполнения.
"""

from datetime import date
from database.connection import get_db

HABIT_CATEGORIES = ["здоровье", "спорт", "питание", "продуктивность", "обучение", "другое"]


async def create_habit(
    chat_id: int,
    name: str,
    category: str = "здоровье",
    reminder_time: str = "21:00",
    target_per_week: int = 7,
) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO habits (chat_id, name, category, reminder_time, target_per_week)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, name, category, reminder_time, target_per_week),
        )
        await db.commit()
        return cursor.lastrowid


async def get_habits(chat_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM habits WHERE chat_id = ? AND is_active = 1", (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_habit(habit_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM habits WHERE id = ? AND is_active = 1", (habit_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def delete_habit(habit_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE habits SET is_active = 0 WHERE id = ? AND chat_id = ?",
            (habit_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def log_habit(habit_id: int, today: date = None, note: str = None) -> bool:
    """Отмечает привычку выполненной на дату (по умолчанию сегодня)."""
    today = today or date.today()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO habit_logs (habit_id, date, completed, note)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(habit_id, date) DO UPDATE SET completed = 1, note = excluded.note
            """,
            (habit_id, today.isoformat(), note),
        )
        await db.commit()
    return True


async def get_streak(habit_id: int) -> int:
    """
    Считает текущий streak (дни подряд) на лету из habit_logs.
    Не храним как число — иначе ломается при правке прошлых записей.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT date, completed FROM habit_logs
            WHERE habit_id = ?
            ORDER BY date DESC
            """,
            (habit_id,),
        ) as cur:
            rows = await cur.fetchall()

    streak = 0
    check_date = date.today()

    for row in rows:
        log_date = date.fromisoformat(row["date"])
        if log_date == check_date and row["completed"]:
            streak += 1
            check_date = date.fromordinal(check_date.toordinal() - 1)
        elif log_date < check_date:
            break

    return streak


async def get_week_stats(habit_id: int) -> dict:
    """Сколько раз выполнено за последние 7 дней."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM habit_logs
            WHERE habit_id = ? AND completed = 1
            AND date >= date('now', '-7 days')
            """,
            (habit_id,),
        ) as cur:
            done = (await cur.fetchone())[0]
    return {"done": done}


async def is_done_today(habit_id: int) -> bool:
    async with get_db() as db:
        async with db.execute(
            "SELECT completed FROM habit_logs WHERE habit_id = ? AND date = ?",
            (habit_id, date.today().isoformat()),
        ) as cur:
            row = await cur.fetchone()
    return bool(row and row["completed"])


async def get_all_active_habits() -> list[dict]:
    """Все активные привычки — для планировщика."""
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM habits WHERE is_active = 1"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
