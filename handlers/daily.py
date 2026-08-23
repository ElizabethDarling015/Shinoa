"""
/daily  — ежедневное напоминание (повторяется каждый день до удаления)
/morning — одноразовая задача, которая придёт завтра утром и самоудалится
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import (
    category_keyboard, priority_keyboard, remove_keyboard, get_nav_buttons, get_category_inline,
    parse_time, priority_from_text,
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


# ──────────────────────────────────────────────────────────
# /daily — ежедневная задача
# ──────────────────────────────────────────────────────────

class NewDaily(StatesGroup):
    title    = State()
    text     = State()
    category = State()
    priority = State()
    time     = State()


@router.message(Command("daily"))
async def cmd_daily(message: Message, state: FSMContext):
    await state.set_state(NewDaily.title)
    await message.answer(
        "📋 <b>Новая ежедневная задача</b>\n\n"
        "Буду напоминать каждый день в заданное время.\n\n"
        "Введите <b>название</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )


@router.message(NewDaily.title)
async def daily_title(message: Message, state: FSMContext):
    if message.text.strip() == "❌ Отмена":
        await _cancel(message, state)
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(NewDaily.text)
    await message.answer(
        "Введите <b>текст</b> напоминания:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )


@router.message(NewDaily.text)
async def daily_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    await state.set_state(NewDaily.category)
    await message.answer("Выберите <b>категорию</b>:", parse_mode="HTML", reply_markup=get_category_inline())


@router.callback_query(F.data.startswith("cat:"))
async def cb_category_daily(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора категории для /daily"""
    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await state.set_state(NewDaily.priority)
    
    await call.message.edit_text(
        f"Категория <b>{category}</b> выбрана.\n\nТеперь выберите <b>приоритет</b>:",
        parse_mode="HTML",
        reply_markup=priority_keyboard()
    )
    await call.answer()


@router.message(NewDaily.priority)
async def daily_priority(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Отмена":
        await _cancel(message, state)
        return
    priority = priority_from_text(text)
    if not priority:
        await message.answer("Выберите приоритет из списка.")
        return
    await state.update_data(priority=priority)
    await state.set_state(NewDaily.time)
    await message.answer(
        "В какое <b>время</b> каждый день? Формат ЧЧ:ММ\n"
        "Пример: <code>09:00</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )


@router.message(NewDaily.time)
async def daily_time(message: Message, state: FSMContext):
    t = parse_time(message.text)
    if not t:
        await message.answer(
            "Не понял время. Формат: <code>09:00</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
        )
        return

    data = await state.get_data()
    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    task_id = await db.create_task(
        chat_id=message.chat.id,
        title=data["title"],
        text=data["text"],
        task_type="daily",
        category=data["category"],
        priority=data["priority"],
    )

    schedule_id = await db.add_schedule(task_id=task_id, time=time_str)

    if _scheduler:
        schedule = {
            "id": schedule_id,
            "task_id": task_id,
            "time": time_str,
            "task_type": "daily",
            "chat_id": message.chat.id,
            "title": data["title"],
            "text": data["text"],
            "priority": data["priority"],
            "one_shot": False,
        }
        _scheduler.add_task_schedule(schedule)

    await state.clear()
    await message.answer(
        f"✅ <b>Ежедневная задача создана!</b>\n\n"
        f"📌 {data['title']}\n"
        f"🕐 Каждый день в {time_str}\n"
        f"🏷 {data['category']} · {db.PRIORITIES[data['priority']]}\n\n"
        f"Удалить задачу: /list",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )


# ──────────────────────────────────────────────────────────
# /morning — одноразовая задача на завтра утром
# ──────────────────────────────────────────────────────────

class NewMorning(StatesGroup):
    text = State()
    time = State()
    priority = State()


def get_morning_priority_keyboard() -> InlineKeyboardMarkup:
    """Inline-клавиатура для выбора приоритета в /morning (4 кнопки в 1 ряд)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Тир-1", callback_data="morning_pri:high"),
            InlineKeyboardButton(text="🟡 Тир-2", callback_data="morning_pri:medium"),
            InlineKeyboardButton(text="🟢 Тир-3", callback_data="morning_pri:low"),
            InlineKeyboardButton(text="⬅️ Отмена", callback_data="start_new"),
        ]
    ])


def get_morning_time_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для этапа ввода времени в /morning"""
    nav = get_nav_buttons()
    # get_nav_buttons возвращает список списков. Аккуратно добавляем его вниз,
    # а сверху размещаем растянутую кнопку (одна кнопка в ряду = растянута).
    bottom_rows = nav if isinstance(nav, list) and len(nav) > 0 and isinstance(nav[0], list) else [nav]
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ Стандартное (10:00)", callback_data="morning_time:10:00")]
    ] + bottom_rows)


