"""
Настройки пользователя:
/settings    — показать текущие настройки
/setcity     — установить город для погоды в сводке
/digesttime  — изменить время утренней сводки
/settimezone — установить часовой пояс
"""

import logging
import asyncio
import html
import os
import sys
import json

from pathlib import Path
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from database.users import get_user, set_city, set_digest_time, upsert_user
from handlers.common import parse_time, remove_keyboard

logger = logging.getLogger(__name__)
router = Router()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SetCity(StatesGroup):
    city = State()


class SetDigestTime(StatesGroup):
    time = State()


class SetTimezone(StatesGroup):
    tz = State()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура настроек"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌅 Изменить время сводки", callback_data="settings_digesttime"),
            InlineKeyboardButton(text="🌆 Установить город", callback_data="settings_setcity"),
        ],
        [
            InlineKeyboardButton(text="🪐 Установить UTC", callback_data="settings_settimezone"),
            InlineKeyboardButton(text="🔄 Обновиться", callback_data="system_update"),
        ],
        [
            InlineKeyboardButton(text="❌ Закрыть", callback_data="settings_close"),
            InlineKeyboardButton(text="➖", callback_data="system_stub"),
        ],
        [
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ]
    ])


def get_settings_nav_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура навигации внутри шагов FSM настроек"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings_back"),
            InlineKeyboardButton(text="❌ Закрыть", callback_data="settings_close"),
        ],
        [
            # Одна кнопка в ряду — растягивается на всю ширину
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ]
    ])


@router.callback_query(F.data == "settings_close")
async def cb_settings_close(call: CallbackQuery):
    """Удаляет текущее сообщение настроек"""
    try:
        await call.message.delete()
    except TelegramBadRequest as e:
        # Сообщение старше 48 часов — Telegram запрещает удаление
        logger.warning("Не удалось удалить сообщение: %s", e)
        try:
            # Фолбэк: снимаем клавиатуру, чтобы кнопки не висели на старом сообщении
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await call.answer()


@router.callback_query(F.data == "settings_back")
async def cb_settings_back(call: CallbackQuery, state: FSMContext):
    """Возврат в меню настроек из FSM"""
    await state.clear()
    from handlers.settings import cmd_settings
    await call.answer()
    await cmd_settings(call.message)

@router.callback_query(F.data == "system_stub")
async def cb_system_stub(call: CallbackQuery):
    """Заглушка — зарезервирована под будущие функции (например, нейросеть)"""
    await call.answer("🚧 Эта кнопка в разработке")


