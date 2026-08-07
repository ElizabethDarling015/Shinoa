"""
Telegram Personal Organizer Bot
Запуск: python bot.py
"""

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import BOT_TOKEN, DEFAULT_TIMEZONE, ALLOWED_USERS
from handlers import main_router, set_scheduler
from scheduler import ReminderScheduler
from middlewares.access import AccessMiddleware

# ──────────────────────────────────────────────
# Логирование: консоль + файл (ротация 5 МБ, 3 архива)
# ──────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "logs/bot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


async def send_startup_message(bot: Bot):
    """
    Отправляет приветственное сообщение при запуске бота.
    Кнопка ведёт на главное меню через callback_data='start_main'.
    """
    text = "Я снова в строю!🎀 Когда начнём?😌"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅Сейчас!",
                    callback_data="start_main"
                )
            ]
        ]
    )

    for chat_id in ALLOWED_USERS:
        try:
            await bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard,
            )
            logger.info(f"Стартовое приветствие отправлено пользователю {chat_id}")
        except TelegramForbiddenError:
            logger.warning(
                f"Пользователь {chat_id} заблокировал бота или ещё не нажал /start"
            )
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                logger.warning(
                    f"Chat not found для {chat_id}. "
                    f"Возможно, пользователь ещё не запускал бота или указан неверный ID."
                )
            else:
                logger.warning(f"TelegramBadRequest для {chat_id}: {e}")
        except Exception as e:
            logger.warning(
                f"Не удалось отправить стартовое приветствие пользователю {chat_id}: {e}"
            )


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # ──────────────────────────────────────────
    # Подключение middleware для ограничения доступа
    # ──────────────────────────────────────────
    dp.update.middleware(AccessMiddleware())

    dp.include_router(main_router)

    # ──────────────────────────────────────────
    # Регистрация команд меню (выполняется при старте)
    # ──────────────────────────────────────────
    @dp.startup()
    async def set_commands(bot: Bot):
        commands = [
            BotCommand(command="start", description="Приветствие и меню"),
            BotCommand(command="help", description="Полная справка"),
            BotCommand(command="new", description="Еженедельное напоминание"),
            BotCommand(command="daily", description="Ежедневная задача"),
            BotCommand(command="morning", description="Задача на завтра"),
            BotCommand(command="monthly", description="Ежемесячное напоминание"),
            BotCommand(command="list", description="Список задач"),
            BotCommand(command="delete", description="Удалить задачу"),
            BotCommand(command="habits", description="Список привычек"),
            BotCommand(command="habit_new", description="Новая привычка"),
            BotCommand(command="idea", description="Сохранить идею"),
            BotCommand(command="find", description="Поиск по архиву"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="settings", description="Настройки"),
            BotCommand(command="setcity", description="Город для погоды"),
            BotCommand(command="settimezone", description="Часовой пояс"),
            BotCommand(command="digesttime", description="Время сводки"),
        ]
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
            logger.info("Команды бота обновлены ✅")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить команды бота (проблема с сетью): {e}")
            logger.info(
                "Бот продолжит работу. Команды можно обновить вручную через @BotFather "
                "или перезапустить бота."
            )

    # ──────────────────────────────────────────
    # Глобальный обработчик ошибок
    # ──────────────────────────────────────────
    @dp.errors
    async def on_error(event: types.ErrorEvent, bot: Bot):
        # Игнорируем сетевые ошибки при выключении бота (Ctrl+C)
        if "Connector is closed" in str(event.exception):
            return

        if "message is not modified" in str(event.exception).lower():
            return

        logger.exception("Unhandled error in update: %s", event.exception)

        if event.update.message:
            try:
                await event.update.message.answer(
                    "⚠️ Произошла ошибка. Попробуй позже или /start.",
                    reply_markup=types.ReplyKeyboardRemove(),
                )
            except Exception as e:
                logger.error("Не удалось отправить сообщение об ошибке: %s", e)

    scheduler = ReminderScheduler(bot, default_timezone=DEFAULT_TIMEZONE)
    set_scheduler(scheduler)
    await scheduler.start()

    from handlers.start import register_start
    register_start(dp)

    logger.info("Бот запущен ✅")

    # ──────────────────────────────────────────
    # Приветствие при запуске бота
    # ──────────────────────────────────────────
    try:
        await send_startup_message(bot)
    except Exception as e:
        logger.error(f"Ошибка стартовой рассылки: {e}")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await scheduler.stop()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())