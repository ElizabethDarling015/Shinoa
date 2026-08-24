"""
/daily  — ежедневное напоминание (повторяется каждый день до удаления)
/morning — одноразовая задача, которая придёт завтра утром и самоудалится
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
    category_keyboard,
    priority_keyboard,
    remove_keyboard,
    get_nav_buttons,
    get_category_inline,
    parse_time,
    priority_from_text,
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


def _make_nav_keyboard() -> list:
    """Гарантирует, что клавиатура навигации всегда является корректным двумерным списком."""
    nav = get_nav_buttons()
    return nav if isinstance(nav, list) and len(nav) > 0 and isinstance(nav[0], list) else [nav]


# ──────────────────────────────────────────────────────────
# /daily — ежедневная задача
# ──────────────────────────────────────────────────────────

class NewDaily(StatesGroup):
    title = State()
    text = State()
    category = State()
    priority = State()
    time = State()


@router.message(Command("daily"))
async def cmd_daily(message: Message, state: FSMContext):
    await state.set_state(NewDaily.title)
    await message.answer(
        "📋 <b>Новая ежедневная задача</b>\n\n"
        "Буду напоминать каждый день в заданное время.\n\n"
        "Введите <b>название</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())
    )


@router.message(NewDaily.text)
async def daily_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    await state.set_state(NewDaily.category)

    await message.answer(
        "Выберите <b>категорию</b>:",
        parse_mode="HTML",
        reply_markup=get_category_inline()
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category_daily(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора категории для /daily"""
    if not call.message:
        await call.answer("Сообщение было удалено. Начните заново через /daily", show_alert=True)
        await state.clear()
        return

    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await state.set_state(NewDaily.priority)

    text = f"Категория <b>{category}</b> выбрана.\n\nТеперь выберите <b>приоритет</b>:"

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=priority_keyboard()
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение категории: {e}")
        await call.message.answer(
            text,
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
        await message.answer(
            "Выберите приоритет из списка:",
            reply_markup=priority_keyboard()
        )
        return

    await state.update_data(priority=priority)
    await state.set_state(NewDaily.time)

    await message.answer(
        "В какое <b>время</b> каждый день? Формат ЧЧ:ММ\n"
        "Пример: <code>09:00</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
    )


@router.message(NewDaily.time)
async def daily_time(message: Message, state: FSMContext):
    t = parse_time(message.text)

    if not t:
        await message.answer(
            "Не поняла время.🙄 Формат: <code>09:00</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
    )


# ──────────────────────────────────────────────────────────
# /morning — одноразовая задача на завтра утром
# ──────────────────────────────────────────────────────────

class NewMorning(StatesGroup):
    text = State()
    time = State()
    priority = State()


# ──────────────────────────────────────────────────────────
# Вспомогательные функции для /morning
# ──────────────────────────────────────────────────────────

def _truncate_for_display(text: str, limit: int = 500) -> str:
    """Обрезает длинный текст для отображения в сообщении."""
    return text if len(text) <= limit else text[:limit] + "…"


async def _get_morning_container_id(state: FSMContext) -> int | None:
    """
    Возвращает ID сообщения-контейнера для /morning.

    Поддерживает два ключа:
    - bot_msg_id — старый ключ из твоего кода;
    - base_msg_id — ключ из моего патча для меню.
    """
    data = await state.get_data()
    return data.get("base_msg_id") or data.get("bot_msg_id")


async def _set_morning_container_id(state: FSMContext, message_id: int):
    """Сохраняет ID сообщения-контейнера сразу под два ключа."""
    await state.update_data(bot_msg_id=message_id, base_msg_id=message_id)


def get_morning_start_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для первого шага /morning."""
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def get_morning_time_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для этапа ввода времени в /morning."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Стандартное (10:00)", callback_data="morning_time:10:00")],
            *_make_nav_keyboard(),
        ]
    )


def get_morning_priority_keyboard() -> InlineKeyboardMarkup:
    """Inline-клавиатура для выбора приоритета в /morning."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔴 Тир-1", callback_data="morning_pri:high"),
                InlineKeyboardButton(text="🟡 Тир-2", callback_data="morning_pri:medium"),
                InlineKeyboardButton(text="🟢 Тир-3", callback_data="morning_pri:low"),
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="cancel_morning")],
        ]
    )


