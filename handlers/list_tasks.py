"""
/list — список задач с фильтрами по категории и приоритету (старый).
/tasks — новое меню задач (Сегодня, Категории, Приоритет, Сводка).
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
    "weekly": "Еженедельно",
    "daily": "Каждый день",
    "monthly_day": "Ежемесячно",
    "monthly_date": "Раз в год",
    "morning": "Разовое (утром)",
    "interval": "С интервалом",
    "workdays": "Рабочие дни",
}

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}

# ──────────────────────────────────────────────────────────
# Хранение параметров последних сообщений со списками задач
# ──────────────────────────────────────────────────────────
last_list_messages = {} 


def get_list_nav_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура навигации для списка задач"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="tasks_menu:categories"),
                InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
            ]
        ]
    )


def get_close_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой 'Закрыть' для удаления сообщения"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_message")]
        ]
    )


# ──────────────────────────────────────────────────────────
# НОВОЕ МЕНЮ ЗАДАЧ
# ──────────────────────────────────────────────────────────

def get_task_categories_keyboard() -> InlineKeyboardMarkup:
    """Старое меню выбора категорий для списка задач"""
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
            [InlineKeyboardButton(text="⬅️ Назад в меню задач", callback_data="start_list")],
        ]
    )


@router.callback_query(F.data == "tasks_menu:today")
async def cb_tasks_today(call: CallbackQuery):
    """Показывает утренние задачи на сегодня ОТДЕЛЬНЫМИ сообщениями с кнопками"""
    await call.answer()
    
    kb_nav = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ В категорию", callback_data="start_list"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main")
        ]
    ])
    
    try:
        await call.message.edit_text(
            "⏳ Отправляю сегодняшние задачи👇",
            parse_mode="HTML",
            reply_markup=kb_nav
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            pass

    tasks = await db.get_tasks(call.message.chat.id, task_type="morning")
    
    if not tasks:
        await call.message.answer(
            "📅 На сегодня утренних задач нет. Отличный повод отдохнуть или добавить новую! ☕",
            reply_markup=get_close_keyboard()
        )
        return

    for task in tasks:
        text = await format_task(task)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task_act:done:{task['id']}"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data=f"task_act:hide:{task['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"task_act:del:{task['id']}"),
            ]
        ])
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("task_act:"))
async def cb_task_action(call: CallbackQuery):
    """Обработчик кнопок Выполнено / Закрыть / Удалить для утренних задач"""
    parts = call.data.split(":")
    action = parts[1]
    task_id = int(parts[2])
    chat_id = call.message.chat.id

    if action == "hide":
        await call.answer()
        try:
            await call.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось скрыть сообщение: {e}")
            
    elif action == "del":
        task = await db.get_task(task_id)
        if task and task["chat_id"] == chat_id:
            schedule_ids = await db.delete_schedules_for_task(task_id)
            await db.delete_task(task_id, chat_id)
            if _scheduler:
                _scheduler.remove_all_for_task(schedule_ids)
                
        await call.answer("🗑 Задача удалена навсегда!", show_alert=True)
        try:
            await call.message.delete()
        except Exception:
            pass

    elif action == "done":
        task = await db.get_task(task_id)
        if task and task["chat_id"] == chat_id:
            from database.connection import get_db
            async with get_db() as conn:
                await conn.execute(
                    """
                    INSERT INTO completed_tasks (original_task_id, chat_id, title, text, category, priority) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, chat_id, task['title'], task.get('text'), task['category'], task['priority'])
                )
                await conn.commit()
            
            schedule_ids = await db.delete_schedules_for_task(task_id)
            await db.delete_task(task_id, chat_id)
            if _scheduler:
                _scheduler.remove_all_for_task(schedule_ids)
                
        await call.answer("✅ Задача выполнена и перенесена в архив!", show_alert=True)
        try:
            await call.message.delete()
        except Exception:
            pass


@router.callback_query(F.data == "tasks_menu:categories")
async def cb_tasks_categories(call: CallbackQuery):
    """Открывает меню категорий (РЕДАКТИРУЕТ сообщение)"""
    await call.answer()

    try:
        await call.message.edit_text(
            "📋 <b>Вот наши списки задач🎁:</b>\n\nКакую категорию рассмотрим?😌",
            parse_mode="HTML",
            reply_markup=get_task_categories_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "tasks_menu:priority")
async def cb_tasks_priority(call: CallbackQuery):
    """Показывает все задачи по приоритетам"""
    await call.answer()
    await send_task_list(call.message, exclude_type="morning", use_close_keyboard=True)


