"""Админка владельцев бота: кабинеты и ссылки входа, группы, интервал,
управление любыми рассылками, владельцы, объявления, бэкап."""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import backup
import config
import db
import notify
import posts
from cabinet import post_text
from ui import POST_ICON, esc, fmt_dt, fmt_hours, kb, person, show

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private", lambda m: db.is_owner(m.from_user.id))
router.callback_query.filter(lambda c: db.is_owner(c.from_user.id))
# события о добавлении бота в чаты идут отдельно, без фильтра по личке
events = Router()

PAGE = 8


class A(StatesGroup):
    cabinet_name = State()
    rename = State()
    owner_id = State()
    announce = State()


# ---------- меню ----------

async def menu(target) -> None:
    s = await db.summary()
    await show(target, (
        "⚙️ <b>Админка</b>\n\n"
        f"🏪 Кабинетов: {s['cabinets']}" + (f" (заморожено {s['frozen']})" if s["frozen"] else "") + "\n"
        f"📄 Рассылок: {s['posts']}, из них идёт {s['active']}\n"
        f"💬 Групп включено: {s['groups']}\n"
        f"⏱ Общий интервал: {fmt_hours(await db.global_interval())}\n"
        f"📊 Всего отправок: {s['sent']}"
    ), kb([
        [("🏪 Кабинеты", "a:cabs:0"), ("📄 Рассылки", "a:posts:0")],
        [("💬 Группы", "a:groups"), ("⏱ Интервал", "a:int")],
        [("👑 Владельцы", "a:own"), ("📣 Объявление", "a:bc")],
        [("💾 Бэкап", "a:bk")],
    ]))


@router.message(Command("adm"))
async def adm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await menu(message)


@router.callback_query(F.data == "a:menu")
async def menu_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await menu(call)


def _arg(call: CallbackQuery, index: int = 2) -> int:
    return int(call.data.split(":")[index])


# ---------- кабинеты ----------

@router.callback_query(F.data.startswith("a:cabs:"))
async def cabinets(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _cabinets_list(call, _arg(call))


async def _cabinets_list(call: CallbackQuery, page: int) -> None:
    total = await db.count_cabinets()
    items = await db.list_cabinets(page * PAGE, PAGE)
    rows = [[(f"{'❄️' if c['status'] != 'active' else '🏪'} {c['name']} · {c['posts']}/{c['slots']}",
              f"a:cab:{c['id']}")] for c in items]
    nav = []
    if page > 0:
        nav.append(("◀️", f"a:cabs:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(("▶️", f"a:cabs:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("➕ Создать кабинет", "a:cnew")])
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, f"🏪 <b>Кабинеты</b>\n\nВсего: {total}\nВ списке: название и занятые места.", kb(rows))


@router.callback_query(F.data == "a:cnew")
async def cabinet_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(A.cabinet_name)
    await show(call, (
        "➕ <b>Новый кабинет</b>\n\n"
        "Пришлите название магазина или организации.\n"
        "После этого бот выдаст одноразовую ссылку для входа."
    ), kb([[("⬅️ Назад", "a:cabs:0")]]))


@router.message(A.cabinet_name)
async def cabinet_new_name(message: Message, state: FSMContext, bot: Bot) -> None:
    name = (message.text or "").strip()
    if not name or name.startswith("/") or len(name) > 60:
        await message.answer("Название должно быть текстом до 60 символов. Пришлите ещё раз.")
        return
    await state.clear()
    cid = await db.create_cabinet(name)
    token = await db.create_invite(cid, "owner")
    await message.answer(await _link_text(bot, name, token, "owner"))
    await _cabinet_card(message, cid)


