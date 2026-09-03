"""
Управление сервисами (Playerok-парсер и любые будущие: Avito, OSINT-тулы и
т.д.) прямо из Telegram. Каждый сервис умеет крутить НЕСКОЛЬКО параллельных
"потоков" одновременно — у каждого своя мини-карточка (ниша, дата запуска,
прогресс, пауза/удаление), открывается в том же сообщении.

Важный нюанс Telegram API: если в сообщение уже вложен документ (файл),
его нельзя превратить обратно в обычное текстовое через editMessageText —
можно редактировать только ПОДПИСЬ (editMessageCaption). Поэтому все
карточки умеют жить в двух режимах — как обычное текстовое сообщение (когда
открыты из меню) и как подпись к файлу (когда открыты из уведомления о
завершении сбора) — см. параметр as_caption, который тянется через всю
цепочку функций ниже.

Список самих сервисов и их запуск/пауза/остановка/статус — в services/,
этот файл не знает специфики ни одного конкретного сервиса.
"""

import asyncio
import logging
import html
import os

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


class ServiceSettingsInput(StatesGroup):
    waiting_value = State()


def _footer_description(cfg: dict) -> str:
    if cfg.get("settings"):
        settings_line = "⚙️ <b>Настройки</b> — Настройки для новых потоков."
    else:
        settings_line = "⚙️ <b>Настройки</b> — параметры этого сервиса (пока нет ни одной)"
    return f"{settings_line}\n🧪 <b>Тест</b> — Быстрый сбор за 2 минуты."


# ──────────────────────────────────────────────────────────
# Трекер "куда сейчас смотрит пользователь" — какое (service_id, run_id)
# сейчас отрисовано в конкретном сообщении (chat_id, message_id).
#
# Зачем: у нас всего ОДНО живое сообщение, которое переиспользуется (редактируется
# in-place) для всех экранов бота. Автообновление прогресса потока (см. _launch/
# on_update) должно трогать это сообщение ТОЛЬКО пока пользователь реально смотрит
# на карточку именно этого потока — иначе два эффекта: (1) периодический апдейт
# перебивает совершенно другое меню, в которое пользователь тем временем перешёл;
# (2) при нескольких параллельных потоках они начинают затирать прогресс друг
# друга в одном и том же сообщении, потому что оба слепо редактируют один message_id.
# ──────────────────────────────────────────────────────────

_view_state: dict[tuple[int, int], tuple] = {}


def _set_view(chat_id: int, message_id: int, kind: str, service_id: str,
               run_id: int | None = None, as_caption: bool = False) -> None:
    _view_state[(chat_id, message_id)] = (kind, service_id, run_id, as_caption)


def _clear_view(chat_id: int, message_id: int) -> None:
    _view_state.pop((chat_id, message_id), None)


def _find_thread_viewer(service_id: str, run_id: int) -> tuple[int, int, bool] | None:
    """Есть ли сейчас сообщение, показывающее карточку именно этого потока —
    и если да, то какое (chat_id, message_id) и живёт ли оно как подпись к файлу.
    Не привязано к тому, кто и когда запустил поток: работает одинаково что для
    только что стартовавшего потока, что для восстановленного после перезапуска
    Shinoa (см. recover_services) — просто в последнем случае обычно вернёт None,
    пока пользователь сам не откроет карточку этого потока заново."""
    for (chat_id, message_id), (kind, sid, rid, as_caption) in _view_state.items():
        if kind == "thread" and sid == service_id and rid == run_id:
            return chat_id, message_id, as_caption
    return None


