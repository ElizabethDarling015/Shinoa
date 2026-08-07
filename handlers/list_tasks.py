"""
/list — список задач с фильтрами по категории и приоритету.
/delete <id> — удаление задачи.
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import WEEKDAY_LABELS

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


TYPE_LABELS = {
    "weekly": "еженедельно",
    "daily": "каждый день",
    "monthly_day": "ежемесячно",
    "monthly_date": "раз в год",
    "morning": "разовое (утром)",
    "interval": "с интервалом",
    "workdays": "рабочие дни",
}

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def get_list_nav_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура навигации для списка задач"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="start_list"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ]
    ])


async def format_task(task: dict) -> str:
    schedules = await db.get_schedules(task["id"])
    p = PRIORITY_EMOJI.get(task["priority"], "🟡")
    t_label = TYPE_LABELS.get(task["type"], task["type"])

    sched_lines = []
    for s in schedules:
        if task["type"] == "weekly":
            if s["days_of_week"]:
                days = [WEEKDAY_LABELS[int(d)] for d in s["days_of_week"].split(",")]
                sched_lines.append(f"  {', '.join(days)} в {s['time']}")
            else:
                sched_lines.append(f"  каждый день в {s['time']}")
        elif task["type"] == "monthly_day":
            sched_lines.append(f"  {s['day_of_month']}-е число в {s['time']}")
        elif task["type"] == "monthly_date":
            sched_lines.append(f"  {s['day_of_month']}.{s['month']:02d} в {s['time']}")
        elif task["type"] in ("daily", "morning", "workdays"):
            sched_lines.append(f"  {s['time']}")

    sched_text = "\n".join(sched_lines) if sched_lines else "  (нет расписания)"

    return (
        f"{p} <b>{task['title']}</b>  <code>[#{task['id']}]</code>\n"
        f"🏷 {task['category']} · {t_label}\n"
        f"🕐{sched_text}"
    )


# ──────────────────────────────────────────────────────────
# Обработчик выбора категории (РЕДАКТИРУЕТ сообщение)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("list_cat:"))
async def cb_list_category(call: CallbackQuery):
    """Показывает задачи конкретной категории, РЕДАКТИРУЯ текущее сообщение"""
    category = call.data.split(":")[1]
    await call.answer()
    
    tasks = await db.get_tasks(call.message.chat.id, category=category)

    if not tasks:
        text = f"📂 В категории «<b>{category}</b>» пока нет активных задач."
        try:
            await call.message.edit_text(
                text, parse_mode="HTML", reply_markup=get_list_nav_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
        return

    # Разделяем задачи по приоритетам для красивого вывода
    tasks_by_priority = {"high": [], "medium": [], "low": []}
    for task in tasks:
        p = task.get("priority", "medium")
        if p in tasks_by_priority:
            tasks_by_priority[p].append(task)

    tier_headers = {
        "high": "🔴 Тир-1 (Срочно)",
        "medium": "🟡 Тир-2 (Средне)",
        "low": "🟢 Тир-3 (Когда-нибудь)",
    }

    parts = []
    for tier_key, tier_tasks in tasks_by_priority.items():
        if not tier_tasks:
            continue
        
        parts.append(f"<b>{tier_headers[tier_key]}</b>")
        for task in tier_tasks:
            parts.append(await format_task(task))

    text = f"📋 <b>Задачи: {category.capitalize()}</b>\n\n" + "\n\n".join(parts)
    text += "\n\n<i>Удалить: /delete &lt;id&gt;</i>"

    try:
        await call.message.edit_text(
            text, parse_mode="HTML", reply_markup=get_list_nav_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


# ──────────────────────────────────────────────────────────
# Отправка нового сообщения (для /list и кнопки "Все задачи")
# ──────────────────────────────────────────────────────────

async def send_task_list(message: Message, category: str = None, priority: str = None, exclude_type: str = None):
    """Основная логика показа списка задач — разбивка по приоритетам (Тир-1/2/3)"""
    # Передаем exclude_type в базу данных
    tasks = await db.get_tasks(message.chat.id, category=category, priority=priority, exclude_type=exclude_type)

    if not tasks:
        filter_note = f" по фильтру «{category or priority}»" if (category or priority) else ""
        await message.answer(
            f"У вас нет активных задач{filter_note}.\n\n"
            "Создать:\n"
            "/week — еженедельное\n"
            "/monthly — ежемесячное\n"
            "/daily — ежедневное\n"
            "/morning — задача на завтра",
            reply_markup=get_list_nav_keyboard()
        )
        return

    # Разделяем задачи по приоритетам
    tasks_by_priority = {
        "high": [],
        "medium": [],
        "low": [],
    }
    for task in tasks:
        p = task.get("priority", "medium")
        if p in tasks_by_priority:
            tasks_by_priority[p].append(task)

    # Заголовки для каждого тира
    tier_headers = {
        "high": "🔴 Тир-1 (Срочно)",
        "medium": "🟡 Тир-2 (Средне)",
        "low": "🟢 Тир-3 (Когда-нибудь)",
    }

    # Отправляем по одному сообщению на каждый заполненный тир
    for tier_key, tier_tasks in tasks_by_priority.items():
        if not tier_tasks:
            continue  # Пропускаем пустые тиры

        parts = []
        for task in tier_tasks:
            parts.append(await format_task(task))

        # Заголовок сообщения
        header = f"<b>{tier_headers[tier_key]}</b>"
        if category:
            header += f" · {category}"
        header += f"\n\n<i>Всего: {len(tier_tasks)}</i>"

        text = header + "\n\n" + "\n\n".join(parts)
        text += "\n\n<i>Удалить: /delete &lt;id&gt;</i>"

        await message.answer(text, parse_mode="HTML", reply_markup=get_list_nav_keyboard())


@router.message(Command("list"))
async def cmd_list(message: Message):
    # Парсим фильтры из аргументов: /list работа или /list high
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    category = None
    priority = None
    for arg in args:
        if arg.lower() in db.CATEGORIES:
            category = arg.lower()
        if arg.lower() in ("high", "medium", "low"):
            priority = arg.lower()

    await send_task_list(message, category=category, priority=priority)


@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Укажите id задачи: /delete 1")
        return
    try:
        task_id = int(command.args.strip())
    except ValueError:
        await message.answer("id должен быть числом.")
        return

    task = await db.get_task(task_id)
    if not task or task["chat_id"] != message.chat.id:
        await message.answer("Задача не найдена.")
        return

    schedule_ids = await db.delete_schedules_for_task(task_id)
    await db.delete_task(task_id, message.chat.id)

    if _scheduler:
        _scheduler.remove_all_for_task(schedule_ids)

    await message.answer(f"🗑 Задача <b>{task['title']}</b> удалена.", parse_mode="HTML")


# ──────────────────────────────────────────────────────────
# Обработчик кнопки "Все задачи" (теперь исключает morning)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "list_all")
async def cb_list_all(call: CallbackQuery):
    """Показывает ВСЕ задачи, КРОМЕ утренних (morning)"""
    await call.answer()
    # Передаем exclude_type="morning", чтобы утренние планы не попадали в этот список
    await send_task_list(call.message, exclude_type="morning")