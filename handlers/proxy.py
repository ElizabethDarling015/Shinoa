"""
Прокси SOCKS5 / HTTP(S) для подключения бота к Telegram API.

Навигация: Настройки -> 🌐 Прокси.
Меню прокси — ОДИН экран: текстовое описание сохранённых прокси +
для каждого строка кнопок [подключение (Гео+тип)][проверка],
ниже «➕ Добавить прокси» и стандартный ряд навигации.
Все переходы редактируют одно сообщение-контейнер,
сообщения пользователя с данными прокси удаляются.
"""
import html
import logging

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database import proxies as proxies_db
from services.proxy_tools import (
    build_proxy_url, check_proxy, flag_emoji, parse_proxy_text, type_label,
)

logger = logging.getLogger(__name__)
router = Router()


class ProxyInput(StatesGroup):
    waiting_data = State()


# ──────────────────────────────────────────────
# Вспомогательные
# ──────────────────────────────────────────────

async def _show(bot: Bot, chat_id: int, message_id: int, text: str, kb: InlineKeyboardMarkup):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=text, parse_mode="HTML", reply_markup=kb,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        logger.warning("Не удалось отредактировать сообщение прокси: %s", e)
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def _geo_str(p: dict) -> str:
    return ", ".join(x for x in (p.get("country_name"), p.get("city")) if x) or "гео не определено"


def _input_formats() -> str:
    return (
        "<code>host:port</code>\n"
        "<code>host:port:user:pass</code>\n"
        "<code>socks5://user:pass@host:port</code>\n"
        "<code>http://host:port</code>"
    )


def _input_text(ptype: str) -> str:
    return (
        f"🌐 <b>Введите данные прокси</b> ({type_label(ptype)})\n\n"
        f"Поддерживаемые форматы:\n{_input_formats()}\n\n"
        f"<i>Сообщение с данными будет удалено.</i>"
    )


def _input_error_text(ptype: str) -> str:
    return (
        "❌ <b>Неверные данные прокси.</b> Проверьте формат и отправьте ещё раз:\n\n"
        f"{_input_formats()}"
    )


def _input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="px:menu"),
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ]
    ])


async def _render_menu(
    bot: Bot, chat_id: int, message_id: int, user_id: int, checking_id: int = None,
):
    """Единственный экран «Меню прокси»."""
    proxies = await proxies_db.list_proxies(user_id)

    lines = ["🌐 <b>Меню прокси</b>", ""]
    if proxies:
        for p in proxies:
            if checking_id == p["id"]:
                status = "⏳ проверка..."
            elif p["is_active"]:
                status = "✅ активен — бот ходит через прокси"
            elif p.get("last_ok") == 0:
                status = "❌ не работает"
            elif p.get("last_ok") == 1:
                status = f"📶 работает ({p.get('last_ms')} мс), не активен"
            else:
                status = "⚪ не активен"
            lines.append(
                f"• {flag_emoji(p.get('country_code'))} {type_label(p['proxy_type'])} "
                f"<code>{html.escape(p['host'])}:{p['port']}</code> — {_geo_str(p)}, {status}"
            )
            if checking_id != p["id"] and p.get("last_ok") == 0 and p.get("last_error"):
                lines.append(f"   ⚠️ <i>{html.escape(str(p['last_error'])[:100])}</i>")
    else:
        lines.append("Список пуст. Добавь первый прокси кнопкой ниже.")

    rows = []
    for p in proxies:
        label = (
            f"{flag_emoji(p.get('country_code'))} {type_label(p['proxy_type'])} • "
            f"{p['host']}:{p['port']}" + (" ✅" if p["is_active"] else "")
        )
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"px:toggle:{p['id']}"),
            InlineKeyboardButton(text="🔍 Проверить", callback_data=f"px:check:{p['id']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить прокси", callback_data="px:add")])
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back"),
        InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
    ])
    await _show(bot, chat_id, message_id, "\n".join(lines),
                InlineKeyboardMarkup(inline_keyboard=rows))


# ──────────────────────────────────────────────
# Экраны
# ──────────────────────────────────────────────