def get_morning_result_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после успешного сохранения или отмены."""
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def _morning_time_question(task_text: str, error: str | None = None) -> str:
    """Текст для этапа выбора времени."""
    text = "🌅 <b>Задача на завтра утром</b>\n\n"
    text += f"<i>Что нужно сделать:</i>\n«{escape(_truncate_for_display(task_text))}»\n\n"

    if error:
        text += f"{error}\n\n"

    text += (
        "В какое время завтра напомнить?\n\n"
        "Вы можете ввести время вручную (например, <code>08:30</code>)\n"
        "или нажать кнопку «Стандартное» ниже."
    )

    return text


def _morning_priority_question(task_text: str, time_str: str) -> str:
    """Текст для этапа выбора приоритета."""
    return (
        "🌅 <b>Задача на завтра утром</b>\n\n"
        f"<i>Что делаем:</i> «{escape(_truncate_for_display(task_text))}»\n"
        f"<i>Время напоминания:</i> <b>{escape(time_str)}</b>\n\n"
        "Выберите <b>приоритет</b> напоминания:"
    )


def _morning_success_text(task_text: str, time_str: str, priority: str) -> str:
    """Текст после успешного сохранения задачи."""
    priority_label = db.PRIORITIES.get(priority, priority)

    return (
        "✅ <b>Запомнила!😌</b>\n\n"
        f"Напомню завтра в <b>{escape(time_str)}</b>:\n"
        f"<i>{escape(_truncate_for_display(task_text))}</i>\n"
        f"🏷 Приоритет: {escape(priority_label)}"
    )


async def _edit_morning_container(
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    fallback_message: Message | None = None,
) -> bool:
    """
    Пытается отредактировать сообщение-контейнер /morning.

    Если контейнер не найден или его нельзя отредактировать,
    отправляет новое сообщение и сохраняет его ID как контейнер.
    """
    container_id = await _get_morning_container_id(state)

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
            logger.warning(f"Не удалось отредактировать morning-контейнер: {e}")

    if fallback_message:
        try:
            new_msg = await fallback_message.answer(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await _set_morning_container_id(state, new_msg.message_id)
            return True
        except Exception as e:
            logger.warning(f"Не удалось отправить fallback morning-сообщение: {e}")

    return False


async def _cancel_morning_flow(
    message: Message | None,
    call: CallbackQuery | None,
    state: FSMContext,
):
    """
    Отмена создания утренней задачи.

    По возможности редактирует сообщение-контейнер,
    иначе отправляет новое сообщение.
    """
    container_id = await _get_morning_container_id(state)

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
    cancel_kb = get_morning_result_keyboard()

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
            logger.warning(f"Не удалось отредактировать morning-контейнер при отмене: {e}")

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
# /morning — команды и состояния
# ──────────────────────────────────────────────────────────

@router.message(Command("morning"))
async def cmd_morning(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewMorning.text)

    bot_msg = await message.answer(
        "🌅 <b>Задача на завтра утром</b>\n\n"
        "Напишу тебе завтра в нужное время и задача исчезнет.\n\n"
        "Что нужно сделать?",
        parse_mode="HTML",
        reply_markup=get_morning_start_keyboard(),
    )

    await _set_morning_container_id(state, bot_msg.message_id)

    # По возможности удаляем команду пользователя, чтобы не засорять чат.
    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewMorning.text)
async def morning_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            "Пожалуйста, отправь задачу текстом.",
            reply_markup=remove_keyboard()
        )
        return

    user_text = message.text.strip()

    if user_text.lower() in {"❌ отмена", "отмена", "cancel", "/cancel"}:
        await _cancel_morning_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    await state.update_data(text=user_text)
    await state.set_state(NewMorning.time)

    await _edit_morning_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_morning_time_question(user_text),
        reply_markup=get_morning_time_keyboard(),
        fallback_message=message,
    )

    # Удаляем сообщение пользователя с текстом задачи.
    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewMorning.time)
async def morning_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            "Время нужно ввести текстом, например: <code>10:00</code>",
            parse_mode="HTML",
        )
        return

    raw_text = message.text.strip()

    if raw_text.lower() in {"❌ отмена", "отмена", "cancel", "/cancel"}:
        await _cancel_morning_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    task_text = data.get("text", "")

    if not task_text:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /morning.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    t = parse_time(raw_text)

    if not t:
        await _edit_morning_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=_morning_time_question(
                task_text,
                "⚠️ Не поняла время. Формат: <code>10:00</code>"
            ),
            reply_markup=get_morning_time_keyboard(),
            fallback_message=message,
        )

        try:
            await message.delete()
        except Exception:
            pass
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    await state.update_data(time=time_str)
    await state.set_state(NewMorning.priority)

    await _edit_morning_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_morning_priority_question(task_text, time_str),
        reply_markup=get_morning_priority_keyboard(),
        fallback_message=message,
    )

    # Удаляем сообщение пользователя с временем.
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "morning_time:10:00")
async def cb_morning_time_standard(call: CallbackQuery, state: FSMContext):
    """Обработчик нажатия на кнопку 'Стандартное (10:00)'"""
    if not call.message:
        await call.answer("Сообщение было удалено. Начни заново через /morning", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    task_text = data.get("text", "")

    if not task_text:
        await call.answer("Данные потерялись. Начни заново через /morning", show_alert=True)
        await state.clear()
        return

    await call.answer()

    time_str = "10:00"
    await state.update_data(time=time_str)
    await state.set_state(NewMorning.priority)

    # Если ID контейнера почему-то не сохранён, считаем контейнером текущее сообщение.
    container_id = await _get_morning_container_id(state)
    if not container_id:
        await _set_morning_container_id(state, call.message.message_id)

    await _edit_morning_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_morning_priority_question(task_text, time_str),
        reply_markup=get_morning_priority_keyboard(),
        fallback_message=call.message,
    )


@router.callback_query(F.data == "cancel_morning")
async def cb_cancel_morning(call: CallbackQuery, state: FSMContext):
    """Обработчик отмены создания утренней задачи"""
    await _cancel_morning_flow(None, call, state)
    await call.answer("Отменено")


@router.callback_query(F.data == "close_morning")
async def cb_close_morning(call: CallbackQuery):
    """Обработчик кнопки 'Закрыть'."""
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            try:
                await call.message.edit_text("❌ Закрыто.")
            except Exception:
                pass

    await call.answer("Закрыто")


@router.callback_query(F.data.startswith("morning_pri:"))
async def cb_morning_priority(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора приоритета для /morning через inline-кнопки"""
    if not call.message:
        await call.answer("Сообщение было удалено.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    task_text = data.get("text")
    time_str = data.get("time")

    if not task_text or not time_str:
        await call.answer("Данные потерялись. Начни заново через /morning", show_alert=True)
        await state.clear()
        return

    priority = call.data.split(":", 1)[1]

    title = task_text[:50] + ("…" if len(task_text) > 50 else "")

    task_id = await db.create_task(
        chat_id=call.message.chat.id,
        title=title,
        text=task_text,
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
            "text": task_text,
            "priority": priority,
            "one_shot": True,
        }
        _scheduler.add_task_schedule(schedule)

    container_id = await _get_morning_container_id(state) or call.message.message_id

    await state.clear()

    success_text = _morning_success_text(task_text, time_str, priority)
    success_kb = get_morning_result_keyboard()

    try:
        await call.bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=container_id,
            text=success_text,
            parse_mode="HTML",
            reply_markup=success_kb,
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Не удалось отредактировать финальное morning-сообщение: {e}")

            try:
                await call.message.answer(
                    success_text,
                    parse_mode="HTML",
                    reply_markup=success_kb,
                )
            except Exception as e2:
                logger.warning(f"Fallback morning-сообщение не удалось: {e2}")

    await call.answer("Запомнила 😌")


# ──────────────────────────────────────────────────────────
# Общая отмена для /daily
# ──────────────────────────────────────────────────────────

async def _cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())
    )