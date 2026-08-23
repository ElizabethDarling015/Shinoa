"""
Утренняя сводка — отправляется каждый день в настроенное время (по умолчанию 07:00).

Содержимое:
  ☀️ Погода (если настроен город)
  📋 Задачи на сегодня (по приоритету)
  💪 Привычки на сегодня

Сводка закрепляется в чате; при следующей сводке предыдущая открепляется.
"""

import logging
from datetime import datetime, date

import pytz
from aiogram import Bot

from config import DEFAULT_TIMEZONE, WEATHER_API_KEY, HOST_IP_URL
from services.weather import get_weather
from database.connection import get_db

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


async def get_home_ip() -> str | None:
    """Запрашивает домашний внешний IP у сервера на хосте (ip_server)."""
    import aiohttp
    if not HOST_IP_URL:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(HOST_IP_URL) as resp:
                return (await resp.json()).get("wan")
    except Exception as e:
        logger.warning("Не удалось получить домашний IP для сводки: %s", e)
        return None


async def build_digest_text(chat_id: int, city: str = None) -> str:
    """
    Собирает текст утренней сводки. Используется и планировщиком,
    и кнопкой «🌅 Сегодняшняя сводка» в меню списка задач.
    """
    from database.tasks import get_tasks
    from database.habits import get_habits, is_done_today

    tz = pytz.timezone(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    today = date.today()
    weekday = WEEKDAY_RU[now.weekday()]

    lines = []

    # ── Заголовок
    lines.append(
        f"🌅 <b>Доброе утро!</b>\n"
        f"<i>{today.strftime('%d.%m.%Y')}, {weekday}</i>"
    )

    # ── Погода
    if city and WEATHER_API_KEY and WEATHER_API_KEY != "YOUR_WEATHER_API_KEY":
        weather = await get_weather(city, WEATHER_API_KEY)
        if weather:
            lines.append(f"\n{weather}")
    elif city:
        lines.append(f"\n🌡 <i>Погода недоступна — добавь WEATHER_API_KEY в config.py</i>")
        
    # ── Домашний внешний IP (под прогнозом) — выводится всегда
    home_ip = await get_home_ip()
    if home_ip:
        lines.append(f"\n🌍 <b>Домашний внешний IP:</b> <code>{home_ip}</code>")
    else:
        lines.append(f"\n🌍 <b>Домашний внешний IP:</b> <i>недоступен</i>")

    # ── Задачи на сегодня (разделяем на утренние и постоянные)
    tasks = await get_tasks(chat_id)
    
    morning_tasks = [t for t in tasks if t.get("type") == "morning"]
    regular_tasks = [t for t in tasks if t.get("type") != "morning"]

    if morning_tasks:
        lines.append("\n<b>🌅 Утренние задачи:</b>")
        for task in morning_tasks[:10]:
            p = PRIORITY_EMOJI.get(task["priority"], "🟡")
            lines.append(f"  {p} {task['title']}")
        if len(morning_tasks) > 10:
            lines.append(f"  <i>...и ещё {len(morning_tasks) - 10}</i>")

    if regular_tasks:
        lines.append("\n<b>📋 Постоянные задачи:</b>")
        for task in regular_tasks[:10]:
            p = PRIORITY_EMOJI.get(task["priority"], "🟡")
            lines.append(f"  {p} {task['title']}")
        if len(regular_tasks) > 10:
            lines.append(f"  <i>...и ещё {len(regular_tasks) - 10}. Смотри /list</i>")

    if not morning_tasks and not regular_tasks:
        lines.append("\n✨ <i>Задач на сегодня нет</i>")

    # ── Привычки
    habits = await get_habits(chat_id)
    pending_habits = []
    for h in habits:
        if not await is_done_today(h["id"]):
            pending_habits.append(h["name"])

    if pending_habits:
        lines.append("\n<b>💪 Привычки сегодня:</b>")
        for name in pending_habits:
            lines.append(f"  ⬜ {name}")

    # ── Подсказка
    lines.append("\n<i>Хорошего дня! 🚀</i>")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Хранение ID последней закреплённой сводки
# ──────────────────────────────────────────────

async def _get_last_digest_pin(chat_id: int) -> int | None:
    """ID сообщения последней закреплённой сводки (или None)."""
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT last_digest_msg_id FROM users WHERE chat_id = ?",
                (chat_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.warning("Не удалось прочитать ID старого пина: %s", e)
        return None


async def _set_last_digest_pin(chat_id: int, message_id: int):
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET last_digest_msg_id = ? WHERE chat_id = ?",
            (message_id, chat_id),
        )
        await db.commit()


# ──────────────────────────────────────────────
# Отправка
# ──────────────────────────────────────────────

async def send_digest(bot: Bot, chat_id: int, city: str = None):
    """Собирает и отправляет утреннюю сводку, закрепляя её (старая открепляется)."""
    text = await build_digest_text(chat_id, city)

    try:
        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        logger.info("Утренняя сводка отправлена → чат %s", chat_id)
    except Exception as e:
        logger.error("Ошибка отправки сводки → чат %s: %s", chat_id, e)
        return

    # ── Закрепление: открепляем прошлую, закрепляем новую
    try:
        old_id = await _get_last_digest_pin(chat_id)
        if old_id:
            try:
                await bot.unpin_chat_message(chat_id=chat_id, message_id=old_id)
            except Exception as e:
                logger.warning("Не удалось открепить старую сводку: %s", e)

        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=msg.message_id,
            disable_notification=True,  # без лишнего звука — сводка уже уведомление
        )
        await _set_last_digest_pin(chat_id, msg.message_id)
        logger.info("Сводка закреплена → чат %s", chat_id)
    except Exception as e:
        # Пин — не критичен: сводка уже доставлена
        logger.warning("Не удалось закрепить сводку: %s", e)


async def send_all_digests(bot: Bot):
    """
    Отправляет сводку всем пользователям.
    Вызывается планировщиком каждый день в digest_time.
    """
    from database.users import get_all_users

    users = await get_all_users()
    for user in users:
        await send_digest(bot, user["chat_id"], city=user.get("city"))