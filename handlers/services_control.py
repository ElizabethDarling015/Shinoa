"""
Управление сервисами (Playerok-парсер и любые будущие: Avito, OSINT-тулы и
т.д.) прямо из Telegram — одна редактируемая карточка на сервис, без спама
сообщениями. Ввод параметров (ссылка/часы) удаляется сразу после обработки,
чтобы не засорять чат.

Список самих сервисов и их запуск/остановка/статус — в services/, этот файл
не знает специфики ни одного конкретного сервиса, только общий UI-паттерн.
"""

import logging
import html

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from services.service_registry import get_service, list_services
from services import service_manager as mgr

logger = logging.getLogger(__name__)
router = Router()


class ServiceInput(StatesGroup):
    waiting_url = State()
    waiting_hours = State()


# ──────────────────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────────────────

def get_service_rows() -> list[list[InlineKeyboardButton]]:
    """Ряды кнопок сервисов из реестра — вставляются в клавиатуру 'Работа с данными'."""
    rows = []
    for service_id, cfg in list_services():
        rows.append([InlineKeyboardButton(text=cfg["title"], callback_data=f"svc_open:{service_id}")])
    return rows


def _card_keyboard(service_id: str) -> InlineKeyboardMarkup:
    running = mgr.is_running(service_id)
    action_row = (
        [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"svc_stop:{service_id}")]
        if running else
        [InlineKeyboardButton(text="▶️ Запустить", callback_data=f"svc_start:{service_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        action_row,
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="start_data"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ],
    ])


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="svc_cancel_input")],
    ])


def _card_text(service_id: str, cfg: dict) -> str:
    status = mgr.read_status(service_id)
    title = cfg["title"]

    if not mgr.is_running(service_id):
        return f"{title}\n\nСейчас не запущен."

    if not status:
        return f"{title}\n\n🟡 Запускается..."

    percent = status.get("progress_percent")
    ptext = status.get("progress_text")
    line = "🟡 Собирается"
    if percent is not None:
        line += f": {percent}%"
    if ptext:
        line += f" ({html.escape(str(ptext))})"

    return f"{title}\n\n{line}"


async def _show_card(message: Message, service_id: str):
    cfg = get_service(service_id)
    if not cfg:
        await message.edit_text("❌ Сервис не найден в реестре.")
        return
    try:
        await message.edit_text(_card_text(service_id, cfg), reply_markup=_card_keyboard(service_id))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


