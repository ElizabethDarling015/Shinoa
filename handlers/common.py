"""
Общие вспомогательные функции и клавиатуры для всех хендлеров.
"""

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
)
from database.tasks import CATEGORIES, PRIORITIES

# ──────────────────────────────────────────────
# Inline клавиатуры (для нового /week и меню)
# ──────────────────────────────────────────────

def get_nav_buttons() -> list:
    """Стандартные кнопки навигации для возврата назад"""
    return [
        InlineKeyboardButton(text="⬅️ Назад к выбору типа", callback_data="task_type_menu"),
        InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
    ]

def get_category_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Личное", callback_data="cat:личное"),
            InlineKeyboardButton(text="💼 Работа", callback_data="cat:работа"),
        ],
        [
            InlineKeyboardButton(text="💰 Финансы", callback_data="cat:финансы"),
            InlineKeyboardButton(text="❤️ Здоровье", callback_data="cat:здоровье"),
        ],
        get_nav_buttons()
    ])

def get_week_category_inline() -> InlineKeyboardMarkup:
    """Категории для еженедельной задачи с уникальным префиксом week_cat:*"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Личное", callback_data="week_cat:личное"),
            InlineKeyboardButton(text="💼 Работа", callback_data="week_cat:работа"),
        ],
        [
            InlineKeyboardButton(text="💰 Финансы", callback_data="week_cat:финансы"),
            InlineKeyboardButton(text="❤️ Здоровье", callback_data="week_cat:здоровье"),
        ],
        get_nav_buttons()
    ])

def get_priority_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Тир-1", callback_data="week_pri:high"),
            InlineKeyboardButton(text="🟡 Тир-2", callback_data="week_pri:medium"),
            InlineKeyboardButton(text="🟢 Тир-3", callback_data="week_pri:low"),
        ],
        get_nav_buttons()
    ])


def get_daily_category_inline() -> InlineKeyboardMarkup:
    """Категории для ежедневной задачи с уникальным префиксом daily_cat:*"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Личное", callback_data="daily_cat:личное"),
            InlineKeyboardButton(text="💼 Работа", callback_data="daily_cat:работа"),
        ],
        [
            InlineKeyboardButton(text="💰 Финансы", callback_data="daily_cat:финансы"),
            InlineKeyboardButton(text="❤️ Здоровье", callback_data="daily_cat:здоровье"),
        ],
        get_nav_buttons()
    ])

def get_daily_priority_inline() -> InlineKeyboardMarkup:
    """Приоритеты для ежедневной задачи с уникальным префиксом daily_pri:*"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Тир-1", callback_data="daily_pri:high"),
            InlineKeyboardButton(text="🟡 Тир-2", callback_data="daily_pri:medium"),
            InlineKeyboardButton(text="🟢 Тир-3", callback_data="daily_pri:low"),
        ],
        get_nav_buttons()
    ])

def get_days_inline(current_days: list) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру дней недели с отметками выбранных"""
    is_all = len(current_days) == 0

    def fmt(day_name, day_num):
        selected = "✅ " if (day_num in current_days or is_all) else ""
        return f"{selected}{day_name}"

    keyboard = [
        [
            InlineKeyboardButton(text=fmt("Пн", 0), callback_data="week_day:0"),
            InlineKeyboardButton(text=fmt("Вт", 1), callback_data="week_day:1"),
            InlineKeyboardButton(text=fmt("Ср", 2), callback_data="week_day:2"),
            InlineKeyboardButton(text=fmt("Чт", 3), callback_data="week_day:3"),
        ],
        [
            InlineKeyboardButton(text=fmt("Пт", 4), callback_data="week_day:4"),
            InlineKeyboardButton(text=fmt("Сб", 5), callback_data="week_day:5"),
            InlineKeyboardButton(text=fmt("Вс", 6), callback_data="week_day:6"),
            InlineKeyboardButton(text="✅ Каждый день" if is_all else "Каждый день", callback_data="week_day:all"),
        ],
        [
            InlineKeyboardButton(text="➡️ Далее: Время", callback_data="week_days_next")
        ],
        get_nav_buttons()
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_monthly_mode_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора режима ежемесячного напоминания"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 По числу месяца", callback_data="monthly_mode_day"),
            InlineKeyboardButton(text="📆 По дате (число + месяц)", callback_data="monthly_mode_date"),
        ],
        get_nav_buttons()
    ])


# ──────────────────────────────────────────────
# Reply клавиатуры (для совместимости с daily.py и monthly.py)
# ──────────────────────────────────────────────

def category_keyboard() -> ReplyKeyboardMarkup:
    cats = CATEGORIES
    rows = [
        [KeyboardButton(text=c) for c in cats[:3]],
        [KeyboardButton(text=c) for c in cats[3:]],
        [KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def priority_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🔴 Срочно"), KeyboardButton(text="🟡 Средне"), KeyboardButton(text="🟢 Когда-нибудь")],
        [KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def weekday_keyboard() -> ReplyKeyboardMarkup:
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    rows = [
        [KeyboardButton(text=d) for d in days[:4]],
        [KeyboardButton(text=d) for d in days[4:]],
        [KeyboardButton(text="Каждый день"), KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=False)


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ──────────────────────────────────────────────
# Парсеры ввода
# ─────────────────────────────────────────────

WEEKDAY_MAP = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

def parse_weekdays(text: str) -> list[int] | None:
    t = text.strip().lower().rstrip(".")
    if t in ("каждый день", "every day", "*", "все", "all"):
        return []
    parts = [p.strip() for p in t.replace(";", ",").split(",")]
    result = []
    for p in parts:
        if p in WEEKDAY_MAP:
            result.append(WEEKDAY_MAP[p])
        else:
            return None
    return list(set(result)) or None

def parse_time(text: str) -> tuple[int, int] | None:
    text = text.strip().replace(".", ":").replace(" ", ":")
    parts = text.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, IndexError):
        pass
    return None

def days_to_str(days: list[int]) -> str:
    if not days:
        return "каждый день"
    return ", ".join(WEEKDAY_LABELS[d] for d in sorted(days))

def priority_from_text(text: str) -> str | None:
    mapping = {
        "🔴 Тир-1": "high", "🔴 срочно": "high", "срочно": "high", "high": "high",
        "🟡 Тир-2": "medium", "🟡 средне": "medium", "средне": "medium", "medium": "medium",
        "🟢 Тир-3": "low", "🟢 когда-нибудь": "low", "когда-нибудь": "low", "low": "low",
    }
    return mapping.get(text.strip().lower())