# ──────────────────────────────────────────────────────────
# СТАРАЯ ЛОГИКА И ГЕНЕРАЦИЯ ТЕКСТА
# ──────────────────────────────────────────────────────────

async def format_task(task: dict) -> str:
    schedules = await db.get_schedules(task["id"])
    p = PRIORITY_EMOJI.get(task["priority"], "🟡")
    t_label = TYPE_LABELS.get(task["type"], task["type"])

    sched_lines = []
    for s in schedules:
        if task["type"] == "weekly":
            if s["days_of_week"]:
                days = [WEEKDAY_LABELS[int(d)] for d in s["days_of_week"].split(",")]
                sched_lines.append(f" {', '.join(days)} в {s['time']}")
            else:
                sched_lines.append(f" каждый день в {s['time']}")
        elif task["type"] == "monthly_day":
            sched_lines.append(f" <b>{s['day_of_month']}</b>-е число в {s['time']}")
        elif task["type"] == "monthly_date":
            sched_lines.append(f" <b>{s['day_of_month']}.{s['month']:02d}</b> в {s['time']}")
        elif task["type"] in ("daily", "morning", "workdays"):
            sched_lines.append(f" {s['time']}")

    sched_text = "\n".join(sched_lines) if sched_lines else " (нет расписания)"

    # Полное описание: не обрезаем; не дублируем, если текст совпадает с заголовком
    text = (task.get("text") or "").strip()
    text_block = ""
    if text and text != (task.get("title") or "").strip():
        text_block = f"\n📋 {escape(text)}"

    return (
        f"{p} <b>{escape(task['title'])}</b> <code>[#{task['id']}]</code>\n"
        f"🏷 {task['category'].capitalize()} 📌 {t_label}\n"
        f"🕐{sched_text}{text_block}"
    )


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПЕРЕСБОРКИ ТЕКСТА ---

async def _build_category_text(chat_id: int, category: str) -> str:
    """Генерирует текст списка задач для конкретной категории."""
    tasks = await db.get_tasks(chat_id, category=category)
    if not tasks:
        return f"📂 В категории «<b>{category.capitalize()}</b>» пока нет активных задач."

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
    text += "\n\n<i>Удалить:</i> <code>/delete</code> <i>&lt;id&gt;</i>"
    return text


async def _build_list_text(chat_id: int, category: str = None, priority: str = None, exclude_type: str = None) -> str:
    """Генерирует текст общего списка задач с учетом фильтров."""
    tasks = await db.get_tasks(chat_id, category=category, priority=priority, exclude_type=exclude_type)
    if not tasks:
        filter_note = f" по фильтру «{category or priority}»" if (category or priority) else ""
        return (
            f"У вас нет активных задач{filter_note}.\n\n"
            "Создать:\n"
            "/week — еженедельное\n"
            "/monthly — ежемесячное\n"
            "/daily — ежедневное\n"
            "/morning — задача на завтра"
        )

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
        task_texts = [await format_task(task) for task in tier_tasks]
        header = f"<b>{tier_headers[tier_key]}</b>"
        if category:
            header += f" · {category.capitalize()}"
        header += f"\n\n<i>Всего: {len(tier_tasks)}</i>"
        parts.append(header + "\n\n" + "\n\n".join(task_texts))

    text = "\n\n".join(parts)
    text += "\n\n<i>Удалить:</i> <code>/delete</code> <i>&lt;id&gt;</i>"
    return text


@router.callback_query(F.data.startswith("list_cat:"))
async def cb_list_category(call: CallbackQuery):
    """Показывает задачи конкретной категории, РЕДАКТИРУЯ текущее сообщение"""
    category = call.data.split(":")[1]
    await call.answer()

    text = await _build_category_text(call.message.chat.id, category)
    keyboard = get_list_nav_keyboard()

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        # Сохраняем параметры, чтобы /delete мог пересобрать этот же список
        last_list_messages[call.message.chat.id] = {
            "message_id": call.message.message_id,
            "type": "category",
            "category": category,
            "keyboard": keyboard
        }
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


async def send_task_list(
    message: Message,
    category: str = None,
    priority: str = None,
    exclude_type: str = None,
    use_close_keyboard: bool = False,
):
    """Основная логика показа списка задач"""
    text = await _build_list_text(message.chat.id, category, priority, exclude_type)
    keyboard = get_close_keyboard() if use_close_keyboard else get_list_nav_keyboard()
    
    msg = await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    
    # Сохраняем параметры, чтобы /delete мог пересобрать этот же список
    last_list_messages[message.chat.id] = {
        "message_id": msg.message_id,
        "type": "list",
        "category": category,
        "priority": priority,
        "exclude_type": exclude_type,
        "use_close_keyboard": use_close_keyboard,
        "keyboard": keyboard
    }


