"""
Меню рекламодателя: главное меню -> «Мои рассылки» -> карточка рассылки.
Всё inline, без нижней клавиатуры.

Доступ к меню имеет не только владелец ключа, но и его админы (см.
database.get_key_for_user) — используйте ИМЕННО эту функцию для проверки
доступа везде в этом файле, а не database.get_key_by_owner напрямую.
"""

import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import config
import database
import keyboards
import mentions
import scheduler
import timeutils
from filters import NotBanned
from states import BroadcasterStates, KeyAdminStates, NewBroadcastStates

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


async def _store_post(bot: Bot, message: Message) -> tuple[int, int]:
    """Возвращает (chat_id, message_id), по которым планировщик потом будет
    пересылать пост.

    Если в config.py задан STORAGE_CHAT_ID (приватный канал-хранилище, в
    котором бот админ) - пост пересылается в него, и хранится ссылка на
    сообщение В КАНАЛЕ. Канал не зависит от бота: при смене токена/бота
    достаточно добавить нового бота в этот канал админом - все посты
    продолжат работать, заново их присылать не нужно.

    Именно forward (а не copy): пометка «переслано из…» у поста остаётся
    такой же, как при пересылке прямо из ЛС.

    Если STORAGE_CHAT_ID не задан или пересылка в канал не удалась - работаем
    по-старому: ссылка на сообщение в ЛС с ботом (тогда при смене бота посты
    придётся присылать заново)."""
    storage_id = getattr(config, "STORAGE_CHAT_ID", None)
    if storage_id:
        try:
            saved = await bot.forward_message(
                chat_id=storage_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            return storage_id, saved.message_id
        except Exception:
            log.exception(
                "Не удалось сохранить пост в канал-хранилище %s - "
                "используем ссылку на ЛС", storage_id,
            )
    return message.chat.id, message.message_id


async def _get_active_key(user_id: int):
    """Ключ, к которому у user_id есть доступ — как владельцу, так и админу
    ключа (см. docstring database.get_key_for_user)."""
    return await database.get_key_for_user(user_id)


async def show_broadcasts_list(target, key_id: int) -> None:
    """target — Message (для «Мои рассылки») или CallbackQuery («Назад» из
    карточек и т.п.) — работает с обоими."""
    broadcasts = await database.list_broadcasts(key_id)
    count = len(broadcasts)
    limit = config.MAX_BROADCASTS_PER_KEY
    if broadcasts:
        text, entities = await database.render_text("BROADCASTS_LIST_HEADER", count=count, limit=limit)
    else:
        text, entities = await database.render_text("BROADCASTS_LIST_EMPTY")
    kb = await keyboards.broadcasts_list_kb(broadcasts)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)
    else:
        await target.answer(text, entities=entities, parse_mode=None, reply_markup=kb)


async def show_main_menu(target, key_row) -> None:
    """target — Message (/start, активация ключа) или CallbackQuery («Назад»
    из «Мои рассылки» / «Инфо»)."""
    text, entities = await database.render_text("ADV_MAIN_MENU")
    kb = await keyboards.adv_main_menu_kb()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)
    else:
        await target.answer(text, entities=entities, parse_mode=None, reply_markup=kb)


async def _render_card(target, broadcast) -> None:
    kb = await keyboards.broadcast_card_kb(broadcast)
    text = f"📌 {broadcast['title']}"
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ---------- список / главное меню ----------

@router.callback_query(F.data == "bc_list_open", NotBanned())
async def cb_bc_list_open(call: CallbackQuery) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    await show_broadcasts_list(call, key_row["id"])
    await call.answer()


@router.callback_query(F.data == "adv_main_menu", NotBanned())
async def cb_adv_main_menu(call: CallbackQuery) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    await show_main_menu(call, key_row)
    await call.answer()


@router.callback_query(F.data == "bc_back_to_list", NotBanned())
async def cb_back_to_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    await show_broadcasts_list(call, key_row["id"])
    await call.answer()


# ---------- мастер создания рассылки: название -> пост -> интервал -> подтверждение ----------

