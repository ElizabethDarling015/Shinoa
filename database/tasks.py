"""
CRUD-операции для задач (tasks).
"""

import datetime
from datetime import date, timedelta

import pytz

from database.connection import get_db

CATEGORIES = ["работа", "личное", "финансы", "здоровье"]
PRIORITIES = {"high": "🔴 Срочно", "medium": "🟡 Средне", "low": "🟢 Когда-нибудь"}
TYPES = ["weekly", "monthly_day", "monthly_date", "daily", "morning", "interval", "workdays"]


async def create_task(
    chat_id: int,
    title: str,
    text: str,
    task_type: str,
    category: str = "личное",
    priority: str = "medium",
    due_date: str | None = None,
) -> int:
    """due_date — локальная дата 'YYYY-MM-DD' для задач «на день» (Сегодня/Завтра)."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO tasks (chat_id, title, text, type, category, priority, due_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, title, text, task_type, category, priority, due_date),
        )
        await db.commit()
        return cursor.lastrowid


async def get_task(task_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM tasks WHERE id = ? AND status != 'deleted'", (task_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_tasks(chat_id: int, category: str = None, priority: str = None, exclude_type: str = None, task_type: str = None) -> list[dict]:
    query = "SELECT * FROM tasks WHERE chat_id = ? AND status = 'active'"
    params = [chat_id]

    if exclude_type:
        query += " AND type != ?"
        params.append(exclude_type)

    # НОВОЕ: Фильтрация по конкретному типу задачи
    if task_type:
        query += " AND type = ?"
        params.append(task_type)

    if category:
        query += " AND category = ?"
        params.append(category)
    if priority:
        query += " AND priority = ?"
        params.append(priority)

    query += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"

    async with get_db() as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def complete_task(task_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            """
            UPDATE tasks SET status = 'done', completed_at = datetime('now')
            WHERE id = ? AND chat_id = ?
            """,
            (task_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_task(task_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ? AND chat_id = ?",
            (task_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_stats(chat_id: int) -> dict:
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND status = 'active'", (chat_id,)
        ) as cur:
            active = (await cur.fetchone())[0]

        async with db.execute(
            """
            SELECT COUNT(*) FROM tasks
            WHERE chat_id = ? AND status = 'done'
            AND completed_at >= datetime('now', '-7 days')
            """,
            (chat_id,),
        ) as cur:
            done_week = (await cur.fetchone())[0]

        async with db.execute(
            """
            SELECT category, COUNT(*) as cnt FROM tasks
            WHERE chat_id = ? AND status = 'done'
            GROUP BY category ORDER BY cnt DESC LIMIT 3
            """,
            (chat_id,),
        ) as cur:
            top_cats = [dict(r) for r in await cur.fetchall()]

    return {"active": active, "done_week": done_week, "top_categories": top_cats}


async def get_monthly_morning_tasks(chat_id: int) -> list[dict]:
    """
    Получает утренние задачи (type='morning').
    Сортируем по ID DESC (новые сверху), чтобы избежать ошибок, если колонки created_at нет.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM tasks
            WHERE chat_id = ? AND type = 'morning'
            ORDER BY id DESC
            LIMIT 30
            """,
            (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_todays_morning_tasks(chat_id: int, created_before: str, today: str) -> list[dict]:
    """
    Активные задачи «на день», относящиеся к сегодняшнему дню.

    today — сегодняшняя локальная дата 'YYYY-MM-DD'.
    created_before — 'YYYY-MM-DD HH:MM:SS' в UTC (полночь сегодня по
    локальному времени, переведённая в UTC).

    Новые задачи (с due_date) попадают сюда, если due_date == сегодня —
    в том числе созданные сегодня кнопкой «Сегодня».
    Старые задачи (due_date пустой) — по прежнему правилу: созданы раньше
    сегодняшней полуночи, то есть вчера или ранее.
    """
    query = """
        SELECT * FROM tasks
        WHERE chat_id = ?
          AND status = 'active'
          AND type = 'morning'
          AND (due_date = ? OR (due_date IS NULL AND created_at < ?))
        ORDER BY id DESC
    """
    async with get_db() as db:
        async with db.execute(query, (chat_id, today, created_before)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


def morning_due_date(task: dict, tz) -> date:
    """
    На какой локальный день приходится задача «на день».
    Новые задачи хранят его в due_date, у старых — «дата создания + 1».
    """
    due = task.get("due_date")
    if due:
        try:
            return datetime.datetime.strptime(str(due)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    try:
        created = datetime.datetime.strptime(str(task["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
        return pytz.UTC.localize(created).astimezone(tz).date() + timedelta(days=1)
    except (KeyError, ValueError):
        return datetime.datetime.now(tz).date()


async def get_pending_dated_morning_schedules(chat_id: int) -> list[dict]:
    """
    Ещё не отправленные напоминания новых задач «на день» (с due_date):
    они привязаны к времени утренней сводки и переносятся при его смене.
    """
    query = """
        SELECT s.*, t.chat_id, t.title, t.text, t.type AS task_type, t.priority,
               t.created_at AS task_created_at, t.due_date AS task_due_date
        FROM schedules s
        JOIN tasks t ON t.id = s.task_id
        WHERE t.chat_id = ?
          AND t.type = 'morning'
          AND t.status = 'active'
          AND t.due_date IS NOT NULL
          AND s.is_active = 1
    """
    async with get_db() as db:
        async with db.execute(query, (chat_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]