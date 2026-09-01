"""
/habits — управление привычками.
/habit_new — создать привычку (через FSM).
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

import database as db
from handlers.common import parse_time, remove_keyboard

logger = logging.getLogger(__name__)
router = Router()

_scheduler = None


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


class NewHabit(StatesGroup):
    name     = State()
    category = State()
    target   = State()
    time     = State()


def habit_category_keyboard() -> ReplyKeyboardMarkup:
    cats = db.HABIT_CATEGORIES
    rows = [
        [KeyboardButton(text=c) for c in cats[:3]],
        [KeyboardButton(text=c) for c in cats[3:]],
        [KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def target_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="7 дней (каждый день)"), KeyboardButton(text="5 дней (рабочие)")],
            [KeyboardButton(text="3 дня"), KeyboardButton(text="1 день")],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True, one_time_keyboard=True,
    )


@router.message(Command("habits"))
async def cmd_habits(message: Message):
    habits = await db.get_habits(message.chat.id)

    if not habits:
        await message.answer(
            "У вас пока нет привычек.🙄\n\n"
            "Создать первую: /habit_new\n\n"
            "<i>Примеры: вода 💧, спорт 🏃, чтение 📚, сон 😴</i>",
            parse_mode="HTML",
        )
        return

    parts = [f"<b>Привычки ({len(habits)})</b>\n"]
    for h in habits:
        streak = await db.get_streak(h["id"])
        week = await db.get_week_stats(h["id"])
        done_today = await db.is_done_today(h["id"])

        status = "✅" if done_today else "⬜"
        streak_text = f" 🔥{streak}" if streak > 1 else ""
        parts.append(
            f"{status} <b>{h['name']}</b>  <code>[#{h['id']}]</code>{streak_text}\n"
            f"   {h['category']} · {week['done']}/{h['target_per_week']} за неделю · ⏰{h['reminder_time']}"
        )

    parts.append("\n<i>Удалить: /delete_habit &lt;id&gt;</i>")
    await message.answer("\n\n".join(parts), parse_mode="HTML")


@router.message(Command("habit_new"))
async def cmd_habit_new(message: Message, state: FSMContext):
    await state.set_state(NewHabit.name)
    await message.answer(
        "💪 <b>Новая привычка</b>\n\nКак называется привычка?\n"
        "<i>Например: Вода 💧, Спорт 🏃, Чтение 📚</i>",
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )


@router.message(NewHabit.name)
async def habit_name(message: Message, state: FSMContext):
    if message.text.strip() == "❌ Отмена":
        await _cancel(message, state)
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(NewHabit.category)
    await message.answer("Выберите <b>категорию</b>:", parse_mode="HTML", reply_markup=habit_category_keyboard())


@router.message(NewHabit.category)
async def habit_category(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Отмена":
        await _cancel(message, state)
        return
    if text not in db.HABIT_CATEGORIES:
        await message.answer("Выберите категорию из списка.")
        return
    await state.update_data(category=text)
    await state.set_state(NewHabit.target)
    await message.answer(
        "Сколько раз в неделю хотите выполнять?",
        reply_markup=target_keyboard(),
    )


@router.message(NewHabit.target)
async def habit_target(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Отмена":
        await _cancel(message, state)
        return
    mapping = {
        "7 дней (каждый день)": 7,
        "5 дней (рабочие)": 5,
        "3 дня": 3,
        "1 день": 1,
    }
    target = mapping.get(text)
    if not target:
        try:
            target = int(text.split()[0])
        except (ValueError, IndexError):
            await message.answer("Выберите из кнопок или введите число.")
            return
    await state.update_data(target=target)
    await state.set_state(NewHabit.time)
    await message.answer(
        "В какое время напоминать? Формат ЧЧ:ММ\n"
        "Пример: <code>21:00</code>",
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )


@router.message(NewHabit.time)
async def habit_time(message: Message, state: FSMContext):
    t = parse_time(message.text)
    if not t:
        await message.answer("Не поняла время.🙄 Формат: <code>21:00</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    habit_id = await db.create_habit(
        chat_id=message.chat.id,
        name=data["name"],
        category=data["category"],
        reminder_time=time_str,
        target_per_week=data["target"],
    )

    if _scheduler:
        habit = {
            "id": habit_id,
            "chat_id": message.chat.id,
            "name": data["name"],
            "reminder_time": time_str,
        }
        _scheduler.add_habit_schedule(habit)

    await state.clear()
    await message.answer(
        f"✅ <b>Привычка создана!</b>\n\n"
        f"💪 {data['name']}\n"
        f"🏷 {data['category']}\n"
        f"🎯 Цель: {data['target']} раз в неделю\n"
        f"⏰ Напоминание в {time_str}\n\n"
        f"Смотреть все привычки: /habits",
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )


@router.message(Command("delete_habit"))
async def cmd_delete_habit(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Укажите id привычки: /delete_habit 1")
        return
    try:
        habit_id = int(parts[1])
    except ValueError:
        await message.answer("id должен быть числом.")
        return

    habit = await db.get_habit(habit_id)
    if not habit or habit["chat_id"] != message.chat.id:
        await message.answer("Привычка не найдена.🙄")
        return

    await db.delete_habit(habit_id, message.chat.id)
    if _scheduler:
        _scheduler.remove_habit_schedule(habit_id)

    await message.answer(f"🗑 Привычка <b>{habit['name']}</b> удалена.", parse_mode="HTML")


async def _cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=remove_keyboard())
