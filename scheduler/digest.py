"""
Утренняя сводка — отправляется каждый день в настроенное время (по умолчанию 07:00).

Содержимое:
  ☀️ Погода (если настроен город)
  🔴 Просроченные задачи
  📋 Задачи на сегодня (по приоритету)
  💪 Привычки на сегодня
"""

import logging
from datetime import datetime, date

import pytz
from aiogram import Bot

from config import DEFAULT_TIMEZONE, WEATHER_API_KEY
from services.weather import get_weather

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


async def send_digest(bot: Bot, chat_id: int, city: str = None):
    """Собирает и отправляет утреннюю сводку для одного пользователя."""
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

    # ── Задачи на сегодня (по приоритету)
    tasks = await get_tasks(chat_id)
    if tasks:
        lines.append("\n<b>📋 Задачи:</b>")
        for task in tasks[:10]:  # не больше 10 чтобы не перегружать
            p = PRIORITY_EMOJI.get(task["priority"], "🟡")
            lines.append(f"  {p} {task['title']}")
        if len(tasks) > 10:
            lines.append(f"  <i>...и ещё {len(tasks) - 10}. Смотри /list</i>")
    else:
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

    text = "\n".join(lines)

    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
        logger.info("Утренняя сводка отправлена → чат %s", chat_id)
    except Exception as e:
        logger.error("Ошибка отправки сводки → чат %s: %s", chat_id, e)


async def send_all_digests(bot: Bot):
    """
    Отправляет сводку всем пользователям.
    Вызывается планировщиком каждый день в digest_time.
    """
    from database.users import get_all_users

    users = await get_all_users()
    for user in users:
        await send_digest(bot, user["chat_id"], city=user.get("city"))
