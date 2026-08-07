"""
Личный архив: сохранение и поиск файлов, идей, заметок.
/idea  — сохранить идею
/save  — сохранить текущее сообщение (фото, документ, текст)
/find  — поиск по архиву
"""

import logging
import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from handlers.common import remove_keyboard

logger = logging.getLogger(__name__)
router = Router()


class SaveIdea(StatesGroup):
    text = State()
    title = State()
    tags = State()


TYPE_EMOJI = {
    "idea": "💡",
    "note": "📝",
    "voice": "🎙",
    "photo": "🖼",
    "document": "📄",
}


def get_idea_nav_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура навигации внутри главного меню архива и при создании"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад в архив", callback_data="start_idea"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ]
    ])


def get_view_item_nav_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """Клавиатура навигации при просмотре ОДНОЙ заметки (возврат к списку + удаление)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ К списку заметок", callback_data="idea_all"),
            InlineKeyboardButton(text="🗑 Удалить заметку", callback_data=f"delete_item:{item_id}"),
        ],
        [
            InlineKeyboardButton(text="🏠 На главную", callback_data="start_main"),
        ]
    ])


# ──────────────────────────────────────────────────────────
# Inline-обработчики для меню Архива
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "idea_new")
async def cb_idea_new(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(SaveIdea.text)
    try:
        await call.message.edit_text(
            "📝 <b>Новая заметка</b>\n\n"
            "1️⃣ Сначала введите <b>текст</b> заметки или идеи.",
            parse_mode="HTML",
            reply_markup=get_idea_nav_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise
    await call.answer()


@router.callback_query(F.data == "idea_all")
async def cb_idea_all(call: CallbackQuery, state: FSMContext):
    """Показывает список заметок с кнопками для просмотра"""
    await state.clear()
    
    items = await db.get_recent_items(call.message.chat.id, limit=20)
    
    if not items:
        text = "📂 <b>Архив пуст</b>\n\nУ тебя пока нет сохраненных заметок или идей."
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_idea_nav_keyboard())
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
        await call.answer()
        return

    parts = ["📂 <b>Твои заметки</b>\n"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for item in items:
        emoji = TYPE_EMOJI.get(item["type"], "📌")
        tags_str = " ".join(f"#{t}" for t in (item.get("tags") or []))
        parts.append(f"{emoji} <b>{item['title'] or 'Без названия'}</b>  <code>[#{item['id']}]</code> {tags_str}")
        
        short_title = (item['title'] or "Заметка")[:45]
        if len(item['title'] or "") > 45:
            short_title += "..."
            
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"📖 Показать (#{item['id']})", 
                callback_data=f"view_item:{item['id']}"
            )
        ])
    
    keyboard.inline_keyboard.extend(get_idea_nav_keyboard().inline_keyboard)

    text = "\n".join(parts)
    
    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise
            
    await call.answer()


@router.callback_query(F.data.startswith("view_item:"))
async def cb_view_item(call: CallbackQuery):
    """Обработчик нажатия на кнопку 'Показать' в списке (РЕДАКТИРУЕТ сообщение)"""
    item_id = int(call.data.split(":")[1])
    item = await db.get_item_by_id(item_id, call.message.chat.id)
    
    if not item:
        await call.answer("⚠️ Заметка не найдена или была удалена", show_alert=True)
        return

    emoji = TYPE_EMOJI.get(item["type"], "📌")
    tags_str = " ".join(f"#{t}" for t in (item.get("tags") or []))
    date = item.get("created_at", "")[:10]

    text = (
        f"{emoji} <b>{item['title'] or 'Без названия'}</b>  <code>[#{item['id']}]</code>\n"
        f"📅 {date}  {tags_str}\n\n"
        f"<b>Содержание:</b>\n"
        f"<i>{item.get('text') or 'Текст отсутствует'}</i>"
    )

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_view_item_nav_keyboard(item_id)
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise
            
    await call.answer()


@router.callback_query(F.data.startswith("delete_item:"))
async def cb_delete_item(call: CallbackQuery):
    """Обработчик удаления заметки"""
    item_id = int(call.data.split(":")[1])
    
    # Удаляем заметку из БД
    success = await db.delete_item(item_id, call.message.chat.id)
    
    if success:
        await call.answer("🗑 Заметка успешно удалена", show_alert=True)
        
        # Сразу возвращаем пользователя к обновленному списку заметок
        items = await db.get_recent_items(call.message.chat.id, limit=20)
        if not items:
            text = "📂 <b>Архив пуст</b>\n\nУ тебя пока нет сохраненных заметок или идей."
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_idea_nav_keyboard())
        else:
            parts = ["📂 <b>Твои заметки</b>\n"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for item in items:
                emoji = TYPE_EMOJI.get(item["type"], "📌")
                tags_str = " ".join(f"#{t}" for t in (item.get("tags") or []))
                parts.append(f"{emoji} <b>{item['title'] or 'Без названия'}</b>  <code>[#{item['id']}]</code> {tags_str}")
                
                short_title = (item['title'] or "Заметка")[:45]
                if len(item['title'] or "") > 45: short_title += "..."
                    
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text=f"📖 Показать (#{item['id']})", callback_data=f"view_item:{item['id']}")
                ])
            
            keyboard.inline_keyboard.extend(get_idea_nav_keyboard().inline_keyboard)
            await call.message.edit_text("\n".join(parts), parse_mode="HTML", reply_markup=keyboard)
    else:
        await call.answer("⚠️ Ошибка: заметка не найдена или уже удалена", show_alert=True)


# ──────────────────────────────────────────────────────────
# Умный обработчик: если пользователь ввел просто число (ID)
# ──────────────────────────────────────────────────────────

@router.message(F.text.regexp(r"^\d+$"))
async def handle_just_number(message: Message):
    """Если пользователь отправил просто число, пробуем найти заметку с таким ID"""
    item_id = int(message.text)
    item = await db.get_item_by_id(item_id, message.chat.id)
    
    if not item:
        return

    emoji = TYPE_EMOJI.get(item["type"], "📌")
    tags_str = " ".join(f"#{t}" for t in (item.get("tags") or []))
    date = item.get("created_at", "")[:10]

    text = (
        f"{emoji} <b>{item['title'] or 'Без названия'}</b>  <code>[#{item['id']}]</code>\n"
        f"📅 {date}  {tags_str}\n\n"
        f"<b>Содержание:</b>\n"
        f"<i>{item.get('text') or 'Текст отсутствует'}</i>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_view_item_nav_keyboard(item_id)
    )


# ──────────────────────────────────────────────────────────
# /idea — сохранить идею (команда)
# ──────────────────────────────────────────────────────────

@router.message(Command("idea"))
async def cmd_idea(message: Message, state: FSMContext):
    await state.set_state(SaveIdea.text)
    await message.answer(
        "💡 <b>Новая идея</b>\n\n"
        "1️⃣ Сначала введите <b>текст</b> заметки или идеи:",
        parse_mode="HTML",
        reply_markup=get_idea_nav_keyboard(),
    )


@router.message(SaveIdea.text)
async def idea_text(message: Message, state: FSMContext):
    if message.text.strip().lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_idea_nav_keyboard())
        return
        
    await state.update_data(text=message.text.strip())
    await state.set_state(SaveIdea.title)
    await message.answer(
        "2️⃣ Теперь введите <b>название</b> (заголовок) заметки:\n\n"
        "<i>Например: Идея для проекта, Список покупок</i>",
        parse_mode="HTML",
        reply_markup=get_idea_nav_keyboard(),
    )


@router.message(SaveIdea.title)
async def idea_title(message: Message, state: FSMContext):
    if message.text.strip().lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_idea_nav_keyboard())
        return
        
    await state.update_data(title=message.text.strip())
    await state.set_state(SaveIdea.tags)
    await message.answer(
        "3️⃣ Добавьте <b>теги</b> через запятую или отправьте <code>-</code> чтобы пропустить:\n\n"
        "<i>Например: vps, сервер, идеи</i>",
        parse_mode="HTML",
        reply_markup=get_idea_nav_keyboard(),
    )


@router.message(SaveIdea.tags)
async def idea_tags(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text.strip()

    tags = []
    if text != "-":
        tags = [t.strip().lower() for t in text.split(",") if t.strip()]

    item_id = await db.save_item(
        chat_id=message.chat.id,
        item_type="idea",
        title=data.get("title", "Без названия"),
        text=data.get("text", ""),
        tags=tags,
    )

    tags_text = " ".join(f"#{t}" for t in tags) if tags else "без тегов"
    await state.clear()
    await message.answer(
        f"💡 <b>Заметка сохранена!</b>  <code>[#{item_id}]</code>\n\n"
        f"📌 <b>{data.get('title')}</b>\n"
        f"<i>{data.get('text')[:200]}</i>\n\n"
        f"🏷 {tags_text}",
        parse_mode="HTML",
        reply_markup=get_idea_nav_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# /save, /find и хелперы
# ──────────────────────────────────────────────────────────

@router.message(Command("save"))
async def cmd_save(message: Message):
    await message.answer(
        "📥 <b>Что сохранить?</b>\n\n"
        "Отправь мне:\n"
        "• фото с подписью → сохранится как фото\n"
        "• документ → сохранится как документ\n"
        "• /idea → сохранить идею с тегами\n\n"
        "<i>Теги добавляются в подписи: «отчёт Q3 #работа #финансы»</i>",
        parse_mode="HTML",
    )


@router.message(F.photo)
async def handle_photo(message: Message):
    caption = message.caption or ""
    tags = _extract_tags(caption)
    title = _clean_caption(caption) or "Фото"
    file_id = message.photo[-1].file_id

    item_id = await db.save_item(
        chat_id=message.chat.id,
        item_type="photo",
        title=title,
        file_id=file_id,
        tags=tags,
    )
    tags_text = " ".join(f"#{t}" for t in tags) if tags else ""
    await message.answer(
        f"🖼 Фото сохранено  <code>[#{item_id}]</code> {tags_text}",
        parse_mode="HTML",
    )


@router.message(F.document)
async def handle_document(message: Message):
    caption = message.caption or ""
    tags = _extract_tags(caption)
    title = message.document.file_name or "Документ"
    file_id = message.document.file_id

    item_id = await db.save_item(
        chat_id=message.chat.id,
        item_type="document",
        title=title,
        file_id=file_id,
        tags=tags,
    )
    tags_text = " ".join(f"#{t}" for t in tags) if tags else ""
    await message.answer(
        f"📄 Документ сохранён  <code>[#{item_id}]</code> {tags_text}",
        parse_mode="HTML",
    )


@router.message(F.voice)
async def handle_voice(message: Message):
    file_id = message.voice.file_id
    item_id = await db.save_item(
        chat_id=message.chat.id,
        item_type="voice",
        title="Голосовое",
        file_id=file_id,
        tags=[],
    )
    await message.answer(
        f"🎙 Голосовое сохранено  <code>[#{item_id}]</code>",
        parse_mode="HTML",
    )


@router.message(Command("find"))
async def cmd_find(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔍 <b>Поиск по архиву</b>\n\n"
            "Использование: /find &lt;запрос&gt;\n"
            "Пример: <code>/find vps</code> или <code>/find #работа</code>",
            parse_mode="HTML",
        )
        return

    query = parts[1].strip()
    tags = [t.lstrip("#") for t in query.split() if t.startswith("#")]
    text_query = " ".join(w for w in query.split() if not w.startswith("#")) or None

    items = await db.search_items(
        chat_id=message.chat.id,
        query=text_query,
        tags=tags or None,
    )

    if not items:
        await message.answer(f"🔍 По запросу «{query}» ничего не найдено.")
        return

    parts_msg = [f"🔍 <b>Найдено: {len(items)}</b>\n"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for item in items[:10]:
        emoji = TYPE_EMOJI.get(item["type"], "📌")
        tags_str = " ".join(f"#{t}" for t in (item.get("tags") or []))
        parts_msg.append(f"{emoji} <b>{item['title'] or 'Без названия'}</b>  <code>[#{item['id']}]</code> {tags_str}")
        
        short_title = (item['title'] or "Заметка")[:45]
        if len(item['title'] or "") > 45: short_title += "..."
            
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"📖 Показать (#{item['id']})", callback_data=f"view_item:{item['id']}")
        ])

    keyboard.inline_keyboard.extend(get_idea_nav_keyboard().inline_keyboard)
    
    if len(items) > 10:
        parts_msg.append(f"\n<i>Показаны первые 10 из {len(items)}.</i>")

    await message.answer("\n".join(parts_msg), parse_mode="HTML", reply_markup=keyboard)


def _extract_tags(text: str) -> list[str]:
    return [w.lstrip("#").lower() for w in text.split() if w.startswith("#")]


def _clean_caption(text: str) -> str:
    return " ".join(w for w in text.split() if not w.startswith("#")).strip()