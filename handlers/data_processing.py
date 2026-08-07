"""
Работа с данными: AI, транскрибация, Obsidian, погода, статистика.
"""

import logging
import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import get_nav_buttons

logger = logging.getLogger(__name__)
router = Router()


def get_data_processing_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подменю 'Работа с данными' (кнопки наравне, главная растянута)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌤 Прогноз", callback_data="data_weather"),
            InlineKeyboardButton(text="📊 За месяц", callback_data="data_monthly_work"),
        ],
        [
            InlineKeyboardButton(text="🎙 Транскрибация", callback_data="data_transcribe"),
            InlineKeyboardButton(text="📓 Obsidian", callback_data="data_obsidian"),
        ],
        [
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ]
    ])


@router.callback_query(F.data == "start_data")
async def cb_start_data(call: CallbackQuery):
    """Открывает подменю 'Работа с данными'"""
    await call.answer()
    try:
        await call.message.edit_text(
            "📊 <b>Работа с данными</b>\n\n"
            "Здесь будут инструменты для обработки твоих файлов, голосовых и информации с помощью AI.\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=get_data_processing_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "data_weather")
async def cb_data_weather(call: CallbackQuery):
    """Показывает погоду для города пользователя"""
    await call.answer()
    
    user = await db.get_user(call.message.chat.id)
    city = user.get("city") if user else None
    
    if not city or city == "не задан":
        try:
            await call.message.edit_text(
                "🌤 <b>Прогноз погоды</b>\n\n"
                "Сначала укажи свой город в <b>Настройках</b> (кнопка ⚙️ или команда /setcity).",
                parse_mode="HTML",
                reply_markup=get_data_processing_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
        return

    try:
        from config import WEATHER_API_KEY
        from services.weather import get_weather
        
        await call.message.edit_text("⏳ Загружаю прогноз погоды...", parse_mode="HTML")
        
        if WEATHER_API_KEY and WEATHER_API_KEY != "YOUR_WEATHER_API_KEY":
            weather_info = await get_weather(city, WEATHER_API_KEY)
            if weather_info:
                text = weather_info
            else:
                text = f"⚠️ Не удалось получить погоду для города «{city}».\nПроверь правильность названия в настройках."
        else:
            text = f"⚠️ API ключ погоды не настроен в `config.py`.\nНо твой город сохранен: <b>{city}</b>."
            
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_data_processing_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при получении погоды: {e}")
        await call.message.edit_text(
            "❌ Произошла ошибка при загрузке погоды. Попробуй позже.",
            parse_mode="HTML",
            reply_markup=get_data_processing_keyboard()
        )


@router.callback_query(F.data == "data_monthly_work")
async def cb_data_monthly_work(call: CallbackQuery):
    """Показывает утренние задачи"""
    await call.answer()
    try:
        await call.message.edit_text("⏳ Загружаю данные...", parse_mode="HTML")
        
        tasks = await db.get_monthly_morning_tasks(call.message.chat.id)
        
        if not tasks:
            text = "📊 <b>Утренние задачи</b>\n\nЗа последнее время утренних задач не было."
        else:
            text = f"📊 <b>Последние утренние задачи</b>\n\n"
            text += f"Всего найдено: <b>{len(tasks)}</b>\n\n"
            
            for task in tasks:
                # Безопасное получение даты: если колонки created_at нет, покажем ID
                date_str = task.get("created_at", "").split(" ")[0] if task.get("created_at") else f"ID: {task['id']}"
                p_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task.get("priority"), "⚪️")
                title = task.get("title", "Без названия")
                
                text += f"📅 {date_str} | {p_emoji} <b>{title}</b>\n"
                if task.get("text"):
                    preview = task["text"][:60] + ("…" if len(task["text"]) > 60 else "")
                    text += f"   <i>{preview}</i>\n"
                text += "\n"

        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_data_processing_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке работы за месяц: {e}")
        # Выводим саму ошибку, чтобы ты видел, в чем именно проблема, если она повторится
        await call.message.edit_text(
            f"❌ Произошла ошибка при загрузке данных.\n\n<i>Детали: {e}</i>",
            parse_mode="HTML",
            reply_markup=get_data_processing_keyboard()
        )


@router.callback_query(F.data == "data_transcribe")
async def cb_data_transcribe(call: CallbackQuery):
    """Заглушка для транскрибации"""
    await call.answer("🚧 Функция транскрибации голосовых в разработке", show_alert=True)


@router.callback_query(F.data == "data_obsidian")
async def cb_data_obsidian(call: CallbackQuery):
    """Заглушка для Obsidian"""
    await call.answer("🚧 Интеграция с Obsidian в разработке", show_alert=True)