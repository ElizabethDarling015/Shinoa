"""
🔐 Генератор 2FA: присылаешь 2FA-ключ — получаешь код входа.

Работа с данными → 🔐 2FA. Ничего не сохраняется: ключ живёт только в
памяти бота на время работы с экраном (чтобы работала кнопка «Обновить»)
и пропадает при выходе из раздела или перезапуске бота.
Сообщение с ключом удаляется из чата, ответ — в том же сообщении-контейнере.
"""

import logging
import time
from html import escape

from aiogram import Bot, Router, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from services import totp

logger = logging.getLogger(__name__)
router = Router()

HEADER = "🔐 <b>Генератор 2FA</b>\n\n"
PROMPT = "Пришли 2FA-ключ, чтобы сгенерировать код входа."


class Totp(StatesGroup):
    waiting_key = State()


# Чаты, где экран 2FA сейчас открыт в сообщении-контейнере.
# Любая «чужая» кнопка в этом сообщении снимает пометку
# (см. middlewares/view_state_cleanup.py) — и следующий текст пользователя
# уже не считается ключом, а уходит другим разделам бота.
_active: dict[int, int] = {}   # chat_id → message_id контейнера


def forget_view(chat_id: int, message_id: int) -> None:
    if _active.get(chat_id) == message_id:
        _active.pop(chat_id, None)


# ──────────────────────────────────────────────
# Экраны
# ──────────────────────────────────────────────

def _keyboard(with_refresh: bool) -> InlineKeyboardMarkup:
    rows = []
    if with_refresh:
        rows.append([InlineKeyboardButton(text="🔄 Обновить код", callback_data="tfa:refresh")])
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="start_data"),
        InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_text(parsed: dict, entered: str) -> str:
    code = totp.generate_code(parsed["secret"], parsed["digits"], parsed["period"], parsed["algorithm"])
    left = totp.seconds_left(parsed["period"])
    text = (
        HEADER
        + f"Ваш код входа: <code>{code}</code>\n"
        + f"Введённый вами 2FA: <code>{escape(entered)}</code>\n\n"
        + f"⏳ Код действует ещё {left} сек."
    )
    if left <= 5:
        nxt = totp.generate_code(parsed["secret"], parsed["digits"], parsed["period"],
                                 parsed["algorithm"], for_time=time.time() + left)
        text += f"\nСледующий: <code>{nxt}</code>"
    text += "\n\n<i>Пришли другой ключ — сгенерирую новый код.</i>"
    return text


async def _edit(bot: Bot, chat_id: int, message_id: int, text: str, kb: InlineKeyboardMarkup):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


# ──────────────────────────────────────────────
# Хендлеры
# ──────────────────────────────────────────────

@router.callback_query(F.data == "tfa:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Totp.waiting_key)
    await state.set_data({"container_id": call.message.message_id})
    _active[call.message.chat.id] = call.message.message_id
    await _edit(call.bot, call.message.chat.id, call.message.message_id,
                HEADER + PROMPT, _keyboard(with_refresh=False))


@router.message(Totp.waiting_key)
async def on_key(message: Message, state: FSMContext):
    data = await state.get_data()
    container_id = data.get("container_id")
    text = (message.text or "").strip()

    # Экран 2FA уже закрыт (ушли в другое меню) или это команда —
    # выходим из режима и отдаём сообщение остальным разделам бота
    if _active.get(message.chat.id) != container_id or text.startswith("/") or not text:
        await state.clear()
        raise SkipHandler()

    # Ключ — секрет: убираем из чата в первую очередь
    try:
        await message.delete()
    except Exception:
        logger.warning("Не удалось удалить сообщение с 2FA-ключом")

    parsed = totp.parse_user_input(text)
    if not parsed:
        await _edit(message.bot, message.chat.id, container_id,
                    HEADER + "⚠️ Это не похоже на 2FA-ключ. Ключ — строка из латинских букв "
                    "A–Z и цифр 2–7 (пробелы не важны) или ссылка <code>otpauth://totp/…</code>\n\n"
                    + PROMPT,
                    _keyboard(with_refresh=False))
        return

    await state.update_data(entered=text, parsed=parsed)
    await _edit(message.bot, message.chat.id, container_id,
                _result_text(parsed, text), _keyboard(with_refresh=True))


@router.callback_query(F.data == "tfa:refresh")
async def cb_refresh(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("parsed"):
        # Бот перезапускался — ключ из памяти пропал
        await call.answer("Ключ не сохранён — пришли его ещё раз", show_alert=True)
        await state.set_state(Totp.waiting_key)
        await state.set_data({"container_id": call.message.message_id})
        _active[call.message.chat.id] = call.message.message_id
        await _edit(call.bot, call.message.chat.id, call.message.message_id,
                    HEADER + PROMPT, _keyboard(with_refresh=False))
        return
    await call.answer()
    await _edit(call.bot, call.message.chat.id, call.message.message_id,
                _result_text(data["parsed"], data["entered"]), _keyboard(with_refresh=True))
