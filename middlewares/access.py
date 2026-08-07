"""
Middleware для проверки доступа по Chat ID.
"""
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Update

from config import ALLOWED_USERS

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        elif event.inline_query:
            user_id = event.inline_query.from_user.id

        if user_id is not None and user_id not in ALLOWED_USERS:
            logger.warning("Доступ запрещён для пользователя ID: %s", user_id)
            if event.message:
                await event.message.answer("🚫 У вас нет доступа к этому боту.")
            return None

        return await handler(event, data)
