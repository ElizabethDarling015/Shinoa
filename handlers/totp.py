"""
🔐 2FA-коды: локальный генератор кодов двухфакторной аутентификации.

Работа с данными → 🔐 2FA → список аккаунтов → код.
Весь раздел живёт в одном сообщении-контейнере (как остальные меню бота).
Показанный код сам скрывается через CODE_TTL секунд — чтобы в чате
не висели действующие коды.
"""

import asyncio
import logging
import time
from html import escape

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from database.totp import add_totp_account, get_totp_accounts, get_totp_account, delete_totp_account
from services import totp

logger = logging.getLogger(__name__)
router = Router()

CODE_TTL = 60          # через сколько секунд код скрывается
NAME_MAX_LEN = 40
CANCEL_WORDS = {"отмена", "cancel", "/cancel", "❌ отмена"}


class AddTotp(StatesGroup):
    name = State()
    secret = State()


# ──────────────────────────────────────────────
# Автоскрытие кода
# ──────────────────────────────────────────────
# (chat_id, message_id) → номер показа. Любое другое действие в этом
# сообщении меняет/сбрасывает номер, и отложенное скрытие уже ничего не трогает.
_shown: dict[tuple[int, int], int] = {}
_counter = 0


def forget_view(chat_id: int, message_id: int) -> None:
    """Сообщение ушло под другой экран — скрывать в нём больше нечего.
    Вызывается из middlewares/view_state_cleanup.py на любую «чужую» кнопку."""
    _shown.pop((chat_id, message_id), None)


async def _hide_later(bot: Bot, chat_id: int, message_id: int, token: int):
    await asyncio.sleep(CODE_TTL)
    if _shown.get((chat_id, message_id)) != token:
        return
    _shown.pop((chat_id, message_id), None)
    text, kb = await _menu_view(chat_id, note="🙈 <i>Код скрыт.</i>")
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


# ──────────────────────────────────────────────
# Экраны
# ──────────────────────────────────────────────

def _nav_row() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Назад", callback_data="start_data"),
        InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
    ]


async def _menu_view(chat_id: int, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    accounts = await get_totp_accounts(chat_id)
    text = "🔐 <b>2FA-коды</b>\n\n"
    if accounts:
        text += "Выбери аккаунт — покажу код."
    else:
        text += "Пока нет ни одного аккаунта. Нажми «➕ Добавить»."
    if note:
        text += f"\n\n{note}"

    buttons = [InlineKeyboardButton(text=a["name"], callback_data=f"tfa:show:{a['id']}")
               for a in accounts]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    action_row = [InlineKeyboardButton(text="➕ Добавить", callback_data="tfa:add")]
    if accounts:
        action_row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="tfa:delmenu"))
    rows.append(action_row)
    rows.append(_nav_row())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _code_view(account: dict, header: str = "") -> tuple[str, InlineKeyboardMarkup]:
    secret = totp.decrypt_secret(account["secret_enc"])
    name = escape(account["name"])
    if secret is None:
        text = (f"🔐 <b>{name}</b>\n\n"
                "⚠️ Не удалось расшифровать ключ. Скорее всего, база перенесена "
                "с другой машины без файла <code>var/totp.key</code>.")
    else:
        digits, period, alg = account["digits"], account["period"], account["algorithm"]
        code = totp.generate_code(secret, digits, period, alg)
        left = totp.seconds_left(period)
        text = header + f"🔐 <b>{name}</b>\n\n<code>{code}</code>\n\n⏳ Действует ещё {left} сек."
        if left <= 5:
            nxt = totp.generate_code(secret, digits, period, alg, for_time=time.time() + left)
            text += f"\nСледующий: <code>{nxt}</code>"
        text += f"\n\n<i>Нажми на код, чтобы скопировать. Скроется через {CODE_TTL} сек.</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"tfa:show:{account['id']}")],
        [
            InlineKeyboardButton(text="⬅️ К списку", callback_data="tfa:menu"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ],
    ])
    return text, kb


async def _edit(bot: Bot, chat_id: int, message_id: int, text: str, kb: InlineKeyboardMarkup):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _show_code(bot: Bot, chat_id: int, message_id: int, account: dict, header: str = ""):
    global _counter
    text, kb = _code_view(account, header)
    await _edit(bot, chat_id, message_id, text, kb)
    _counter += 1
    _shown[(chat_id, message_id)] = _counter
    asyncio.create_task(_hide_later(bot, chat_id, message_id, _counter))


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="tfa:menu")]
    ])


# ──────────────────────────────────────────────
# Список и показ кода
# ──────────────────────────────────────────────