@router.callback_query(F.data == "bc_new", NotBanned())
async def cb_new_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    count = await database.count_broadcasts(key_row["id"])
    if count >= config.MAX_BROADCASTS_PER_KEY:
        return await call.answer(
            await database.t("LIMIT_REACHED", limit=config.MAX_BROADCASTS_PER_KEY), show_alert=True
        )
    await state.set_state(NewBroadcastStates.title)
    await state.update_data(key_id=key_row["id"])
    content, entities = await database.render_text("ENTER_TITLE")
    await call.message.answer(
        content, entities=entities, parse_mode=None, reply_markup=await keyboards.cancel_kb("bc_back_to_list")
    )
    await call.answer()


@router.message(NewBroadcastStates.title, NotBanned())
async def msg_wizard_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()[:100] if message.text else "Без названия"
    await state.update_data(title=title)
    await state.set_state(NewBroadcastStates.post)
    content, entities = await database.render_text("ASK_FORWARD_POST")
    await message.answer(
        content, entities=entities, parse_mode=None, reply_markup=await keyboards.cancel_kb("bc_back_to_list")
    )


@router.message(NewBroadcastStates.post, NotBanned())
async def msg_wizard_post(message: Message, state: FSMContext, bot: Bot) -> None:
    # Пост принимается ЛЮБОЙ — форвард откуда угодно или сообщение, которое
    # пользователь написал/прислал прямо сюда сам (не обязательно форвард).
    post_chat_id, post_message_id = await _store_post(bot, message)
    await state.update_data(post_chat_id=post_chat_id, post_message_id=post_message_id)
    await state.set_state(NewBroadcastStates.interval)
    content, entities = await database.render_text("CHOOSE_INTERVAL")
    await message.answer(content, entities=entities, parse_mode=None, reply_markup=keyboards.new_broadcast_interval_kb())


@router.callback_query(F.data.startswith("nb_interval:"), NewBroadcastStates.interval, NotBanned())
async def cb_wizard_interval(call: CallbackQuery, state: FSMContext) -> None:
    hours = int(call.data.split(":")[1])
    data = await state.get_data()
    broadcast_id = await database.create_broadcast(data["key_id"], data["title"])
    await database.set_broadcast_post(broadcast_id, data["post_chat_id"], data["post_message_id"])
    await database.set_broadcast_interval(broadcast_id, hours)
    await state.clear()
    content, entities = await database.render_text("NEW_BROADCAST_READY", title=data["title"], hours=hours)
    await call.message.edit_text(
        content, entities=entities, parse_mode=None, reply_markup=keyboards.new_broadcast_confirm_kb(broadcast_id)
    )
    await call.answer()


# ---------- карточка существующей рассылки ----------

@router.callback_query(F.data.startswith("bc_open:"), NotBanned())
async def cb_open_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    broadcast_id = int(call.data.split(":")[1])
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast:
        return await call.answer()
    await _render_card(call, broadcast)
    await call.answer()


# ---------- название (переименование существующей рассылки) ----------

@router.callback_query(F.data.startswith("bc_edit_title:"), NotBanned())
async def cb_edit_title(call: CallbackQuery, state: FSMContext) -> None:
    broadcast_id = int(call.data.split(":")[1])
    await state.set_state(BroadcasterStates.editing_title)
    await state.update_data(broadcast_id=broadcast_id)
    content, entities = await database.render_text("ENTER_TITLE")
    await call.message.answer(
        content, entities=entities, parse_mode=None, reply_markup=await keyboards.cancel_kb(f"bc_open:{broadcast_id}")
    )
    await call.answer()


@router.message(BroadcasterStates.editing_title, NotBanned())
async def msg_edit_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    broadcast_id = data["broadcast_id"]
    title = message.text.strip()[:100] if message.text else "Без названия"
    await database.set_broadcast_title(broadcast_id, title)
    await state.clear()
    content, entities = await database.render_text("TITLE_SAVED", title=title)
    await message.answer(content, entities=entities, parse_mode=None)
    broadcast = await database.get_broadcast(broadcast_id)
    await _render_card(message, broadcast)


# ---------- пост (замена поста существующей рассылки) ----------

@router.callback_query(F.data.startswith("bc_edit_post:"), NotBanned())
async def cb_edit_post(call: CallbackQuery, state: FSMContext) -> None:
    broadcast_id = int(call.data.split(":")[1])
    await state.set_state(BroadcasterStates.waiting_post)
    await state.update_data(broadcast_id=broadcast_id)
    content, entities = await database.render_text("ASK_FORWARD_POST")
    await call.message.answer(
        content, entities=entities, parse_mode=None, reply_markup=await keyboards.cancel_kb(f"bc_open:{broadcast_id}")
    )
    await call.answer()


