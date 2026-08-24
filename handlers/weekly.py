"""
/week — создание еженедельного напоминания через inline-меню.
Контейнерная модель: весь мастер проходит в одном редактируемом сообщении.
"""

import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import (
    get_nav_buttons,
    get_week_category_inline,
    get_priority_inline,
    get_days_inline,
    parse_time,
    days_to_str,
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None

CANCEL_WORDS = {"❌ отмена", "отмена", "cancel", "/cancel"}


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
# Вспомогательные функции контейнерной модели
# ──────────────────────────────────────────────────────────

def _make_nav_keyboard() -> list:
    nav = get_nav_buttons()
    return nav if isinstance(nav, list) and len(nav) > 0 and isinstance(nav[0], list) else [nav]


def _truncate_for_display(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


async def _get_week_container_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    return data.get("base_msg_id") or data.get("bot_msg_id")


async def _set_week_container_id(state: FSMContext, message_id: int):
    await state.update_data(bot_msg_id=message_id, base_msg_id=message_id)


def get_week_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def get_week_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def get_week_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def _week_text_question(title: str) -> str:
    return (
        "📆 <b>Еженедельное напоминание</b>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n\n"
        "Введите <b>текст</b> напоминания:"
    )


def _week_category_question(title: str, text: str) -> str:
    return (
        "📆 <b>Еженедельное напоминание</b>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Текст:</i> «{escape(_truncate_for_display(text))}»\n\n"
        "Выберите <b>категорию</b>:"
    )


def _week_priority_question(title: str, text: str, category: str) -> str:
    return (
        "📆 <b>Еженедельное напоминание</b>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Текст:</i> «{escape(_truncate_for_display(text))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b>\n\n"
        "Выберите <b>приоритет</b>:"
    )


def _week_days_question(title: str, text: str, category: str, priority: str) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    return (
        "📆 <b>Еженедельное напоминание</b>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b> · <i>Приоритет:</i> <b>{escape(priority_label)}</b>\n\n"
        "📅 В какие <b>дни</b> присылать напоминание?\n(Нажимайте на дни, чтобы выбрать/снять)"
    )


def _week_time_question(title: str, days_label: str) -> str:
    return (
        "📆 <b>Еженедельное напоминание</b>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Дни:</i> <b>{escape(days_label)}</b>\n\n"
        "🕐 В какое <b>время</b>? Формат ЧЧ:ММ\nПример: <code>09:00</code> или <code>18:30</code>"
    )


def _week_success_text(title: str, text: str, category: str, priority: str, days: list, time_str: str) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    return (
        "✅ <b>Напоминание создано!</b>\n\n"
        f"📌 {escape(title)}\n"
        f"📅 {escape(days_to_str(days))}\n"
        f"🕐 {escape(time_str)}\n"
        f"🏷 {escape(category)} · {escape(priority_label)}\n\n"
        f"<i>{escape(_truncate_for_display(text))}</i>"
    )


async def _edit_week_container(
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    fallback_message: Message | None = None,
) -> bool:
    """
    Пытается отредактировать сообщение-контейнер /week.
    Если контейнер не найден или его нельзя отредактировать,
    отправляет новое сообщение и сохраняет его ID как контейнер.
    """
    container_id = await _get_week_container_id(state)

    if container_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=container_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return True
            logger.warning(f"Не удалось отредактировать week-контейнер: {e}")

    if fallback_message:
        try:
            new_msg = await fallback_message.answer(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await _set_week_container_id(state, new_msg.message_id)
            return True
        except Exception as e:
            logger.warning(f"Не удалось отправить fallback week-сообщение: {e}")

    return False


async def _cancel_week_flow(
    message: Message | None,
    call: CallbackQuery | None,
    state: FSMContext,
):
    """Отмена создания еженедельной задачи."""
    container_id = await _get_week_container_id(state)

    chat_id = None
    bot = None

    if message:
        chat_id = message.chat.id
        bot = message.bot
    elif call and call.message:
        chat_id = call.message.chat.id
        bot = call.bot

    await state.clear()

    cancel_text = "🙄Ладно, забудем.🥱"
    cancel_kb = get_week_result_keyboard()

    if container_id and chat_id and bot:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=container_id,
                text=cancel_text,
                reply_markup=cancel_kb,
            )
            return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            logger.warning(f"Не удалось отредактировать week-контейнер при отмене: {e}")

    if message:
        try:
            await message.answer(cancel_text, reply_markup=cancel_kb)
        except Exception:
            pass
    elif call and call.message:
        try:
            await call.message.answer(cancel_text, reply_markup=cancel_kb)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────
# /week — команды и состояния
# ──────────────────────────────────────────────────────────

@router.message(Command("week"))
async def cmd_week(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewWeek.title)
    await state.update_data(selected_days=[])

    bot_msg = await message.answer(
        "📆 <b>Еженедельное напоминание</b>\n\nВведите <b>название</b> задачи:",
        parse_mode="HTML",
        reply_markup=get_week_start_keyboard(),
    )

    await _set_week_container_id(state, bot_msg.message_id)

    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewWeek.title)
async def step_title(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправь название текстом.")
        return

    user_text = message.text.strip()

    if user_text.lower() in CANCEL_WORDS:
        await _cancel_week_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    await state.update_data(title=user_text)
    await state.set_state(NewWeek.text)

    await _edit_week_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_week_text_question(user_text),
        reply_markup=get_week_start_keyboard(),
        fallback_message=message,
    )

    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewWeek.text)
