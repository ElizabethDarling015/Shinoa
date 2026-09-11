"""
Middleware, чинящая баг: карточка потока обновлялась в чужом меню.

Контекст: весь бот переиспользует ОДНО сообщение на чат, редактируя его
in-place под любой текущий экран (см. шапку handlers/services_control.py про
_view_state). services_control.py помечает "сейчас в этом (chat_id,
message_id) показана карточка потока N" через _set_view — и живое обновление
прогресса (on_update) редактирует именно это сообщение, ПОКА пользователь на
него смотрит.

Проблема была в том, что _view_state выставляется и читается ТОЛЬКО внутри
services_control.py — а любой ДРУГОЙ хендлер бота (главное меню, привычки,
архив и т.д.), когда переиспользует то же самое сообщение под свой экран, не
знает про эту пометку и не обязан её сбрасывать. Пометка оставалась висеть с
устаревшим (service_id, run_id) — и следующее живое обновление парсера тихо
перезаписывало ЧУЖОЕ меню поверх того, что пользователь реально открыл.

Решение — не заставлять КАЖДЫЙ хендлер бота знать про это (это бы значило
лезть правкой во все файлы сразу и держать их в курсе чужой внутренней
механики), а поймать сам факт "пользователь нажал кнопку НЕ из
services_control.py" на уровне диспетчера, ДО того как соответствующий
хендлер вообще отработает — и сбросить пометку тут, в одном месте.

Список префиксов — это ВСЕ callback_data, которые реально обрабатываются
внутри services_control.py (см. router.callback_query(F.data == ...) /
startswith(...) в этом файле). Если туда добавится новый callback с другим
префиксом — его тоже нужно вписать сюда, иначе он будет ошибочно считаться
"чужим" и обнулять пометку прямо во время правомерной работы с потоком.
"""
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Update

_OWN_PREFIXES = (
    "svc_", "pair_", "thread_",
)


class ViewStateCleanupMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        cq = event.callback_query
        if cq and cq.message and not (cq.data or "").startswith(_OWN_PREFIXES):
            # Локальный импорт — чтобы не создавать цикл импорта на старте
            # (services_control.py, в свою очередь, не должен знать про
            # middlewares/ вообще).
            from handlers.services_control import _clear_view
            _clear_view(cq.message.chat.id, cq.message.message_id)

        return await handler(event, data)
