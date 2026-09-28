"""
/adm - основная админ-панель. Полностью inline, без нижней клавиатуры.
Все хендлеры защищены фильтром IsAdmin - если пишет не админ, aiogram не
находит хендлер, и бот молчит (никакого сообщения об отказе).
"""

import html
import re

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

from aiogram.types import FSInputFile

import config
import database
import keyboards
import mentions
import timeutils
from filters import IsAdmin
from states import AdminStates, OwnerBroadcastStates

router = Router()
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(F.message.chat.type == "private", IsAdmin())


@router.message(Command("adm"))
async def cmd_adm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await database.t("ADMIN_MENU_TITLE"), reply_markup=keyboards.admin_menu_kb())


@router.message(Command("factory_reset"))
async def cmd_factory_reset(message: Message, state: FSMContext) -> None:
    """Очистка перед передачей бота новому клиенту: удаляет ключи, рассылки,
    целевые чаты, статистику отправок и баны. Тексты/кнопки (/admin1) и
    список супер-админов не трогает - они хранятся отдельно (content.db)."""
    await state.clear()
    await message.answer(
        "⚠️ Это удалит ВСЕ ключи, рассылки, целевые чаты, статистику отправок и баны "
        "из текущей базы.\n\n"
        "Тексты и оформление кнопок (/admin1) не пострадают - они хранятся отдельно.\n\n"
        "Действие необратимо. Подтвердить очистку?",
        reply_markup=keyboards.factory_reset_confirm_kb(),
    )


@router.callback_query(F.data == "adm_factory_reset_confirm")
async def cb_factory_reset_confirm(call: CallbackQuery) -> None:
    await database.factory_reset()
    await call.answer("✅ Операционные данные очищены.", show_alert=True)
    await call.message.edit_text(await database.t("ADMIN_MENU_TITLE"), reply_markup=keyboards.admin_menu_kb())


@router.callback_query(F.data == "adm_factory_reset_cancel")
async def cb_factory_reset_cancel(call: CallbackQuery) -> None:
    await call.answer("Отменено.")
    await call.message.edit_text(await database.t("ADMIN_MENU_TITLE"), reply_markup=keyboards.admin_menu_kb())


@router.callback_query(F.data == "adm_menu")
async def cb_adm_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(await database.t("ADMIN_MENU_TITLE"), reply_markup=keyboards.admin_menu_kb())
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    # Кнопка-"заглушка" (например, сам владелец в списке супер-админов) -
    # просто гасим часики, действия не требуется.
    await call.answer()


# ---------- ключи ----------

@router.callback_query(F.data == "adm_keys")
async def cb_adm_keys(call: CallbackQuery) -> None:
    await call.message.edit_text(await database.t("ADMIN_KEYS_MENU_TITLE"), reply_markup=keyboards.keys_menu_kb())
    await call.answer()


@router.callback_query(F.data == "adm_key_new")
async def cb_adm_key_new(call: CallbackQuery) -> None:
    # call.answer() - ПЕРВЫМ делом, до записи в БД. У Telegram есть жёсткий
    # лимит: если не ответить на callback_query в течение ~15 секунд, он
    # "протухает" и следующий call.answer() падает с TelegramBadRequest
    # "query is too old". Раньше это было последней строкой, и если
    # database.create_key() подвисало (см. фикс блокировок БД в database.py),
    # ответить уже не успевали - отсюда ощущение, что создание ключа "виснет".
    await call.answer()
    code = await database.create_key()
    await call.message.answer(await database.t("ADMIN_KEY_CREATED", code=code), parse_mode="Markdown")


