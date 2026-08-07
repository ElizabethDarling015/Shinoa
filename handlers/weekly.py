"""
/week — создание еженедельного напоминания через inline-меню.
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import (
    get_nav_buttons, get_category_inline, get_priority_inline, get_days_inline,
    parse_time, priority_from_text, days_to_str
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None

def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


class NewWeek(StatesGroup):
    title = State()
    text = State()
    category = State()
    priority = State()
    days = State()
    time = State()


# ──────────────────────────────────────────────────────────
# Запуск /week
# ──────────────────────────────────────────────────────────

@router.message(Command("week"))
async def cmd_week(message: Message, state: FSMContext):
    await state.set_state(NewWeek.title)
    await state.update_data(selected_days=[]) # Инициализируем пустой список дней
    await message.answer(
        " <b>Еженедельное напоминание</b>\n\nВведите <b>название</b> задачи:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )


@router.message(NewWeek.title)
async def step_title(message: Message, state: FSMContext):
    if message.text.strip().lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]))
        return
    
    await state.update_data(title=message.text.strip())
    await state.set_state(NewWeek.text)
    await message.answer(
        "Введите <b>текст</b> напоминания:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )


@router.message(NewWeek.text)
async def step_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    await state.set_state(NewWeek.category)
    await message.answer(
        "Выберите <b>категорию</b>:",
        parse_mode="HTML",
        reply_markup=get_category_inline()
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery, state: FSMContext):
    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await state.set_state(NewWeek.priority)
    
    await call.message.edit_text(
        f"Категория <b>{category}</b> выбрана.\n\nТеперь выберите <b>приоритет</b>:",
        parse_mode="HTML",
        reply_markup=get_priority_inline()
    )
    await call.answer()


@router.callback_query(F.data.startswith("week_pri:"))
async def cb_priority(call: CallbackQuery, state: FSMContext):
    priority = call.data.split(":")[1]
    await state.update_data(priority=priority)
    await state.set_state(NewWeek.days)
    
    # Получаем текущие дни для отображения
    data = await state.get_data()
    current_days = data.get("selected_days", [])
    
    await call.message.edit_text(
        "📅 В какие <b>дни</b> присылать напоминание?\n(Нажимайте на дни, чтобы выбрать/снять)",
        parse_mode="HTML",
        reply_markup=get_days_inline(current_days)
    )
    await call.answer()


@router.callback_query(F.data.startswith("week_day:"))
async def cb_day_toggle(call: CallbackQuery, state: FSMContext):
    day_val = call.data.split(":")[1]
    data = await state.get_data()
    current_days = data.get("selected_days", [])
    
    if day_val == "all":
        current_days = [] # Пустой список = каждый день в нашей логике
    else:
        day_num = int(day_val)
        if day_num in current_days:
            current_days.remove(day_num)
        else:
            current_days.append(day_num)
            
    await state.update_data(selected_days=current_days)
    
    # Перерисовываем клавиатуру
    await call.message.edit_reply_markup(reply_markup=get_days_inline(current_days))
    await call.answer()


@router.callback_query(F.data == "week_days_next")
async def cb_days_next(call: CallbackQuery, state: FSMContext):
    await state.set_state(NewWeek.time)
    await call.message.edit_text(
        "🕐 В какое <b>время</b>? Формат ЧЧ:ММ\nПример: <code>09:00</code> или <code>18:30</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )
    await call.answer()


@router.message(NewWeek.time)
async def step_time(message: Message, state: FSMContext):
    t = parse_time(message.text)
    if not t:
        await message.answer(
            "Не понял время. Используйте формат ЧЧ:ММ, например <code>09:00</code>", 
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
        return

    data = await state.get_data()
    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"
    days: list[int] = data.get("selected_days", [])

    # Сохраняем задачу
    task_id = await db.create_task(
        chat_id=message.chat.id,
        title=data["title"],
        text=data["text"],
        task_type="weekly",
        category=data["category"],
        priority=data["priority"],
    )

    days_of_week = ",".join(str(d) for d in days) if days else None
    schedule_id = await db.add_schedule(
        task_id=task_id,
        time=time_str,
        days_of_week=days_of_week,
    )

    if _scheduler:
        schedule = {
            "id": schedule_id,
            "task_id": task_id,
            "time": time_str,
            "days_of_week": days_of_week,
            "task_type": "weekly",
            "chat_id": message.chat.id,
            "title": data["title"],
            "text": data["text"],
            "priority": data["priority"],
            "one_shot": False,
        }
        _scheduler.add_task_schedule(schedule)

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
        f"✅ <b>Напоминание создано!</b>\n\n"
        f" {data['title']}\n"
        f"📅 {days_to_str(days)}\n"
        f"🕐 {time_str}\n"
        f"🏷 {data['category']} · {db.PRIORITIES.get(data['priority'], 'Средне')}\n\n",
        parse_mode="HTML",
        reply_markup=success_kb
    )