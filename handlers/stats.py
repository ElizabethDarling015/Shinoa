"""
/stats — статистика по задачам и привычкам.
"""

import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import database as db

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    chat_id = message.chat.id
    task_stats = await db.get_stats(chat_id)
    habits = await db.get_habits(chat_id)

    # ── Задачи
    lines = ["📊 <b>Статистика</b>\n"]

    lines.append(f"<b>Задачи</b>")
    lines.append(f"• Активных: {task_stats['active']}")
    lines.append(f"• Выполнено за неделю: {task_stats['done_week']}")

    if task_stats["top_categories"]:
        top = ", ".join(f"{r['category']} ({r['cnt']})" for r in task_stats["top_categories"])
        lines.append(f"• Топ категории: {top}")

    # ── Привычки
    if habits:
        lines.append(f"\n<b>Привычки</b>")
        for h in habits:
            streak = await db.get_streak(h["id"])
            week = await db.get_week_stats(h["id"])
            done_today = await db.is_done_today(h["id"])

            streak_text = f" 🔥{streak}" if streak > 1 else ""
            today_text = " ✅" if done_today else ""
            lines.append(
                f"• {h['name']}{today_text}{streak_text} — "
                f"{week['done']}/{h['target_per_week']} за неделю"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")