@router.callback_query(F.data == "adm_key_list")
async def cb_adm_key_list(call: CallbackQuery) -> None:
    keys = await database.list_all_keys()
    if not keys:
        try:
            await call.message.edit_text(await database.t("ADMIN_NO_KEYS"), reply_markup=keyboards.keys_menu_kb())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        return await call.answer()
    await call.message.edit_text(
        await database.t("ADMIN_KEY_LIST_TITLE"), reply_markup=await keyboards.keys_list_kb(keys)
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_key_open:"))
async def cb_adm_key_open(call: CallbackQuery) -> None:
    key_id = int(call.data.split(":")[1])
    key_row = await database.get_key_by_id(key_id)
    if not key_row:
        await call.answer("Ключ не найден - возможно, уже отозван.", show_alert=True)
        keys = await database.list_all_keys()
        if not keys:
            return await call.message.edit_text(await database.t("ADMIN_NO_KEYS"), reply_markup=keyboards.keys_menu_kb())
        return await call.message.edit_text(
            await database.t("ADMIN_KEY_LIST_TITLE"), reply_markup=await keyboards.keys_list_kb(keys)
        )
    header = f"🔑 Ключ: {key_row['key_code']}"
    created_line = f"Создан: {timeutils.fmt_dt(key_row['created_at'])}"
    activated_line = f"Активирован: {timeutils.fmt_dt(key_row['activated_at'])}"
    if key_row["owner_id"]:
        status_line, status_entities = await mentions.mention_line_with_id("Статус: занят - владелец ", key_row["owner_id"])
    else:
        status_line, status_entities = "Статус: свободен", []
    text, entities = mentions.join_lines([
        (header, []), (status_line, status_entities), (created_line, []), (activated_line, []),
    ])
    await call.message.edit_text(text, entities=entities or None, parse_mode=None,
                                  reply_markup=keyboards.key_card_kb(key_id))
    await call.answer()


@router.callback_query(F.data.startswith("adm_key_del:"))
async def cb_adm_key_del(call: CallbackQuery) -> None:
    key_id = int(call.data.split(":")[1])
    key_row = await database.get_key_by_id(key_id)
    if not key_row:
        return await call.answer()
    await call.message.edit_text(
        await database.t("ADMIN_KEY_REVOKE_CONFIRM", code=key_row["key_code"]),
        reply_markup=keyboards.key_delete_confirm_kb(key_id),
        parse_mode="Markdown",
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_key_del_confirm:"))
async def cb_adm_key_del_confirm(call: CallbackQuery) -> None:
    key_id = int(call.data.split(":")[1])
    key_row = await database.get_key_by_id(key_id)
    code = key_row["key_code"] if key_row else "?"
    await database.delete_key(key_id)
    await call.answer(await database.t("ADMIN_KEY_REVOKED", code=code), show_alert=True)
    keys = await database.list_all_keys()
    if not keys:
        return await call.message.edit_text(await database.t("ADMIN_NO_KEYS"), reply_markup=keyboards.keys_menu_kb())
    await call.message.edit_text(
        await database.t("ADMIN_KEY_LIST_TITLE"), reply_markup=await keyboards.keys_list_kb(keys)
    )


# ---------- целевые чаты ----------

@router.callback_query(F.data == "adm_targets")
async def cb_adm_targets(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    chats = await database.list_target_chats_with_stats()
    if not chats:
        await call.message.edit_text(
            "Целевых чатов пока нет.\n\n"
            "Добавьте бота администратором в нужную группу/канал, узнайте её ID "
            "(например через @userinfobot, переслав туда любое сообщение) и нажмите \"Добавить группу\".",
            reply_markup=keyboards.targets_list_kb(chats),
        )
        return await call.answer()
    await call.message.edit_text("🎯 Целевые чаты (в скобках - сколько рассылок туда уже ушло):",
                                  reply_markup=keyboards.targets_list_kb(chats))
    await call.answer()


@router.callback_query(F.data == "adm_target_add")
async def cb_adm_target_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.entering_target_chat)
    await call.message.edit_text(
        await database.t("ADMIN_ASK_TARGET_CHAT_ID"),
        reply_markup=keyboards.back_to_admin_kb(),
    )
    await call.answer()


@router.message(AdminStates.entering_target_chat)
async def msg_target_chat(message: Message, state: FSMContext, bot: Bot) -> None:
    try:
        chat_id = int(message.text.strip())
    except (ValueError, AttributeError):
        return await message.answer("Некорректный ID. Пришлите числом, например -1001234567890.")
    title = None
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or chat.full_name
    except Exception:
        # бот не в чате / нет доступа - всё равно добавляем по ID,
        # просто без красивого названия в списке
        pass
    await database.add_target_chat(chat_id, title)
    await state.clear()
    chats = await database.list_target_chats_with_stats()
    label = title or str(chat_id)
    await message.answer(
        f"🎯 Группа добавлена: {label}\nТеперь рассылки автоматически идут и туда.",
        reply_markup=keyboards.targets_list_kb(chats),
    )


@router.callback_query(F.data.startswith("adm_target_open:"))
async def cb_adm_target_open(call: CallbackQuery) -> None:
    chat_id = int(call.data.split(":")[1])
    chat = await database.get_target_chat(chat_id)
    if not chat:
        await call.answer("Группа не найдена - возможно, уже удалена.", show_alert=True)
        chats = await database.list_target_chats_with_stats()
        return await call.message.edit_text("🎯 Целевые чаты:", reply_markup=keyboards.targets_list_kb(chats))
    # Заголовок: если у чата есть название - показываем его и отдельно ID.
    # Если названия нет (бот не смог его получить), title == None, и раньше
    # ID выводился дважды подряд ("🎯 -100... / ID: -100...") - теперь только один раз.
    if chat["title"]:
        lines = [f"🎯 {chat['title']}", f"ID: {chat_id}", ""]
    else:
        lines = [f"🎯 ID {chat_id}", ""]
    # Без разбивки по ключам/пользователям - при сотне рекламодателей такой
    # список не влезет в одно сообщение. Подробности по конкретному
    # рекламодателю смотрите в разделе "Пользователи".
    summary = await database.chat_stats_summary(chat_id)
    lines.append(f"Рассылок отправлено сюда: {summary['total_sent']} (сегодня: {summary['sent_today']})")
    await call.message.edit_text("\n".join(lines), reply_markup=keyboards.target_card_kb(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("adm_target_del:"))
async def cb_adm_target_del(call: CallbackQuery) -> None:
    chat_id = int(call.data.split(":")[1])
    await call.message.edit_text(
        "Удалить эту группу из списка целевых? Рассылки перестанут туда уходить "
        "(статистика по прошлым отправкам сохранится).",
        reply_markup=keyboards.target_delete_confirm_kb(chat_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_target_del_confirm:"))
async def cb_adm_target_del_confirm(call: CallbackQuery) -> None:
    chat_id = int(call.data.split(":")[1])
    await database.remove_target_chat(chat_id)
    await call.answer("🗑 Группа удалена.", show_alert=True)
    chats = await database.list_target_chats_with_stats()
    await call.message.edit_text("🎯 Целевые чаты:", reply_markup=keyboards.targets_list_kb(chats))


# ---------- супер-админы ----------

@router.callback_query(F.data == "adm_superadmins")
async def cb_adm_superadmins(call: CallbackQuery) -> None:
    admins = await database.list_admins()
    await call.message.edit_text(
        await database.t("ADMIN_SUPERADMINS_TITLE"), reply_markup=keyboards.superadmins_kb(admins, config.OWNER_ID)
    )
    await call.answer()


@router.callback_query(F.data == "adm_superadmin_add")
async def cb_adm_superadmin_add(call: CallbackQuery, state: FSMContext) -> None:
    admins = await database.list_admins()
    if len(admins) >= config.MAX_SUPER_ADMINS:
        return await call.answer(
            await database.t("ADMIN_SUPERADMIN_LIMIT", limit=config.MAX_SUPER_ADMINS), show_alert=True
        )
    await state.set_state(AdminStates.entering_superadmin_id)
    await call.message.edit_text(await database.t("ADMIN_ASK_SUPERADMIN_ID"), reply_markup=keyboards.back_to_admin_kb())
    await call.answer()


@router.message(AdminStates.entering_superadmin_id)
async def msg_superadmin_id(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except (ValueError, AttributeError):
        return await message.answer("Некорректный ID.")
    added = await database.add_admin(user_id)
    await state.clear()
    if added:
        await message.answer(await database.t("ADMIN_SUPERADMIN_ADDED", user_id=user_id))
    else:
        await message.answer(await database.t("ADMIN_SUPERADMIN_LIMIT", limit=config.MAX_SUPER_ADMINS))
    admins = await database.list_admins()
    await message.answer(
        await database.t("ADMIN_SUPERADMINS_TITLE"), reply_markup=keyboards.superadmins_kb(admins, config.OWNER_ID)
    )


@router.callback_query(F.data.startswith("adm_superadmin_del:"))
async def cb_adm_superadmin_del(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[1])
    await database.remove_admin(user_id)
    await call.answer(await database.t("ADMIN_SUPERADMIN_REMOVED", user_id=user_id), show_alert=True)
    admins = await database.list_admins()
    await call.message.edit_text(
        await database.t("ADMIN_SUPERADMINS_TITLE"), reply_markup=keyboards.superadmins_kb(admins, config.OWNER_ID)
    )


# ---------- пользователи ----------

@router.callback_query(F.data == "adm_users")
async def cb_adm_users(call: CallbackQuery) -> None:
    owners = await database.list_owners()
    orphans = await database.find_orphan_broadcasts()
    if not owners and not orphans:
        text = await database.t("ADMIN_NO_USERS")
        await call.message.edit_text(text, reply_markup=keyboards.users_list_kb())
        return await call.answer()

    rows = []
    for o in owners:
        user_id = o["owner_id"]
        broadcasts = await database.list_broadcasts(o["id"])
        try:
            label = await mentions.button_label(user_id)
        except Exception:
            label = f"user_{user_id}"
        rows.append({
            "user_id": user_id,
            "label": label,
            "banned": await database.is_banned(user_id),
            "total_count": len(broadcasts),
            "active_count": sum(1 for b in broadcasts if b["is_active"]),
        })

    text = await database.t("ADMIN_USERS_LIST_TITLE")
    kb = await keyboards.owners_list_kb(rows, len(orphans))
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# ---------- рассылки без ключа (см. database.find_orphan_broadcasts) ----------

@router.callback_query(F.data == "adm_orphans")
async def cb_adm_orphans(call: CallbackQuery) -> None:
    orphans = await database.find_orphan_broadcasts()
    if not orphans:
        return await call.answer("Таких рассылок больше нет.", show_alert=True)
    await call.message.edit_text(
        "⚠️ Рассылки без ключа (ключ удалён не через /adm - обычно правкой базы "
        "напрямую). Включённые сюда не попадут по расписанию - планировщик сам "
        "выключает их на первом же тике.",
        reply_markup=keyboards.orphans_list_kb(orphans),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_orphan_open:"))
async def cb_adm_orphan_open(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast:
        return await call.answer("Рассылка уже удалена.", show_alert=True)
    text = (
        f"⚠️ Рассылка #{broadcast['id']} «{html.escape(broadcast['title'])}»\n"
        f"Интервал: {broadcast['interval_hours']} ч.\n"
        f"Статус: {'🟢 включена' if broadcast['is_active'] else '⚪️ выключена'}\n\n"
        "Ключ, которому она принадлежала, в базе не найден."
    )
    await call.message.edit_text(text, reply_markup=keyboards.orphan_card_kb(broadcast_id, broadcast["is_active"]))
    await call.answer()


@router.callback_query(F.data.startswith("adm_orphan_off:"))
async def cb_adm_orphan_off(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    await database.toggle_broadcast(broadcast_id, False)
    await call.answer("⏸ Выключена.", show_alert=True)
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast:
        return
    await call.message.edit_reply_markup(reply_markup=keyboards.orphan_card_kb(broadcast_id, False))


@router.callback_query(F.data.startswith("adm_orphan_del:"))
async def cb_adm_orphan_del(call: CallbackQuery) -> None:
    broadcast_id = int(call.data.split(":")[1])
    await database.delete_broadcast(broadcast_id)
    await call.answer("🗑 Удалена.", show_alert=True)
    orphans = await database.find_orphan_broadcasts()
    if not orphans:
        return await call.message.edit_text(
            "Рассылок без ключа больше нет.", reply_markup=keyboards.back_to_admin_kb(),
        )
    await call.message.edit_text(
        "⚠️ Рассылки без ключа:", reply_markup=keyboards.orphans_list_kb(orphans),
    )


@router.callback_query(F.data == "adm_user_search")
async def cb_adm_user_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.entering_user_search_id)
    await call.message.edit_text("Пришлите Telegram ID пользователя для поиска:", reply_markup=keyboards.back_to_admin_kb())
    await call.answer()


@router.message(AdminStates.entering_user_search_id)
async def msg_user_search(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except (ValueError, AttributeError):
        return await message.answer("Некорректный ID.")
    await state.clear()
    text, entities, kb = await _build_user_card(user_id)
    await message.answer(text, entities=entities, parse_mode=None, reply_markup=kb)


async def _build_user_card(user_id: int):
    """Общий рендер карточки пользователя: (текст, entities, клавиатура).
    Используется и при открытии из списка, и при поиске по ID, и после
    бана/разбана/отзыва ключа."""
    key_row = await database.get_key_by_owner(user_id)
    banned = await database.is_banned(user_id)
    header, entities = await mentions.mention_line_with_id("👤 Пользователь ", user_id)
    lines = [(header, entities), (f"Статус: {'🚫 забанен' if banned else '✅ активен'}", [])]
    broadcasts = []
    key_id = None
    if key_row:
        key_id = key_row["id"]
        lines.append((f"Ключ: {key_row['key_code']}", []))
        lines.append((f"Ключ активирован: {timeutils.fmt_dt(key_row['activated_at'])}", []))
        broadcasts = await database.list_broadcasts(key_row["id"])
        lines.append((f"Рассылок: {len(broadcasts)}", []))
        for b in broadcasts:
            mark = "🟢" if b["is_active"] else "⚪️"
            lines.append((f"  {mark} {b['title']} (интервал {b['interval_hours']} ч.)", []))
    else:
        lines.append(("Активированного ключа нет.", []))
    text, all_entities = mentions.join_lines(lines)
    kb = keyboards.user_card_kb(user_id, banned, key_id, broadcasts)
    return text, all_entities, kb


@router.callback_query(F.data.startswith("adm_user_open:"))
async def cb_adm_user_open(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[1])
    text, entities, kb = await _build_user_card(user_id)
    await call.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("adm_user_preview:"))
async def cb_adm_user_preview(call: CallbackQuery, bot: Bot) -> None:
    broadcast_id = int(call.data.split(":")[1])
    broadcast = await database.get_broadcast(broadcast_id)
    if not broadcast or not broadcast["source_message_id"]:
        return await call.answer("Пост этой рассылки не найден (возможно, рассылка удалена).", show_alert=True)
    try:
        # copy_message, а не forward_message - визуально то же самое (для
        # проверки контента разница не важна), но, в отличие от форварда,
        # поддерживает reply_markup: можно повесить кнопку "Закрыть", чтобы
        # превью не зависало в чате навсегда после возврата в меню.
        await bot.copy_message(
            chat_id=call.message.chat.id,
            from_chat_id=broadcast["source_chat_id"],
            message_id=broadcast["source_message_id"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Закрыть", callback_data="adm_close_msg")],
            ]),
        )
    except Exception:
        return await call.answer("Не удалось скопировать пост - возможно, исходное сообщение удалено.", show_alert=True)
    await call.answer()


@router.callback_query(F.data == "adm_close_msg")
async def cb_adm_close_msg(call: CallbackQuery) -> None:
    """Общая кнопка "Закрыть" под сообщениями, которые бот не может
    редактировать кнопками меню (превью поста и т.п. - отдельные сообщения,
    а не то, что открыто через edit_text)."""
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("adm_user_stop_all:"))
async def cb_adm_user_stop_all(call: CallbackQuery) -> None:
    """Выключает все рассылки пользователя, ключ не трогает - в отличие от
    отзыва ключа, владелец не теряет доступ и может включить их заново сам."""
    _, user_id, key_id = call.data.split(":")
    user_id, key_id = int(user_id), int(key_id)
    stopped = await database.deactivate_all_broadcasts(key_id)
    await call.answer(f"⏸ Остановлено рассылок: {stopped}. Ключ остался активным.", show_alert=True)
    text, entities, kb = await _build_user_card(user_id)
    await call.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("adm_user_revoke:"))
async def cb_adm_user_revoke(call: CallbackQuery) -> None:
    _, user_id, key_id = call.data.split(":")
    user_id, key_id = int(user_id), int(key_id)
    await database.delete_key(key_id)
    await call.answer("🗑 Ключ отозван, рассылки удалены. Ключ больше недействителен.", show_alert=True)
    text, entities, kb = await _build_user_card(user_id)
    await call.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ban:"))
async def cb_adm_ban(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[1])
    await database.ban_user(user_id)
    await call.answer(await database.t("ADMIN_USER_BANNED", user_id=user_id), show_alert=True)
    text, entities, kb = await _build_user_card(user_id)
    await call.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("adm_unban:"))
async def cb_adm_unban(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[1])
    await database.unban_user(user_id)
    await call.answer(await database.t("ADMIN_USER_UNBANNED", user_id=user_id), show_alert=True)
    text, entities, kb = await _build_user_card(user_id)
    await call.message.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)


# ---------- обычная рассылка владельца (не форвард) ----------

@router.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OwnerBroadcastStates.waiting_post)
    await call.message.edit_text(
        "Пришлите пост (текст/фото + при желании кнопки с Premium Emoji) - "
        "он будет отправлен в целевой(е) чат(ы) один раз, сейчас.",
        reply_markup=keyboards.back_to_admin_kb(),
    )
    await call.answer()


@router.message(OwnerBroadcastStates.waiting_post, IsAdmin())
async def msg_owner_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    targets = await database.list_target_chats()
    if not targets:
        return await message.answer("⚠️ Целевой чат не задан. Сначала укажите его в разделе \"Целевой чат\".")
    sent = 0
    for target in targets:
        try:
            await message.copy_to(target["chat_id"])
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Отправлено в {sent} чат(ов).")


# ---------- статистика ----------

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery) -> None:
    stats = await database.overall_stats()
    lines = [
        "📊 Статистика",
        "",
        f"🔑 Ключей всего: {stats['total_keys']} (активировано: {stats['activated_keys']})",
        f"👤 Пользователей: {stats['total_users']}",
        f"📣 Активных рассылок: {stats['active_broadcasts']}",
        f"🎯 Целевых чатов: {stats['active_chats']}",
        f"📨 Отправлено всего: {stats['total_sent']} (сегодня: {stats['sent_today']})",
        f"⚠️ Ошибок отправки: {stats['total_errors']}",
    ]
    await call.message.edit_text("\n".join(lines), reply_markup=keyboards.stats_kb())
    await call.answer()


@router.callback_query(F.data == "adm_stats_errors")
async def cb_adm_stats_errors(call: CallbackQuery) -> None:
    errors = await database.recent_send_errors(limit=10)
    if not errors:
        text = "Ошибок отправки не было. 🎉"
    else:
        lines = ["⚠️ Последние ошибки отправки:"]
        for e in errors:
            lines.append(f"\n{timeutils.fmt_dt(e['occurred_at'])} - чат {e['chat_id']}\n{e['error']}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=keyboards.back_to_admin_kb())
    await call.answer()


# ---------- бэкап баз ----------

@router.callback_query(F.data == "adm_backup")
async def cb_adm_backup(call: CallbackQuery) -> None:
    await call.answer("Собираю файлы…")
    await _send_backup(call.message)


@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    await _send_backup(message)


async def _send_backup(message: Message) -> None:
    import os
    if os.path.exists(config.DB_PATH):
        await message.answer_document(
            FSInputFile(config.DB_PATH), caption="📦 adbot.db - операционные данные (ключи, рассылки, статистика)"
        )
    if os.path.exists(config.CONTENT_DB_PATH):
        await message.answer_document(
            FSInputFile(config.CONTENT_DB_PATH), caption="🎨 content.db - тексты и оформление кнопок (/admin1)"
        )


# ---------- /setpost: привязать к рассылке пост, который уже лежит в чате ----------

# ссылка на сообщение в супергруппе/канале: t.me/c/<id без -100>/<msg>
# (в группах с топиками между ними ещё номер топика)
_POST_LINK_RE = re.compile(r"t\.me/c/(\d+)/(?:\d+/)?(\d+)")

_SETPOST_USAGE = (
    "Формат:\n"
    "/setpost ID_РАССЫЛКИ ССЫЛКА_НА_ПОСТ [on]\n\n"
    "Ссылка: в целевом чате нажми на пост -> «Копировать ссылку». "
    "Вместо ссылки можно указать просто номер сообщения (берётся первый целевой чат).\n"
    "«on» в конце - сразу включить рассылку."
)


def _parse_post_ref(ref: str) -> tuple[int | None, int | None]:
    """(chat_id или None, message_id или None). chat_id=None - значит
    «первый целевой чат», message_id=None - не распознали."""
    m = _POST_LINK_RE.search(ref)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    if ref.isdigit():
        return None, int(ref)
    return None, None


@router.message(Command("setpost"))
async def cmd_setpost(message: Message, command: CommandObject, bot: Bot) -> None:
    """Админ привязывает к существующей рассылке пост, который бот уже
    когда-то отправил в целевой чат (нужно после смены бота: пересылка из
    ЛС старого бота больше недоступна, а сами посты в чате остались).

    Бот не умеет читать историю чата, поэтому пост указывает админ - ссылкой
    или номером сообщения. Бот пересылает его в канал-хранилище (если задан
    config.STORAGE_CHAT_ID) и запоминает уже ссылку на копию там, а также
    показывает пересланный пост админу - для проверки, что взят нужный."""
    args = (command.args or "").split()
    if len(args) < 2 or not args[0].isdigit():
        return await message.answer(_SETPOST_USAGE)

    broadcast = await database.get_broadcast(int(args[0]))
    if not broadcast:
        return await message.answer("Рассылки с таким id нет.")

    src_chat, src_msg = _parse_post_ref(args[1])
    if src_msg is None:
        return await message.answer(_SETPOST_USAGE)
    if src_chat is None:
        targets = await database.list_target_chats()
        if not targets:
            return await message.answer("Целевой чат не задан - укажите ссылку на пост целиком.")
        src_chat = targets[0]["chat_id"]

    storage_id = getattr(config, "STORAGE_CHAT_ID", None)
    try:
        # показываем админу, какой именно пост взяли (заодно проверка доступа)
        await bot.forward_message(chat_id=message.chat.id, from_chat_id=src_chat, message_id=src_msg)
        if storage_id:
            saved = await bot.forward_message(
                chat_id=storage_id, from_chat_id=src_chat, message_id=src_msg
            )
            post_chat, post_msg = storage_id, saved.message_id
        else:
            post_chat, post_msg = src_chat, src_msg
    except Exception as exc:
        return await message.answer(
            f"❌ Не удалось взять сообщение {src_msg} из чата {src_chat}:\n{html.escape(str(exc))}"
        )

    await database.set_broadcast_post(broadcast["id"], post_chat, post_msg)
    turned_on = len(args) > 2 and args[2].lower() == "on"
    if turned_on:
        # next_send_at остаётся пустым -> планировщик отправит на ближайшем тике
        await database.toggle_broadcast(broadcast["id"], True)
    await message.answer(
        f"✅ Пост привязан к рассылке #{broadcast['id']} «{html.escape(broadcast['title'])}». "
        + ("Рассылка включена - уйдёт в течение ~30 секунд." if turned_on
           else "Рассылка не включена (добавьте «on» в конце команды или включит владелец).")
    )