async def _link(bot: Bot, token: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start={token}"


async def _link_text(bot: Bot, name: str, token: str, role: str) -> str:
    who = "владельца кабинета" if role == "owner" else "помощника"
    return (
        f"🔗 <b>Ссылка для {who}</b>\n"
        f"Кабинет: {esc(name)}\n\n"
        f"<code>{await _link(bot, token)}</code>\n\n"
        f"Ссылка одноразовая и действует {config.INVITE_TTL_HOURS} ч. "
        "Перешлите её нужному человеку. После входа она перестаёт работать."
    )


async def _cabinet_card(target, cabinet_id: int) -> None:
    c = await db.get_cabinet(cabinet_id)
    if not c:
        await show(target, "Кабинет не найден.", kb([[("⬅️ Назад", "a:cabs:0")]]))
        return
    members = await db.cabinet_members(cabinet_id)
    owner = next((m for m in members if m["role"] == "owner"), None)
    helper = next((m for m in members if m["role"] == "helper"), None)
    invites = {i["role"] for i in await db.get_invites(cabinet_id)}
    used = await db.count_posts(cabinet_id)
    text = (
        f"🏪 <b>{esc(c['name'])}</b>\n"
        f"Кабинет #{c['id']}\n\n"
        f"{'🟢 Активен' if c['status'] == 'active' else '❄️ Заморожен'}\n"
        f"👤 Владелец: {person(owner) if owner else 'не вошёл'}"
        + (" (выдана новая ссылка)" if "owner" in invites else "") + "\n"
        f"🤝 Помощник: {person(helper) if helper else 'нет'}"
        + (" (ссылка выдана)" if "helper" in invites else "") + "\n\n"
        f"📦 Рассылок: {used} из {c['slots']}\n"
        f"💬 Группы: {'все, включая скрытые' if c['all_groups'] else 'только открытые'}\n"
        f"📅 Создан: {fmt_dt(c['created_at'])}"
    )
    rows = [
        [("📄 Рассылки кабинета", f"a:cps:{c['id']}")],
        [("🔁 Сменить владельца" if owner else "🔗 Ссылка владельцу", f"a:clo:{c['id']}")],
        [("🤝 Ссылка помощнику", f"a:clh:{c['id']}")],
    ] + ([[("❌ Убрать помощника", f"a:crh:{c['id']}")]] if helper else []) + [
        [("➖ Место", f"a:csl:{c['id']}:-1"), ("➕ Место", f"a:csl:{c['id']}:1")],
        [("💬 Группы: " + ("все" if c["all_groups"] else "открытые"), f"a:cag:{c['id']}")],
        [("▶️ Разморозить" if c["status"] != "active" else "❄️ Заморозить", f"a:cfz:{c['id']}")],
        [("✏️ Переименовать", f"a:crn:{c['id']}")],
        [("🗑 Удалить кабинет", f"a:cdel:{c['id']}")],
        [("⬅️ К списку", "a:cabs:0")],
    ]
    await show(target, text, kb(rows))


@router.callback_query(F.data.startswith("a:cab:"))
async def cabinet_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _cabinet_card(call, _arg(call))


@router.callback_query(F.data.startswith("a:clo:") | F.data.startswith("a:clh:"))
async def cabinet_link(call: CallbackQuery, bot: Bot) -> None:
    cid = _arg(call)
    cabinet = await db.get_cabinet(cid)
    if not cabinet:
        return
    role = "owner" if call.data.startswith("a:clo:") else "helper"
    token = await db.create_invite(cid, role)
    await call.answer()
    text = await _link_text(bot, cabinet["name"], token, role)
    if role == "owner":
        text += ("\n\nПока новый человек не вошёл, прежний владелец сохраняет доступ. "
                 "После входа управление и все рассылки перейдут новому, прежний доступ отключится.")
    await call.message.answer(text, reply_markup=kb([[("⬅️ К кабинету", f"a:cab:{cid}")]]))


@router.callback_query(F.data.startswith("a:crh:"))
async def cabinet_remove_helper(call: CallbackQuery, bot: Bot) -> None:
    cid = _arg(call)
    for m in await db.cabinet_members(cid):
        if m["role"] == "helper":
            await db.remove_member(m["user_id"])
            try:
                await bot.send_message(m["user_id"], "Ваш доступ к кабинету прекращён администрацией.")
            except Exception:
                pass
    await _cabinet_card(call, cid)


@router.callback_query(F.data.startswith("a:csl:"))
async def cabinet_slots(call: CallbackQuery) -> None:
    cid = _arg(call)
    cabinet = await db.get_cabinet(cid)
    if cabinet:
        await db.set_cabinet_slots(cid, cabinet["slots"] + _arg(call, 3))
    await _cabinet_card(call, cid)


@router.callback_query(F.data.startswith("a:cag:"))
async def cabinet_all_groups(call: CallbackQuery) -> None:
    await db.toggle_cabinet_all_groups(_arg(call))
    await _cabinet_card(call, _arg(call))


@router.callback_query(F.data.startswith("a:cfz:"))
async def cabinet_freeze(call: CallbackQuery, bot: Bot) -> None:
    cid = _arg(call)
    cabinet = await db.get_cabinet(cid)
    if not cabinet:
        return
    frozen = cabinet["status"] == "active"
    await db.set_cabinet_status(cid, "frozen" if frozen else "active")
    await notify.cabinet(bot, cid, (
        "❄️ Кабинет заморожен администрацией. Рассылки приостановлены." if frozen
        else "✅ Кабинет разморожен. Рассылки продолжаются."))
    await _cabinet_card(call, cid)


@router.callback_query(F.data.startswith("a:crn:"))
async def cabinet_rename(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(A.rename)
    await state.set_data({"cid": _arg(call)})
    await show(call, "✏️ Пришлите новое название кабинета.", kb([[("⬅️ Назад", f"a:cab:{_arg(call)}")]]))


@router.message(A.rename)
async def cabinet_rename_done(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or name.startswith("/") or len(name) > 60:
        await message.answer("Название должно быть текстом до 60 символов. Пришлите ещё раз.")
        return
    cid = (await state.get_data())["cid"]
    await state.clear()
    await db.rename_cabinet(cid, name)
    await _cabinet_card(message, cid)


@router.callback_query(F.data.startswith("a:cdel:"))
async def cabinet_delete_ask(call: CallbackQuery) -> None:
    cid = _arg(call)
    cabinet = await db.get_cabinet(cid)
    if not cabinet:
        return
    await show(call, (
        f"🗑 <b>Удалить кабинет {esc(cabinet['name'])}?</b>\n\n"
        "Будут удалены все его рассылки и доступы. Отменить удаление нельзя.\n"
        "Если нужно только сменить человека, используйте Сменить владельца."
    ), kb([[("🗑 Да, удалить", f"a:cdelc:{cid}")], [("⬅️ Отмена", f"a:cab:{cid}")]]))


@router.callback_query(F.data.startswith("a:cdelc:"))
async def cabinet_delete(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    cid = _arg(call)
    await notify.cabinet(bot, cid, "Ваш кабинет удалён администрацией.")
    await db.delete_cabinet(cid)
    await state.clear()
    await _cabinets_list(call, 0)


# ---------- рассылки ----------

@router.callback_query(F.data.startswith("a:cps:"))
async def cabinet_posts(call: CallbackQuery) -> None:
    await _cabinet_posts(call, _arg(call))


async def _cabinet_posts(call: CallbackQuery, cid: int) -> None:
    cabinet = await db.get_cabinet(cid)
    if not cabinet:
        return
    items = await db.list_posts(cid)
    rows = [[(f"{POST_ICON[p['status']]} #{p['id']} {p['title']}", f"a:p:{p['id']}")] for p in items]
    rows.append([("⬅️ К кабинету", f"a:cab:{cid}")])
    await show(call, f"📄 <b>Рассылки кабинета {esc(cabinet['name'])}</b>\n\n"
                     + (f"Всего: {len(items)}" if items else "Рассылок нет."), kb(rows))


@router.callback_query(F.data.startswith("a:posts:"))
async def all_posts(call: CallbackQuery) -> None:
    page = _arg(call)
    total = await db.count_all_posts()
    items = await db.list_all_posts(page * PAGE, PAGE)
    rows = [[(f"{POST_ICON[p['status']]} #{p['id']} {p['cabinet_name']}: {p['title']}"[:60], f"a:p:{p['id']}")]
            for p in items]
    nav = []
    if page > 0:
        nav.append(("◀️", f"a:posts:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(("▶️", f"a:posts:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, f"📄 <b>Все рассылки</b>\n\nВсего: {total}", kb(rows))


async def _admin_post_card(target, post) -> None:
    text = await post_text(post)
    text += f"\n\n🏪 Кабинет: {esc(post['cabinet_name'])}"
    if post["last_error"]:
        text += f"\n⚠️ Последняя ошибка: {esc(post['last_error'])}"
    pid = post["id"]
    await show(target, text, kb([
        [("👁 Превью", f"a:pv:{pid}")],
        [("▶️ Возобновить" if post["status"] == "stopped" else "⏹ Остановить", f"a:pst:{pid}")],
        [("🗑 Удалить", f"a:pdel:{pid}")],
        [("🏪 К кабинету", f"a:cab:{post['cabinet_id']}")],
        [("⬅️ Все рассылки", "a:posts:0")],
    ]))


async def _post(call: CallbackQuery):
    post = await db.get_post(_arg(call))
    if not post:
        await call.answer("Рассылка не найдена.", show_alert=True)
    return post


@router.callback_query(F.data.startswith("a:p:"))
async def post_open(call: CallbackQuery) -> None:
    post = await _post(call)
    if post:
        await _admin_post_card(call, post)


@router.callback_query(F.data.startswith("a:pv:"))
async def post_preview(call: CallbackQuery, bot: Bot) -> None:
    post = await _post(call)
    if not post:
        return
    await call.answer()
    try:
        await posts.send_post(bot, call.message.chat.id, post)
    except Exception as exc:
        await call.message.answer(f"⚠️ Не удалось показать пост: {esc(exc)}")
        return
    await call.message.answer("👆 Так пост выглядит в группе.",
                              reply_markup=kb([[("⬅️ К рассылке", f"a:p:{post['id']}")]]))


@router.callback_query(F.data.startswith("a:pst:"))
async def post_stop(call: CallbackQuery, bot: Bot) -> None:
    post = await _post(call)
    if not post:
        return
    stop = post["status"] != "stopped"
    await db.set_post_status(post["id"], "stopped" if stop else "active")
    await notify.cabinet(bot, post["cabinet_id"], (
        f"⏹ Рассылка #{post['id']} остановлена администрацией. Свяжитесь с администрацией для уточнения."
        if stop else f"▶️ Рассылка #{post['id']} возобновлена администрацией."))
    await _admin_post_card(call, await db.get_post(post["id"]))


@router.callback_query(F.data.startswith("a:pdel:"))
async def post_delete_ask(call: CallbackQuery) -> None:
    post = await _post(call)
    if not post:
        return
    await show(call, (
        f"🗑 <b>Удалить рассылку #{post['id']}?</b>\n"
        f"Кабинет: {esc(post['cabinet_name'])}\n{esc(post['title'])}\n\n"
        "Партнёр получит уведомление. Отменить удаление нельзя."
    ), kb([[("🗑 Да, удалить", f"a:pdelc:{post['id']}")], [("⬅️ Отмена", f"a:p:{post['id']}")]]))


@router.callback_query(F.data.startswith("a:pdelc:"))
async def post_delete(call: CallbackQuery, bot: Bot) -> None:
    post = await _post(call)
    if not post:
        return
    await db.delete_post(post["id"])
    await posts.drop_stored(bot, post["storage_chat_id"], posts.ids_of(post))
    await notify.cabinet(bot, post["cabinet_id"], f"🗑 Рассылка #{post['id']} удалена администрацией.")
    await _cabinet_posts(call, post["cabinet_id"])


# ---------- группы ----------

@router.callback_query(F.data == "a:groups")
async def groups(call: CallbackQuery) -> None:
    items = await db.list_groups()
    rows = [[(f"{'🟢' if g['is_active'] else '⚪️'}{'' if g['is_public'] else '🔒'} "
              f"{g['title'] or g['chat_id']} · {g['posts']}", f"a:g:{g['id']}")] for g in items]
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, (
        "💬 <b>Группы</b>\n\n"
        "🟢 рассылка включена, ⚪️ выключена, 🔒 скрыта от партнёров.\n"
        "Число в конце: сколько рассылок идёт в группу.\n\n"
        "Чтобы добавить группу, сделайте бота её администратором. Она появится здесь сама."
        if items else
        "💬 <b>Группы</b>\n\nГрупп пока нет.\n"
        "Сделайте бота администратором группы, и она появится здесь."
    ), kb(rows))


async def _group_card(target, group_id: int) -> None:
    g = await db.get_group(group_id)
    if not g:
        await show(target, "Группа не найдена.", kb([[("⬅️ Назад", "a:groups")]]))
        return
    interval = (fmt_hours(g["interval_hours"]) if g["interval_hours"]
                else f"общий ({fmt_hours(await db.global_interval())})")
    await show(target, (
        f"💬 <b>{esc(g['title'])}</b>\n"
        f"ID <code>{g['chat_id']}</code>\n\n"
        f"{'🟢 Рассылка включена' if g['is_active'] else '⚪️ Рассылка выключена'}\n"
        f"{'👁 Видна партнёрам' if g['is_public'] else '🔒 Скрыта от партнёров'}\n"
        f"⏱ Интервал: {interval}"
    ), kb([
        [("⚪️ Выключить" if g["is_active"] else "🟢 Включить", f"a:gact:{g['id']}")],
        [("🔒 Скрыть от партнёров" if g["is_public"] else "👁 Открыть партнёрам", f"a:gpub:{g['id']}")],
        [("⏱ Интервал группы", f"a:gint:{g['id']}")],
        [("🗑 Убрать группу", f"a:gdel:{g['id']}")],
        [("⬅️ К списку", "a:groups")],
    ]))


@router.callback_query(F.data.startswith("a:g:"))
async def group_open(call: CallbackQuery) -> None:
    await _group_card(call, _arg(call))


@router.callback_query(F.data.startswith("a:gact:") | F.data.startswith("a:gpub:"))
async def group_toggle(call: CallbackQuery) -> None:
    g = await db.get_group(_arg(call))
    if g:
        field = "is_active" if call.data.startswith("a:gact:") else "is_public"
        await db.set_group_flag(g["id"], field, not g[field])
    await _group_card(call, _arg(call))


@router.callback_query(F.data.startswith("a:gint:"))
async def group_interval(call: CallbackQuery) -> None:
    gid = _arg(call)
    hours = [(fmt_hours(h), f"a:gis:{gid}:{h}") for h in config.INTERVAL_CHOICES]
    await show(call, "⏱ <b>Интервал группы</b>\n\nВыберите интервал для этой группы или оставьте общий.",
               kb([hours[:4], hours[4:], [("Общий интервал", f"a:gis:{gid}:0")], [("⬅️ Назад", f"a:g:{gid}")]]))


@router.callback_query(F.data.startswith("a:gis:"))
async def group_interval_set(call: CallbackQuery) -> None:
    await db.set_group_interval(_arg(call), _arg(call, 3) or None)
    await _group_card(call, _arg(call))


@router.callback_query(F.data.startswith("a:gdel:"))
async def group_delete_ask(call: CallbackQuery) -> None:
    g = await db.get_group(_arg(call))
    if not g:
        return
    await show(call, (
        f"🗑 <b>Убрать группу {esc(g['title'])}?</b>\n\n"
        "Все рассылки в эту группу будут удалены. Чтобы только приостановить, выключите группу."
    ), kb([[("🗑 Да, убрать", f"a:gdelc:{g['id']}")], [("⬅️ Отмена", f"a:g:{g['id']}")]]))


@router.callback_query(F.data.startswith("a:gdelc:"))
async def group_delete(call: CallbackQuery) -> None:
    await db.delete_group(_arg(call))
    await groups(call)


@events.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_my_member(event: ChatMemberUpdated, bot: Bot) -> None:
    status = event.new_chat_member.status
    chat = event.chat
    if status in ("administrator", "member"):
        if not db.is_owner(event.from_user.id) and not await db.get_group_by_chat(chat.id):
            # бота добавил посторонний: в чужих чатах он не работает
            try:
                await bot.leave_chat(chat.id)
            except Exception:
                pass
            return
        known = await db.get_group_by_chat(chat.id)
        await db.add_group(chat.id, chat.title)
        if known is None:
            warn = "" if status == "administrator" else "\n⚠️ Бот не администратор: писать в группу он не сможет."
            await notify.owners(bot, f"➕ Бот добавлен в группу <b>{esc(chat.title)}</b>.{warn}\n\n"
                                     "Включите её: Админка, раздел Группы.")
    elif status in ("left", "kicked"):
        group = await db.get_group_by_chat(chat.id)
        if group and group["is_active"]:
            await db.set_group_flag(group["id"], "is_active", False)
            await notify.owners(bot, f"➖ Бота убрали из группы <b>{esc(chat.title)}</b>. Рассылка в неё выключена.")


# ---------- интервал ----------

@router.callback_query(F.data == "a:int")
async def interval(call: CallbackQuery) -> None:
    current = await db.global_interval()
    hours = [(("✅ " if h == current else "") + fmt_hours(h), f"a:ints:{h}") for h in config.INTERVAL_CHOICES]
    await show(call, (
        "⏱ <b>Общий интервал рассылки</b>\n\n"
        f"Сейчас: {fmt_hours(current)}\n\n"
        "С этим интервалом пересылается каждый пост. Партнёры изменить его не могут.\n"
        "Новый интервал применяется после очередной отправки поста."
    ), kb([hours[:4], hours[4:], [("⬅️ Назад", "a:menu")]]))


@router.callback_query(F.data.startswith("a:ints:"))
async def interval_set(call: CallbackQuery) -> None:
    hours = _arg(call)
    if hours in config.INTERVAL_CHOICES:
        await db.set_setting("interval_hours", str(hours))
    await interval(call)


# ---------- владельцы ----------

@router.callback_query(F.data == "a:own")
async def owners(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    items = await db.list_owners()
    lines = [f"👑 {person(o)}" for o in items]
    rows = [[(f"❌ Убрать {o['full_name'] or o['user_id']}", f"a:odel:{o['user_id']}")]
            for o in items if o["user_id"] != call.from_user.id]
    rows.append([("➕ Добавить владельца", "a:oadd")])
    rows.append([("⬅️ Назад", "a:menu")])
    await show(call, "👑 <b>Владельцы бота</b>\n\n" + "\n".join(lines)
                     + "\n\nВладельцы имеют полный доступ к админке.", kb(rows))


@router.callback_query(F.data == "a:oadd")
async def owner_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(A.owner_id)
    await show(call, "➕ Пришлите Telegram ID нового владельца числом.\n"
                     "Узнать ID можно у @userinfobot.", kb([[("⬅️ Назад", "a:own")]]))


@router.message(A.owner_id)
async def owner_add_done(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой ID. Пришлите ещё раз.")
        return
    await state.clear()
    await db.add_owner(int(raw))
    await message.answer(f"✅ Владелец <code>{raw}</code> добавлен. Ему нужно открыть бота и нажать /start.",
                         reply_markup=kb([[("⬅️ К владельцам", "a:own")]]))


@router.callback_query(F.data.startswith("a:odel:"))
async def owner_del(call: CallbackQuery, state: FSMContext) -> None:
    uid = _arg(call)
    if uid == call.from_user.id or not await db.remove_owner(uid):
        await call.answer("Этого владельца убрать нельзя.", show_alert=True)
        return
    await owners(call, state)


# ---------- объявление и бэкап ----------

@router.callback_query(F.data == "a:bc")
async def announce(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(A.announce)
    await show(call, (
        "📣 <b>Объявление партнёрам</b>\n\n"
        "Пришлите сообщение. Его получат все владельцы кабинетов и помощники."
    ), kb([[("⬅️ Назад", "a:menu")]]))


@router.message(A.announce)
async def announce_send(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        return
    await state.clear()
    ok = 0
    ids = await db.all_member_ids()
    for uid in ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Объявление доставлено: {ok} из {len(ids)}.",
                         reply_markup=kb([[("⬅️ В админку", "a:menu")]]))


@router.callback_query(F.data == "a:bk")
async def backup_now(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("Готовлю бэкап.")
    target = config.BACKUP_CHAT_ID or call.from_user.id
    try:
        await backup.send_backup(bot, target)
    except Exception as exc:
        await call.message.answer(f"⚠️ Бэкап не удался: {esc(exc)}")
        return
    await call.message.answer("✅ Бэкап отправлен " + ("в канал бэкапов." if config.BACKUP_CHAT_ID else "вам в личку."))