_background_tasks: set = set()
_hiding_runs: set = set()  # (service_id, run_id) — досрочно скрытые из списка на время остановки, см. _visible_runs
def _fire_and_forget(coro) -> None:
    """
    Создаёт фоновую asyncio-задачу и, в отличие от голого asyncio.create_task(),
    держит на неё живую ссылку, пока она не завершится.

    Это не стилистика — без этого задачу может в любой момент подобрать
    сборщик мусора и молча уничтожить ПОСРЕДИ выполнения: у event loop'а
    ссылки на задачи только слабые (см. официальное предупреждение в доке
    asyncio.create_task). Ровно это и произошло с досрочной остановкой потока
    (см. cb_svc_delete_thread) — задача "остановить и прислать результат"
    создавалась без сохранённой ссылки, могла быть уничтожена на середине, и
    пользователь оставался без файла и без объяснения, что случилось.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ──────────────────────────────────────────────────────────
# Универсальное редактирование карточки: текст ИЛИ подпись к файлу
# ──────────────────────────────────────────────────────────

async def _edit_card(bot, chat_id: int, message_id: int, text: str,
                      reply_markup: InlineKeyboardMarkup, as_caption: bool):
    try:
        if as_caption:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=text, parse_mode="HTML", reply_markup=reply_markup,
            )
        else:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id,
                parse_mode="HTML", reply_markup=reply_markup,
                disable_web_page_preview=True,  # ниша даётся ссылкой в <code> — превью сайта тут не нужно, это дублирует и раздувает карточку
            )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


def _has_real_progress(status: dict | None) -> bool:
    if not status:
        return False
    percent = status.get("progress_percent")
    ptext = status.get("progress_text")
    return (percent not in (None, 0)) or bool(ptext)


def _status_dot(status: dict | None, paused: bool) -> str:
    """⏸ на паузе, 🟡 пока нет ни одного реального замера, 🟢 как только пришли первые данные."""
    if paused:
        return "⏸"
    return "🟢" if _has_real_progress(status) else "🟡"


def _progress_line(status: dict | None) -> str:
    dot = "🟡" if not status else ("🟢" if _has_real_progress(status) else "🟡")
    if not status:
        return "🟡 Запускается..."
    percent = status.get("progress_percent")
    ptext = status.get("progress_text")
    line = f"{dot} Собирается"
    if percent is not None:
        line += f": {percent}%"
    if ptext:
        line += f" ({html.escape(str(ptext))})"
    return line


def _fmt_duration(seconds) -> str | None:
    """Компактный формат длительности: '3ч20м', '3ч', '45м'. None, если данных нет.
    Намеренно без символа '<' — при parse_mode='HTML' Telegram пытается разобрать
    его как начало тега ('<1м' → незакрытый тег '1м') и роняет ЛЮБОЕ сообщение,
    где эта строка встретится, ошибкой 'Unsupported start tag'."""
    if seconds is None:
        return None
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return None
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours >= 1:
        return f"{hours}ч{minutes:02d}м" if minutes else f"{hours}ч"
    return f"{minutes}м" if minutes else "≈0м"


def _thread_label(service_id: str, run_id: int) -> str:
    """
    Однострочная метка потока для кнопки в списке — вместо голого 'Поток N':
    'Roblox - Аккаунты 🟢 (3ч/12ч)' или 'Adobe - Подписки 🟡 (test)'.
    Название ниши берём из status.json (niche_title, резолвится парсером через
    GraphQL) — это реальное название игры/категории, а не то, что мы можем
    надёжно угадать по URL на стороне Shinoa. Пока парсер его ещё не прислал
    (самое начало сбора) — временно показываем саму ссылку.
    """
    params = mgr.get_params(service_id, run_id) or {}
    status = mgr.read_status(service_id, run_id)
    paused = mgr.is_paused(service_id, run_id)

    title = (status or {}).get("niche_title") or params.get("url") or f"поток {run_id}"
    dot = _status_dot(status, paused)

    if params.get("test"):
        suffix = "test"
    else:
        remaining = _fmt_duration((status or {}).get("remaining_seconds"))
        total = _fmt_duration((status or {}).get("total_seconds"))
        suffix = f"{remaining}/{total}" if remaining and total else None

    # Кнопки в Telegram — plain text, HTML не парсится, поэтому без html.escape.
    label = f"{title} {dot}"
    return f"{label} ({suffix})" if suffix else label


# ──────────────────────────────────────────────────────────
# Клавиатуры и тексты — главная карточка сервиса (список потоков)
# ──────────────────────────────────────────────────────────

def get_service_rows() -> list[list[InlineKeyboardButton]]:
    """Ряды кнопок сервисов из реестра — вставляются в клавиатуру 'Работа с данными'."""
    rows = []
    for service_id, cfg in list_services():
        rows.append([InlineKeyboardButton(text=cfg["title"], callback_data=f"svc_open:{service_id}")])
    return rows


def _visible_runs(service_id: str) -> list:
    """
    Как mgr.list_runs(), но без потоков, которые прямо сейчас останавливаются
    (см. cb_svc_delete_thread/_hiding_runs). Фактическая остановка (SIGTERM →
    дозачистка → отчёт) занимает до минуты, но с точки зрения списка поток
    должен пропасть из него сразу по нажатию "Удалить поток" — ждать нечего,
    результат придёт отдельным сообщением, когда реально будет готов.
    """
    return [rid for rid in mgr.list_runs(service_id) if (service_id, rid) not in _hiding_runs]


def _card_keyboard(service_id: str, minimal: bool = False) -> InlineKeyboardMarkup:
    """
    minimal=True — усечённый набор кнопок для карточки, живущей поверх файла
    в уведомлении (без Назад/На главную).
    """
    run_ids = _visible_runs(service_id)
    rows = []

    if run_ids:
        for rid in run_ids:
            rows.append([InlineKeyboardButton(text=_thread_label(service_id, rid), callback_data=f"svc_thread:{service_id}:{rid}")])
        rows.append([InlineKeyboardButton(text="➕ Добавить поток", callback_data=f"svc_start:{service_id}")])
    else:
        rows.append([InlineKeyboardButton(text="▶️ Запустить", callback_data=f"svc_start:{service_id}")])

    rows.append([
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"svc_settings:{service_id}"),
        InlineKeyboardButton(text="🧪 Тест", callback_data=f"svc_test:{service_id}"),
    ])

    if minimal:
        rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="svc_close")])
    else:
        rows.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="start_data"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _run_settings_lines(service_id: str, run_id: int, cfg: dict) -> list:
    """
    Строки "эмодзи Метка: значение" — что реально применено К ЭТОМУ КОНКРЕТНОМУ
    потоку. Читаем не текущие настройки сервиса, а именно ЗАФИКСИРОВАННУЮ
    командную строку этого запуска (mgr.get_cmd) — потому что настройки могли
    поменять уже ПОСЛЕ его старта, у разных потоков одного сервиса они вполне
    могут отличаться, и показывать нужно то, что реально используется этим
    потоком, а не то, что стоит в настройках прямо сейчас.

    Секретные значения (куки) в чат не выводим — только само имя файла,
    которого достаточно, чтобы понять, использует ли этот поток кастомную
    сессию и делят ли несколько потоков одну и ту же (одинаковое имя файла).
    Прокси не секретен в этом смысле (это просто IP/VPN пользователя) —
    показываем как есть.
    """
    fields = cfg.get("settings", [])
    if not fields:
        return []
    cmd = mgr.get_cmd(service_id, run_id) or []
    lines = []
    for field in fields:
        raw = None
        if field["cli_flag"] in cmd:
            idx = cmd.index(field["cli_flag"])
            if idx + 1 < len(cmd):
                raw = cmd[idx + 1]
        if raw is None:
            value_str = "по умолчанию"
        elif field.get("is_file_content"):
            value_str = f"свои ({html.escape(os.path.basename(raw))})"
        else:
            value_str = html.escape(raw)
        lines.append(f"{field['label']}: {value_str}")
    return lines


def _card_text(service_id: str, cfg: dict) -> str:
    run_ids = _visible_runs(service_id)
    if run_ids:
        blocks = []
        for i, rid in enumerate(run_ids, start=1):
            params = mgr.get_params(service_id, rid) or {}
            status = mgr.read_status(service_id, rid)
            paused = mgr.is_paused(service_id, rid)
            dot = _status_dot(status, paused)
            url = params.get("url", "—")
            title = (status or {}).get("niche_title") or url

            percent = (status or {}).get("progress_percent")
            percent_str = f"{percent}%" if percent is not None else "0%"

            elapsed = _fmt_duration((status or {}).get("elapsed_seconds"))
            remaining = _fmt_duration((status or {}).get("remaining_seconds"))
            if params.get("test"):
                time_str = "тест"
            elif elapsed and remaining:
                time_str = f"{elapsed} прошло / {remaining} осталось"
            else:
                time_str = "старт"

            head = f"{dot} <b>{i}. {html.escape(str(title))}</b>. ({time_str}). {percent_str}"
            block_lines = [head] + _run_settings_lines(service_id, rid, cfg) + [f"<code>{html.escape(url)}</code>"]
            blocks.append("\n".join(block_lines))
        body = "\n\n".join(blocks)
    else:
        body = "Сейчас не запущен."
    return f"{cfg['title']}\n\n{body}\n\n{_footer_description(cfg)}"


async def _show_card(message: Message, service_id: str, as_caption: bool = False, minimal: bool = False):
    cfg = get_service(service_id)
    if not cfg:
        await message.answer("❌ Сервис не найден в реестре.")
        return
    _set_view(message.chat.id, message.message_id, "card", service_id)
    await _edit_card(
        message.bot, message.chat.id, message.message_id,
        _card_text(service_id, cfg), _card_keyboard(service_id, minimal=minimal), as_caption,
    )


# ──────────────────────────────────────────────────────────
# Клавиатура и текст — карточка конкретного потока
# ──────────────────────────────────────────────────────────

def _thread_keyboard(service_id: str, run_id: int, minimal: bool = False) -> InlineKeyboardMarkup:
    paused = mgr.is_paused(service_id, run_id)
    pause_btn = (
        InlineKeyboardButton(text="▶️ Старт", callback_data=f"svc_resume:{service_id}:{run_id}")
        if paused else
        InlineKeyboardButton(text="⏸ Пауза", callback_data=f"svc_pause:{service_id}:{run_id}")
    )
    rows = [
        [pause_btn, InlineKeyboardButton(text="🗑 Удалить поток", callback_data=f"svc_delete_thread:{service_id}:{run_id}")],
    ]
    if minimal:
        rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="svc_close")])
    else:
        rows.append([
            InlineKeyboardButton(text="⬅️ Назад в категорию", callback_data=f"svc_open:{service_id}"),
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="start_main"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _thread_text(service_id: str, cfg: dict, run_id: int) -> str:
    if not mgr.is_running(service_id, run_id):
        return f"{cfg['title']} — поток {run_id}\n\nЗавершён или не найден."

    params = mgr.get_params(service_id, run_id) or {}
    url = params.get("url", "—")
    started_at = (mgr.get_started_at(service_id, run_id) or "")[:16].replace("T", " ")
    status = mgr.read_status(service_id, run_id)
    paused_note = "\n⏸ <i>На паузе</i>" if mgr.is_paused(service_id, run_id) else ""

    title = (status or {}).get("niche_title") or url
    lines = [
        f"{cfg['title']} — {html.escape(str(title))}",
        "",
        f"Ниша: <code>{html.escape(url)}</code>",
        f"Дата запуска: {html.escape(started_at)} UTC",
        f"Прогресс: {_progress_line(status)}",
    ]
    if params.get("test"):
        lines.append("Режим: тест (~2 мин)")
    else:
        remaining = _fmt_duration((status or {}).get("remaining_seconds"))
        total = _fmt_duration((status or {}).get("total_seconds"))
        if remaining and total:
            lines.append(f"Осталось: {remaining} из {total}")

    return "\n".join(lines) + paused_note


async def _show_thread(message: Message, service_id: str, run_id: int, as_caption: bool = False, minimal: bool = False):
    cfg = get_service(service_id)
    if not cfg:
        await message.answer("❌ Сервис не найден в реестре.")
        return
    _set_view(message.chat.id, message.message_id, "thread", service_id, run_id, as_caption)
    await _edit_card(
        message.bot, message.chat.id, message.message_id,
        _thread_text(service_id, cfg, run_id), _thread_keyboard(service_id, run_id, minimal=minimal), as_caption,
    )


def _notification_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="svc_close")],
    ])


def _cancel_keyboard(service_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"svc_cancel_input:{service_id}")],
    ])


# ──────────────────────────────────────────────────────────
# Открытие карточек / настройки (заглушка) / закрытие
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("svc_open:"))
async def cb_svc_open(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    service_id = call.data.split(":", 1)[1]
    await _show_card(call.message, service_id)


@router.callback_query(F.data.startswith("svc_thread:"))
async def cb_svc_thread(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    _, service_id, run_id = call.data.split(":")
    await _show_thread(call.message, service_id, int(run_id), as_caption=bool(call.message.document))


@router.callback_query(F.data.startswith("svc_pause:"))
async def cb_svc_pause(call: CallbackQuery):
    _, service_id, run_id = call.data.split(":")
    ok = mgr.pause(service_id, int(run_id))
    await call.answer("На паузе" if ok else "Не удалось поставить на паузу")
    await _show_thread(call.message, service_id, int(run_id), as_caption=bool(call.message.document))


@router.callback_query(F.data.startswith("svc_resume:"))
async def cb_svc_resume(call: CallbackQuery):
    _, service_id, run_id = call.data.split(":")
    ok = mgr.resume(service_id, int(run_id))
    await call.answer("Продолжаю" if ok else "Не удалось возобновить")
    await _show_thread(call.message, service_id, int(run_id), as_caption=bool(call.message.document))


@router.callback_query(F.data.startswith("svc_delete_thread:"))
async def cb_svc_delete_thread(call: CallbackQuery):
    """
    Удаление потока — это не мгновенная операция (парсер ещё должен
    дозачистить данные и сформировать отчёт по уже собранному, до минуты),
    поэтому здесь два независимых сообщения, а не одно:

    1. Карточка, на которой нажали "Удалить поток" (call.message), СРАЗУ
       возвращается к списку потоков сервиса — минус этот поток. Она
       никогда не показывает "Останавливаю...": с точки зрения списка
       поток уже снят, ждать тут нечего.
    2. Отдельное НОВОЕ сообщение — "результат придёт сюда же" — и именно
       ОНО, то же самое сообщение, чуть позже редактируется в финальный
       результат (файл/сводка или ошибка), когда парсер реально закончит.
       Не блокируем этим обработчик нажатия — mgr.stop() может занять
       до ~минуты (см. её докстринг) — досчитываем в фоне.
    """
    _, service_id, run_id = call.data.split(":")
    run_id = int(run_id)
    cfg = get_service(service_id)
    as_caption = bool(call.message.document)
    await call.answer("Останавливаю поток…")

    # Прячем из списка СРАЗУ (см. _visible_runs) — фактическая остановка
    # (SIGTERM → дозачистка → отчёт) может занять до минуты, но ждать этого,
    # чтобы поток пропал из списка, не нужно — с точки зрения пользователя
    # он уже "удалён", результат придёт отдельным сообщением позже.
    _hiding_runs.add((service_id, run_id))

    # 1. Список потоков — сразу к обычному виду, без промежуточных состояний.
    await _show_card(call.message, service_id, as_caption=as_caption)

    # 2. Отдельный плейсхолдер, который позже сам превратится в результат.
    placeholder = await call.message.bot.send_message(
        call.message.chat.id,
        f"⏳ <b>{cfg['title'] if cfg else service_id}</b> — поток остановлен, "
        "парсер дозачищает данные и формирует отчёт по уже собранному — "
        "результат придёт сюда же, это может занять до минуты...",
        parse_mode="HTML",
    )

    async def _stop_and_deliver():
        try:
            final = await mgr.stop(service_id, run_id)
        finally:
            _hiding_runs.discard((service_id, run_id))
        if final is None:
            # Собственный watcher потока успел сам доставить результат
            # обычным путём (новым сообщением), пока мы ждали, — этот
            # плейсхолдер больше не нужен, не оставляем его висеть вечным "⏳".
            try:
                await placeholder.delete()
            except TelegramBadRequest:
                pass
            return
        if not cfg:
            return
        await _deliver_result_by_edit(call.message.bot, placeholder.chat.id, placeholder.message_id,
                                       service_id, run_id, cfg, final)

    _fire_and_forget(_stop_and_deliver())


@router.callback_query(F.data == "svc_close")
async def cb_svc_close(call: CallbackQuery):
    """Удаляет ТОЛЬКО сообщение в Telegram. Файл на диске сервера это никак не трогает —
    это два независимых места хранения, удаление сообщения никогда не трогает исходник."""
    await call.answer()
    _clear_view(call.message.chat.id, call.message.message_id)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass


def _settings_text(cfg: dict, service_id: str) -> str:
    settings = mgr.get_settings(service_id)
    lines = [f"{cfg['title']} — настройки", ""]
    for field in cfg.get("settings", []):
        value = settings.get(field["key"])
        state_str = "задано ✅" if value else "не задано"
        lines.append(f"{field['label']}: {state_str}")
    lines.append("")
    lines.append("Действуют на все НОВЫЕ потоки этого сервиса — уже запущенные не затрагивают.")
    return "\n".join(lines)


def _settings_keyboard(service_id: str, cfg: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=field["label"], callback_data=f"svc_setting_field:{service_id}:{field['key']}")]
        for field in cfg.get("settings", [])
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc_open:{service_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("svc_settings:"))
async def cb_svc_settings(call: CallbackQuery):
    service_id = call.data.split(":", 1)[1]
    cfg = get_service(service_id)
    if not cfg or not cfg.get("settings"):
        await call.answer("🚧 У этого сервиса пока нет настроек", show_alert=True)
        return
    await call.answer()
    as_caption = bool(call.message.document)
    # Не "thread" — просто чтобы автообновление прогресса какого-то потока
    # не перезатёрло этот экран, пока пользователь смотрит на настройки.
    _set_view(call.message.chat.id, call.message.message_id, "card", service_id)
    await _edit_card(call.message.bot, call.message.chat.id, call.message.message_id,
                      _settings_text(cfg, service_id), _settings_keyboard(service_id, cfg), as_caption)


@router.callback_query(F.data.startswith("svc_setting_field:"))
async def cb_svc_setting_field(call: CallbackQuery, state: FSMContext):
    _, service_id, key = call.data.split(":", 2)
    cfg = get_service(service_id)
    field = next((f for f in (cfg or {}).get("settings", []) if f["key"] == key), None)
    if not cfg or not field:
        await call.answer("Настройка не найдена", show_alert=True)
        return

    await call.answer()
    as_caption = bool(call.message.document)
    await state.set_state(ServiceSettingsInput.waiting_value)
    await state.update_data(
        service_id=service_id, key=key, as_caption=as_caption,
        card_chat_id=call.message.chat.id, card_msg_id=call.message.message_id,
    )
    _set_view(call.message.chat.id, call.message.message_id, "input", service_id)
    await _edit_card(call.message.bot, call.message.chat.id, call.message.message_id,
                      field["prompt"], _cancel_keyboard(service_id), as_caption)


@router.message(ServiceSettingsInput.waiting_value, lambda m: not _is_command(m))
async def step_setting_value(message: Message, state: FSMContext):
    data = await state.get_data()
    service_id = data["service_id"]
    key = data["key"]
    as_caption = data.get("as_caption", False)
    cfg = get_service(service_id)
    field = next((f for f in (cfg or {}).get("settings", []) if f["key"] == key), None)

    text = (message.text or "").strip()
    try:
        # Куки — по сути секрет, да и прокси с логином/паролем тоже — не
        # оставляем это висеть в истории чата, сразу удаляем сообщение с вводом.
        await message.delete()
    except TelegramBadRequest:
        pass

    if not cfg or not field:
        await state.clear()
        return

    if text == "-":
        mgr.set_setting(service_id, key, None)
    elif field.get("is_file_content"):
        # Парсер ждёт ПУТЬ к файлу (--cookies-file), не сам текст — сохраняем
        # присланное отдельным файлом рядом со status.json/registry.json.
        override_path = mgr.STATUS_DIR / f"{service_id}_{key}_override.txt"
        override_path.write_text(text, encoding="utf-8")
        mgr.set_setting(service_id, key, str(override_path))
    else:
        mgr.set_setting(service_id, key, text)

    await state.clear()
    _set_view(data["card_chat_id"], data["card_msg_id"], "card", service_id)
    await _edit_card(message.bot, data["card_chat_id"], data["card_msg_id"],
                      _settings_text(cfg, service_id), _settings_keyboard(service_id, cfg), as_caption)


@router.callback_query(F.data.startswith("svc_cancel_input:"))
async def cb_svc_cancel_input(call: CallbackQuery, state: FSMContext):
    """
    Кнопка 'Отмена' восстанавливает ИМЕННО карточку сервиса (не конкретного
    потока — на этапе ввода параметров поток ещё не создан) по service_id,
    зашитому прямо в callback_data — не полагается на общее FSM-состояние.
    """
    service_id = call.data.split(":", 1)[1]
    await state.clear()
    await call.answer("Отменено")
    as_caption = bool(call.message.document)
    await _show_card(call.message, service_id, as_caption=as_caption, minimal=as_caption)


# ──────────────────────────────────────────────────────────
# Запуск потока — FSM-ввод параметров (пока поддержан input_kind=url_hours)
# ──────────────────────────────────────────────────────────

def _invalid_input_text(original_prompt: str) -> str:
    """Единый формат ошибки ввода — заголовок + повтор исходного вопроса, чтобы
    сразу было видно, что именно ожидалось, не листая историю сообщений."""
    return f"❌ <b>Некорректные данные!</b>\n\n{original_prompt}"


def _is_command(message: Message) -> bool:
    """Команды (/start, /help и т.п.) не должны 'застревать' в наших FSM-хендлерах —
    пропускаем их дальше, к настоящим обработчикам этих команд."""
    return bool(message.text and message.text.startswith("/"))


async def _begin_input_flow(call: CallbackQuery, state: FSMContext, service_id: str, test_mode: bool):
    await state.clear()
    cfg = get_service(service_id)
    if not cfg:
        await call.answer("Сервис не найден", show_alert=True)
        return

    await call.answer()
    input_kind = cfg.get("input_kind", "none")
    as_caption = bool(call.message.document)

    if input_kind == "none":
        params = {"test": True} if test_mode else {}
        await _launch(call.message.bot, call.message.chat.id, call.message.message_id, service_id, params, as_caption)
        return

    if input_kind != "url_hours":
        await call.answer("Этот тип ввода пока не поддержан в интерфейсе", show_alert=True)
        return

    await state.set_state(ServiceInput.waiting_url)
    await state.update_data(
        service_id=service_id, test_mode=test_mode, as_caption=as_caption,
        card_chat_id=call.message.chat.id, card_msg_id=call.message.message_id,
    )

    # Экран ввода параметров — не карточка и не поток, чтобы автообновление
    # прогресса ДРУГОГО, уже бегущего потока не затёрло этот промпт.
    _set_view(call.message.chat.id, call.message.message_id, "input", service_id)

    prompt = cfg["input_prompts"]["url"]
    await _edit_card(call.message.bot, call.message.chat.id, call.message.message_id,
                      prompt, _cancel_keyboard(service_id), as_caption)


@router.callback_query(F.data.startswith("svc_start:"))
async def cb_svc_start(call: CallbackQuery, state: FSMContext):
    service_id = call.data.split(":", 1)[1]
    await _begin_input_flow(call, state, service_id, test_mode=False)


@router.callback_query(F.data.startswith("svc_test:"))
async def cb_svc_test(call: CallbackQuery, state: FSMContext):
    service_id = call.data.split(":", 1)[1]
    await _begin_input_flow(call, state, service_id, test_mode=True)


@router.message(ServiceInput.waiting_url, lambda m: not _is_command(m))
async def step_waiting_url(message: Message, state: FSMContext):
    url = message.text.strip() if message.text else ""
    data = await state.get_data()
    service_id = data["service_id"]
    test_mode = data.get("test_mode", False)
    as_caption = data.get("as_caption", False)
    cfg = get_service(service_id)

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if not url.startswith("http"):
        await _edit_card(
            message.bot, data["card_chat_id"], data["card_msg_id"],
            _invalid_input_text(cfg["input_prompts"]["url"]),
            _cancel_keyboard(service_id), as_caption,
        )
        return

    if test_mode:
        await state.clear()
        await _launch(message.bot, data["card_chat_id"], data["card_msg_id"], service_id,
                      {"url": url, "test": True}, as_caption)
        return

    await state.update_data(url=url)
    await state.set_state(ServiceInput.waiting_hours)
    _set_view(data["card_chat_id"], data["card_msg_id"], "input", service_id)

    prompt = cfg["input_prompts"]["hours"]
    await _edit_card(message.bot, data["card_chat_id"], data["card_msg_id"], prompt, _cancel_keyboard(service_id), as_caption)


@router.message(ServiceInput.waiting_hours, lambda m: not _is_command(m))
async def step_waiting_hours(message: Message, state: FSMContext):
    data = await state.get_data()
    service_id = data["service_id"]
    as_caption = data.get("as_caption", False)
    cfg = get_service(service_id)
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
        await _edit_card(
            message.bot, data["card_chat_id"], data["card_msg_id"],
            _invalid_input_text(cfg["input_prompts"]["hours"]),
            _cancel_keyboard(service_id), as_caption,
        )
        return

    params = {"url": data["url"], "hours": hours}
    await state.clear()

    await _launch(message.bot, data["card_chat_id"], data["card_msg_id"], service_id, params, as_caption)


# ──────────────────────────────────────────────────────────
# Запуск + подписка на live-обновления карточки потока
# ──────────────────────────────────────────────────────────

def _run_title(service_id: str, run_id: int, status: dict, cfg: dict) -> str:
    """Название для уведомлений (done/error) — из status.json, иначе ссылка, иначе заголовок сервиса."""
    params = mgr.get_params(service_id, run_id) or {}
    return status.get("niche_title") or params.get("url") or cfg["title"]


def _final_result_caption(cfg: dict, title: str, status: dict) -> tuple[str, str | None]:
    """
    Строит готовый HTML-текст итогового уведомления из финального status-словаря
    (done/error) — общий для ОБОИХ способов доставки: обычного (новое
    сообщение, см. _build_on_update) и через редактирование заранее отправленного
    "результат придёт сюда же" плейсхолдера (см. _deliver_result_by_edit,
    используется при досрочной остановке потока). Возвращает (текст, путь_к_файлу
    или None — файл есть только у "done", да и то не всегда).
    """
    if status.get("status") == "done":
        summary = status.get("result_text") or "Готово, без сводки."
        text = f"✅ <b>{cfg['title']}</b> — {html.escape(str(title))} готово\n\n{summary}"
        return text, status.get("result_file")
    err = status.get("error") or "неизвестная ошибка"
    text = f"❌ <b>{cfg['title']}</b> — {html.escape(str(title))} — ошибка\n\n<code>{html.escape(err)}</code>"
    return text, None


async def _deliver_result_by_edit(bot, chat_id: int, message_id: int, service_id: str, run_id: int,
                                   cfg: dict, status: dict) -> None:
    """
    Доставляет финальный результат (done/error) РЕДАКТИРУЯ уже существующее
    сообщение (плейсхолдер "результат придёт сюда же", отправленный при
    досрочной остановке потока — см. cb_svc_delete_thread) вместо отправки
    нового. Если есть файл — переводим сообщение из текстового в
    документ-с-подписью через edit_message_media (Telegram это разрешает:
    единственное направление редактирования, которое НЕЛЬЗЯ — обратное,
    убрать уже прикреплённый файл через edit_message_text, см. комментарий
    в шапке файла про as_caption).
    """
    title = _run_title(service_id, run_id, status, cfg)
    text, result_file = _final_result_caption(cfg, title, status)

    if result_file:
        try:
            from aiogram.types import FSInputFile, InputMediaDocument
            await bot.edit_message_media(
                chat_id=chat_id, message_id=message_id,
                media=InputMediaDocument(media=FSInputFile(result_file), caption=text, parse_mode="HTML"),
                reply_markup=_notification_keyboard(),
            )
            return
        except Exception as e:
            logger.warning("Не удалось прикрепить файл через edit_message_media (%s): %s", result_file, e)
            text = (f"{text}\n\n⚠️ Файл не отправился в чат.\n"
                    f"Путь на сервере: <code>{html.escape(str(result_file))}</code>\n"
                    f"Причина: <code>{html.escape(str(e))}</code>")

    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML",
                                     reply_markup=_notification_keyboard(), disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("Не удалось отредактировать плейсхолдер результата: %s", e)


def _build_on_update(bot, service_id: str, cfg: dict, chat_id: int):
    """
    Строит колбэк on_update(run_id, status), который менеджер сервисов дёргает
    на каждый опрос. Специально НЕ привязан к конкретному (chat_id, message_id)
    "домашней" карточки — вместо этого на каждый вызов заново ищет через
    _find_thread_viewer, смотрит ли кто-то ПРЯМО СЕЙЧАС на карточку именно
    этого потока, и редактирует только её (см. рассуждение в шапке файла про
    _view_state). Это же делает колбэк одинаково пригодным и для свежего
    запуска (_launch), и для потока, восстановленного после перезапуска Shinoa
    (recover_services) — во втором случае просто сразу после перезапуска
    "смотрящего" ещё ни у кого нет, пока пользователь сам не откроет карточку
    потока — и живые обновления процента сами включатся с этого момента.

    chat_id — единственное, что обязательно привязано заранее: он не может
    поменяться в течение жизни потока и нужен, чтобы было куда слать
    уведомление о завершении, даже если никакой карточки этого потока сейчас
    не открыто нигде (например, сразу после restart).
    """
    async def on_update(run_id: int, status: dict):
        st = status.get("status")
        viewer = _find_thread_viewer(service_id, run_id)
        try:
            if st == "running":
                # Живое обновление % прогресса — только если это самое
                # сообщение прямо сейчас показывает карточку именно этого
                # потока. Иначе молчим: экран увидит актуальные данные сам,
                # как только пользователь откроет эту карточку (_show_thread
                # всегда читает status.json заново, не из кэша).
                if viewer:
                    v_chat, v_msg, v_caption = viewer
                    await _edit_card(bot, v_chat, v_msg, _thread_text(service_id, cfg, run_id),
                                      _thread_keyboard(service_id, run_id, minimal=v_caption), v_caption)
                return

            title = _run_title(service_id, run_id, status, cfg)
            text, result_file = _final_result_caption(cfg, title, status)

            if result_file:
                try:
                    from aiogram.types import FSInputFile
                    await bot.send_document(
                        chat_id, FSInputFile(result_file), caption=text, parse_mode="HTML",
                        reply_markup=_notification_keyboard(),
                    )
                except Exception as e:
                    logger.warning("Не удалось отправить файл результата (%s): %s", result_file, e)
                    await bot.send_message(
                        chat_id,
                        f"{text}\n\n⚠️ Файл не отправился в чат.\n"
                        f"Путь на сервере: <code>{html.escape(str(result_file))}</code>\n"
                        f"Причина: <code>{html.escape(str(e))}</code>",
                        parse_mode="HTML", reply_markup=_notification_keyboard(),
                        disable_web_page_preview=True,
                    )
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML",
                                        reply_markup=_notification_keyboard(),
                                        disable_web_page_preview=True)

            # Поток завершился (done/error). Карточку, которая его показывала,
            # возвращаем к главной карточке сервиса — если, конечно, кто-то её
            # прямо сейчас показывал (см. viewer выше). Если никто не смотрел —
            # это сообщение вообще не трогаем, уведомление выше уже отправлено.
            if viewer:
                v_chat, v_msg, v_caption = viewer
                _set_view(v_chat, v_msg, "card", service_id)
                await _edit_card(bot, v_chat, v_msg, _card_text(service_id, cfg),
                                  _card_keyboard(service_id, minimal=v_caption), v_caption)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning("Ошибка обновления карточки сервиса %s (поток %s): %s", service_id, run_id, e)

    return on_update


async def _launch(bot, chat_id: int, message_id: int, service_id: str, params: dict, as_caption: bool = False):
    cfg = get_service(service_id)
    on_update = _build_on_update(bot, service_id, cfg, chat_id)

    try:
        run_id = await mgr.start(service_id, params, chat_id, on_update=on_update)
    except mgr.ServiceError as e:
        await _edit_card(bot, chat_id, message_id, f"❌ {e}", _card_keyboard(service_id, minimal=as_caption), as_caption)
        return

    # Сразу после запуска это сообщение показывает карточку нового потока —
    # регистрируем это в трекере, иначе первое же периодическое обновление
    # прогресса будет молча проигнорировано как "никто не смотрит".
    _set_view(chat_id, message_id, "thread", service_id, run_id, as_caption)
    await _edit_card(bot, chat_id, message_id, _thread_text(service_id, cfg, run_id),
                      _thread_keyboard(service_id, run_id, minimal=as_caption), as_caption)


async def recover_services(bot) -> None:
    """
    Дёргается ОДИН РАЗ при старте бота (см. bot.py, перед start_polling) —
    восстанавливает слежение за потоками, которые остались физически
    запущены (или успели сами дойти до done/error), пока Shinoa была
    выключена. Подробности механизма — в service_manager.recover() и в
    большом комментарии в шапке service_manager.py.
    """
    def factory(service_id: str, run_id: int, chat_id: int):
        cfg = get_service(service_id)
        if not cfg:
            # Сервис исчез из реестра (например, убрали .env-блок) —
            # не с кем и незачем разговаривать про этот поток.
            async def _noop(_run_id, _status):
                pass
            return _noop
        return _build_on_update(bot, service_id, cfg, chat_id)

    recovered = await mgr.recover(factory)
    if recovered:
        logger.info("После перезапуска Shinoa восстановлено/донесено: %s", recovered)
