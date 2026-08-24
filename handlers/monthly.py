"""
/monthly — ежемесячные напоминания (inline-версия).
Два режима:
  - По числу определенного месяца (каждое 15-е число)
  - По дате (15 июня, каждый год)
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import (
    category_keyboard, priority_keyboard, remove_keyboard, get_nav_buttons,
    get_monthly_mode_keyboard, get_category_inline,
    parse_time, priority_from_text,
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None

def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler

MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTH_NAMES = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

class NewMonthly(StatesGroup):
    mode     = State()
    title    = State()
    text     = State()
    category = State()
    priority = State()
    day      = State()
    month    = State()
    time     = State()

# ──────────────────────────────────────────────────────────
# Запуск /monthly
# ──────────────────────────────────────────────────────────

@router.message(Command("monthly"))
async def cmd_monthly(message: Message, state: FSMContext):
    await state.set_state(NewMonthly.mode)
    await message.answer(
        "📅 <b>Ежемесячное напоминание</b>\n\n"
        "Выберите режим:\n"
        "• <b>По числу определенного месяца</b> — например, каждое 15-е число\n"
        "• <b>По дате</b> — например, 15 июня каждый год",
        parse_mode="HTML",
        reply_markup=get_monthly_mode_keyboard(),
    )

# ──────────────────────────────────────────────────────────
# Обработка inline-кнопок выбора режима
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "monthly_mode_day")
async def cb_mode_day(call: CallbackQuery, state: FSMContext):
    await state.update_data(mode="day")
    await state.set_state(NewMonthly.title)
    try:
        await call.message.edit_text(
            "📅 <b>Режим: По числу определенного месяца</b>\n\n"
            "Введите <b>название</b> напоминания:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
    await call.answer()

@router.callback_query(F.data == "monthly_mode_date")
async def cb_mode_date(call: CallbackQuery, state: FSMContext):
    await state.update_data(mode="date")
    await state.set_state(NewMonthly.title)
    try:
        await call.message.edit_text(
            "📆 <b>Режим: По дате (число + месяц)</b>\n\n"
            "Введите <b>название</b> напоминания:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
    await call.answer()

# ──────────────────────────────────────────────────────────
# Шаги FSM
# ─────────────────────────────────────────────────────────

@router.message(NewMonthly.title)
async def step_title(message: Message, state: FSMContext):
    if message.text.strip().lower() in ("отмена", "cancel"):
        await _cancel(message, state)
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(NewMonthly.text)
    await message.answer(
        "Введите <b>текст</b> напоминания:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )

@router.message(NewMonthly.text)
async def step_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    await state.set_state(NewMonthly.category)
    await message.answer(
        "Выберите <b>категорию</b>:",
        parse_mode="HTML",
        reply_markup=get_category_inline()
    )

@router.callback_query(F.data.startswith("cat:"))
async def cb_category_monthly(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора категории для /monthly"""
    # Защита от старых кнопок
    data = await state.get_data()
    if not data.get("title") or not data.get("text"):
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /monthly", show_alert=True)
        await state.clear()
        return

    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await state.set_state(NewMonthly.priority)
    
    try:
        await call.message.edit_text(
            f"Категория <b>{category}</b> выбрана.\n\nТеперь выберите <b>приоритет</b>:",
            parse_mode="HTML",
            reply_markup=priority_keyboard()
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
    
    await call.answer()

@router.message(NewMonthly.priority)
async def step_priority(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Отмена":
        await _cancel(message, state)
        return
    priority = priority_from_text(text)
    if not priority:
        await message.answer("Выберите приоритет из списка.")
        return
    await state.update_data(priority=priority)
    await state.set_state(NewMonthly.day)
    await message.answer(
        "Введите <b>число месяца</b> (1–31):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )

@router.message(NewMonthly.day)
async def step_day(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 31:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите число от 1 до 31.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
        return

    await state.update_data(day=day)
    data = await state.get_data()

    warning = ""
    if day > 28:
        warning = f"\n\n⚠️ В некоторых месяцах нет {day}-го числа — в такие месяцы напоминание не придёт."

    if data.get("mode") == "date":
        await state.set_state(NewMonthly.month)
        await message.answer(
            f"Введите <b>месяц</b> (например: июнь, июня, 6):{warning}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
    else:
        await state.set_state(NewMonthly.time)
        await message.answer(
            f"В какое <b>время</b>? Формат ЧЧ:ММ{warning}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )

@router.message(NewMonthly.month)
async def step_month(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    
    month_num = MONTHS.get(text)
    
    if not month_num:
        try:
            month_num = int(text)
            if not 1 <= month_num <= 12:
                raise ValueError
        except ValueError:
            await message.answer(
                "Не понял месяц. Введите название (июнь) или число (6).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
            )
            return

    await state.update_data(month=month_num)
    await state.set_state(NewMonthly.time)
    await message.answer(
        "В какое <b>время</b>? Формат ЧЧ:ММ\nПример: <code>10:00</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )

@router.message(NewMonthly.time)
async def step_time(message: Message, state: FSMContext):
    t = parse_time(message.text)
    if not t:
        await message.answer(
            "Не понял время. Формат: <code>10:00</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
        return

    data = await state.get_data()
    
    # Защита от отсутствия данных
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")
    priority = data.get("priority")
    day = data.get("day")
    mode = data.get("mode")
    
    if not title or not text or not category or not priority or not day or not mode:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    task_type = "monthly_day" if mode == "day" else "monthly_date"

    task_id = await db.create_task(
        chat_id=message.chat.id,
        title=title,
        text=text,
        task_type=task_type,
        category=category,
        priority=priority,
    )

    schedule_id = await db.add_schedule(
        task_id=task_id,
        time=time_str,
        day_of_month=day,
        month=data.get("month"),
    )

    if _scheduler:
        schedule = {
            "id": schedule_id,
            "task_id": task_id,
            "time": time_str,
            "task_type": task_type,
            "day_of_month": day,
            "month": data.get("month"),
            "chat_id": message.chat.id,
            "title": title,
            "text": text,
            "priority": priority,
            "one_shot": False,
        }
        _scheduler.add_task_schedule(schedule)

    if mode == "day":
        schedule_desc = f"каждое {day}-е число в {time_str}"
    else:
        schedule_desc = f"каждый год {day} {MONTH_NAMES[data['month']].lower()} в {time_str}"

    await state.clear()
    
    success_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад к выбору типа", callback_data="task_type_menu"),
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ],
        [
            InlineKeyboardButton(text="📋 Посмотреть все", callback_data="start_list"),
        ]
    ])
    
    await message.answer(
        f"✅ <b>Ежемесячное напоминание создано!</b>\n\n"
        f" {title}\n"
        f"📅 {schedule_desc}\n"
        f"🏷 {category} · {db.PRIORITIES[priority]}\n\n",
        parse_mode="HTML",
        reply_markup=success_kb
    )

async def _cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🙄Ладно, забудем.🥱",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )