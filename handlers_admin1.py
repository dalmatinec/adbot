"""
/admin1 — скрытая команда (не показывается в списке команд бота).

Двухуровневое меню:
  a1_menu (корень) -> [🔘 Кнопки] [📝 Тексты]
  🔘 Кнопки  -> список кнопок рекламодателя -> карточка кнопки
               (название/цвет/эмодзи/позиция; «Назад» -> список кнопок)
  📝 Тексты  -> категории -> список текстов категории -> карточка текста
               (изменить/сбросить; «Назад» -> список текстов той же категории)
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import database
import keyboards
import texts
from filters import IsAdmin
from states import Admin1States

router = Router()
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(F.message.chat.type == "private", IsAdmin())


@router.message(Command("admin1"))
async def cmd_admin1(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await database.t("ADMIN1_MENU_TITLE"), reply_markup=keyboards.admin1_root_kb())


@router.callback_query(F.data == "a1_menu")
async def cb_a1_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(await database.t("ADMIN1_MENU_TITLE"), reply_markup=keyboards.admin1_root_kb())
    await call.answer()


# ================= кнопки =================

@router.callback_query(F.data == "a1_buttons")
async def cb_a1_buttons(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    buttons = await database.list_editable_buttons()
    await call.message.edit_text("🔘 Кнопки рекламодателя:", reply_markup=keyboards.admin1_buttons_menu_kb(buttons))
    await call.answer()


@router.callback_query(F.data.startswith("a1_open:"))
async def cb_a1_open(call: CallbackQuery) -> None:
    button_key = call.data.split(":", 1)[1]
    row = await database.get_button(button_key)
    await call.message.edit_text(
        f"Кнопка: {row['label']}\nЦвет: {row['style']}\nЭмодзи: {'установлен' if row['emoji_id'] else 'нет'}",
        reply_markup=keyboards.admin1_button_edit_kb(button_key),
    )
    await call.answer()


@router.callback_query(F.data.startswith("a1_label:"))
async def cb_a1_label(call: CallbackQuery, state: FSMContext) -> None:
    button_key = call.data.split(":", 1)[1]
    await state.set_state(Admin1States.editing_label)
    await state.update_data(button_key=button_key)
    await call.message.answer(await database.t("ADMIN1_ASK_LABEL"))
    await call.answer()


@router.message(Admin1States.editing_label)
async def msg_a1_label(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    button_key = data["button_key"]
    await database.update_button(button_key, label=message.text.strip()[:64])
    await state.clear()
    await message.answer(await database.t("ADMIN1_SAVED"))
    row = await database.get_button(button_key)
    await message.answer(
        f"Кнопка: {row['label']}\nЦвет: {row['style']}", reply_markup=keyboards.admin1_button_edit_kb(button_key)
    )


@router.callback_query(F.data.startswith("a1_style:"))
async def cb_a1_style(call: CallbackQuery) -> None:
    _, button_key, style = call.data.split(":")
    await database.update_button(button_key, style=style)
    await call.answer(await database.t("ADMIN1_SAVED"))
    row = await database.get_button(button_key)
    await call.message.edit_text(
        f"Кнопка: {row['label']}\nЦвет: {row['style']}", reply_markup=keyboards.admin1_button_edit_kb(button_key)
    )


@router.callback_query(F.data.startswith("a1_emoji:"))
async def cb_a1_emoji(call: CallbackQuery, state: FSMContext) -> None:
    button_key = call.data.split(":", 1)[1]
    await state.set_state(Admin1States.editing_emoji)
    await state.update_data(button_key=button_key)
    await call.message.answer(await database.t("ADMIN1_ASK_EMOJI"))
    await call.answer()


@router.message(Admin1States.editing_emoji)
async def msg_a1_emoji(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    button_key = data["button_key"]
    emoji_id = None
    if message.entities:
        for e in message.entities:
            if e.type == "custom_emoji":
                emoji_id = e.custom_emoji_id
                break
    if not emoji_id:
        return await message.answer("Не нашёл кастомный эмодзи в сообщении. Пришлите его ещё раз.")
    await database.update_button(button_key, emoji_id=emoji_id)
    await state.clear()
    await message.answer(await database.t("ADMIN1_SAVED"))
    row = await database.get_button(button_key)
    await message.answer(
        f"Кнопка: {row['label']}\nЦвет: {row['style']}", reply_markup=keyboards.admin1_button_edit_kb(button_key)
    )


@router.callback_query(F.data.startswith("a1_pos:"))
async def cb_a1_pos(call: CallbackQuery) -> None:
    _, button_key, direction = call.data.split(":")
    await database.swap_button_position(button_key, direction)
    await call.answer(await database.t("ADMIN1_SAVED"))
    row = await database.get_button(button_key)
    await call.message.edit_text(
        f"Кнопка: {row['label']}\nЦвет: {row['style']}", reply_markup=keyboards.admin1_button_edit_kb(button_key)
    )


# ================= тексты =================

@router.callback_query(F.data == "a1_texts")
async def cb_a1_texts(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("📝 Выберите раздел текстов:", reply_markup=keyboards.admin1_texts_categories_kb())
    await call.answer()


@router.callback_query(F.data.startswith("a1_text_group:"))
async def cb_a1_text_group(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    group = call.data.split(":", 1)[1]
    items = await database.list_texts_by_group(group)
    title = texts.GROUP_LABELS.get(group, "Тексты")
    await call.message.edit_text(f"{title}:", reply_markup=keyboards.admin1_texts_list_kb(group, items))
    await call.answer()


@router.callback_query(F.data.startswith("a1_text_open:"))
async def cb_a1_text_open(call: CallbackQuery) -> None:
    key = call.data.split(":", 1)[1]
    content = await database.get_text(key)
    meta = texts.TEXT_META.get(key, {"group": "other", "label": key})
    await call.message.edit_text(
        f"📝 {meta['label']}\n\nТекущий текст:\n———\n{content}\n———",
        reply_markup=keyboards.admin1_text_edit_kb(key, meta["group"]),
        parse_mode=None,
    )
    await call.answer()


@router.callback_query(F.data.startswith("a1_text_edit:"))
async def cb_a1_text_edit(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    current = await database.get_text(key)
    await state.set_state(Admin1States.editing_text)
    await state.update_data(text_key=key)
    await call.message.answer(
        "Пришлите новый текст сообщением. Если в тексте есть плейсхолдеры "
        "в фигурных скобках (например {count} или {hours}) — сохраните их "
        "как есть, иначе бот не сможет подставить туда значения.\n\n"
        f"Текущий текст:\n———\n{current}\n———",
        parse_mode=None,
    )
    await call.answer()


@router.message(Admin1States.editing_text)
async def msg_a1_text_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data["text_key"]
    new_content = message.text or message.caption
    if not new_content:
        return await message.answer("Нужен текст сообщением. Пришлите ещё раз.")
    # Кастомные эмодзи (и другое форматирование — жирный, курсив и т.п.),
    # которые админ вставил в сообщение, Telegram передаёт не в самом тексте,
    # а отдельным списком "entities" поверх него. Раньше эта разметка нигде
    # не сохранялась, поэтому кастомный эмодзи после сохранения текста
    # превращался обратно в обычный символ-заглушку. Сохраняем entities
    # вместе с текстом, чтобы бот мог показать их пользователю как есть.
    raw_entities = message.entities or message.caption_entities
    entities = [e.model_dump(mode="json", exclude_none=True) for e in raw_entities] if raw_entities else None
    await database.update_text(key, new_content, entities=entities)
    await state.clear()
    await message.answer(await database.t("ADMIN1_SAVED"))
    meta = texts.TEXT_META.get(key, {"group": "other", "label": key})
    await message.answer(
        f"📝 {meta['label']}\n\nТекущий текст:\n———\n{new_content}\n———",
        reply_markup=keyboards.admin1_text_edit_kb(key, meta["group"]),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("a1_text_reset:"))
async def cb_a1_text_reset(call: CallbackQuery) -> None:
    key = call.data.split(":", 1)[1]
    await database.reset_text(key)
    content = await database.get_text(key)
    meta = texts.TEXT_META.get(key, {"group": "other", "label": key})
    await call.answer("↩️ Сброшено к тексту по умолчанию.", show_alert=True)
    await call.message.edit_text(
        f"📝 {meta['label']}\n\nТекущий текст:\n———\n{content}\n———",
        reply_markup=keyboards.admin1_text_edit_kb(key, meta["group"]),
        parse_mode=None,
    )
