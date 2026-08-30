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
    """Клавиатура навигации внутри шагов FSM настроек (без кнопки 'Закрыть')"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings_back"),
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ]
    ])


async def _edit_settings_msg(
    bot,
    chat_id: int,
    message_id: int | None,
    text: str,
    keyboard: InlineKeyboardMarkup,
):
    """
    Редактирует сообщение-контейнер настроек по сохранённому ID.
    Если ID нет или редактирование невозможно — отправляет новое (фолбэк).
    """
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.warning("Не удалось отредактировать сообщение настроек: %s", e)
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "settings_close")
async def cb_settings_close(call: CallbackQuery):
    """Удаляет текущее сообщение настроек"""
    try:
        await call.message.delete()
    except TelegramBadRequest as e:
        logger.warning("Не удалось удалить сообщение: %s", e)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await call.answer()


@router.callback_query(F.data == "settings_back")
async def cb_settings_back(call: CallbackQuery, state: FSMContext):
    """Возврат в меню настроек из FSM — редактирует ТЕКУЩЕЕ сообщение"""
    await state.clear()
    await call.answer()

    try:
        await call.message.edit_text(
            await _settings_text(call.message.chat.id),
            parse_mode="HTML",
            reply_markup=get_settings_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "system_stub")
async def cb_system_stub(call: CallbackQuery):
    """Заглушка — зарезервирована под будущие функции (например, нейросеть)"""
    await call.answer("🚧 Эта кнопка в разработке")


@router.callback_query(F.data == "system_update")
async def cb_system_update(call: CallbackQuery):
    """Обновление с GitHub: git pull + pip install + перезапуск. Все статусы — в одном сообщении."""
    await call.answer()

    async def show(text: str, keyboard: InlineKeyboardMarkup = None):
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise

    await show("⏳ Обновляюсь из репозитория...")

    # ШАГ 1: git pull
    try:
        import shutil
        git_bin = shutil.which("git") or "/usr/bin/git"
        proc = await asyncio.create_subprocess_exec(
            git_bin, "pull", "--ff-only",
            cwd=PROJECT_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode().strip()
    except asyncio.TimeoutError:
        proc.kill()
        await show("❌ Таймаут обновления — GitHub не ответил. Попробуй позже.", get_settings_keyboard())
        return
    except Exception as e:
        logger.exception("Ошибка обновления: %s", e)
        await show(f"❌ Не удалось запустить обновление:\n<code>{html.escape(str(e))}</code>", get_settings_keyboard())
        return

    if proc.returncode != 0:
        await show(f"❌ Ошибка обновления:\n<code>{html.escape(output[:1000])}</code>", get_settings_keyboard())
        return

    if "Already up to date" in output:
        await show("✅ Уже актуально — обновлений нет.", get_settings_keyboard())
        return

    # ШАГ 2: pip install -r requirements.txt
    await show("✅ Код обновлён!\n⏳ Устанавливаю зависимости...")

    try:
        pip_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
            cwd=PROJECT_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        pip_stdout, _ = await asyncio.wait_for(pip_proc.communicate(), timeout=120)
        pip_output = pip_stdout.decode().strip()
    except asyncio.TimeoutError:
        pip_proc.kill()
        await show("❌ Таймаут установки зависимостей — pip завис. Попробуй позже.", get_settings_keyboard())
        return
    except Exception as e:
        logger.exception("Ошибка установки зависимостей: %s", e)
        await show(f"❌ Не удалось установить зависимости:\n<code>{html.escape(str(e))}</code>", get_settings_keyboard())
        return

    if pip_proc.returncode != 0:
        await show(f"❌ Ошибка установки зависимостей:\n<code>{html.escape(pip_output[:1000])}</code>", get_settings_keyboard())
        return

    # ШАГ 3: Перезапуск бота
    await show("✅ Код и зависимости обновлены!\n🔄 Перезапускаюсь...")
    asyncio.create_task(_restart_bot(call.message.chat.id, call.message.message_id))


RESTART_MARKER = PROJECT_ROOT / "restart_marker.json"

async def _restart_bot(chat_id: int, message_id: int):
    """Перезапуск бота: маркер + systemctl (если сервис), иначе os.execv"""
    import shutil
    await asyncio.sleep(2)

    try:
        RESTART_MARKER.write_text(
            json.dumps({"chat_id": chat_id, "message_id": message_id}),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Не удалось записать маркер перезапуска: %s", e)

    if shutil.which("systemctl"):
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", "shinoa.service",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info("Перезапуск через systemctl shinoa.service выполнен")
            return

    logger.info("Перезапуск через os.execv (фолбэк)")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ──────────────────────────────────────────────────────────
# ВРЕМЯ СВОДКИ
# ──────────────────────────────────────────────────────────

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


@router.message(Command("digesttime"))
async def cmd_digesttime(message: Message, state: FSMContext):
    await state.clear()
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
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")

    # Удаляем сообщение пользователя с введённым временем
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение пользователя: %s", e)

    t = parse_time(message.text)
    if not t:
        # Ошибку показываем в том же сообщении-контейнере, состояние сохраняем
        await _edit_settings_msg(
            message.bot, message.chat.id, bot_msg_id,
            "⚠️ Не понял время. Формат: <code>07:00</code>\n\n"
            "🌅 В какое время присылать утреннюю сводку?",
            get_settings_nav_keyboard(),
        )
        return

    hour, minute = t
    time_str = f"{hour:02d}:{minute:02d}"

    await set_digest_time(message.chat.id, time_str)
    await state.clear()

    success_text = (
        f"✅ Утренняя сводка будет приходить в <b>{time_str}</b>.\n\n"
        f"<i>Изменение вступит в силу со следующего дня.</i>"
    )
    await _edit_settings_msg(
        message.bot, message.chat.id, bot_msg_id,
        success_text,
        get_settings_nav_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# ГОРОД (весь алгоритм — в одном сообщении-контейнере)
# ──────────────────────────────────────────────────────────

CITY_PROMPT = (
    "🌆 Введите название города для погоды в утренней сводке:\n\n"
    "Примеры: <code>Москва</code>, <code>Санкт-Петербург</code>, <code>Berlin</code>"
)


@router.callback_query(F.data == "settings_setcity")
async def cb_settings_setcity(call: CallbackQuery, state: FSMContext):
    """Переход к установке города — РЕДАКТИРУЕТ текущее сообщение"""
    await state.clear()
    await state.set_state(SetCity.city)
    await state.update_data(bot_msg_id=call.message.message_id)
    await call.answer()

    try:
        await call.message.edit_text(
            CITY_PROMPT,
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.message(Command("setcity"))
async def cmd_setcity(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SetCity.city)
    bot_msg = await message.answer(
        CITY_PROMPT,
        parse_mode="HTML",
        reply_markup=get_settings_nav_keyboard(),
    )
    await state.update_data(bot_msg_id=bot_msg.message_id)


@router.message(SetCity.city)
async def step_city(message: Message, state: FSMContext):
    city = message.text.strip()

    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")

    # Удаляем сообщение пользователя с введённым городом
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение пользователя: %s", e)

    if city.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await _edit_settings_msg(
            message.bot, message.chat.id, bot_msg_id,
            "Отменено.",
            get_settings_nav_keyboard(),
        )
        return

    # Проверяем что погода для этого города работает
    from config import WEATHER_API_KEY
    from services.weather import get_weather

    # Статус проверки — в том же сообщении-контейнере
    await _edit_settings_msg(
        message.bot, message.chat.id, bot_msg_id,
        "⏳ Проверяю город...",
        get_settings_nav_keyboard(),
    )

    if WEATHER_API_KEY and WEATHER_API_KEY != "YOUR_WEATHER_API_KEY":
        weather = await get_weather(city, WEATHER_API_KEY)
        if not weather:
            # Город не найден — остаёмся в состоянии, просим повторить ввод в том же сообщении
            await _edit_settings_msg(
                message.bot, message.chat.id, bot_msg_id,
                f"⚠️ Не могу найти город «{html.escape(city)}».\n"
                "Попробуй написать по-другому или на латинице.\n\n"
                f"{CITY_PROMPT}",
                get_settings_nav_keyboard(),
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

    await _edit_settings_msg(
        message.bot, message.chat.id, bot_msg_id,
        f"✅ Город сохранён: <b>{html.escape(city)}</b>{city_confirmation}\n\n"
        f"Утренняя сводка будет приходить с погодой.",
        get_settings_nav_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# ЧАСОВОЙ ПОЯС
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "settings_settimezone")
async def cb_settings_settimezone(call: CallbackQuery, state: FSMContext):
    """Переход к установке часового пояса через кнопку — показывает подсказку и ждёт ввод"""
    await state.clear()
    await state.set_state(SetTimezone.tz)
    await state.update_data(bot_msg_id=call.message.message_id)
    await call.answer()

    try:
        await call.message.edit_text(
            "☀️Укажи название <b>UTC</b>:\n\n"
            "<b>Например:</b> <code>Asia/Yekaterinburg</code> 😌",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.message(SetTimezone.tz)
async def step_timezone(message: Message, state: FSMContext):
    """Обработчик ввода часового пояса через FSM (после нажатия кнопки)"""
    tz = message.text.strip()

    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")

    # Удаляем сообщение пользователя с введённым поясом
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение пользователя: %s", e)

    if tz.lower() in ("отмена", "cancel"):
        await state.clear()
        await _edit_settings_msg(
            message.bot, message.chat.id, bot_msg_id,
            "Отменено.",
            get_settings_nav_keyboard(),
        )
        return

    try:
        import pytz
        pytz.timezone(tz)
        await upsert_user(message.chat.id, timezone=tz)
        await state.clear()
        await _edit_settings_msg(
            message.bot, message.chat.id, bot_msg_id,
            f"✅ Часовой пояс: <b>{html.escape(tz)}</b>",
            get_settings_nav_keyboard(),
        )
    except Exception:
        await _edit_settings_msg(
            message.bot, message.chat.id, bot_msg_id,
            "❌ Неверный часовой пояс. Пример: <code>Asia/Yekaterinburg</code>\n\n"
            "☀️Укажи название <b>UTC</b>:",
            get_settings_nav_keyboard(),
        )


# ──────────────────────────────────────────────────────────
# ЭКРАН НАСТРОЕК
# ──────────────────────────────────────────────────────────

async def _settings_text(chat_id: int) -> str:
    """Собирает текст экрана настроек (для команды и для редактирования)"""
    user = await get_user(chat_id)

    city = user["city"] if user and user.get("city") else "не задан"
    digest_time = user["digest_time"] if user else "07:00"
    tz = user["timezone"] if user else "Europe/Moscow"

    return (
        "<b>⚙️ Настройки</b>\n\n"
        f"🌆 Город для погоды: <b>{city}</b>\n"
        f"🌅 Время утренней сводки: <b>{digest_time}</b>\n"
        f"🕐 Часовой пояс: <b>{tz}</b>\n\n"
        "<i>Используй кнопки ниже для изменения настроек.</i>"
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer(
        await _settings_text(message.chat.id),
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
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
            f"✅ Часовой пояс: <b>{html.escape(tz)}</b>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )
    except Exception:
        await message.answer(
            "❌ Неверный часовой пояс. Пример: <code>Asia/Yekaterinburg</code>",
            parse_mode="HTML",
            reply_markup=get_settings_nav_keyboard(),
        )