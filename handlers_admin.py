"""Админка владельцев основного бота (/adm).

Роли: owner - всё; moderator - зеркала (пауза/блокировка), пользователи (баны),
модерация постов и статистика. Все настройки рассылки - группы, интервал,
слоты, модерация - только у owner.
"""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, MessageEntity

import applications
import backup
import config
import db
import texts
from ui import STATUS_ICON, STATUS_TEXT, esc, fmt_dt, fmt_interval, kb, show, user_link

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

PAGE = 8


class AdminStates(StatesGroup):
    add_group = State()
    group_interval = State()      # data: gid
    global_interval = State()
    user_search = State()
    add_owner = State()
    stopwords = State()
    desc_full = State()
    desc_short = State()
    bc_wait = State()             # data: audience
    bc_confirm = State()
    newmain = State()


async def _role(user_id: int) -> str | None:
    return await db.get_role(user_id)


async def _need(event, owner_only: bool = False) -> str | None:
    """Роль, если доступ есть; иначе None (для чужих бот молчит)."""
    role = await _role(event.from_user.id)
    if role is None:
        return None
    if owner_only and role != "owner":
        if isinstance(event, CallbackQuery):
            await event.answer("Только для владельцев", show_alert=True)
        return None
    return role


# ---------- меню ----------

async def _menu(target, role: str) -> None:
    n_apps = await db.count_applications()
    apps_btn = (f"📥 Заявки ({n_apps})" if n_apps else "📥 Заявки", "a:apps")
    rows = [[apps_btn, ("🪞 Зеркала", "a:mirrors:0")],
            [("👥 Пользователи", "a:users"), ("📊 Статистика", "a:stats")]]
    if role == "owner":
        rows = [[apps_btn, ("🪞 Зеркала", "a:mirrors:0")],
                [("🏘 Группы", "a:groups"), ("👥 Пользователи", "a:users")],
                [("📊 Статистика", "a:stats")],
                [("📢 Рассылка", "a:bc"), ("⚙️ Настройки", "a:set")],
                [("👑 Владельцы", "a:owners"), ("💾 Бэкап", "a:bk")]]
    rows.append([("⬅️ В начало", "m:home")])
    await show(target, "⚙️ <b>Админка</b>", kb(rows))


@router.message(Command("adm"))
async def cmd_adm(message: Message, state: FSMContext) -> None:
    await state.clear()
    role = await _need(message)
    if role:
        await _menu(message, role)


@router.callback_query(F.data == "a:menu")
async def menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    role = await _need(call)
    if role:
        await _menu(call, role)


# ---------- группы ----------

