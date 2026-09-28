"""
/start и приём ключа текстом.

Логика молчания: бот отвечает только (1) подтверждённым админам и
(2) валидным свободным ключом. Во всех остальных случаях — молчит.
Этот роутер должен подключаться ПОСЛЕДНИМ (после admin/user роутеров),
чтобы не перехватывать команды и FSM-состояния, которые уже обработаны выше.
"""

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database
from filters import NotBanned

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(CommandStart(), NotBanned())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    key_row = await database.get_key_for_user(message.from_user.id)
    if key_row:
        # уже активированный рекламодатель — показываем главное меню
        from handlers_user import show_main_menu
        await show_main_menu(message, key_row)
        return
    # ключа нет — по ТЗ бот молчит
    return


@router.message(F.text, NotBanned())
async def maybe_key(message: Message, state: FSMContext) -> None:
    # если это FSM-ввод (например, ждём название рассылки) — этот хендлер
    # не должен сюда доходить, т.к. хендлеры состояний зарегистрированы раньше.
    current_state = await state.get_state()
    if current_state is not None:
        return  # молчим, состояние обработается своим хендлером выше

    existing = await database.get_key_for_user(message.from_user.id)
    if existing:
        return  # уже активирован, свободный текст вне меню игнорируем

    code = message.text.strip().upper()
    key_row = await database.get_key_by_code(code)
    if key_row is None or key_row["owner_id"] is not None:
        return  # невалидный или уже занятый ключ — молчим

    await database.activate_key(key_row["id"], message.from_user.id)
    content, entities = await database.render_text("KEY_ACTIVATED")
    await message.answer(content, entities=entities, parse_mode=None)
    from handlers_user import show_main_menu
    await show_main_menu(message, key_row)