# ──────────────────────────────────────────────────────────
# Открытие карточки сервиса
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("svc_open:"))
async def cb_svc_open(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    service_id = call.data.split(":", 1)[1]
    await _show_card(call.message, service_id)


@router.callback_query(F.data.startswith("svc_stop:"))
async def cb_svc_stop(call: CallbackQuery):
    service_id = call.data.split(":", 1)[1]
    stopped = await mgr.stop(service_id)
    await call.answer("Остановлено" if stopped else "Уже не запущен")
    await _show_card(call.message, service_id)


@router.callback_query(F.data == "svc_cancel_input")
async def cb_svc_cancel_input(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    service_id = data.get("service_id")
    await state.clear()
    await call.answer("Отменено")
    if service_id:
        await _show_card(call.message, service_id)


# ──────────────────────────────────────────────────────────
# Запуск сервиса — FSM-ввод параметров (пока поддержан input_kind=url_hours)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("svc_start:"))
async def cb_svc_start(call: CallbackQuery, state: FSMContext):
    service_id = call.data.split(":", 1)[1]
    cfg = get_service(service_id)
    if not cfg:
        await call.answer("Сервис не найден", show_alert=True)
        return
    if mgr.is_running(service_id):
        await call.answer("Уже запущен", show_alert=True)
        return

    await call.answer()
    input_kind = cfg.get("input_kind", "none")

    if input_kind == "none":
        await _launch(call.message.bot, call.message.chat.id, call.message.message_id, service_id, {})
        return

    if input_kind != "url_hours":
        # заготовка под text/number — пока не реализовано конкретно, но не падаем
        await call.answer("Этот тип ввода пока не поддержан в интерфейсе", show_alert=True)
        return

    await state.set_state(ServiceInput.waiting_url)
    await state.update_data(service_id=service_id, card_chat_id=call.message.chat.id, card_msg_id=call.message.message_id)

    prompt = cfg["input_prompts"]["url"]
    await call.message.edit_text(prompt, parse_mode="HTML", reply_markup=_cancel_keyboard())


@router.message(ServiceInput.waiting_url)
async def step_waiting_url(message: Message, state: FSMContext):
    url = message.text.strip() if message.text else ""
    data = await state.get_data()
    service_id = data["service_id"]
    cfg = get_service(service_id)

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if not url.startswith("http"):
        await message.bot.edit_message_text(
            "❌ Это не похоже на ссылку. Пришли ссылку целиком, начиная с https://",
            chat_id=data["card_chat_id"], message_id=data["card_msg_id"],
            reply_markup=_cancel_keyboard(),
        )
        return

    await state.update_data(url=url)
    await state.set_state(ServiceInput.waiting_hours)

    prompt = cfg["input_prompts"]["hours"]
    await message.bot.edit_message_text(
        prompt, parse_mode="HTML",
        chat_id=data["card_chat_id"], message_id=data["card_msg_id"],
        reply_markup=_cancel_keyboard(),
    )


@router.message(ServiceInput.waiting_hours)
async def step_waiting_hours(message: Message, state: FSMContext):
    data = await state.get_data()
    service_id = data["service_id"]
    raw = message.text.strip() if message.text else ""

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    try:
        hours = float(raw.replace(",", "."))
        if hours <= 0:
            raise ValueError
    except ValueError:
        await message.bot.edit_message_text(
            "❌ Нужно положительное число часов, например 6",
            chat_id=data["card_chat_id"], message_id=data["card_msg_id"],
            reply_markup=_cancel_keyboard(),
        )
        return

    params = {"url": data["url"], "hours": hours}
    await state.clear()

    await _launch(message.bot, data["card_chat_id"], data["card_msg_id"], service_id, params)


# ──────────────────────────────────────────────────────────
# Запуск + подписка на live-обновления карточки
# ──────────────────────────────────────────────────────────

async def _launch(bot, chat_id: int, message_id: int, service_id: str, params: dict):
    cfg = get_service(service_id)

    async def on_update(status: dict):
        st = status.get("status")
        try:
            if st == "running":
                percent = status.get("progress_percent")
                ptext = status.get("progress_text")
                line = "🟡 Собирается"
                if percent is not None:
                    line += f": {percent}%"
                if ptext:
                    line += f" ({html.escape(str(ptext))})"
                await bot.edit_message_text(
                    f"{cfg['title']}\n\n{line}",
                    chat_id=chat_id, message_id=message_id,
                    reply_markup=_card_keyboard(service_id),
                )
            elif st == "done":
                summary = status.get("result_text") or "Готово, без сводки."
                await bot.send_message(chat_id, f"✅ <b>{cfg['title']}</b> — готово\n\n{summary}", parse_mode="HTML")
                result_file = status.get("result_file")
                if result_file:
                    try:
                        from aiogram.types import FSInputFile
                        await bot.send_document(chat_id, FSInputFile(result_file))
                    except Exception as e:
                        logger.warning("Не удалось отправить файл результата: %s", e)
                await bot.edit_message_text(
                    _card_text(service_id, cfg), chat_id=chat_id, message_id=message_id,
                    reply_markup=_card_keyboard(service_id),
                )
            elif st == "error":
                err = status.get("error") or "неизвестная ошибка"
                await bot.send_message(chat_id, f"❌ <b>{cfg['title']}</b> — ошибка\n\n<code>{html.escape(err)}</code>", parse_mode="HTML")
                await bot.edit_message_text(
                    _card_text(service_id, cfg), chat_id=chat_id, message_id=message_id,
                    reply_markup=_card_keyboard(service_id),
                )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning("Ошибка обновления карточки сервиса %s: %s", service_id, e)

    try:
        await mgr.start(service_id, params, on_update=on_update)
    except mgr.ServiceError as e:
        await bot.edit_message_text(
            f"❌ {e}", chat_id=chat_id, message_id=message_id,
            reply_markup=_card_keyboard(service_id),
        )
        return

    await bot.edit_message_text(
        f"{cfg['title']}\n\n🟡 Запускается...", chat_id=chat_id, message_id=message_id,
        reply_markup=_card_keyboard(service_id),
    )
