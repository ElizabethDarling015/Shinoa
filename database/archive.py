"""
CRUD для личного архива (файлы, голосовые, идеи, фото).
"""

import json
from database.connection import get_db

ARCHIVE_TYPES = ["idea", "note", "voice", "photo", "document"]


async def save_item(
    chat_id: int,
    item_type: str,
    title: str = None,
    text: str = None,
    file_id: str = None,
    tags: list = None,
) -> int:
    """Сохраняет новый элемент в архив"""
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO archive_items (chat_id, type, title, text, file_id, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, item_type, title, text, file_id, tags_json),
        )
        await db.commit()
        return cursor.lastrowid


async def get_recent_items(chat_id: int, limit: int = 10) -> list[dict]:
    """Получает последние сохраненные элементы пользователя (для кнопки 'Все заметки')"""
    sql = """
        SELECT id, type, title, text, file_id, tags, created_at 
        FROM archive_items 
        WHERE chat_id = ? AND is_deleted = 0 
        ORDER BY id DESC 
        LIMIT ?
    """
    async with get_db() as db:
        async with db.execute(sql, (chat_id, limit)) as cur:
            rows = await cur.fetchall()
    
    results = []
    for row in rows:
        item = dict(row)
        # Преобразуем строку тегов JSON обратно в список Python
        try:
            item["tags"] = json.loads(item["tags"]) if item["tags"] else []
        except (json.JSONDecodeError, TypeError):
            item["tags"] = []
        results.append(item)
        
    return results


async def get_item_by_id(item_id: int, chat_id: int) -> dict | None:
    """Получает одну конкретную заметку по ID (для кнопки 'Показать' и ввода числа)"""
    sql = """
        SELECT id, type, title, text, file_id, tags, created_at 
        FROM archive_items 
        WHERE id = ? AND chat_id = ? AND is_deleted = 0
    """
    async with get_db() as db:
        async with db.execute(sql, (item_id, chat_id)) as cur:
            row = await cur.fetchone()
    
    if not row:
        return None
        
    item = dict(row)
    try:
        item["tags"] = json.loads(item["tags"]) if item["tags"] else []
    except (json.JSONDecodeError, TypeError):
        item["tags"] = []
    return item


async def search_items(chat_id: int, query: str = None, tags: list = None, item_type: str = None) -> list[dict]:
    """Поиск по архиву с фильтрацией"""
    sql = "SELECT * FROM archive_items WHERE chat_id = ? AND is_deleted = 0"
    params = [chat_id]

    if query:
        sql += " AND (title LIKE ? OR text LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    if item_type:
        sql += " AND type = ?"
        params.append(item_type)

    sql += " ORDER BY id DESC LIMIT 100"  # Увеличил лимит, чтобы фильтр по тегам в Python срабатывал корректно

    async with get_db() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()

    results = []
    for row in rows:
        item = dict(row)
        try:
            item["tags"] = json.loads(item["tags"]) if item["tags"] else []
        except (json.JSONDecodeError, TypeError):
            item["tags"] = []
            
        # Фильтр по тегам (в Python, т.к. теги хранятся как JSON-строка в SQLite)
        if tags:
            if not any(t in item["tags"] for t in tags):
                continue
                
        results.append(item)

    return results


async def delete_item(item_id: int, chat_id: int) -> bool:
    """Мягкое удаление элемента (помечает как is_deleted = 1)"""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE archive_items SET is_deleted = 1 WHERE id = ? AND chat_id = ?",
            (item_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0