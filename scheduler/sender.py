"""
Отправка напоминаний с inline-кнопками (✅ Выполнено, ⏰ Отложить, ❌ Удалить).
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.schedules import deactivate_schedule

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def task_keyboard(task_id: int, schedule_id: int) -> InlineKeyboardMarkup:
    """Inline-кнопки под напоминанием."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done:{task_id}"),
            InlineKeyboardButton(text="⏰ Отложить", callback_data=f"snooze_menu:{task_id}:{schedule_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Удалить задачу", callback_data=f"delete_task:{task_id}:{schedule_id}"),
        ],
    ])


def snooze_keyboard(task_id: int, schedule_id: int) -> InlineKeyboardMarkup:
    """Кнопки выбора времени откладывания."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏰ Через 1 час", callback_data=f"snooze:1h:{task_id}:{schedule_id}"),
            InlineKeyboardButton(text="🌙 Вечером (20:00)", callback_data=f"snooze:evening:{task_id}:{schedule_id}"),
        ],
        [
            InlineKeyboardButton(text="📅 Завтра утром", callback_data=f"snooze:tomorrow:{task_id}:{schedule_id}"),
            InlineKeyboardButton(text="📆 Через неделю", callback_data=f"snooze:week:{task_id}:{schedule_id}"),
        ],
    ])


async def send_reminder(
    bot: Bot,
    chat_id: int,
    task_id: int,
    schedule_id: int,
    title: str,
    text: str,
    priority: str = "medium",
    one_shot: bool = False,
):
    """Отправляет напоминание с кнопками. Для one_shot деактивирует расписание."""
    now = datetime.now().strftime("%H:%M")
    p_emoji = PRIORITY_EMOJI.get(priority, "🟡")

    msg = (
        f"🔔 {p_emoji} <b>{title}</b>\n\n"
        f"{text}\n\n"
        f"<i>{now}</i>"
    )

    try:
        await bot.send_message(
            chat_id, msg,
            parse_mode="HTML",
            reply_markup=task_keyboard(task_id, schedule_id),
        )
        logger.info("Напоминание отправлено → чат %s: %s", chat_id, title)

        if one_shot:
            await deactivate_schedule(schedule_id)

    except Exception as e:
        logger.error("Ошибка отправки → чат %s: %s", chat_id, e)

def _pre_days_label(days: int) -> str:
    """
    Красивая подпись для предварительного напоминания.
    """
    labels = {
        7: "7 дней",
        3: "3 дня",
        1: "сутки",
    }
    return labels.get(days, f"{days} дн.")


async def send_yearly_pre_reminder(
    bot: Bot,
    chat_id: int,
    title: str,
    text: str,
    priority: str = "medium",
    days_left: int = 7,
    occurrence_date: str = "",
):
    """
    Отправляет предварительное уведомление к годовому напоминанию.
    Например, за 7 дней / 3 дня / сутки до основной даты.
    """
    p_emoji = PRIORITY_EMOJI.get(priority, "🟡")
    label = _pre_days_label(days_left)

    msg = (
        f"⏳ <b>Предварительное напоминание</b>\n\n"
        f"Через {label} ({occurrence_date}) будет годовое напоминание:\n"
        f"{p_emoji} <b>{title}</b>\n\n"
        f"{text}"
    )

    try:
        await bot.send_message(
            chat_id,
            msg,
            parse_mode="HTML",
        )
        logger.info(
            "Предварительное напоминание отправлено → чат %s: за %s",
            chat_id,
            label,
        )
    except Exception as e:
        logger.error(
            "Ошибка предварительного напоминания → чат %s: %s",
            chat_id,
            e,
        )