@router.message(Command("list"))
async def cmd_list(message: Message):
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    category = None
    priority = None

    for arg in args:
        if arg.lower() in db.CATEGORIES:
            category = arg.lower()
        if arg.lower() in ("high", "medium", "low"):
            priority = arg.lower()

    await send_task_list(message, category=category, priority=priority)


# ──────────────────────────────────────────────────────────
# ОБНОВЛЕННЫЙ ОБРАБОТЧИК /delete (с пересборкой списка)
# ──────────────────────────────────────────────────────────

@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject):
    # 1. Удаляем сообщение пользователя с командой
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

    if not command.args:
        await message.answer("Укажите id задачи: <code>/delete 1</code>", parse_mode="HTML")
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

    # 2. Удаляем задачу из БД
    schedule_ids = await db.delete_schedules_for_task(task_id)
    await db.delete_task(task_id, message.chat.id)
    if _scheduler:
        _scheduler.remove_all_for_task(schedule_ids)

    # 3. Пересобираем список и обновляем сообщение
    if message.chat.id in last_list_messages:
        msg_data = last_list_messages[message.chat.id]
        
        # Генерируем новый текст списка (задача уже удалена из БД, её не будет в тексте)
        if msg_data.get("type") == "category":
            new_text = await _build_category_text(message.chat.id, msg_data.get("category"))
        else:
            new_text = await _build_list_text(
                message.chat.id,
                category=msg_data.get("category"),
                priority=msg_data.get("priority"),
                exclude_type=msg_data.get("exclude_type")
            )
            
        # Добавляем уведомление об успешном удалении
        new_text += "\n\n✅ <b>Удалила указанную задачу😌</b>"
        
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_data["message_id"],
                text=new_text,
                parse_mode="HTML",
                reply_markup=msg_data.get("keyboard")
            )
            return
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение (возможно, оно удалено): {e}")
            
    # Fallback, если редактирование не удалось
    await message.answer(f"🗑 Задача <b>{task['title']}</b> удалена.", parse_mode="HTML")


@router.callback_query(F.data == "list_all")
async def cb_list_all(call: CallbackQuery):
    """Показывает ВСЕ задачи, КРОМЕ утренних (morning)"""
    await call.answer()
    await send_task_list(call.message, exclude_type="morning", use_close_keyboard=True)


@router.callback_query(F.data == "close_message")
async def cb_close_message(call: CallbackQuery):
    """Удаляет сообщение при нажатии кнопки 'Закрыть'"""
    await call.answer()
    try:
        await call.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")


# Команды для проверки (Тест-план)

@router.message(Command("test_backup"))
async def cmd_test_backup(message: Message):
    """Принудительно отправляет отчет по выполненным задачам за прошлый месяц"""
    from database.connection import get_db
    import datetime

    today = datetime.date.today()
    first_day_this_month = today.replace(day=1)
    last_day_last_month = first_day_this_month - datetime.timedelta(days=1)
    first_day_last_month = last_day_last_month.replace(day=1)

    sql = """
        SELECT title, category, priority, completed_at 
        FROM completed_tasks 
        WHERE chat_id = ? AND date(completed_at) BETWEEN ? AND ?
        ORDER BY completed_at ASC
    """
    async with get_db() as conn:
        async with conn.execute(sql, (message.chat.id, first_day_last_month.isoformat(), last_day_last_month.isoformat())) as cur:
            rows = await cur.fetchall()
            
    if not rows:
        await message.answer(f"📊 <b>Тест бэкапа:</b>\n\nВ прошлом месяце не было выполненных утренних задач.", parse_mode="HTML")
        return
        
    text = f"📊 <b>Тест отчета за {first_day_last_month.strftime('%B %Y')}:</b>\n\n"
    text += f"✅ Выполнено задач: <b>{len(rows)}</b>\n\n"
    for r in rows:
        text += f"• {r['title']} <i>({r['category']})</i>\n"
        
    await message.answer(text, parse_mode="HTML")


@router.message(Command("test_expired"))
async def cmd_test_expired(message: Message):
    """Принудительно запускает проверку и удаление просроченных morning задач"""
    from scheduler.digest import process_expired_morning_tasks
    await process_expired_morning_tasks(message.bot, message.chat.id)
    await message.answer("✅ Проверка просроченных задач запущена! Проверьте чат на наличие уведомлений.")