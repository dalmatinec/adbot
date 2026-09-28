"""
Все клавиатуры бота - только inline (по решению: нижней reply-клавиатуры нет вообще).

Кнопки рекламодателя (bc_*) берут текст/цвет/эмодзи из database.get_button(),
которые редактируются через /admin1 -> "🔘 Кнопки". Кнопки самой /adm-панели -
статичные (не редактируются через /admin1, только их текст - через
"📝 Тексты", если у сообщения-заголовка есть свой ключ в texts.py).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
import texts


async def _adv_button(button_key: str, callback_data: str) -> InlineKeyboardButton:
    row = await database.get_button(button_key)
    kwargs = {"text": row["label"], "callback_data": callback_data}
    # Нативные поля Bot API 9.4 (с 09.02.2026): цветной style и кастомный emoji.
    style = row["style"]
    emoji_id = row["emoji_id"]
    if style:
        kwargs["style"] = style
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)


# ---------- меню рекламодателя ----------

async def broadcasts_list_kb(broadcasts) -> InlineKeyboardMarkup:
    """Кнопка "Новая рассылка" здесь показывается ТОЛЬКО если список пуст -
    когда рассылки уже есть, создавать новую предлагается из главного меню
    (там она есть всегда, пока не достигнут лимит)."""
    rows = []
    for b in broadcasts:
        mark = "🟢" if b["is_active"] else "⚪️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {b['title']}", callback_data=f"bc_open:{b['id']}"
            )
        ])
    if not broadcasts:
        rows.append([await _adv_button("bc_new", "bc_new")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adv_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_list_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="bc_back_to_list")]]
    )


def back_to_main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adv_main_menu")]]
    )


async def adv_main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню рекламодателя (после /start и активации ключа):
    ➕ Новая рассылка / 📋 Мои рассылки / 👥 Админы / ℹ️ Инфо."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [await _adv_button("bc_new", "bc_new")],
        [
            await _adv_button("adv_broadcasts", "bc_list_open"),
            await _adv_button("adv_admins", "key_admins_open")
        ],
        [await _adv_button("adv_help", "adv_help")],
    ])


async def broadcast_card_kb(broadcast) -> InlineKeyboardMarkup:
    toggle_key = "bc_toggle_off" if broadcast["is_active"] else "bc_toggle_on"
    toggle_cb = f"bc_toggle:{broadcast['id']}"
    rows = [
        [await _adv_button("bc_title", f"bc_edit_title:{broadcast['id']}")],
        [await _adv_button("bc_post", f"bc_edit_post:{broadcast['id']}")],
        [await _adv_button("bc_interval", f"bc_interval:{broadcast['id']}")],
        [await _adv_button(toggle_key, toggle_cb)],
        [await _adv_button("bc_status", f"bc_status:{broadcast['id']}")],
        [await _adv_button("bc_delete", f"bc_delete:{broadcast['id']}")],
        [await _adv_button("btn_back", "bc_back_to_list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def interval_kb(broadcast_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{h} ч.", callback_data=f"bc_set_interval:{broadcast_id}:{h}")]
        for h in config.ALLOWED_INTERVALS_HOURS
    ]
    rows.append([await _adv_button("btn_back", f"bc_open:{broadcast_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cancel_kb(callback_data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[await _adv_button("btn_cancel", callback_data)]]
    )


async def confirm_delete_kb(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"bc_delete_confirm:{broadcast_id}")],
            [await _adv_button("btn_back", f"bc_open:{broadcast_id}")],
        ]
    )


# ---------- мастер создания рассылки (title -> post -> интервал -> подтверждение) ----------

def new_broadcast_interval_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{h} ч.", callback_data=f"nb_interval:{h}")]
        for h in config.ALLOWED_INTERVALS_HOURS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def new_broadcast_confirm_kb(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Включить сейчас", callback_data=f"bc_toggle:{broadcast_id}")],
        [InlineKeyboardButton(text="🕒 Позже", callback_data=f"bc_open:{broadcast_id}")],
    ])


# ---------- админы ключа (owner + до MAX_KEY_ADMINS помощников) ----------

def key_admins_kb(admin_labels: list[tuple[int, str]], is_owner: bool) -> InlineKeyboardMarkup:
    """admin_labels - список (user_id, подпись_для_кнопки), уже готовые
    строки (см. mentions.button_label - вызывается в handlers_user.py,
    сюда просто передаётся готовый текст, чтобы клавиатура оставалась
    синхронной функцией)."""
    rows = []
    for user_id, label in admin_labels:
        if is_owner:
            rows.append([InlineKeyboardButton(text=f"❌ {label}", callback_data=f"key_admin_del:{user_id}")])
        else:
            rows.append([InlineKeyboardButton(text=f"👤 {label}", callback_data="noop")])
    if is_owner:
        rows.append([InlineKeyboardButton(text="➕ Добавить админа", callback_data="key_admin_add")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adv_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- /adm ----------

def admin_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="adm_keys")],
        [InlineKeyboardButton(text="📢 Все рассылки ключей", callback_data="adm_users")],
        [InlineKeyboardButton(text="📣 Разовая рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🎯 Целевые чаты", callback_data="adm_targets")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="👥 Супер-админы", callback_data="adm_superadmins")],
        [InlineKeyboardButton(text="💾 Бэкап баз", callback_data="adm_backup")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")]]
    )


def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Последние ошибки отправки", callback_data="adm_stats_errors")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")],
    ])


def keys_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Сгенерировать ключ", callback_data="adm_key_new")],
        [InlineKeyboardButton(text="📋 Список ключей", callback_data="adm_key_list")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def keys_list_kb(keys) -> InlineKeyboardMarkup:
    """Список ключей кнопками (вместо простыни текста) - открыть карточку
    конкретного ключа и оттуда его отозвать. Рядом с кодом сразу видно имя
    владельца (если он уже писал боту) - не нужно открывать каждый ключ по
    отдельности, чтобы понять, чей он."""
    import mentions
    rows = []
    for k in keys:
        if k["owner_id"]:
            owner_label = await mentions.button_label(k["owner_id"])
            text = f"🔒 {k['key_code']} - {owner_label}"
        else:
            text = f"🟢 {k['key_code']} - свободен"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"adm_key_open:{k['id']}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_keys")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def key_card_kb(key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Отозвать ключ", callback_data=f"adm_key_del:{key_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_key_list")],
        ]
    )


def key_delete_confirm_kb(key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, отозвать", callback_data=f"adm_key_del_confirm:{key_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"adm_key_open:{key_id}")],
        ]
    )


def superadmins_kb(admins: list[int], owner_id: int) -> InlineKeyboardMarkup:
    rows = []
    for a in admins:
        label = f"👑 {a} (владелец)" if a == owner_id else f"❌ Убрать {a}"
        cb = "noop" if a == owner_id else f"adm_superadmin_del:{a}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.append([InlineKeyboardButton(text="➕ Добавить супер-админа", callback_data="adm_superadmin_add")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def users_list_kb() -> InlineKeyboardMarkup:
    """Пустой раздел "Рассылки" (владельцев с ключами пока нет вообще)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Поиск по ID", callback_data="adm_user_search")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")],
    ])


