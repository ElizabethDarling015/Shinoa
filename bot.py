"""
Telegram Personal Organizer Bot
Запуск: python bot.py
"""

import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands
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

# ──────────────────────────────────────────────
# Роутер для обработки кнопки «До завтра» на прощальном сообщении
# ──────────────────────────────────────────────
goodbye_router = Router(name="goodbye_router")


@goodbye_router.callback_query(F.data == "goodbye_close")
async def cb_goodbye_close(call: CallbackQuery):
    """Кнопка 'До завтра 🌙' — удаляет прощальное сообщение."""
    try:
        await call.message.delete()
    except TelegramBadRequest as e:
        # Если прошло >48 часов — Telegram запрещает удаление, снимаем только клавиатуру
        logger.warning("Не удалось удалить прощальное сообщение: %s", e)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Не удалось удалить прощальное сообщение: %s", e)
    
    try:
        await call.answer()
    except TelegramBadRequest:
        pass  # Callback устарел — игнорируем


# ──────────────────────────────────────────────
# Рассылка приветствия при старте
# ──────────────────────────────────────────────
STARTUP_TEXT = "Я снова в строю!🎀 Когда начнём?😌"
STARTUP_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅Сейчас!", callback_data="start_main")]
    ]
)


async def send_startup_message(bot: Bot):
    """Отправляет приветственное сообщение при обычном запуске бота."""
    for chat_id in ALLOWED_USERS:
        try:
            await bot.send_message(chat_id, STARTUP_TEXT, reply_markup=STARTUP_KEYBOARD)
            logger.info(f"Стартовое приветствие отправлено пользователю {chat_id}")
        except TelegramForbiddenError:
            logger.warning(f"Пользователь {chat_id} заблокировал бота или ещё не нажал /start")
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                logger.warning(f"Chat not found для {chat_id}. Возможно, пользователь ещё не запускал бота или указан неверный ID.")
            else:
                logger.warning(f"TelegramBadRequest для {chat_id}: {e}")
        except Exception as e:
            logger.warning(f"Не удалось отправить стартовое приветствие пользователю {chat_id}: {e}")


# ──────────────────────────────────────────────
# Рассылка прощания при остановке
# ──────────────────────────────────────────────
async def send_goodbye_message(bot: Bot):
    """
    Отправляет уведомление об остановке всем ALLOWED_USERS.
    Вызывается в finally при остановке polling'а.
    """
    text = "Я ушла. До завтра 🌙"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="До завтра 🌙",
                    callback_data="goodbye_close"
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
            logger.info(f"Прощальное сообщение отправлено пользователю {chat_id}")
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
                f"Не удалось отправить прощальное сообщение пользователю {chat_id}: {e}"
            )


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # ──────────────────────────────────────────
    # Подключение middleware для ограничения доступа
    # ──────────────────────────────────────────
    dp.update.middleware(AccessMiddleware())

    dp.include_router(main_router)
    dp.include_router(goodbye_router)   # ← роутер для кнопки прощания

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
        
        # 1. Очищаем глобальные команды и глобальную кнопку меню
        try:
            await bot.delete_my_commands(scope=BotCommandScopeDefault())
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands()) # Возвращаем глобальную кнопку как фоллбэк
            logger.info("Глобальные настройки очищены и сброшены 🧹")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось очистить глобальные настройки: {e}")

        # 2. Устанавливаем команды и кнопку меню ТОЛЬКО для авторизованных пользователей
        for user_id in ALLOWED_USERS:
            try:
                await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
                await bot.set_chat_menu_button(
                    chat_id=user_id, 
                    menu_button=MenuButtonCommands()
                )
                logger.info(f"Команды и кнопка 'Меню' установлены для пользователя {user_id} ✅")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось настроить пользователя {user_id}: {e}")

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

    # ──────────────────────────────────────────
    # Восстановление сервисных потоков (парсер и т.п.), переживших этот
    # рестарт бота — см. services/service_manager.py (recover) и
    # handlers/services_control.py (recover_services) для подробностей.
    # ──────────────────────────────────────────
    try:
        from handlers.services_control import recover_services
        await recover_services(bot)
    except Exception as e:
        logger.warning("Не удалось восстановить сервисные потоки после рестарта: %s", e)

    logger.info("Бот запущен ✅")

    try:
        # ──────────────────────────────────────────
        # Приветствие при старте ИЛИ продолжение сообщения после update-перезапуска
        # ──────────────────────────────────────────
        try:
            marker_path = Path(__file__).resolve().parent / "restart_marker.json"
            if marker_path.exists():
                try:
                    data = json.loads(marker_path.read_text(encoding="utf-8"))
                    await bot.edit_message_text(
                        STARTUP_TEXT,
                        chat_id=data["chat_id"],
                        message_id=data["message_id"],
                        reply_markup=STARTUP_KEYBOARD,
                    )
                    logger.info("Перезапуск после обновления: сообщение отредактировано на месте")
                except Exception as e:
                    logger.warning("Не удалось отредактировать сообщение после обновления: %s", e)
                    await send_startup_message(bot)
                finally:
                    marker_path.unlink(missing_ok=True)
            else:
                await send_startup_message(bot)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise  # Ctrl+C — уходим во внешний finally
        except Exception as e:
            logger.error(f"Ошибка стартовой рассылки: {e}")

        await dp.start_polling(bot)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Получен сигнал остановки — завершаюсь корректно")
    except Exception as e:
        logger.exception("Критическая ошибка в main(): %s", e)
    finally:
        # Прощание и очистка — ТЕПЕРЬ при любой остановке, в любой фазе
        try:
            await send_goodbye_message(bot)
        except Exception as e:
            logger.error(f"Ошибка прощальной рассылки: {e}")

        await scheduler.stop()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Ctrl+C уже обработан внутри main — здесь просто гасим traceback
        pass