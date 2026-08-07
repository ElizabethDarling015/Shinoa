"""
Конфигурация бота.
"""
import os
import sys
from dotenv import load_dotenv, find_dotenv

# Явно находим и загружаем .env рядом с запущенным файлом
dotenv_path = find_dotenv()
if not dotenv_path:
    sys.exit("❌ Файл .env не найден! Положи его рядом с bot.py")

load_dotenv(dotenv_path, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    sys.exit("❌ BOT_TOKEN не найден в .env")

DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Asia/Yekaterinburg")
DB_PATH = os.getenv("DB_PATH", "bot.db")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")  # Будет None, если ключа нет
ALLOWED_USERS_STR = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(x.strip()) for x in ALLOWED_USERS_STR.split(",") if x.strip()]