@router.message(Command("morning"))
async def cmd_morning(message: Message, state: FSMContext):
    await state.set_state(NewMorning.text)
    
    # Отправляем сообщение и СРАЗУ сохраняем его ID для будущего редактирования
    bot_msg = await message.answer(
        "🌅 <b>Задача на завтра утром</b>\n\n"
        "Напишу тебе завтра в нужное время и задача исчезнет.\n\n"
        "Что нужно сделать?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )
    await state.update_data(bot_msg_id=bot_msg.message_id)


@router.message(NewMorning.text)
async def morning_text(message: Message, state: FSMContext):
    if message.text.strip() in ("❌ Отмена", "Отмена", "cancel"):
        await _cancel(message, state)
        return
    
    await state.update_data(text=message.text.strip())
    await state.set_state(NewMorning.time)
    
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    
    question_text = (
        "В какое время завтра напомнить?\n\n"
        "Вы можете ввести время вручную (например, <code>08:30</code>)\n"
        "или нажать кнопку «Стандартное» ниже."
    )
    
    # Пытаемся отредактировать первое сообщение
    if bot_msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_msg_id,
                text=question_text,
                parse_mode="HTML",
                reply_markup=get_morning_time_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")
            # Если редактирование не вышло, отправляем новое (страховка)
            await message.answer(question_text, parse_mode="HTML", reply_markup=get_morning_time_keyboard())
    else:
        await message.answer(question_text, parse_mode="HTML", reply_markup=get_morning_time_keyboard())


@router.message(NewMorning.time)
async def morning_time(message: Message, state: FSMContext):
    if message.text.strip() in ("❌ Отмена", "Отмена", "cancel"):
        await _cancel(message, state)
        return

    t = parse_time(message.text)
    if not t:
        await message.answer(
            "Не понял время. Формат: <code>10:00</code>",
            parse_mode="HTML",
            reply_markup=remove_keyboard()
        )
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"
    await state.update_data(time=time_str)
    
    # Получаем message_id предыдущего сообщения бота
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    
    await state.set_state(NewMorning.priority)
    
    # Редактируем сообщение бота вместо отправки нового
    if bot_msg_id:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text="Выберите <b>приоритет</b> напоминания:",
            parse_mode="HTML",
            reply_markup=get_morning_priority_keyboard()
        )


@router.callback_query(F.data=="morning_time:10:00")
async def cb_morning_time_standard(call: CallbackQuery, state: FSMContext):
    """Обработчик нажатия на кнопку 'Стандартное (10:00)'"""
    await call.answer()
    time_str = "10:00"
    await state.update_data(time=time_str)
    
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    await state.set_state(NewMorning.priority)
    
    if bot_msg_id:
        try:
            await call.bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=bot_msg_id,
                text="Выберите <b>приоритет</b> напоминания:",
                parse_mode="HTML",
                reply_markup=get_morning_priority_keyboard()
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")
            # Страховка: если редактирование не прошло, отправляем новое сообщение с кнопками
            await call.message.answer(
                "Выберите <b>приоритет</b> напоминания:",
                parse_mode="HTML",
                reply_markup=get_morning_priority_keyboard()
            )
    else:
        await call.message.answer(
            "Выберите <b>приоритет</b> напоминания:",
            parse_mode="HTML",
            reply_markup=get_morning_priority_keyboard()
        )


@router.callback_query(F.data.startswith("morning_pri:"))
async def cb_morning_priority(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора приоритета для /morning через inline-кнопки"""
    priority = call.data.split(":")[1]
    data = await state.get_data()
    time_str = data.get("time")

    # Для morning название = текст задачи (укорочённый)
    title = data["text"][:50] + ("…" if len(data["text"]) > 50 else "")

    task_id = await db.create_task(
        chat_id=call.message.chat.id,
        title=title,
        text=data["text"],
        task_type="morning",
        category="личное",
        priority=priority,
    )

    schedule_id = await db.add_schedule(
        task_id=task_id,
        time=time_str,
        one_shot=True,
    )

    if _scheduler:
        schedule = {
            "id": schedule_id,
            "task_id": task_id,
            "time": time_str,
            "task_type": "morning",
            "chat_id": call.message.chat.id,
            "title": "🌅 Задача на завтра",
            "text": data["text"],
            "priority": priority,
            "one_shot": True,
        }
        _scheduler.add_task_schedule(schedule)

    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Запомнил!</b>\n\n"
        f"Напомню завтра в <b>{time_str}</b>:\n"
        f"<i>{data['text']}</i>\n"
        f"🏷 Приоритет: {db.PRIORITIES[priority]}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )
    await call.answer()


async def _cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()])
    )