@router.callback_query(F.data == "system_update")
async def cb_system_update(call: CallbackQuery):
    """Обновление с GitHub: git pull + перезапуск. Все статусы — в одном сообщении."""
    await call.answer()

    async def show(text: str, keyboard: InlineKeyboardMarkup = None):
        """Редактирует ТЕКУЩЕЕ сообщение; игнорирует 'message is not modified'"""
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise

    await show("⏳ Обновляюсь из репозитория...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "--ff-only",
            cwd=PROJECT_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode().strip()
    except asyncio.TimeoutError:
        proc.kill()
        await show(
            "❌ Таймаут обновления — GitHub не ответил. Попробуй позже.",
            get_settings_keyboard(),
        )
        return
    except Exception as e:
        logger.exception("Ошибка обновления: %s", e)
        await show(
            f"❌ Не удалось запустить обновление:\n<code>{html.escape(str(e))}</code>",
            get_settings_keyboard(),
        )
        return

    if proc.returncode != 0:
        await show(
            f"❌ Ошибка обновления:\n<code>{html.escape(output[:1000])}</code>",
            get_settings_keyboard(),
        )
        return

    if "Already up to date" in output:
        await show("✅ Уже актуально — обновлений нет.", get_settings_keyboard())
        return

    await show("✅ Обновилась! Перезапускаюсь... 🔄")
    asyncio.create_task(
        _restart_bot(call.message.chat.id, call.message.message_id)
    )


# Файл-маркер: говорит новому процессу, какое сообщение отредактировать вместо приветствия
RESTART_MARKER = PROJECT_ROOT / "restart_marker.json"


async def _restart_bot(chat_id: int, message_id: int):
    """Перезапуск: даём сообщению долететь, оставляем маркер, заменяем процесс"""
    await asyncio.sleep(2)
    try:
        RESTART_MARKER.write_text(
            json.dumps({"chat_id": chat_id, "message_id": message_id}),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Не удалось записать маркер перезапуска: %s", e)
    logger.info("Перезапуск бота после обновления (os.execv)")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@router.callback_query(F.data == "settings_digesttime")
async def cb_settings_digesttime(call: CallbackQuery, state: FSMContext):
    """Переход к изменению времени сводки (редактирует сообщение)"""
    await state.clear()
    await state.set_state(SetDigestTime.time)
    
    # Сохраняем ID сообщения для последующего редактирования
    await state.update_data(bot_msg_id=call.message.message_id)
    
    await call.message.edit_text(
        "🌅 В какое время присылать утреннюю сводку?\n\n"
        "Формат ЧЧ:ММ, например: <code>07:00</code> или <code>08:30</code>",
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "settings_setcity")
async def cb_settings_setcity(call: CallbackQuery, state: FSMContext):
    """Переход к установке города"""
    await state.clear()
    from handlers.settings import cmd_setcity
    await call.answer()
    await cmd_setcity(call.message, state)


@router.callback_query(F.data == "settings_settimezone")
async def cb_settings_settimezone(call: CallbackQuery, state: FSMContext):
    """Переход к установке часового пояса через кнопку — показывает подсказку и ждёт ввод"""
    await state.clear()
    await state.set_state(SetTimezone.tz)
    await call.message.answer(
        "☀️Укажи название <b>UTC</b>:\n\n"
        "<b>Например:</b> <code>Asia/Yekaterinburg</code> 😌",
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )
    await call.answer()


@router.message(SetTimezone.tz)
async def step_timezone(message: Message, state: FSMContext):
    """Обработчик ввода часового пояса через FSM (после нажатия кнопки)"""
    tz = message.text.strip()
    if tz.lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_settings_nav_keyboard())
        return
    try:
        import pytz
        pytz.timezone(tz)
        await upsert_user(message.chat.id, timezone=tz)
        await state.clear()
        await message.answer(
            f"✅ Часовой пояс: <b>{tz}</b>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    except Exception:
        await message.answer(
            "❌ Неверный часовой пояс. Пример: <code>Asia/Yekaterinburg</code>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    user = await get_user(message.chat.id)

    city = user["city"] if user and user.get("city") else "не задан"
    digest_time = user["digest_time"] if user else "07:00"
    tz = user["timezone"] if user else "Europe/Moscow"

    await message.answer(
        "<b>⚙️ Настройки</b>\n\n"
        f"🌆 Город для погоды: <b>{city}</b>\n"
        f"🌅 Время утренней сводки: <b>{digest_time}</b>\n"
        f"🕐 Часовой пояс: <b>{tz}</b>\n\n"
        "<i>Используй кнопки ниже для изменения настроек.</i>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )


@router.message(Command("setcity"))
async def cmd_setcity(message: Message, state: FSMContext):
    await state.set_state(SetCity.city)
    await message.answer(
        "🌆 Введите название города для погоды в утренней сводке:\n\n"
        "Примеры: <code>Москва</code>, <code>Санкт-Петербург</code>, <code>Berlin</code>",
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )


@router.message(SetCity.city)
async def step_city(message: Message, state: FSMContext):
    city = message.text.strip()
    if city == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_settings_nav_keyboard())
        return

    # Проверяем что погода для этого города работает
    from config import WEATHER_API_KEY
    from services.weather import get_weather

    await message.answer("Проверяю город...")

    if WEATHER_API_KEY and WEATHER_API_KEY != "YOUR_WEATHER_API_KEY":
        weather = await get_weather(city, WEATHER_API_KEY)
        if not weather:
            await message.answer(
                f"⚠️ Не могу найти город «{city}».\n"
                "Попробуй написать по-другому или на латинице.",
                reply_markup=get_settings_nav_keyboard(),
            )
            return
        city_confirmation = f"\n\nТекущая погода: {weather}"
    else:
        weather = None
        city_confirmation = (
            "\n\n<i>Погода пока недоступна — WEATHER_API_KEY не задан в config.py.\n"
            "Город сохранён и будет использован когда добавишь ключ.</i>"
        )

    # Создаём запись пользователя если нет
    await upsert_user(message.chat.id, city=city)
    await state.clear()

    await message.answer(
        f"✅ Город сохранён: <b>{city}</b>{city_confirmation}\n\n"
        f"Утренняя сводка будет приходить с погодой.",
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )


@router.message(Command("digesttime"))
async def cmd_digesttime(message: Message, state: FSMContext):
    await state.set_state(SetDigestTime.time)
    # Отправляем новое сообщение и сохраняем его ID для редактирования на следующем шаге
    bot_msg = await message.answer(
        "🌅 В какое время присылать утреннюю сводку?\n\n"
        "Формат ЧЧ:ММ, например: <code>07:00</code> или <code>08:30</code>",
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )
    await state.update_data(bot_msg_id=bot_msg.message_id)


@router.message(SetDigestTime.time)
async def step_digest_time(message: Message, state: FSMContext):
    t = parse_time(message.text)
    if not t:
        await message.answer(
            "Не понял время. Формат: <code>07:00</code>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    # Получаем ID сообщения ДО очистки состояния
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")

    await set_digest_time(message.chat.id, time_str)
    await state.clear()

    success_text = (
        f"✅ Утренняя сводка будет приходить в <b>{time_str}</b>.\n\n"
        f"<i>Изменение вступит в силу со следующего дня.</i>"
    )

    # Если у нас есть ID сообщения, редактируем его. Иначе отправляем новое (фоллбэк)
    if bot_msg_id:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text=success_text,
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    else:
        await message.answer(
            success_text,
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )


@router.message(Command("settimezone"))
async def cmd_settimezone(message: Message):
    """Обработчик команды /settimezone (для прямого ввода через команду)"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "☀️Укажи название <b>UTC</b>🪐, после: <code>/settimezone</code> \n\n"
            "<b>Например:</b> <code>/settimezone Asia/Yekaterinburg</code> 😌",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
        return
    tz = parts[1].strip()
    try:
        import pytz
        pytz.timezone(tz)  # проверка валидности
        await upsert_user(message.chat.id, timezone=tz)
        await message.answer(
            f"✅ Часовой пояс: <b>{tz}</b>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    except Exception:
        await message.answer(
            "❌ Неверный часовой пояс. Пример: <code>Asia/Yekaterinburg</code>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )