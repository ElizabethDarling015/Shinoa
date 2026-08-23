"""
/start и /help — приветствие и справка с inline-меню.
"""

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from handlers.common import get_nav_buttons

logger = logging.getLogger(__name__)
router = Router()


MAIN_MENU_TEXT = (
    "🎀 <b>Тук-тук! У нас сегодня много дел!</b>\n\n"
    "Я помогу тебе не забыть о важном, выработать привычки и сохранить идеи.\n\n"
    "<b>Выбери действие ниже или введи команду вручную:</b>"
)


HELP_TEXT = (
    "<b>📖 Полная справка</b>\n\n"
    "<b>Типы задач:</b>\n"
    "• /week — еженедельное напоминание (по дням недели)\n"
    "• /daily — каждый день в одно время\n"
    "• /morning — один раз, завтра утром, потом удаляется\n"
    "• /monthly — каждый месяц в определённое число или раз в год\n\n"
    "<b>Фильтры /list:</b>\n"
    "• /list работа — только задачи категории «работа»\n"
    "• /list high — только срочные\n\n"
    "<b>Категории задач:</b>\n"
    "Работа · Личное · Финансы · Здоровье\n\n"
    "<b>Приоритеты:</b>\n"
    "🔴 Тир-1 (Срочно) · 🟡 Тир-2 (Средне) · 🟢 Тир-3 (Когда-нибудь)\n\n"
    "<b>Кнопки под напоминанием:</b>\n"
    "✅ Выполнено — закрывает задачу\n"
    "⏰ Отложить — через 1ч / вечером / завтра / неделю\n"
    "❌ Удалить — удаляет задачу насовсем\n\n"
    "<b>Архив:</b>\n"
    "• Пришли фото/документ — сохранится автоматически\n"
    "• /idea — сохранить текстовую идею с тегами\n"
    "• /find #тег или /find запрос — поиск\n\n"
    "<b>Привычки:</b>\n"
    "Каждый вечер напомню отметить привычку.\n"
    "Streak 🔥 показывает сколько дней подряд не пропускал.\n\n"
    "<b>Утренняя сводка:</b>\n"
    "Каждое утро пришлю план дня, задачи и погоду.\n"
    "• /setcity — установить город для погоды\n"
    "• /digesttime — изменить время сводки (по умолчанию 07:00)\n"
    "• /settings — все настройки"
)


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Создаёт inline-клавиатуру для главного меню /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Новая задача", callback_data="start_new"),
                InlineKeyboardButton(text="📋 Список задач", callback_data="start_list"),
            ],
            [
                InlineKeyboardButton(text="📊 Работа с данными", callback_data="start_data"),
                InlineKeyboardButton(text="🗂 Архив идей", callback_data="start_idea"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="start_settings"),
                InlineKeyboardButton(text="📖 Справка", callback_data="start_help"),
            ],
        ]
    )