async def owners_list_kb(owners_rows: list[dict], orphan_count: int = 0) -> InlineKeyboardMarkup:
    """Раздел "Рассылки" - кнопка на каждого владельца ключа (имя, сколько
    рассылок и сколько из них включено), открывает его карточку из
    handlers_admin._build_user_card. Раньше это был текстовый список без
    кнопок - теперь тот же паттерн, что и у keys_list_kb, единообразно.
    "Без ключа" внизу появляется, только если такие вообще нашлись -
    в норме их быть не должно (см. database.find_orphan_broadcasts)."""
    import mentions
    rows = []
    for o in owners_rows:
        mark = "🚫" if o["banned"] else "🟢"
        active = f"{o['active_count']}/{o['total_count']}" if o["total_count"] else "0"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {o['label']} - {active}",
            callback_data=f"adm_user_open:{o['user_id']}",
        )])
    if orphan_count:
        rows.append([InlineKeyboardButton(
            text=f"⚠️ Без ключа ({orphan_count})", callback_data="adm_orphans",
        )])
    rows.append([InlineKeyboardButton(text="🔎 Поиск по ID", callback_data="adm_user_search")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def orphans_list_kb(broadcasts) -> InlineKeyboardMarkup:
    """Раздел "Рассылки" -> "Без ключа" - кнопка на каждую осиротевшую
    рассылку, открывает мини-карточку с "Выключить"/"Удалить"."""
    rows = [
        [InlineKeyboardButton(
            text=f"⚠️ #{b['id']} {b['title']} ({'вкл' if b['is_active'] else 'выкл'})",
            callback_data=f"adm_orphan_open:{b['id']}",
        )]
        for b in broadcasts
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def orphan_card_kb(broadcast_id: int, is_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_active:
        rows.append([InlineKeyboardButton(
            text="⏸ Выключить", callback_data=f"adm_orphan_off:{broadcast_id}",
        )])
    rows.append([InlineKeyboardButton(
        text="🗑 Удалить рассылку", callback_data=f"adm_orphan_del:{broadcast_id}",
    )])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_orphans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_card_kb(user_id: int, banned: bool, key_id: int | None = None, broadcasts=None) -> InlineKeyboardMarkup:
    """key_id/broadcasts - если у пользователя есть активированный ключ:
    добавляем превью каждого загруженного поста и кнопку отзыва ключа
    (после отзыва ключ становится недействительным, а все рассылки на
    него удаляются - см. database.delete_key)."""
    rows = []
    if key_id:
        for b in (broadcasts or []):
            if b["source_message_id"]:
                rows.append([InlineKeyboardButton(
                    text=f"👁 Превью - {b['title']}",
                    callback_data=f"adm_user_preview:{b['id']}",
                )])
        if any(b["is_active"] for b in (broadcasts or [])):
            rows.append([InlineKeyboardButton(
                text="⏸ Остановить все рассылки", callback_data=f"adm_user_stop_all:{user_id}:{key_id}",
            )])
        rows.append([InlineKeyboardButton(text="🗑 Отозвать ключ", callback_data=f"adm_user_revoke:{user_id}:{key_id}")])
    ban_btn = (
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"adm_unban:{user_id}")
        if banned else
        InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm_ban:{user_id}")
    )
    rows.append([ban_btn])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- целевые чаты (/adm -> "Целевые чаты") ----------

def targets_list_kb(chats: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        mark = "🟢" if c["is_active"] else "⚪️"
        label = c["title"] or str(c["chat_id"])
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {label} ({c['total_sent']})",
                callback_data=f"adm_target_open:{c['chat_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить группу", callback_data="adm_target_add")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_card_kb(chat_id: int) -> InlineKeyboardMarkup:
    """Упрощённая карточка группы - без разбивки по ключам и пользователям
    (при сотне рекламодателей такой список не поместился бы на экран).
    Подробности по конкретному пользователю смотрите в разделе "Пользователи"."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить группу", callback_data=f"adm_target_del:{chat_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_targets")],
    ])


def target_delete_confirm_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"adm_target_del_confirm:{chat_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"adm_target_open:{chat_id}")],
    ])


def factory_reset_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, очистить всё", callback_data="adm_factory_reset_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="adm_factory_reset_cancel")],
    ])


# ---------- /admin1 ----------

def admin1_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Кнопки", callback_data="a1_buttons")],
        [InlineKeyboardButton(text="📝 Тексты", callback_data="a1_texts")],
    ])


def admin1_buttons_menu_kb(buttons) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=b["label"], callback_data=f"a1_open:{b['button_key']}")]
        for b in buttons
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="a1_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin1_button_edit_kb(button_key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Название", callback_data=f"a1_label:{button_key}")],
        [
            InlineKeyboardButton(text="🔵 Синий", callback_data=f"a1_style:{button_key}:primary"),
            InlineKeyboardButton(text="🟢 Зелёный", callback_data=f"a1_style:{button_key}:success"),
            InlineKeyboardButton(text="🔴 Красный", callback_data=f"a1_style:{button_key}:danger"),
        ],
        [InlineKeyboardButton(text="✨ Кастомный эмодзи", callback_data=f"a1_emoji:{button_key}")],
        [
            InlineKeyboardButton(text="⬆️ Выше", callback_data=f"a1_pos:{button_key}:up"),
            InlineKeyboardButton(text="⬇️ Ниже", callback_data=f"a1_pos:{button_key}:down"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="a1_buttons")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin1_texts_categories_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.GROUP_LABELS[g], callback_data=f"a1_text_group:{g}")]
        for g in texts.GROUP_ORDER
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="a1_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin1_texts_list_kb(group: str, items: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=item["label"], callback_data=f"a1_text_open:{item['key']}")]
        for item in items
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="a1_texts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin1_text_edit_kb(key: str, group: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"a1_text_edit:{key}")],
        [InlineKeyboardButton(text="↩️ Сбросить по умолчанию", callback_data=f"a1_text_reset:{key}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"a1_text_group:{group}")],
    ])