@router.message(BroadcasterStates.waiting_post, NotBanned())
async def msg_post_received(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    broadcast_id = data["broadcast_id"]
    post_chat_id, post_message_id = await _store_post(bot, message)
    await database.set_broadcast_post(broadcast_id, post_chat_id, post_message_id)
    await state.clear()
    content, entities = await database.render_text("POST_SAVED")
    await message.answer(content, entities=entities, parse_mode=None)
    broadcast = await database.get_broadcast(broadcast_id)
    await _render_card(message, broadcast)


# ---------- интервал (изменение у существующей рассылки) ----------

@router.callback_query(F.data.startswith("bc_interval:"), NotBanned())
async def cb_interval(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    kb = await keyboards.interval_kb(broadcast_id)
    content, entities = await database.render_text("CHOOSE_INTERVAL")
    await call.message.edit_text(content, entities=entities, parse_mode=None, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("bc_set_interval:"), NotBanned())
async def cb_set_interval(call: CallbackQuery) -> None:
    _, broadcast_id, hours = call.data.split(":")
    broadcast_id, hours = int(broadcast_id), int(hours)
    await database.set_broadcast_interval(broadcast_id, hours)
    broadcast = await database.get_broadcast(broadcast_id)
    if broadcast["is_active"]:
        await database.reschedule_broadcast(broadcast_id, hours)
    await call.answer(await database.t("INTERVAL_SAVED", hours=hours), show_alert=True)
    broadcast = await database.get_broadcast(broadcast_id)
    await _render_card(call, broadcast)


# ---------- вкл/выкл ----------

@router.callback_query(F.data.startswith("bc_toggle:"), NotBanned())
async def cb_toggle(call: CallbackQuery, bot: Bot) -> None:
    broadcast_id = int(call.data.split(":")[1])
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast:
        return await call.answer()

    if not broadcast["is_active"]:
        if not broadcast["source_message_id"]:
            return await call.answer(await database.t("BROADCAST_NEEDS_POST"), show_alert=True)

        # 1) включаем 2) отправляем СРАЗУ 3) и только теперь ставим время
        # следующей отправки = сейчас + интервал — таймер отсчитывается от
        # момента реальной первой отправки, а не от нажатия кнопки.
        await database.toggle_broadcast(broadcast_id, True)
        sent = await scheduler.send_broadcast(bot, broadcast)
        await database.reschedule_broadcast(broadcast_id, broadcast["interval_hours"])

        if sent:
            alert = await database.t("BROADCAST_ENABLED", hours=broadcast["interval_hours"])
        else:
            alert = await database.t("BROADCAST_ENABLED_NO_TARGET", hours=broadcast["interval_hours"])
        await call.answer(alert, show_alert=True)
    else:
        await database.toggle_broadcast(broadcast_id, False)
        await call.answer(await database.t("BROADCAST_DISABLED"), show_alert=True)

    broadcast = await database.get_broadcast(broadcast_id)
    await _render_card(call, broadcast)


# ---------- статус ----------

@router.callback_query(F.data.startswith("bc_status:"), NotBanned())
async def cb_status(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast["source_message_id"]:
        text = await database.t("STATUS_NO_POST")
    elif broadcast["is_active"]:
        text = await database.t("STATUS_ACTIVE", next_send=timeutils.fmt_dt(broadcast["next_send_at"]))
    else:
        text = await database.t("STATUS_INACTIVE")
    await call.answer(text, show_alert=True)


# ---------- удаление ----------

@router.callback_query(F.data.startswith("bc_delete:"), NotBanned())
async def cb_delete_ask(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    kb = await keyboards.confirm_delete_kb(broadcast_id)
    content, entities = await database.render_text("CONFIRM_DELETE_BROADCAST")
    await call.message.edit_text(content, entities=entities, parse_mode=None, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("bc_delete_confirm:"), NotBanned())
async def cb_delete_confirm(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    await database.delete_broadcast(broadcast_id)
    await call.answer(await database.t("BROADCAST_DELETED"), show_alert=True)
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return
    await show_broadcasts_list(call, key_row["id"])


# ---------- помощь / инфо ----------

@router.callback_query(F.data == "adv_help", NotBanned())
async def cb_adv_help(call: CallbackQuery) -> None:
    content, entities = await database.render_text("MAIN_HELP_TEXT")
    await call.message.edit_text(content, entities=entities, parse_mode=None, reply_markup=keyboards.back_to_main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "adv_mykey", NotBanned())
async def cb_adv_mykey(call: CallbackQuery) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    count = await database.count_broadcasts(key_row["id"])
    content, entities = await database.render_text(
        "MY_KEY_INFO",
        code=key_row["key_code"],
        activated=timeutils.fmt_dt(key_row["activated_at"]),
        count=count,
        limit=config.MAX_BROADCASTS_PER_KEY,
    )
    await call.message.edit_text(content, entities=entities, parse_mode=None, reply_markup=keyboards.back_to_main_menu_kb())
    await call.answer()


# ---------- админы ключа (владелец + до config.MAX_KEY_ADMINS помощников) ----------

async def _show_key_admins(target, key_row, is_owner: bool) -> None:
    admins = await database.list_key_admins(key_row["id"])
    lines = [await database.t("KEY_ADMINS_TITLE")]
    if not admins:
        lines.append(await database.t("KEY_ADMINS_EMPTY"))
    labels = []
    for a in admins:
        label = await mentions.button_label(a["user_id"])
        labels.append((a["user_id"], label))
    kb = keyboards.key_admins_kb(labels, is_owner)
    text = "\n".join(lines)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "key_admins_open", NotBanned())
async def cb_key_admins_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    is_owner = await database.is_key_owner(key_row["id"], call.from_user.id)
    await _show_key_admins(call, key_row, is_owner)
    await call.answer()


@router.callback_query(F.data == "key_admin_add", NotBanned())
async def cb_key_admin_add(call: CallbackQuery, state: FSMContext) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    if not await database.is_key_owner(key_row["id"], call.from_user.id):
        return await call.answer(await database.t("KEY_ADMINS_OWNER_ONLY"), show_alert=True)
    admins = await database.list_key_admins(key_row["id"])
    if len(admins) >= config.MAX_KEY_ADMINS:
        return await call.answer(await database.t("KEY_ADMINS_LIMIT", limit=config.MAX_KEY_ADMINS), show_alert=True)
    await state.set_state(KeyAdminStates.entering_admin_id)
    await state.update_data(key_id=key_row["id"])
    await call.message.answer(await database.t("KEY_ADMINS_ASK_ID"))
    await call.answer()


@router.message(KeyAdminStates.entering_admin_id, NotBanned())
async def msg_key_admin_id(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key_id = data["key_id"]
    try:
        user_id = int(message.text.strip())
    except (ValueError, AttributeError):
        return await message.answer("Некорректный ID.")
    added = await database.add_key_admin(key_id, user_id, config.MAX_KEY_ADMINS)
    await state.clear()
    if added:
        await message.answer(await database.t("KEY_ADMINS_ADDED"))
    else:
        await message.answer(await database.t("KEY_ADMINS_LIMIT", limit=config.MAX_KEY_ADMINS))
    key_row = await database.get_key_by_id(key_id)
    await _show_key_admins(message, key_row, True)


@router.callback_query(F.data.startswith("key_admin_del:"), NotBanned())
async def cb_key_admin_del(call: CallbackQuery) -> None:
    key_row = await _get_active_key(call.from_user.id)
    if not key_row:
        return await call.answer()
    if not await database.is_key_owner(key_row["id"], call.from_user.id):
        return await call.answer(await database.t("KEY_ADMINS_OWNER_ONLY"), show_alert=True)
    user_id = int(call.data.split(":")[1])
    await database.remove_key_admin(key_row["id"], user_id)
    await call.answer(await database.t("KEY_ADMINS_REMOVED"), show_alert=True)
    await _show_key_admins(call, key_row, True)


@router.callback_query(F.data == "noop", NotBanned())
async def cb_noop(call: CallbackQuery) -> None:
    # Кнопка-«заглушка» (не-владелец видит себя/других в списке админов
    # ключа без возможности что-то нажать) — просто гасим часики.
    await call.answer()
