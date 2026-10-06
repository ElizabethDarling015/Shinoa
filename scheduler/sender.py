"""
Отправка напоминаний с inline-кнопками (✅ Выполнено, ⏰ Отложить, ❌ Удалить).
"""

import logging
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import pytz

from config import DEFAULT_TIMEZONE
from database.tasks import get_task
from database.schedules import deactivate_schedule, mark_schedule_fired

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
    missed_at: str | None = None,
):
    """
    Отправляет напоминание с кнопками. Для one_shot деактивирует расписание.

    missed_at — если задано (строка вида "06.10 10:00"), это догоняющая
    отправка пропущенного срабатывания: в сообщение добавляется пометка.
    """
    # Защита: не напоминать о задаче, которую уже выполнили или удалили.
    # (Например, отложенное «через неделю» напоминание переживает удаление
    # задачи, пока бот не перезапущен.)
    task = await get_task(task_id)
    if not task or task.get("status") != "active":
        logger.info("Пропуск напоминания: задача %s неактивна или удалена", task_id)
        return

    now = datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime("%H:%M")
    p_emoji = PRIORITY_EMOJI.get(priority, "🟡")

    body = ""
    if text and str(text).strip() != (title or "").strip():
        body = f"\n\n{escape(str(text))}"
    missed_note = ""
    if missed_at:
        missed_note = f"\n⚠️ <i>Пропущено, пока бот был выключен (должно было прийти {missed_at})</i>"
    msg = (
        f"🔔 {p_emoji} <b>{escape(title)}</b>{body}\n\n"
        f"<i>{now}</i>{missed_note}"
    )

    try:
        await bot.send_message(
            chat_id, msg,
            parse_mode="HTML",
            reply_markup=task_keyboard(task_id, schedule_id),
        )
        logger.info(
            "Напоминание отправлено%s → чат %s: %s",
            " (догоняющее)" if missed_at else "", chat_id, title,
        )

        try:
            await mark_schedule_fired(schedule_id)
        except Exception as e:
            logger.warning("Не удалось записать last_fired_at для %s: %s", schedule_id, e)

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