def get_new_task_keyboard() -> InlineKeyboardMarkup:
    """Подменю выбора типа задачи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌅 Планы на завтра", callback_data="task_morning"),
                InlineKeyboardButton(text="📅 Ежедневное", callback_data="task_daily"),
            ],
            [
                InlineKeyboardButton(text="📆 Еженедельное", callback_data="task_week"),
                InlineKeyboardButton(text="🗓 Ежемесячное", callback_data="task_month"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="start_main"),
            ],
        ]
    )


def get_list_categories_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора категории для списка задач."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Личное", callback_data="list_cat:личное"),
                InlineKeyboardButton(text="💼 Работа", callback_data="list_cat:работа"),
            ],
            [
                InlineKeyboardButton(text="💰 Финансы", callback_data="list_cat:финансы"),
                InlineKeyboardButton(text="❤️ Здоровье", callback_data="list_cat:здоровье"),
            ],
            [
                InlineKeyboardButton(text="📋 Все задачи", callback_data="list_all"),
                InlineKeyboardButton(text="🌅 Сегодняшняя сводка", callback_data="digest_now"),
            ],
            [
                InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
            ],
        ]
    )


def get_idea_archive_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для меню Архива идей."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Новая заметка", callback_data="idea_new"),
                InlineKeyboardButton(text="📂 Все заметки", callback_data="idea_all"),
            ],
            [
                InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
            ],
        ]
    )


def get_help_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для справки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
            ]
        ]
    )


async def _safe_edit(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    """
    Пытается отредактировать сообщение.
    Если редактирование невозможно, отправляет новое сообщение.
    """
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return

        logger.warning("Не удалось отредактировать сообщение: %s", e)

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


@router.message(CommandStart())
async def cmd_start(message: Message):
    from database.users import upsert_user

    await upsert_user(message.chat.id)

    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=get_start_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=get_help_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Навигация по меню
# ──────────────────────────────────────────────────────────


@router.callback_query(F.data == "start_new")
async def cb_start_new(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    await _safe_edit(
        call.message,
        "📝 <b>Создание задачи</b>\n\nВыберите тип задачи:",
        get_new_task_keyboard(),
    )


@router.callback_query(F.data == "task_type_menu")
async def cb_task_type_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    await _safe_edit(
        call.message,
        "📝 <b>Создание задачи</b>\n\nВыберите тип задачи:",
        get_new_task_keyboard(),
    )


# Поддерживаем оба варианта: start_main и start_now
@router.callback_query(F.data.in_({"start_main", "start_now"}))
async def cb_start_main(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    await _safe_edit(
        call.message,
        MAIN_MENU_TEXT,
        get_start_keyboard(),
    )


@router.callback_query(F.data == "start_help")
async def cb_start_help(call: CallbackQuery):
    await call.answer()

    await _safe_edit(
        call.message,
        HELP_TEXT,
        get_help_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Маршрутизация кнопок создания задач на соответствующие FSM
# ──────────────────────────────────────────────────────────


@router.callback_query(F.data == "task_morning")
async def cb_task_morning(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    from handlers.daily import NewMorning

    await state.set_state(NewMorning.text)

    await _safe_edit(
        call.message,
        "🌅 <b>Задача на завтра утром</b>\n\n"
        "Напишу тебе завтра в нужное время и задача исчезнет.\n\n"
        "Что нужно сделать?",
        InlineKeyboardMarkup(inline_keyboard=[get_nav_buttons()]),
    )


@router.callback_query(F.data == "task_daily")
async def cb_task_daily(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    from handlers.daily import cmd_daily

    await cmd_daily(call.message, state)


@router.callback_query(F.data == "task_week")
async def cb_task_week(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    from handlers.weekly import cmd_week

    await cmd_week(call.message, state)


@router.callback_query(F.data == "task_month")
async def cb_task_month(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    from handlers.monthly import cmd_monthly

    await cmd_monthly(call.message, state)


# ──────────────────────────────────────────────────────────
# Обработка остальных кнопок главного меню
# ──────────────────────────────────────────────────────────


@router.callback_query(F.data == "start_list")
async def cb_start_list(call: CallbackQuery):
    await call.answer()

    text = "📋 <b>Вот наш список задач🎁:</b>\n\nКакую категорию рассмотрим?😌"

    await _safe_edit(
        call.message,
        text,
        get_list_categories_keyboard(),
    )


@router.callback_query(F.data == "list_all")
async def cb_list_all(call: CallbackQuery):
    await call.answer()

    from handlers.list_tasks import send_task_list

    # Утренние задачи исключаем из общего списка
    await send_task_list(call.message, exclude_type="morning")


@router.callback_query(F.data == "start_settings")
async def cb_start_settings(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()

    from database.users import get_user
    from handlers.settings import get_settings_keyboard

    user = await get_user(call.message.chat.id)

    city = user["city"] if user and user.get("city") else "не задан"
    digest_time = user["digest_time"] if user else "07:00"
    tz = user["timezone"] if user else "Europe/Moscow"

    text = (
        "<b>⚙️ Настройки</b>\n\n"
        f"🌆 Город для погоды: <b>{city}</b>\n"
        f"🌅 Время утренней сводки: <b>{digest_time}</b>\n"
        f"🕐 Часовой пояс: <b>{tz}</b>\n\n"
        "<i>Используй кнопки ниже для изменения настроек.</i>"
    )

    await _safe_edit(
        call.message,
        text,
        get_settings_keyboard(),
    )


@router.callback_query(F.data == "start_idea")
async def cb_start_idea(call: CallbackQuery, state: FSMContext):
    """Открывает меню Архива идей."""
    await call.answer()
    await state.clear()

    text = (
        "🗂 <b>Личный архив идей</b>\n\n"
        "📎 <i>Просто отправь мне фото, голосовое или документ — я сохраню его автоматически!</i>\n\n"
        "Выберите действие:"
    )

    await _safe_edit(
        call.message,
        text,
        get_idea_archive_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Запасной обработчик для start_* кнопок
# ──────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("start_"))
async def handle_start_menu(call: CallbackQuery):
    await call.answer()

    action = call.data.split("_", 1)[1] if "_" in call.data else ""

    if action == "help":
        await _safe_edit(
            call.message,
            HELP_TEXT,
            get_help_keyboard(),
        )
        return

    if action == "data":
        try:
            from handlers.data_processing import get_data_processing_keyboard

            await _safe_edit(
                call.message,
                "📊 <b>Работа с данными</b>\n\n"
                "Инструменты для обработки файлов и информации с помощью AI.\n\n"
                "Выберите действие:",
                get_data_processing_keyboard(),
            )
        except Exception as e:
            logger.warning("Не удалось открыть меню работы с данными: %s", e)

            await _safe_edit(
                call.message,
                "📊 <b>Работа с данными</b>\n\n"
                "Инструменты для обработки файлов и информации с помощью AI.",
                get_start_keyboard(),
            )

        return

    await _safe_edit(
        call.message,
        "❓ Неизвестное действие.",
        get_start_keyboard(),
    )


@router.message(F.text.lower().in_({"отмена", "cancel", "/cancel"}))
async def cmd_cancel_global(message: Message):
    await message.answer(
        "Нечего отменять. Нажми /start чтобы увидеть меню.",
        reply_markup=get_start_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Кнопка «🌅 Сегодняшняя сводка» — собирает и показывает сводку вручную
# ──────────────────────────────────────────────────────────

def _get_digest_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура под сообщением сводки: Назад / На главную"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="start_list"),
                InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
            ]
        ]
    )


@router.callback_query(F.data == "digest_now")
async def cb_digest_now(call: CallbackQuery):
    """Кнопка «🌅 Сегодняшняя сводка» — собирает сводку и показывает В ТОМ ЖЕ сообщении."""
    await call.answer()

    async def show(text: str, keyboard: InlineKeyboardMarkup = None):
        """Редактирует текущее сообщение; если нельзя — шлёт новое"""
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            await call.message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    # Пока собирается погода/задачи — в том же сообщении показываем статус
    await show("⏳ Собираю сводку...")

    try:
        from database.users import get_user
        from scheduler.digest import build_digest_text

        user = await get_user(call.message.chat.id)
        city = user["city"] if user and user.get("city") else None

        text = await build_digest_text(call.message.chat.id, city=city)
        await show(text, _get_digest_keyboard())
    except Exception as e:
        logger.exception("Ошибка при сборке сводки: %s", e)
        await show(
            "⚠️ Не удалось собрать сводку. Попробуй позже.",
            _get_digest_keyboard(),
        )

def register_start(dp):
    dp.include_router(router)