@router.callback_query(F.data == "tfa:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    forget_view(call.message.chat.id, call.message.message_id)
    text, kb = await _menu_view(call.message.chat.id)
    await _edit(call.bot, call.message.chat.id, call.message.message_id, text, kb)


@router.callback_query(F.data.startswith("tfa:show:"))
async def cb_show(call: CallbackQuery):
    account = await get_totp_account(int(call.data.split(":")[2]), call.message.chat.id)
    if not account:
        await call.answer("Аккаунт не найден", show_alert=True)
        return
    await call.answer()
    await _show_code(call.bot, call.message.chat.id, call.message.message_id, account)


# ──────────────────────────────────────────────
# Добавление
# ──────────────────────────────────────────────

@router.callback_query(F.data == "tfa:add")
async def cb_add(call: CallbackQuery, state: FSMContext):
    await call.answer()
    forget_view(call.message.chat.id, call.message.message_id)
    await state.set_state(AddTotp.name)
    await state.update_data(container_id=call.message.message_id)
    await _edit(call.bot, call.message.chat.id, call.message.message_id,
                "➕ <b>Новый аккаунт 2FA</b>\n\n"
                "Как назовём? Например: <i>Claude</i>, <i>GitHub</i>.",
                _cancel_kb())


async def _cancel_by_text(message: Message, state: FSMContext) -> bool:
    if (message.text or "").strip().lower() not in CANCEL_WORDS:
        return False
    data = await state.get_data()
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    if data.get("container_id"):
        text, kb = await _menu_view(message.chat.id)
        await _edit(message.bot, message.chat.id, data["container_id"], text, kb)
    return True


@router.message(AddTotp.name)
async def step_name(message: Message, state: FSMContext):
    if await _cancel_by_text(message, state):
        return
    data = await state.get_data()
    container_id = data.get("container_id")
    name = " ".join((message.text or "").split())
    try:
        await message.delete()
    except Exception:
        pass

    if not name or len(name) > NAME_MAX_LEN:
        await _edit(message.bot, message.chat.id, container_id,
                    "➕ <b>Новый аккаунт 2FA</b>\n\n"
                    f"⚠️ Нужно название текстом, до {NAME_MAX_LEN} символов.\n\nКак назовём?",
                    _cancel_kb())
        return

    await state.update_data(name=name)
    await state.set_state(AddTotp.secret)
    await _edit(message.bot, message.chat.id, container_id,
                f"➕ <b>{escape(name)}</b>\n\n"
                "Пришли секретный ключ — строку из букв и цифр, которую сайт показывает "
                "рядом с QR-кодом (обычно по ссылке «Не можешь отсканировать?» "
                "или «Ввести ключ вручную»).\n\n"
                "Подойдёт и ссылка <code>otpauth://…</code>\n\n"
                "<i>🔒 Сообщение с ключом я сразу удалю из чата, а в базе он хранится зашифрованным.</i>",
                _cancel_kb())


@router.message(AddTotp.secret)
async def step_secret(message: Message, state: FSMContext):
    if await _cancel_by_text(message, state):
        return
    data = await state.get_data()
    container_id, name = data.get("container_id"), data.get("name")
    raw = message.text or ""
    # Ключ — секрет: убираем из чата в первую очередь
    try:
        await message.delete()
    except Exception:
        logger.warning("Не удалось удалить сообщение с ключом 2FA")

    parsed = totp.parse_user_input(raw)
    if not parsed:
        await _edit(message.bot, message.chat.id, container_id,
                    f"➕ <b>{escape(name)}</b>\n\n"
                    "⚠️ Это не похоже на ключ 2FA. Ключ — это строка из латинских букв "
                    "A–Z и цифр 2–7 (пробелы не важны), либо ссылка <code>otpauth://totp/…</code>\n\n"
                    "Пришли ключ ещё раз:",
                    _cancel_kb())
        return

    account_id = await add_totp_account(
        message.chat.id, name, totp.encrypt_secret(parsed["secret"]),
        parsed["digits"], parsed["period"], parsed["algorithm"],
    )
    await state.clear()
    account = await get_totp_account(account_id, message.chat.id)
    await _show_code(message.bot, message.chat.id, container_id, account,
                     header="✅ <b>Добавлено!</b> Если сайт просит подтвердить — введи этот код.\n\n")


# ──────────────────────────────────────────────
# Удаление
# ──────────────────────────────────────────────

@router.callback_query(F.data == "tfa:delmenu")
async def cb_delmenu(call: CallbackQuery):
    await call.answer()
    forget_view(call.message.chat.id, call.message.message_id)
    accounts = await get_totp_accounts(call.message.chat.id)
    buttons = [InlineKeyboardButton(text=f"🗑 {a['name']}", callback_data=f"tfa:del:{a['id']}")
               for a in accounts]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="tfa:menu")])
    await _edit(call.bot, call.message.chat.id, call.message.message_id,
                "🗑 <b>Удаление</b>\n\nКакой аккаунт удалить?",
                InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("tfa:del:"))
async def cb_del(call: CallbackQuery):
    account = await get_totp_account(int(call.data.split(":")[2]), call.message.chat.id)
    if not account:
        await call.answer("Аккаунт не найден", show_alert=True)
        return
    await call.answer()
    await _edit(call.bot, call.message.chat.id, call.message.message_id,
                f"🗑 Удалить <b>{escape(account['name'])}</b>?\n\n"
                "⚠️ Это необратимо: без ключа коды для этого аккаунта больше не получить. "
                "Убедись, что на сайте 2FA отключена или есть другой способ входа.",
                InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"tfa:delok:{account['id']}"),
                    InlineKeyboardButton(text="⬅️ Отмена", callback_data="tfa:menu"),
                ]]))


@router.callback_query(F.data.startswith("tfa:delok:"))
async def cb_delok(call: CallbackQuery):
    account = await get_totp_account(int(call.data.split(":")[2]), call.message.chat.id)
    await delete_totp_account(int(call.data.split(":")[2]), call.message.chat.id)
    await call.answer("Удалено")
    note = f"🗑 <i>{escape(account['name'])} удалён.</i>" if account else ""
    text, kb = await _menu_view(call.message.chat.id, note=note)
    await _edit(call.bot, call.message.chat.id, call.message.message_id, text, kb)
