"""
Проверка needrestart: если shinoa.service или PlayerokCardinal.service
всё ещё используют библиотеки, обновлённые на диске — шлём уведомление
админу вместо автоматического рестарта (см. override_rc в needrestart.conf).
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ALLOWED_USERS

logger = logging.getLogger(__name__)

WATCHED_SERVICES = ("shinoa.service", "PlayerokCardinal.service")

CLOSE_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_message")],
])


async def check_needrestart(bot: Bot):
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "/usr/sbin/needrestart", "-b",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except Exception as e:
        logger.warning(f"Не удалось запустить needrestart: {e}")
        return

    output = stdout.decode(errors="ignore")
    flagged = [s for s in WATCHED_SERVICES if f"NEEDRESTART-SVC: {s}" in output]
    if not flagged:
        return

    text = (
        "⚠️ Обновились библиотеки, которые всё ещё использует:\n"
        + "\n".join(f"— {s}" for s in flagged)
        + "\n\nКогда будет удобно:\n"
        f"`sudo systemctl restart {' '.join(flagged)}`"
    )
    for chat_id in ALLOWED_USERS:
        try:
            await bot.send_message(
                chat_id, text,
                parse_mode="Markdown",
                reply_markup=CLOSE_KEYBOARD,
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить needrestart-уведомление {chat_id}: {e}")