@router.callback_query(F.data == "px:menu")
async def cb_proxy_menu(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await call.answer()
    await _render_menu(bot, call.message.chat.id, call.message.message_id, call.from_user.id)


@router.callback_query(F.data == "px:add")
async def cb_proxy_add(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await call.answer()
    await _show(
        bot, call.message.chat.id, call.message.message_id,
        "🌐 <b>Выберите тип прокси</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🌐 SOCKS5", callback_data="px:type:socks5"),
                InlineKeyboardButton(text="🔗 HTTP/HTTPS", callback_data="px:type:http"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="px:menu"),
                InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
            ],
        ]),
    )


@router.callback_query(F.data.in_({"px:type:socks5", "px:type:http"}))
async def cb_proxy_type(call: CallbackQuery, state: FSMContext, bot: Bot):
    ptype = call.data.split(":")[2]
    await state.set_state(ProxyInput.waiting_data)
    await state.update_data(ptype=ptype, bot_msg_id=call.message.message_id)
    await call.answer()
    await _show(bot, call.message.chat.id, call.message.message_id,
                _input_text(ptype), _input_kb())


@router.message(ProxyInput.waiting_data)
async def step_proxy_data(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    ptype = data.get("ptype", "socks5")

    # Не захламляем чат — удаляем сообщение с данными
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение с данными прокси: %s", e)

    parsed = parse_proxy_text(message.text, ptype)
    if not parsed:
        await _show(bot, message.chat.id, bot_msg_id, _input_error_text(ptype), _input_kb())
        return

    proxy = await proxies_db.add_or_update_proxy(
        message.chat.id, parsed["type"], parsed["host"], parsed["port"],
        parsed["username"], parsed["password"],
    )
    await state.clear()

    # Возвращаемся в то же меню, прокси сразу проверяется
    await _render_menu(bot, message.chat.id, bot_msg_id, message.chat.id,
                       checking_id=proxy["id"])
    res = await check_proxy(build_proxy_url(proxy))
    await proxies_db.update_check(proxy["id"], res)
    await _render_menu(bot, message.chat.id, bot_msg_id, message.chat.id)


@router.callback_query(F.data.startswith("px:check:"))
async def cb_proxy_check(call: CallbackQuery, bot: Bot):
    proxy_id = int(call.data.split(":")[2])
    proxy = await proxies_db.get_proxy(proxy_id)
    if not proxy:
        await call.answer("❌ Прокси не найден", show_alert=True)
        return
    await call.answer("⏳ Проверяю...")
    await _render_menu(bot, call.message.chat.id, call.message.message_id,
                       call.from_user.id, checking_id=proxy_id)
    res = await check_proxy(build_proxy_url(proxy))
    await proxies_db.update_check(proxy_id, res)
    await _render_menu(bot, call.message.chat.id, call.message.message_id, call.from_user.id)


@router.callback_query(F.data.startswith("px:toggle:"))
async def cb_proxy_toggle(call: CallbackQuery, bot: Bot):
    proxy_id = int(call.data.split(":")[2])
    proxy = await proxies_db.get_proxy(proxy_id)
    if not proxy:
        await call.answer("❌ Прокси не найден", show_alert=True)
        return

    if proxy["is_active"]:
        # Отключаем — возврат к прямому подключению
        await proxies_db.set_active(proxy_id, False)
        bot.session.clear_proxy()
        logger.info("Прокси отключён пользователем %s", call.from_user.id)
        await call.answer()
        await _render_menu(bot, call.message.chat.id, call.message.message_id, call.from_user.id)
        return

    # Активация: сначала проверка, чтобы мёртвый прокси не уронил бота
    await call.answer("⏳ Проверяю перед подключением...")
    await _render_menu(bot, call.message.chat.id, call.message.message_id,
                       call.from_user.id, checking_id=proxy_id)
    res = await check_proxy(build_proxy_url(proxy))
    await proxies_db.update_check(proxy_id, res)

    if res["ok"]:
        await proxies_db.set_active(proxy_id, True)
        try:
            bot.session.proxy = build_proxy_url(proxy)
            logger.info("Бот подключён через прокси %s:%s", proxy["host"], proxy["port"])
        except Exception as e:
            logger.warning("Не удалось применить прокси к сессии: %s", e)

    await _render_menu(bot, call.message.chat.id, call.message.message_id, call.from_user.id)