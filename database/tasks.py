"""
CRUD-операции для задач (tasks).
"""

import datetime
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
) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO tasks (chat_id, title, text, type, category, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, title, text, task_type, category, priority),
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


async def get_tasks(chat_id: int, category: str = None, priority: str = None, exclude_type: str = None) -> list[dict]:
    query = "SELECT * FROM tasks WHERE chat_id = ? AND status = 'active'"
    params = [chat_id]

    # Исключаем определенный тип задач (например, "morning")
    if exclude_type:
        query += " AND type != ?"
        params.append(exclude_type)

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