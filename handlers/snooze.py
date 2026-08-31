"""
Обработка inline-кнопок под напоминаниями:
✅ Выполнено, ⏰ Отложить, ❌ Удалить задачу.
"""

import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from config import DEFAULT_TIMEZONE
from apscheduler.triggers.date import DateTrigger
from scheduler.sender import snooze_keyboard

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None

def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler

# ──────────────────────────────────────────────────────────
# ✅ Выполнено
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("done:"))
async def cb_done(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    task = await db.get_task(task_id)

    if not task or task["chat_id"] != call.message.chat.id:
        await call.answer("Задача не найдена.", show_alert=True)
        return

    if task["type"] == "morning":
        # НОВАЯ ЛОГИКА: morning-задача уходит в архив выполненных
        # (таблица completed_tasks — из неё собирается ежемесячный отчёт)
        from database.connection import get_db
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO completed_tasks
                    (original_task_id, chat_id, title, text, category, priority)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    call.message.chat.id,
                    task["title"],
                    task.get("text"),
                    task["category"],
                    task["priority"],
                ),
            )
            await conn.commit()

        schedule_ids = await db.delete_schedules_for_task(task_id)
        await db.delete_task(task_id, call.message.chat.id)
        if _scheduler:
            _scheduler.remove_all_for_task(schedule_ids)
    else:
        # Старая логика для постоянных задач (daily/weekly/monthly)
        await db.complete_task(task_id, call.message.chat.id)
        schedules = await db.get_schedules(task_id)
        if _scheduler:
            _scheduler.remove_all_for_task([s["id"] for s in schedules])

    await call.message.edit_text(
        call.message.text + "\n\n✅ <b>Выполнено!</b>",
        parse_mode="HTML",
        reply_markup=None,
    )
    await call.answer("Отлично! Задача выполнена 💪")

# ──────────────────────────────────────────────────────────
# ⏰ Меню откладывания
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("snooze_menu:"))
async def cb_snooze_menu(call: CallbackQuery):
    _, task_id, schedule_id = call.data.split(":")
    await call.message.edit_reply_markup(
        reply_markup=snooze_keyboard(int(task_id), int(schedule_id))
    )
    await call.answer()

# ──────────────────────────────────────────────────────────
# ⏰ Выбор времени откладывания
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("snooze:"))
async def cb_snooze(call: CallbackQuery):
    parts = call.data.split(":")
    # snooze:{period}:{task_id}:{schedule_id}
    period = parts[1]
    task_id = int(parts[2])
    schedule_id = int(parts[3])

    task = await db.get_task(task_id)
    if not task or task["chat_id"] != call.message.chat.id:
        await call.answer("Задача не найдена.", show_alert=True)
        return

    tz = pytz.timezone(DEFAULT_TIMEZONE)
    now = datetime.now(tz)

    if period == "1h":
        fire_at = now + timedelta(hours=1)
        label = "через 1 час"
    elif period == "evening":
        fire_at = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if fire_at <= now:
            fire_at += timedelta(days=1)
        label = "вечером в 20:00"
    elif period == "tomorrow":
        fire_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        label = "завтра в 09:00"
    elif period == "week":
        fire_at = now + timedelta(weeks=1)
        label = f"через неделю ({fire_at.strftime('%d.%m %H:%M')})"
    else:
        await call.answer("Неизвестный период.")
        return

    if _scheduler:
        trigger = DateTrigger(run_date=fire_at, timezone=tz)
        _scheduler.add_snooze_job(
            trigger=trigger,
            chat_id=call.message.chat.id,
            task_id=task_id,
            schedule_id=schedule_id,
            title=task["title"],
            text=task["text"],
            priority=task["priority"],
        )

    await call.message.edit_text(
        call.message.text + f"\n\n⏰ <i>Отложено: {label}</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    await call.answer(f"Напомню {label}")

# ──────────────────────────────────────────────────────────
# ❌ Удалить задачу
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("delete_task:"))
async def cb_delete_task(call: CallbackQuery):
    parts = call.data.split(":")
    task_id = int(parts[1])

    task = await db.get_task(task_id)
    if not task or task["chat_id"] != call.message.chat.id:
        await call.answer("Задача не найдена.", show_alert=True)
        return

    schedule_ids = await db.delete_schedules_for_task(task_id)
    await db.delete_task(task_id, call.message.chat.id)

    if _scheduler:
        _scheduler.remove_all_for_task(schedule_ids)

    await call.message.edit_text(
        call.message.text + "\n\n🗑 <i>Задача удалена.</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    await call.answer("Задача удалена.")

# ──────────────────────────────────────────────────────────
# ✅ Отметить привычку выполненной
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("habit_done:"))
async def cb_habit_done(call: CallbackQuery):
    habit_id = int(call.data.split(":")[1])
    habit = await db.get_habit(habit_id)

    if not habit or habit["chat_id"] != call.message.chat.id:
        await call.answer("Привычка не найдена.", show_alert=True)
        return

    await db.log_habit(habit_id)
    streak = await db.get_streak(habit_id)

    streak_text = f" 🔥 Серия: {streak} дн." if streak > 1 else ""
    await call.message.edit_text(
        call.message.text + f"\n\n✅ <b>Отмечено!</b>{streak_text}",
        parse_mode="HTML",
        reply_markup=None,
    )
    await call.answer(f"Отлично!{streak_text}")