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
    """Клавиатура подменю 'Работа с данными': существующие инструменты + сервисы из реестра (парсеры и т.п.)."""
    from services.service_registry import list_services

    rows = [
        [
            InlineKeyboardButton(text="🌤 Прогноз", callback_data="data_weather"),
            InlineKeyboardButton(text="📊 За месяц", callback_data="data_monthly_work"),
        ],
        [
            InlineKeyboardButton(text="🎙 Транскрибация", callback_data="data_transcribe"),
            InlineKeyboardButton(text="📓 Obsidian", callback_data="data_obsidian"),
        ],
    ]

    # "Мой IP" и все сервисы из реестра пакуются по 2 в ряд — так они не
    # растягиваются по одному в ряд и новые сервисы просто продолжают сетку.
    tail_buttons = [InlineKeyboardButton(text="🌐 Мой IP", callback_data="data_myip")]
    for service_id, cfg in list_services():
        tail_buttons.append(InlineKeyboardButton(text=cfg["title"], callback_data=f"svc_open:{service_id}"))

    for i in range(0, len(tail_buttons), 2):
        rows.append(tail_buttons[i:i + 2])

    rows.append([InlineKeyboardButton(text="🏠 На главную", callback_data="start_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

@router.callback_query(F.data == "data_stub")
async def cb_data_stub(call: CallbackQuery):
    """Заглушка для чётности"""
    await call.answer("🚧 Эта кнопка в разработке")

@router.callback_query(F.data == "data_myip")
async def cb_data_myip(call: CallbackQuery):
    """🌐 Мой IP: локальный VM, туннельный и домашний внешний (через хост)"""
    import socket
    import aiohttp
    from urllib.parse import urlparse
    from config import HOST_IP_URL

    await call.answer()

    def local_ip(route_to: str) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((route_to, 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "н/д"

    gateway = urlparse(HOST_IP_URL).hostname if HOST_IP_URL else None

    lan = local_ip(gateway) if gateway else "н/д"
    tun = local_ip("8.8.8.8")

    wan = None
    if HOST_IP_URL:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(HOST_IP_URL) as resp:
                    wan = (await resp.json()).get("wan")
        except Exception as e:
            logger.warning("Хост недоступен для запроса IP: %s", e)

    lines = ["🌐 <b>Мой IP</b>", ""]
    lines.append(f"🏠 Локальный (VM): <code>{lan}</code>")
    if tun != lan:
        lines.append(f"🕳️ Туннель (VPN): <code>{tun}</code>")
    lines.append(
        f"🌍 Домашний внешний: <code>{wan}</code>"
        if wan else "🌍 Домашний внешний: <i>недоступен</i>"
    )

    nav = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="start_data"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ]
    ])

    try:
        await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=nav)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            await call.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=nav)