@router.callback_query(F.data == "a:groups")
async def groups(call: CallbackQuery, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
    if not await _need(call, True):
        return
    items = await db.list_groups()
    gi = await db.global_interval()
    rows = []
    for g in items:
        icon = "🟢" if g["is_active"] else "⚪"
        rows.append([(f"{icon} {g['title'] or g['chat_id']}", f"a:g:{g['id']}")])
    rows.append([("➕ Добавить по ID", "a:ga")])
    rows.append([("⬅️ Назад", "a:menu")])
    text = (f"🏘 <b>Группы для рассылки</b>\nОбщий интервал: {fmt_interval(gi)}\n\n"
            "Бот сам предлагает группу, когда его добавляют админом. Включите её здесь."
            if items else "Групп пока нет. Добавьте бота админом в чат или введите ID вручную.")
    await show(call, text, kb(rows))


async def _group_card(call: CallbackQuery, gid: int) -> None:
    g = await db.get_group(gid)
    if not g:
        await groups(call)
        return
    interval = await db.effective_interval(g["interval_minutes"])
    n_posts = (await db.one("SELECT COUNT(*) AS c FROM posts WHERE group_id=?", (gid,)))["c"]
    own = "свой" if g["interval_minutes"] else "общий"
    text = (f"🏘 <b>{esc(g['title'])}</b>\nID чата: <code>{g['chat_id']}</code>\n"
            f"Статус: {'🟢 включена' if g['is_active'] else '⚪ выключена'}\n"
            f"Интервал: {fmt_interval(interval)} ({own})\nПостов: {n_posts}")
    await show(call, text, kb([
        [("⏸ Выключить" if g["is_active"] else "▶️ Включить", f"a:gt:{gid}")],
        [("⏱ Интервал", f"a:gi:{gid}")],
        [("🗑 Удалить", f"a:gd:{gid}")],
        [("⬅️ К группам", "a:groups")],
    ]))


@router.callback_query(F.data.startswith("a:g:"))
async def group_open(call: CallbackQuery) -> None:
    if await _need(call, True):
        await _group_card(call, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("a:gt:"))
async def group_toggle(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    gid = int(call.data.split(":")[2])
    g = await db.get_group(gid)
    if g:
        await db.set_group_active(gid, not g["is_active"])
    await _group_card(call, gid)


@router.callback_query(F.data.startswith("a:gi:"))
async def group_interval(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    gid = int(call.data.split(":")[2])
    presets = [30, 60, 120, 180, 360, 720]
    rows = [[(fmt_interval(m), f"a:gip:{gid}:{m}") for m in presets[i:i + 3]] for i in (0, 3)]
    rows.append([("Общий", f"a:gip:{gid}:0"), ("✏️ Свой", f"a:gic:{gid}")])
    rows.append([("⬅️ Назад", f"a:g:{gid}")])
    await show(call, f"⏱ Интервал группы (минимум {config.MIN_INTERVAL_MINUTES} мин.).\n"
                     "Вариант Общий использует значение из настроек.", kb(rows))


@router.callback_query(F.data.startswith("a:gip:"))
async def group_interval_set(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    _, _, gid, minutes = call.data.split(":")
    value = int(minutes)
    await db.set_group_interval(int(gid), None if value == 0 else max(value, config.MIN_INTERVAL_MINUTES))
    await _group_card(call, int(gid))


@router.callback_query(F.data.startswith("a:gic:"))
async def group_interval_custom(call: CallbackQuery, state: FSMContext) -> None:
    if not await _need(call, True):
        return
    gid = int(call.data.split(":")[2])
    await state.set_state(AdminStates.group_interval)
    await state.update_data(gid=gid)
    await show(call, "Пришлите интервал в минутах числом, например 90.", kb([[("⬅️ Отмена", f"a:g:{gid}")]]))


@router.message(AdminStates.group_interval)
async def group_interval_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    if not (message.text or "").strip().isdigit():
        await message.answer("Нужно число минут.")
        return
    gid = (await state.get_data())["gid"]
    await state.clear()
    await db.set_group_interval(gid, max(int(message.text), config.MIN_INTERVAL_MINUTES))
    await message.answer("✅ Интервал сохранён.", reply_markup=kb([[("К группе", f"a:g:{gid}")]]))


@router.callback_query(F.data.startswith("a:gd:"))
async def group_delete_ask(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    gid = int(call.data.split(":")[2])
    await show(call, "Удалить группу? Все посты, привязанные к ней, тоже удалятся.",
               kb([[("🗑 Да, удалить", f"a:gdc:{gid}")], [("⬅️ Отмена", f"a:g:{gid}")]]))


@router.callback_query(F.data.startswith("a:gdc:"))
async def group_delete(call: CallbackQuery) -> None:
    if await _need(call, True):
        await db.delete_group(int(call.data.split(":")[2]))
        await groups(call)


@router.callback_query(F.data == "a:ga")
async def group_add(call: CallbackQuery, state: FSMContext) -> None:
    if not await _need(call, True):
        return
    await state.set_state(AdminStates.add_group)
    await show(call, "Пришлите ID чата (для супергрупп и каналов он вида <code>-1001234567890</code>). "
                     "Бот должен уже быть в нём админом.", kb([[("⬅️ Отмена", "a:groups")]]))


@router.message(AdminStates.add_group)
async def group_add_msg(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await _need(message, True):
        return
    try:
        chat_id = int((message.text or "").strip())
        chat = await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        await message.answer("Не нашёл такой чат или бота в нём нет. Проверьте ID и права бота.")
        return
    await state.clear()
    gid = await db.add_group(chat_id, chat.title, active=True)
    warn = "" if member.status == "administrator" else "\n⚠️ Бот не админ, писать он, возможно, не сможет."
    await message.answer(f"Группа <b>{esc(chat.title)}</b> добавлена и включена.{warn}",
                         reply_markup=kb([[("К группе", f"a:g:{gid}")]]))


# ---------- заявки на зеркала ----------

@router.callback_query(F.data == "a:apps")
async def apps_list(call: CallbackQuery) -> None:
    if not await _need(call):
        return
    items = await db.list_applications()
    rows = []
    for a in items:
        user = await db.get_user(a["user_id"])
        name = (user["full_name"] if user and user["full_name"] else str(a["user_id"]))
        rows.append([(f"📥 {name} · @{a['username']}", f"a:apo:{a['user_id']}")])
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, f"📥 <b>Заявки на зеркала</b>: {len(items)}" if items else "Заявок нет.", kb(rows))


@router.callback_query(F.data.startswith("a:apo:"))
async def app_open(call: CallbackQuery) -> None:
    if not await _need(call):
        return
    uid = int(call.data.split(":")[2])
    app = await db.get_application(uid)
    if not app:
        await call.answer("Заявка уже обработана", show_alert=True)
        await apps_list(call)
        return
    await show(call, applications.card_text(app, await db.get_user(uid)),
               applications.card_buttons(uid))


@router.callback_query(F.data.startswith("a:ap:"))
async def app_approve(call: CallbackQuery, bot: Bot, manager) -> None:
    if not await _need(call):
        return
    uid = int(call.data.split(":")[2])
    name = await applications.approve(bot, manager, uid)
    who = esc(call.from_user.full_name)
    if name is None:
        await show(call, "Эта заявка уже обработана или истекла.")
        return
    await show(call, f"✅ Заявка {user_link(uid)} на @{esc(name)} принята ({who}).")


@router.callback_query(F.data.startswith("a:ar:"))
async def app_reject(call: CallbackQuery, bot: Bot, manager) -> None:
    if not await _need(call):
        return
    uid = int(call.data.split(":")[2])
    ok = await applications.reject(bot, uid, manager=manager)
    who = esc(call.from_user.full_name)
    await show(call, f"❌ Заявка {user_link(uid)} отклонена ({who}), токен удалён."
               if ok else "Эта заявка уже обработана или истекла.")


# ---------- зеркала ----------

@router.callback_query(F.data.startswith("a:mirrors:"))
async def mirrors(call: CallbackQuery) -> None:
    await _mirrors_screen(call, int(call.data.split(":")[2]))


async def _mirrors_screen(call: CallbackQuery, page: int) -> None:
    if not await _need(call):
        return
    total = await db.count_mirrors()
    items = await db.list_mirrors(page * PAGE, PAGE)
    rows = [[(f"{STATUS_ICON[m['status']]} @{m['username']} · {m['owner_id']}", f"a:m:{m['id']}")]
            for m in items]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"a:mirrors:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(("➡️", f"a:mirrors:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, f"🪞 <b>Зеркала</b>: {total}" if total else "Зеркал пока нет.", kb(rows))


async def _mirror_card(call: CallbackQuery, mid: int, role: str) -> None:
    m = await db.get_mirror(mid)
    if not m:
        await call.answer("Зеркало не найдено", show_alert=True)
        return
    st = await db.mirror_stats(mid)
    admins = await db.list_mirror_admins(mid)
    text = (f"{STATUS_ICON[m['status']]} <b>@{esc(m['username'])}</b>\n"
            f"Владелец: {user_link(m['owner_id'])} (<code>{m['owner_id']}</code>)\n"
            f"Админов: {len(admins)}\nСтатус: {STATUS_TEXT[m['status']]}\n"
            f"Слоты: {st['posts']}/{m['slots']} (потолок {config.MAX_SLOTS})\n"
            f"Отправок: {st['sent']}, ошибок: {st['errors']}\nСоздано: {fmt_dt(m['created_at'])}")
    rows = []
    if role == "owner":
        rows.append([("➖ слот", f"a:ms:{mid}:-1"), ("➕ слот", f"a:ms:{mid}:1")])
    if m["status"] in ("active",):
        rows.append([("⏸ Пауза", f"a:mp:{mid}"), ("⛔ Заблокировать", f"a:mb:{mid}")])
    elif m["status"] in ("paused", "blocked"):
        rows.append([("▶️ Включить", f"a:mp:{mid}")])
    if role == "owner":
        rows.append([("🗑 Удалить зеркало", f"a:md:{mid}")])
    rows.append([("⬅️ К зеркалам", "a:mirrors:0")])
    await show(call, text, kb(rows))


@router.callback_query(F.data.startswith("a:m:"))
async def mirror_open(call: CallbackQuery) -> None:
    role = await _need(call)
    if role:
        await _mirror_card(call, int(call.data.split(":")[2]), role)


@router.callback_query(F.data.startswith("a:ms:"))
async def mirror_slots(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    _, _, mid, delta = call.data.split(":")
    m = await db.get_mirror(int(mid))
    if m:
        new = await db.set_mirror_slots(m["id"], m["slots"] + int(delta))
        await call.answer(f"Слотов: {new}")
    await _mirror_card(call, int(mid), "owner")


@router.callback_query(F.data.startswith("a:mp:"))
async def mirror_pause(call: CallbackQuery) -> None:
    role = await _need(call)
    if not role:
        return
    mid = int(call.data.split(":")[2])
    m = await db.get_mirror(mid)
    if m:
        await db.set_mirror_status(mid, "paused" if m["status"] == "active" else "active")
    await _mirror_card(call, mid, role)


@router.callback_query(F.data.startswith("a:mb:"))
async def mirror_block(call: CallbackQuery, manager) -> None:
    role = await _need(call)
    if not role:
        return
    mid = int(call.data.split(":")[2])
    await db.set_mirror_status(mid, "blocked")
    await manager.notify_staff(mid, "Доступ к рассылке ограничен владельцами сети.")
    await _mirror_card(call, mid, role)


@router.callback_query(F.data.startswith("a:md:"))
async def mirror_delete_ask(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    mid = int(call.data.split(":")[2])
    await show(call, "Удалить зеркало и все его посты? Сам бот в Telegram останется у владельца.",
               kb([[("🗑 Да, удалить", f"a:mdc:{mid}")], [("⬅️ Отмена", f"a:m:{mid}")]]))


@router.callback_query(F.data.startswith("a:mdc:"))
async def mirror_delete(call: CallbackQuery, manager) -> None:
    if not await _need(call, True):
        return
    mid = int(call.data.split(":")[2])
    await manager.stop(mid)
    await db.delete_mirror(mid)
    await call.answer("Удалено")
    await _mirrors_screen(call, 0)


# ---------- модерация постов ----------

@router.callback_query(F.data.startswith("a:ok:") | F.data.startswith("a:no:"))
async def moderate(call: CallbackQuery, manager) -> None:
    if not await _need(call):
        return
    _, action, pid = call.data.split(":")
    post = await db.get_post(int(pid))
    if not post or post["status"] != "pending":
        await show(call, "Этот пост уже обработан или удалён.")
        return
    if action == "ok":
        await db.set_post_status(post["id"], "active")
        await manager.notify_staff(post["mirror_id"], f"Пост {esc(post['title'])} одобрен, рассылка запущена.")
        await show(call, f"✅ Пост #{pid} одобрен.")
    else:
        await db.delete_post(post["id"])
        await manager.notify_staff(post["mirror_id"], f"Пост {esc(post['title'])} отклонён.")
        await show(call, f"❌ Пост #{pid} отклонён и удалён.")


# ---------- пользователи и баны ----------

@router.callback_query(F.data == "a:users")
async def users(call: CallbackQuery, state: FSMContext) -> None:
    if not await _need(call):
        return
    await state.set_state(AdminStates.user_search)
    await show(call, "Пришлите ID пользователя или перешлите его сообщение.",
               kb([[("⬅️ Назад", "a:menu")]]))


async def _user_card(target, uid: int) -> None:
    u = await db.get_user(uid)
    mirror = await db.mirror_for_staff(uid)
    banned = bool(u and u["is_banned"])
    text = (f"👤 {user_link(uid, u['full_name'] if u else None)}\n"
            f"ID: <code>{uid}</code>\nUsername: @{esc(u['username']) if u else 'нет'}\n"
            f"Статус: {'⛔ в бане' if banned else '✅ активен'}\n"
            f"Зеркало: {'@' + esc(mirror['username']) if mirror else 'нет'}")
    btn = ("✅ Разбанить", f"a:uu:{uid}") if banned else ("⛔ Забанить", f"a:ub:{uid}")
    await show(target, text, kb([[btn], [("⬅️ Назад", "a:menu")]]))


@router.message(AdminStates.user_search)
async def user_search_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message):
        return
    uid = None
    origin = message.forward_origin
    if origin is not None and getattr(origin, "type", "") == "user":
        uid = origin.sender_user.id
    elif (message.text or "").strip().isdigit():
        uid = int(message.text.strip())
    if uid is None:
        await message.answer("Нужен числовой ID или пересланное сообщение.")
        return
    await state.clear()
    await _user_card(message, uid)


@router.callback_query(F.data.startswith("a:ub:"))
async def user_ban(call: CallbackQuery, manager) -> None:
    if not await _need(call):
        return
    uid = int(call.data.split(":")[2])
    if await db.get_role(uid):
        await call.answer("Владельцев и модераторов банить нельзя", show_alert=True)
        return
    await db.ban_user(uid)
    mirror = await db.get_mirror_by_owner(uid)
    if mirror:
        await manager.notify_staff(mirror["id"], "Доступ к рассылке ограничен владельцами сети.")
    await _user_card(call, uid)


@router.callback_query(F.data.startswith("a:uu:"))
async def user_unban(call: CallbackQuery) -> None:
    if not await _need(call):
        return
    uid = int(call.data.split(":")[2])
    await db.unban_user(uid)
    await _user_card(call, uid)


# ---------- статистика ----------

@router.callback_query(F.data == "a:stats")
async def stats(call: CallbackQuery) -> None:
    if not await _need(call):
        return
    s = await db.overall_stats()
    lines = [
        "📊 <b>Статистика</b>\n",
        f"Зеркал: {s['mirrors']} (активных {s['mirrors_active']})",
        f"Постов в эфире: {s['posts_active']}, на модерации: {s['posts_pending']}",
        f"Пользователей: {s['users']}, в бане: {s['banned']}",
        f"За 24 ч: отправок {s['sent_24h']}, ошибок {s['errors_24h']}",
    ]
    errors = await db.recent_errors(5)
    if errors:
        lines.append("\nПоследние ошибки:")
        for e in errors:
            lines.append(f"• {fmt_dt(e['sent_at'])}: {esc(e['error'])}")
    await show(call, "\n".join(lines), kb([[("⬅️ Назад", "a:menu")]]))


# ---------- настройки ----------

@router.callback_query(F.data == "a:set")
async def settings(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await _need(call, True):
        return
    gi = await db.global_interval()
    mod = await db.moderation_on()
    slots = await db.get_setting("default_slots", str(config.DEFAULT_SLOTS))
    sw = await db.get_setting("stopwords", "") or "нет"
    text = (f"⚙️ <b>Настройки</b>\n\nОбщий интервал: {fmt_interval(gi)}\n"
            f"Модерация постов: {'включена' if mod else 'выключена'}\n"
            f"Слотов у новых зеркал: {slots}\nСтоп-слова: {esc(sw)}")
    await show(call, text, kb([
        [("⏱ Общий интервал", "a:si")],
        [(f"🛡 Модерация: {'выключить' if mod else 'включить'}", "a:smod")],
        [("🎟 Слоты по умолчанию", "a:ssl")],
        [("🚫 Стоп-слова", "a:sw")],
        [("📝 Описание зеркал", "a:desc")],
        [("⬅️ Назад", "a:menu")],
    ]))


@router.callback_query(F.data == "a:smod")
async def set_moderation(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await db.set_setting("moderation", "0" if await db.moderation_on() else "1")
        await settings(call, state)


@router.callback_query(F.data == "a:si")
async def set_interval(call: CallbackQuery) -> None:
    if not await _need(call, True):
        return
    presets = [30, 60, 120, 180, 360, 720]
    rows = [[(fmt_interval(m), f"a:sip:{m}") for m in presets[i:i + 3]] for i in (0, 3)]
    rows.append([("✏️ Свой", "a:sic")])
    rows.append([("⬅️ Назад", "a:set")])
    await show(call, "⏱ Общий интервал рассылки. Применяется к группам без своего интервала.", kb(rows))


@router.callback_query(F.data.startswith("a:sip:"))
async def set_interval_preset(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await db.set_setting("interval_minutes", str(max(int(call.data.split(":")[2]), config.MIN_INTERVAL_MINUTES)))
        await settings(call, state)


@router.callback_query(F.data == "a:sic")
async def set_interval_custom(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.global_interval)
        await show(call, "Пришлите интервал в минутах числом.", kb([[("⬅️ Отмена", "a:set")]]))


@router.message(AdminStates.global_interval)
async def set_interval_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    if not (message.text or "").strip().isdigit():
        await message.answer("Нужно число минут.")
        return
    await state.clear()
    await db.set_setting("interval_minutes", str(max(int(message.text), config.MIN_INTERVAL_MINUTES)))
    await message.answer("✅ Сохранено.", reply_markup=kb([[("К настройкам", "a:set")]]))


@router.callback_query(F.data == "a:ssl")
async def set_slots(call: CallbackQuery) -> None:
    if await _need(call, True):
        rows = [[(str(n), f"a:ssn:{n}") for n in range(1, 6)], [(str(n), f"a:ssn:{n}") for n in range(6, 11)]]
        rows.append([("⬅️ Назад", "a:set")])
        await show(call, "🎟 Сколько постов на один токен у новых зеркал. "
                         "Существующим слоты меняются в карточке зеркала.", kb(rows))


@router.callback_query(F.data.startswith("a:ssn:"))
async def set_slots_value(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        n = max(1, min(int(call.data.split(":")[2]), config.MAX_SLOTS))
        await db.set_setting("default_slots", str(n))
        await settings(call, state)


@router.callback_query(F.data == "a:sw")
async def stopwords(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.stopwords)
        await show(call, "Пришлите стоп-слова через запятую. Посты с ними не принимаются. "
                         "Для очистки списка отправьте дефис.", kb([[("⬅️ Отмена", "a:set")]]))


@router.message(AdminStates.stopwords)
async def stopwords_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    value = (message.text or "").strip()
    await state.clear()
    await db.set_setting("stopwords", "" if value == "-" else value)
    await message.answer("✅ Сохранено.", reply_markup=kb([[("К настройкам", "a:set")]]))


# ---------- описание зеркал ----------

async def _desc_screen(target) -> None:
    desc = await db.get_setting("mirror_description", texts.DEFAULT_DESCRIPTION)
    short = await db.get_setting("mirror_short_description", texts.DEFAULT_SHORT_DESCRIPTION)
    await show(target, (
        "<b>Описание зеркал</b>\n\n"
        f"Описание (видно до нажатия Старт, до 512 символов):\n{esc(desc)}\n\n"
        f"Краткое описание (профиль бота, до 120 символов):\n{esc(short)}\n\n"
        "Изменение применяется ко всем зеркалам."), kb([
        [("Изменить описание", "a:descp")],
        [("Изменить краткое описание", "a:descs")],
        [("Вернуть стандартные", "a:descr")],
        [("⬅️ Назад", "a:set")],
    ]))


@router.callback_query(F.data == "a:desc")
async def desc_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await _need(call, True):
        await _desc_screen(call)


@router.callback_query(F.data == "a:descp")
async def desc_edit(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.desc_full)
        await show(call, "Отправьте новый текст описания (до 512 символов).", kb([[("⬅️ Отмена", "a:desc")]]))


@router.callback_query(F.data == "a:descs")
async def desc_short_edit(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.desc_short)
        await show(call, "Отправьте новый текст краткого описания (до 120 символов).",
                   kb([[("⬅️ Отмена", "a:desc")]]))


async def _save_desc(message: Message, state: FSMContext, manager, key: str, limit: int) -> None:
    if not await _need(message, True):
        return
    value = (message.text or "").strip()
    if not value or len(value) > limit:
        await message.answer(f"Текст должен содержать от 1 до {limit} символов. Сейчас: {len(value)}.")
        return
    await state.clear()
    await db.set_setting(key, value)
    count = await manager.apply_descriptions_all()
    await message.answer(f"Сохранено. Обновлено зеркал: {count}.", reply_markup=kb([[("К описанию", "a:desc")]]))


@router.message(AdminStates.desc_full)
async def desc_full_msg(message: Message, state: FSMContext, manager) -> None:
    await _save_desc(message, state, manager, "mirror_description", 512)


@router.message(AdminStates.desc_short)
async def desc_short_msg(message: Message, state: FSMContext, manager) -> None:
    await _save_desc(message, state, manager, "mirror_short_description", 120)


@router.callback_query(F.data == "a:descr")
async def desc_reset(call: CallbackQuery, manager) -> None:
    if not await _need(call, True):
        return
    await db.set_setting("mirror_description", texts.DEFAULT_DESCRIPTION)
    await db.set_setting("mirror_short_description", texts.DEFAULT_SHORT_DESCRIPTION)
    await manager.apply_descriptions_all()
    await _desc_screen(call)


# ---------- владельцы ----------

@router.callback_query(F.data == "a:owners")
async def owners(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await _need(call, True):
        return
    rows, lines = [], ["👑 <b>Владельцы и модераторы</b>\n"]
    for o in await db.list_owners():
        role = "владелец" if o["role"] == "owner" else "модератор"
        lines.append(f"• {user_link(o['user_id'])} · {role}")
        if o["user_id"] not in config.OWNER_IDS:
            rows.append([(f"❌ Убрать {o['user_id']}", f"a:odel:{o['user_id']}")])
    rows.append([("➕ Добавить", "a:oadd")])
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "a:oadd")
async def owner_add(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.add_owner)
        await show(call, "Пришлите ID или перешлите сообщение человека.", kb([[("⬅️ Отмена", "a:owners")]]))


@router.message(AdminStates.add_owner)
async def owner_add_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    uid = None
    origin = message.forward_origin
    if origin is not None and getattr(origin, "type", "") == "user":
        uid = origin.sender_user.id
    elif (message.text or "").strip().isdigit():
        uid = int(message.text.strip())
    if uid is None:
        await message.answer("Нужен числовой ID или пересланное сообщение.")
        return
    await state.clear()
    await message.answer(f"Какую роль дать {uid}?", reply_markup=kb([
        [("👑 Владелец", f"a:oadr:{uid}:owner"), ("🛡 Модератор", f"a:oadr:{uid}:moderator")],
        [("⬅️ Отмена", "a:owners")]]))


@router.callback_query(F.data.startswith("a:oadr:"))
async def owner_add_role(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        _, _, uid, role = call.data.split(":")
        await db.add_owner(int(uid), role)
        await owners(call, state)


@router.callback_query(F.data.startswith("a:odel:"))
async def owner_del(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        uid = int(call.data.split(":")[2])
        if uid == call.from_user.id:
            await call.answer("Себя убрать нельзя", show_alert=True)
            return
        await db.remove_owner(uid)
        await owners(call, state)


# ---------- рассылка ----------

@router.callback_query(F.data == "a:bc")
async def broadcast(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await _need(call, True):
        await show(call, "📢 <b>Рассылка</b>\nКому отправить?", kb([
            [("👤 Пользователям основного бота", "a:bca:users")],
            [("🪞 Владельцам и админам зеркал", "a:bca:mirrors")],
            [("📣 Всем сразу", "a:bca:all")],
            [("🔁 Сообщить о смене основного бота", "a:newmain")],
            [("⬅️ Назад", "a:menu")],
        ]))


@router.callback_query(F.data.startswith("a:bca:"))
async def broadcast_audience(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.bc_wait)
        await state.update_data(audience=call.data.split(":")[2])
        await show(call, "Пришлите сообщение для рассылки. Пользователям уйдёт как есть, "
                         "зеркалам только текст.", kb([[("⬅️ Отмена", "a:bc")]]))


@router.message(AdminStates.bc_wait)
async def broadcast_message(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    entities = message.entities or message.caption_entities or []
    await state.update_data(
        chat_id=message.chat.id, message_id=message.message_id,
        text=message.text or message.caption or "",
        entities=[e.model_dump(exclude_none=True) for e in entities],
    )
    await state.set_state(AdminStates.bc_confirm)
    await message.answer("Отправить это сообщение?", reply_markup=kb([
        [("✅ Отправить", "a:bcgo")], [("✖️ Отмена", "a:bc")]]))


@router.callback_query(F.data == "a:bcgo", AdminStates.bc_confirm)
async def broadcast_go(call: CallbackQuery, state: FSMContext, bot: Bot, manager) -> None:
    if not await _need(call, True):
        return
    data = await state.get_data()
    await state.clear()
    await call.answer("Отправляю…")
    await show(call, "⏳ Рассылка идёт…")
    ok_u = fail_u = ok_m = fail_m = 0
    audience = data["audience"]
    if audience in ("users", "all"):
        for uid in await db.list_user_ids():
            try:
                await bot.copy_message(uid, data["chat_id"], data["message_id"])
                ok_u += 1
            except Exception:
                fail_u += 1
            await asyncio.sleep(0.05)
    if audience in ("mirrors", "all") and data.get("text"):
        entities = [MessageEntity.model_validate(e) for e in data["entities"]] or None
        ok_m, fail_m = await manager.broadcast_staff(data["text"], entities)
    await call.message.answer(
        f"✅ Готово.\nПользователям: дошло {ok_u}, не дошло {fail_u}\n"
        f"Зеркалам: дошло {ok_m}, не дошло {fail_m}",
        reply_markup=kb([[("⬅️ В админку", "a:menu")]]))


@router.callback_query(F.data == "a:newmain")
async def new_main(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, True):
        await state.set_state(AdminStates.newmain)
        await show(call, "Пришлите юзернейм нового основного бота, например <code>@new_ads_bot</code>. "
                         "Зеркалам уйдёт уведомление.", kb([[("⬅️ Отмена", "a:bc")]]))


@router.message(AdminStates.newmain)
async def new_main_msg(message: Message, state: FSMContext) -> None:
    if not await _need(message, True):
        return
    name = (message.text or "").strip().lstrip("@")
    if not name.lower().endswith("bot") or " " in name:
        await message.answer("Юзернейм бота заканчивается на bot, например @new_ads_bot.")
        return
    text = (f"Основной бот изменён. Новый основной бот: @{name}. Откройте его и нажмите Старт. "
            "Ваше зеркало и посты сохранены.")
    await state.set_state(AdminStates.bc_confirm)
    await state.update_data(audience="mirrors", chat_id=message.chat.id,
                            message_id=message.message_id, text=text, entities=[])
    await message.answer(f"Зеркалам уйдёт:\n\n{esc(text)}", reply_markup=kb([
        [("✅ Отправить", "a:bcgo")], [("✖️ Отмена", "a:bc")]]))


# ---------- бэкап ----------

@router.callback_query(F.data == "a:bk")
async def backup_menu(call: CallbackQuery) -> None:
    if await _need(call, True):
        where = "в канал бэкапов" if config.BACKUP_CHAT_ID else "вам в личку"
        await show(call, f"💾 <b>Бэкап</b>\nФайл базы уйдёт {where}. Токены зеркал в нём "
                         "зашифрованы, ключ шифрования в бэкап не входит.\n"
                         f"Автоматически: раз в {config.BACKUP_EVERY_HOURS} ч.",
                   kb([[("💾 Сделать сейчас", "a:bkgo")], [("⬅️ Назад", "a:menu")]]))


@router.callback_query(F.data == "a:bkgo")
async def backup_go(call: CallbackQuery, bot: Bot) -> None:
    if not await _need(call, True):
        return
    await call.answer("Собираю…")
    try:
        await backup.send_backup(bot, config.BACKUP_CHAT_ID or call.from_user.id)
        await call.message.answer("✅ Бэкап отправлен.")
    except Exception as exc:
        log.exception("Бэкап не удался")
        await call.message.answer(f"⚠️ Бэкап не удался: {esc(exc)}")
