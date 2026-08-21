"""
Сервис для получения погоды через OpenWeatherMap API.
"""

import logging
import ssl
import aiohttp
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Словарь для красивых эмодзи погоды (на основе кодов OWM)
WEATHER_EMOJI = {
    "Clear": "☀️",
    "Clouds": "⛅️",
    "Rain": "🌧",
    "Drizzle": "🌦",
    "Thunderstorm": "⛈",
    "Snow": "❄️",
    "Mist": "🌫",
    "Fog": "🌫",
}

# Отключаем проверку SSL для обхода ошибки на macOS
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


async def get_weather(city: str, api_key: str) -> str | None:
    """
    Получает текущую погоду и прогноз на ближайшие дни.
    Возвращает отформатированную HTML-строку или None при ошибке.
    """
    # Создаём сессию с отключённой проверкой SSL
    connector = aiohttp.TCPConnector(ssl=_SSL_CONTEXT)
    
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Получаем текущую погоду
            current_url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric&lang=ru"
            async with session.get(current_url) as resp:
                if resp.status != 200:
                    logger.warning(f"Weather API error (current): {resp.status}")
                    return None
                current_data = await resp.json()

            # 2. Получаем прогноз на 5 дней (с шагом 3 часа)
            forecast_url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={api_key}&units=metric&lang=ru"
            async with session.get(forecast_url) as resp:
                if resp.status != 200:
                    logger.warning(f"Weather API error (forecast): {resp.status}")
                    return None
                forecast_data = await resp.json()

    except Exception as e:
        logger.error(f"Ошибка при запросе погоды: {e}")
        return None

    # --- Форматируем текущую погоду ---
    temp = round(current_data["main"]["temp"])
    feels_like = round(current_data["main"]["feels_like"])
    desc = current_data["weather"][0]["description"].capitalize()
    main_condition = current_data["weather"][0]["main"]
    emoji = WEATHER_EMOJI.get(main_condition, "🌤")
    wind = round(current_data["wind"]["speed"])
    
    current_text = (
        f"{emoji} <b>Сейчас:</b> <b>{temp}°C</b> (ощущается как {feels_like}°C)\n"
        f"   <i>{desc}</i>, ветер {wind} м/с\n"
    )

    # --- Форматируем прогноз по дням ---
    daily_forecast = []
    seen_dates = set()
    
    for item in forecast_data["list"]:
        dt_txt = item["dt_txt"]
        date_part = dt_txt.split(" ")[0]
        time_part = dt_txt.split(" ")[1]
        
        # Берем данные за 12:00 (или 15:00, если 12:00 нет)
        if "12:00:00" in time_part or ("15:00:00" in time_part and date_part not in seen_dates):
            if date_part in seen_dates:
                continue
            
            seen_dates.add(date_part)
            
            dt_obj = datetime.strptime(dt_txt, "%Y-%m-%d %H:%M:%S")
            today = datetime.now().date()
            
            # Перевод дней недели на русский
            weekdays_ru = {
                "Monday": "Понедельник",
                "Tuesday": "Вторник",
                "Wednesday": "Среда",
                "Thursday": "Четверг",
                "Friday": "Пятница",
                "Saturday": "Суббота",
                "Sunday": "Воскресенье",
            }
            day_en = dt_obj.strftime("%A")

            if dt_obj.date() == today:
                day_name = "📅 <b>Сегодня</b>"
            elif dt_obj.date() == today + timedelta(days=1):
                day_name = f"📅 <b>Завтра ({weekdays_ru.get(day_en, day_en)})</b>"
            else:
                day_name = f"📅 <b>{weekdays_ru.get(day_en, day_en)}</b>"

            temp_day = round(item["main"]["temp"])
            desc_day = item["weather"][0]["description"].capitalize()
            main_cond_day = item["weather"][0]["main"]
            emoji_day = WEATHER_EMOJI.get(main_cond_day, "🌤")
            
            daily_forecast.append(f"{day_name}: <b>{temp_day}°C</b>, {emoji_day} <i>{desc_day}</i>")
            
            # Ограничиваем 4 днями
            if len(daily_forecast) >= 4:
                break

    forecast_text = "\n".join(daily_forecast) if daily_forecast else "   <i>Прогноз недоступен</i>"

    # --- Собираем итоговое сообщение ---
    full_report = (
        f"🌍 <b>Погода в городе: {city.capitalize()}</b>\n\n"
        f"{current_text}"
        f"{forecast_text}"
    )
    
    return full_report