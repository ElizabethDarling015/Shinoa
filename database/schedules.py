"""
CRUD-операции для расписаний (schedules).
"""

from database.connection import get_db


async def add_schedule(
    task_id: int,
    time: str,
    days_of_week: str = None,
    day_of_month: int = None,
    month: int = None,
    interval_days: int = None,
    one_shot: bool = False,
) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO schedules
                (task_id, time, days_of_week, day_of_month, month, interval_days, one_shot)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, time, days_of_week, day_of_month, month, interval_days, int(one_shot)),
        )
        await db.commit()
        return cursor.lastrowid


async def get_schedules(task_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM schedules WHERE task_id = ? AND is_active = 1", (task_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def deactivate_schedule(schedule_id: int):
    """Деактивирует расписание (для one-shot задач после срабатывания)."""
    async with get_db() as db:
        await db.execute(
            "UPDATE schedules SET is_active = 0 WHERE id = ?", (schedule_id,)
        )
        await db.commit()


async def delete_schedules_for_task(task_id: int) -> list[int]:
    """Возвращает id расписаний перед удалением (нужно для планировщика)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM schedules WHERE task_id = ?", (task_id,)
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        await db.execute("DELETE FROM schedules WHERE task_id = ?", (task_id,))
        await db.commit()
    return ids


async def get_all_active_schedules() -> list[dict]:
    """
    Все активные расписания — используется планировщиком при старте.
    Возвращает расписания вместе с данными задачи.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT s.*, t.chat_id, t.title, t.text, t.type AS task_type, t.priority
            FROM schedules s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.is_active = 1 AND t.status = 'active'
            """
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
