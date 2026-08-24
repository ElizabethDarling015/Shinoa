"""
/monthly — ежемесячные напоминания (inline-версия).
Контейнерная модель: весь мастер проходит в одном редактируемом сообщении.
Два режима:
  - По числу (каждое N-е число, task_type = monthly_day)
  - По дате (N число месяца каждый год, task_type = monthly_date)
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
    get_monthly_mode_keyboard,
    get_month_category_inline,
    get_monthly_priority_inline,
    parse_time,
)

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None

CANCEL_WORDS = {"❌ отмена", "отмена", "cancel", "/cancel"}

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


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


class NewMonthly(StatesGroup):
    mode = State()
    title = State()
    text = State()
    category = State()
    priority = State()
    day = State()
    month = State()
    time = State()


# ──────────────────────────────────────────────────────────
# Вспомогательные функции контейнерной модели
# ──────────────────────────────────────────────────────────

def _make_nav_keyboard() -> list:
    nav = get_nav_buttons()
    return nav if isinstance(nav, list) and len(nav) > 0 and isinstance(nav[0], list) else [nav]


def _truncate_for_display(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


async def _get_month_container_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    return data.get("base_msg_id") or data.get("bot_msg_id")


async def _set_month_container_id(state: FSMContext, message_id: int):
    await state.update_data(bot_msg_id=message_id, base_msg_id=message_id)


def get_month_start_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для шага 'режим'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 По числу месяца", callback_data="month_mode:day"),
                InlineKeyboardButton(text="📆 По дате (число+месяц)", callback_data="month_mode:date"),
            ],
            *_make_nav_keyboard(),
        ]
    )


def get_month_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard())


def _month_mode_question() -> str:
    return (
        "📅 <b>Ежемесячное напоминание</b>\n\n"
        "Выберите режим:\n"
        "• <b>По числу месяца</b> — например, каждое 15-е число\n"
        "• <b>По дате</b> — например, 15 июня каждый год"
    )


def _month_text_question(mode: str, title: str) -> str:
    mode_label = "По числу месяца" if mode == "day" else "По дате (число+месяц)"
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>{escape(mode_label)}</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n\n"
        "Введите <b>текст</b> напоминания:"
    )


def _month_category_question(mode: str, title: str, text: str) -> str:
    mode_label = "По числу месяца" if mode == "day" else "По дате (число+месяц)"
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>{escape(mode_label)}</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Текст:</i> «{escape(_truncate_for_display(text))}»\n\n"
        "Выберите <b>категорию</b>:"
    )


def _month_priority_question(mode: str, title: str, text: str, category: str) -> str:
    mode_label = "По числу месяца" if mode == "day" else "По дате (число+месяц)"
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>{escape(mode_label)}</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Текст:</i> «{escape(_truncate_for_display(text))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b>\n\n"
        "Выберите <b>приоритет</b>:"
    )


def _month_day_question(mode: str, title: str, text: str, category: str, priority: str) -> str:
    mode_label = "По числу месяца" if mode == "day" else "По дате (число+месяц)"
    priority_label = db.PRIORITIES.get(priority, priority)
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>{escape(mode_label)}</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b> · <i>Приоритет:</i> <b>{escape(priority_label)}</b>\n\n"
        "Введите <b>число месяца</b> (1–31):"
    )


def _month_month_question(mode: str, title: str, text: str, category: str, priority: str, day: int) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    warning = "\n\n⚠️ В некоторых месяцах нет этого числа — напоминание не придёт." if day > 28 else ""
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>По дате (число+месяц)</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b> · <i>Приоритет:</i> <b>{escape(priority_label)}</b>\n"
        f"<i>Число:</i> <b>{day}</b>\n\n"
        f"Введите <b>месяц</b> (например: июнь, июня, 6):{escape(warning)}"
    )


def _month_time_question_day_mode(title: str, text: str, category: str, priority: str, day: int) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    warning = "\n\n⚠️ В некоторых месяцах нет этого числа — напоминание не придёт." if day > 28 else ""
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>По числу месяца</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b> · <i>Приоритет:</i> <b>{escape(priority_label)}</b>\n"
        f"<i>Число:</i> <b>{day}</b>{escape(warning)}\n\n"
        "В какое <b>время</b>? Формат ЧЧ:ММ\nПример: <code>10:00</code>"
    )


def _month_time_question_date_mode(title: str, text: str, category: str, priority: str, day: int, month: int) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    month_name = MONTH_NAMES[month] if 1 <= month <= 12 else str(month)
    return (
        f"📅 <b>Ежемесячное напоминание</b> · <i>По дате (число+месяц)</i>\n\n"
        f"<i>Название:</i> «{escape(_truncate_for_display(title))}»\n"
        f"<i>Категория:</i> <b>{escape(category)}</b> · <i>Приоритет:</i> <b>{escape(priority_label)}</b>\n"
        f"<i>Дата:</i> <b>{day} {escape(month_name.lower())}</b> каждый год\n\n"
        "В какое <b>время</b>? Формат ЧЧ:ММ\nПример: <code>10:00</code>"
    )


def _month_success_text_day_mode(title: str, text: str, category: str, priority: str, day: int, time_str: str) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    return (
        "✅ <b>Ежемесячное напоминание создано!</b>\n\n"
        f"📌 {escape(title)}\n"
        f"📅 каждое {day}-е число в {escape(time_str)}\n"
        f"🏷 {escape(category)} · {escape(priority_label)}\n\n"
        f"<i>{escape(_truncate_for_display(text))}</i>"
    )


def _month_success_text_date_mode(title: str, text: str, category: str, priority: str, day: int, month: int, time_str: str) -> str:
    priority_label = db.PRIORITIES.get(priority, priority)
    month_name = MONTH_NAMES[month] if 1 <= month <= 12 else str(month)
    return (
        "✅ <b>Ежемесячное напоминание создано!</b>\n\n"
        f"📌 {escape(title)}\n"
        f"📅 каждый год {day} {escape(month_name.lower())} в {escape(time_str)}\n"
        f"🏷 {escape(category)} · {escape(priority_label)}\n\n"
        f"<i>{escape(_truncate_for_display(text))}</i>"
    )


async def _edit_month_container(
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    fallback_message: Message | None = None,
) -> bool:
    container_id = await _get_month_container_id(state)

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
            logger.warning(f"Не удалось отредактировать month-контейнер: {e}")

    if fallback_message:
        try:
            new_msg = await fallback_message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            await _set_month_container_id(state, new_msg.message_id)
            return True
        except Exception as e:
            logger.warning(f"Не удалось отправить fallback month-сообщение: {e}")

    return False


async def _cancel_month_flow(
    message: Message | None,
    call: CallbackQuery | None,
    state: FSMContext,
):
    container_id = await _get_month_container_id(state)

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
    cancel_kb = get_month_result_keyboard()

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
            logger.warning(f"Не удалось отредактировать month-контейнер при отмене: {e}")

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
# /monthly — команды и состояния
# ──────────────────────────────────────────────────────────

@router.message(Command("monthly"))
async def cmd_monthly(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewMonthly.mode)

    bot_msg = await message.answer(
        _month_mode_question(),
        parse_mode="HTML",
        reply_markup=get_month_start_keyboard(),
    )

    await _set_month_container_id(state, bot_msg.message_id)

    try:
        await message.delete()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# Выбор режима
# ──────────────────────────────────────────────────────────

async def _handle_mode_selection(
    mode: str,
    call: CallbackQuery,
    state: FSMContext,
):
    """Общая логика для обоих режимов: сохраняем mode и переходим к названию."""
    await state.update_data(mode=mode)
    await state.set_state(NewMonthly.title)

    await call.answer()

    container_id = await _get_month_container_id(state)
    if not container_id:
        await _set_month_container_id(state, call.message.message_id)

    mode_label = "По числу месяца" if mode == "day" else "По дате (число+месяц)"
    emoji = "📅" if mode == "day" else "📆"

    await _edit_month_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=(
            f"{emoji} <b>Ежемесячное напоминание</b> · <i>{escape(mode_label)}</i>\n\n"
            "Введите <b>название</b> напоминания:"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        fallback_message=call.message,
    )


@router.callback_query(F.data == "monthly_mode_day")
async def cb_mode_day(call: CallbackQuery, state: FSMContext):
    await _handle_mode_selection("day", call, state)


@router.callback_query(F.data == "month_mode:day")
async def cb_mode_day_new(call: CallbackQuery, state: FSMContext):
    await _handle_mode_selection("day", call, state)


@router.callback_query(F.data == "monthly_mode_date")
async def cb_mode_date(call: CallbackQuery, state: FSMContext):
    await _handle_mode_selection("date", call, state)


@router.callback_query(F.data == "month_mode:date")
async def cb_mode_date_new(call: CallbackQuery, state: FSMContext):
    await _handle_mode_selection("date", call, state)


# ──────────────────────────────────────────────────────────
# Шаги FSM
# ──────────────────────────────────────────────────────────

@router.message(NewMonthly.title)
async def step_title(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправь название текстом.")
        return

    user_text = message.text.strip()

    if user_text.lower() in CANCEL_WORDS:
        await _cancel_month_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    mode = data.get("mode")
    if not mode:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    await state.update_data(title=user_text)
    await state.set_state(NewMonthly.text)

    await _edit_month_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_month_text_question(mode, user_text),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        fallback_message=message,
    )

    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewMonthly.text)
async def step_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправь текст задачи.")
        return

    user_text = message.text.strip()

    if user_text.lower() in CANCEL_WORDS:
        await _cancel_month_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")

    if not mode or not title:
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    await state.update_data(text=user_text)
    await state.set_state(NewMonthly.category)

    await _edit_month_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_month_category_question(mode, title, user_text),
        reply_markup=get_month_category_inline(),
        fallback_message=message,
    )

    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("month_cat:"))
async def cb_category_monthly(call: CallbackQuery, state: FSMContext):
    """Обработчик выбора категории для /monthly"""
    if not call.message:
        await call.answer("Сообщение было удалено. Начни заново через /monthly", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")
    text = data.get("text")

    if not mode or not title or not text:
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /monthly", show_alert=True)
        await state.clear()
        return

    category = call.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(NewMonthly.priority)

    await call.answer()

    container_id = await _get_month_container_id(state)
    if not container_id:
        await _set_month_container_id(state, call.message.message_id)

    await _edit_month_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_month_priority_question(mode, title, text, category),
        reply_markup=get_monthly_priority_inline(),
        fallback_message=call.message,
    )


@router.callback_query(F.data.startswith("month_pri:"))
async def cb_priority_monthly(call: CallbackQuery, state: FSMContext):
    if not call.message:
        await call.answer("Сообщение было удалено. Начни заново через /monthly", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")

    if not mode or not title or not text or not category:
        await call.answer("⚠️ Цепочка прервалась. Начни заново через /monthly", show_alert=True)
        await state.clear()
        return

    priority = call.data.split(":", 1)[1]
    await state.update_data(priority=priority)
    await state.set_state(NewMonthly.day)

    await call.answer()

    container_id = await _get_month_container_id(state)
    if not container_id:
        await _set_month_container_id(state, call.message.message_id)

    await _edit_month_container(
        bot=call.bot,
        chat_id=call.message.chat.id,
        state=state,
        text=_month_day_question(mode, title, text, category, priority),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        fallback_message=call.message,
    )


@router.message(NewMonthly.day)
async def step_day(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Введите число от 1 до 31.")
        return

    raw_text = message.text.strip()

    if raw_text.lower() in CANCEL_WORDS:
        await _cancel_month_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    try:
        day = int(raw_text)
        if not 1 <= day <= 31:
            raise ValueError
    except ValueError:
        data = await state.get_data()
        mode = data.get("mode")
        title = data.get("title", "")
        text = data.get("text", "")
        category = data.get("category", "")
        priority = data.get("priority", "")

        if not all([mode, title, text, category, priority]):
            await state.clear()
            await message.answer(
                "Данные потерялись. Начни заново через /monthly.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
            )
            return

        await _edit_month_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=_month_day_question(mode, title, text, category, priority) + "\n\n⚠️ Введите число от 1 до 31.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
            fallback_message=message,
        )
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")
    priority = data.get("priority")

    if not all([mode, title, text, category, priority]):
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    await state.update_data(day=day)

    if mode == "date":
        await state.set_state(NewMonthly.month)
        await _edit_month_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=_month_month_question(mode, title, text, category, priority, day),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
            fallback_message=message,
        )
    else:
        await state.set_state(NewMonthly.time)
        await _edit_month_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=_month_time_question_day_mode(title, text, category, priority, day),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
            fallback_message=message,
        )

    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewMonthly.month)
async def step_month(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Введите месяц: название (июнь) или число (6).")
        return

    raw_text = message.text.strip()

    if raw_text.lower() in CANCEL_WORDS:
        await _cancel_month_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    month_num = MONTHS.get(raw_text.lower())

    if not month_num:
        try:
            month_num = int(raw_text)
            if not 1 <= month_num <= 12:
                raise ValueError
        except ValueError:
            data = await state.get_data()
            mode = data.get("mode")
            title = data.get("title", "")
            text = data.get("text", "")
            category = data.get("category", "")
            priority = data.get("priority", "")
            day = data.get("day", 1)

            if not all([mode, title, text, category, priority, day]):
                await state.clear()
                await message.answer(
                    "Данные потерялись. Начни заново через /monthly.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
                )
                return

            await _edit_month_container(
                bot=message.bot,
                chat_id=message.chat.id,
                state=state,
                text=_month_month_question(mode, title, text, category, priority, day) + "\n\n⚠️ Не понял месяц. Введите название (июнь) или число (6).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
                fallback_message=message,
            )
            try:
                await message.delete()
            except Exception:
                pass
            return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")
    priority = data.get("priority")
    day = data.get("day")

    if not all([mode, title, text, category, priority, day]):
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    await state.update_data(month=month_num)
    await state.set_state(NewMonthly.time)

    await _edit_month_container(
        bot=message.bot,
        chat_id=message.chat.id,
        state=state,
        text=_month_time_question_date_mode(title, text, category, priority, day, month_num),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        fallback_message=message,
    )

    try:
        await message.delete()
    except Exception:
        pass


@router.message(NewMonthly.time)
async def step_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Время нужно ввести текстом, например: <code>10:00</code>", parse_mode="HTML")
        return

    raw_text = message.text.strip()

    if raw_text.lower() in CANCEL_WORDS:
        await _cancel_month_flow(message, None, state)
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    mode = data.get("mode")
    title = data.get("title")
    text = data.get("text")
    category = data.get("category")
    priority = data.get("priority")
    day = data.get("day")
    month = data.get("month")

    if not all([mode, title, text, category, priority, day]):
        await state.clear()
        await message.answer(
            "Данные потерялись. Начни заново через /monthly.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
        )
        return

    t = parse_time(raw_text)

    if not t:
        if mode == "day":
            text_q = _month_time_question_day_mode(title, text, category, priority, day)
        else:
            text_q = _month_time_question_date_mode(title, text, category, priority, day, month or 1)

        await _edit_month_container(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            text=text_q + "\n\n⚠️ Не понял время. Формат: <code>10:00</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_make_nav_keyboard()),
            fallback_message=message,
        )
        try:
            await message.delete()
        except Exception:
            pass
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
        month=month,
    )

    if _scheduler:
        schedule = {
            "id": schedule_id,
            "task_id": task_id,
            "time": time_str,
            "task_type": task_type,
            "day_of_month": day,
            "month": month,
            "chat_id": message.chat.id,
            "title": title,
            "text": text,
            "priority": priority,
            "one_shot": False,
        }
        _scheduler.add_task_schedule(schedule)

    container_id = await _get_month_container_id(state) or message.message_id

    await state.clear()

    if mode == "day":
        success_text = _month_success_text_day_mode(title, text, category, priority, day, time_str)
    else:
        success_text = _month_success_text_date_mode(title, text, category, priority, day, month, time_str)

    success_kb = get_month_result_keyboard()

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
            logger.warning(f"Не удалось отредактировать финальное month-сообщение: {e}")
            try:
                await message.answer(success_text, parse_mode="HTML", reply_markup=success_kb)
            except Exception as e2:
                logger.warning(f"Fallback month-сообщение не удалось: {e2}")

    try:
        await message.delete()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# Общая отмена (для совместимости с текстовой отменой)
# ──────────────────────────────────────────────────────────

async def _cancel(message: Message, state: FSMContext):
    await _cancel_month_flow(message, None, state)