async def step_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправь текст задачи.")
        return

    user_text = message.text.strip()

    if user_text.lower() in CANCEL_WORDS:
        await _cancel_week_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    title = data.get("title", "")

    if not title:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /week.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    await state.update_data(text=user_text)
    await state.set_state(NewWeek.category)

    await _edit_week_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_week_category_question(title, user_text),
        reply_markup=get_week_category_inline(),
        fallback_message=message,
    )

    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("week_cat:"))
async def cb_category(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора категории для /week"""
    if not call.message:
        await call.answer("Сообщение было удалено. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    title = data.get("title")
    text = data.get("text")

    if not title or not text:
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    category = call.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(NewWeek.priority)

    await call.answer()

    container_id = await _get_week_container_id(state)
    if not container_id:
        await _set_week_container_id(state, call.message.message_id)

    await _edit_week_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_week_priority_question(title, text, category),
        reply_markup=get_priority_inline(),
        fallback_message=call.message,
    )


@router.callback_query(F.data.startswith("week_pri:"))
async def cb_priority(call: CallbackQuery, state: FSMContext):
    if not call.message:
        await call.answer("Сообщение было удалено. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")

    if not title or not text or not category:
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    priority = call.data.split(":", 1)[1]
    await state.update_data(priority=priority)
    await state.set_state(NewWeek.days)

    await call.answer()

    container_id = await _get_week_container_id(state)
    if not container_id:
        await _set_week_container_id(state, call.message.message_id)

    current_days = data.get("selected_days", [])

    await _edit_week_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_week_days_question(title, text, category, priority),
        reply_markup=get_days_inline(current_days),
        fallback_message=call.message,
    )


@router.callback_query(F.data.startswith("week_day:"))
async def cb_day_toggle(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("title"):
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    day_val = call.data.split(":")[1]

    # Кнопка «Каждый день» осталась только на старых сообщениях в истории
    if day_val == "all":
        await call.answer("Кнопка «Каждый день» больше недоступна — выбери отдельные дни 🙂")
        return

    current_days = data.get("selected_days", [])

    day_num = int(day_val)
    if day_num in current_days:
        current_days.remove(day_num)
    else:
        current_days.append(day_num)

    await state.update_data(selected_days=current_days)

    try:
        await call.message.edit_reply_markup(reply_markup=get_days_inline(current_days))
    except Exception as e:
        logger.warning(f"Не удалось обновить клавиатуру дней: {e}")

    await call.answer()


@router.callback_query(F.data == "week_days_next")
async def cb_days_next(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("title") or not data.get("priority"):
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /week", show_alert=True)
        await state.clear()
        return

    # Требуем хотя бы один выбранный день
    if not data.get("selected_days"):
        await call.answer("⚠️ Сначала выбери хотя бы один день недели", show_alert=True)
        return

    await state.set_state(NewWeek.time)

    await call.answer()

    container_id = await _get_week_container_id(state)
    if not container_id:
        await _set_week_container_id(state, call.message.message_id)

    title = data.get("title", "")
    days_label = days_to_str(data.get("selected_days", []))

    await _edit_week_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_week_time_question(title, days_label),
        reply_markup=get_week_time_keyboard(),
        fallback_message=call.message,
    )


@router.message(NewWeek.time)
async def step_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Время нужно ввести текстом, например: <code>09:00</code>", parse_mode="HTML")
        return

    raw_text = message.text.strip()

    if raw_text.lower() in CANCEL_WORDS:
        await _cancel_week_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")
    priority = data.get("priority")

    if not title or not text or not category or not priority:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /week.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    t = parse_time(raw_text)

    if not t:
        days_label = days_to_str(data.get("selected_days", []))
        await _edit_week_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=_week_time_question(title, days_label) + "\n\n⚠️ Не понял время. Формат: <code>09:00</code>",
            reply_markup=get_week_time_keyboard(),
            fallback_message=message,
        )
        try:
            await message.delete()
        except Exception:
            pass
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"
    days: list[int] = data.get("selected_days", [])

    task_id = await db.create_task(
        chat_id=message.chat.id,
        title=title,
        text=text,
        task_type="weekly",
        category=category,
        priority=priority,
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
            "title": title,
            "text": text,
            "priority": priority,
            "one_shot": False,
        }
        _scheduler.add_task_schedule(schedule)

    container_id = await _get_week_container_id(state) or message.message_id

    await state.clear()

    success_text = _week_success_text(title, text, category, priority, days, time_str)
    success_kb = get_week_result_keyboard()

    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=container_id,
            text=success_text,
            parse_mode="HTML",
            reply_markup=success_kb,
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Не удалось отредактировать финальное week-сообщение: {e}")
            try:
                await message.answer(success_text, parse_mode="HTML", reply_markup=success_kb)
            except Exception as e2:
                logger.warning(f"Fallback week-сообщение не удалось: {e2}")

    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "cancel_week")
async def cb_cancel_week(call: CallbackQuery, state: FSMContext):
    """Обработчик отмены создания еженедельной задачи"""
    await _cancel_week_flow(None, call, state)
    await call.answer("Отменено")