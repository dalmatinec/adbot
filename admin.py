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
import look
import notify
import posts
import report
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
    search = State()
    look_text = State()
    look_btn_text = State()
    look_btn_icon = State()


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
        [("🏪 Кабинеты", "a:cabs:0"), ("🔎 Поиск", "a:find")],
        [("💬 Группы", "a:groups")],
        [("📣 Объявление партнёрам", "a:bc")],
        [("⚙️ Настройки", "a:set")],
    ]))


async def _log(event, text: str) -> None:
    await db.log_action(event.from_user.id, text)


@router.callback_query(F.data == "a:noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "a:set")
async def settings(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    on = await report.enabled()
    await show(call, (
        "⚙️ <b>Настройки</b>\n\n"
        f"⏱ Общий интервал: {fmt_hours(await db.global_interval())}\n"
        f"📊 Отчёт: {'каждый день в ' + str(await report.hour()) + ':00' if on else 'выключен'}\n"
        f"💾 Бэкап: раз в {config.BACKUP_EVERY_HOURS} ч. в хранилище"
    ), kb([
        [("⏱ Интервал", "a:int"), ("📊 Отчёт", "a:rep")],
        [("🎨 Оформление", "a:lk")],
        [("👑 Владельцы", "a:own"), ("📜 Журнал", "a:log")],
        [("💾 Бэкап сейчас", "a:bk")],
        [("⬅️ Назад", "a:menu")],
    ]))


# ---------- поиск ----------

@router.callback_query(F.data == "a:find")
async def find(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(A.search)
    await show(call, (
        "🔎 <b>Поиск</b>\n\n"
        "Пришлите, что искать:\n"
        "• название кабинета или его часть\n"
        "• имя, юзернейм или ID человека\n"
        "• название или номер рассылки"
    ), kb([[("⬅️ Назад", "a:menu")]]))


@router.message(A.search)
async def find_run(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return
    cabs, found = await db.search(query)
    rows = [[(f"{'❄️' if c['status'] != 'active' else '🏪'} {c['name']}", f"a:cab:{c['id']}")] for c in cabs]
    rows += [[(f"{POST_ICON[p['status']]} #{p['id']} {p['title']} · {p['cabinet_name']}"[:60], f"a:p:{p['id']}")]
             for p in found]
    rows.append([("⬅️ В админку", "a:menu")])
    if cabs or found:
        text = (f"🔎 <b>Результаты поиска</b>\nЗапрос: {esc(query)}\n\n"
                f"🏪 Кабинетов: {len(cabs)}\n📄 Рассылок: {len(found)}\n\n"
                "Можно прислать новый запрос.")
    else:
        text = f"🔎 По запросу <b>{esc(query)}</b> ничего не найдено.\n\nПришлите другой запрос."
    await show(message, text, kb(rows))


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
    await _log(message, f"создал кабинет {name}")
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
    await _log(call, f"выдал ссылку {'владельца' if role == 'owner' else 'помощника'}: {cabinet['name']}")
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
    await _log(call, f"{'заморозил' if frozen else 'разморозил'} кабинет {cabinet['name']}")
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
    gone = await db.get_cabinet(cid)
    if gone:
        await _log(call, f"удалил кабинет {gone['name']}")
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
    people = "\n".join(
        f"{'👤 Владелец' if m['role'] == 'owner' else '🤝 Помощник'}: {person(m)}"
        for m in await db.cabinet_members(cid)) or "👤 Владелец: не вошёл"
    await show(call, f"📄 <b>Рассылки кабинета {esc(cabinet['name'])}</b>\n\n{people}\n\n"
                     + (f"Всего рассылок: {len(items)}" if items else "Рассылок нет."), kb(rows))


async def _admin_post_card(target, post) -> None:
    text = await post_text(post)
    text += f"\n\n🏪 Кабинет: {esc(post['cabinet_name'])}"
    author = await db.get_user(post["created_by"])
    if author:
        text += f"\n👤 Создал: {person(author)}"
    if post["last_error"]:
        text += f"\n⚠️ Последняя ошибка: {esc(post['last_error'])}"
    pid = post["id"]
    await show(target, text, kb([
        [("👁 Превью", f"a:pv:{pid}")],
        [("▶️ Возобновить" if post["status"] == "stopped" else "⏹ Остановить", f"a:pst:{pid}")],
        [("🗑 Удалить", f"a:pdel:{pid}")],
        [("⬅️ К рассылкам кабинета", f"a:cps:{post['cabinet_id']}")],
        [("🏪 К кабинету", f"a:cab:{post['cabinet_id']}")],
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
    await _log(call, f"{'остановил' if stop else 'возобновил'} рассылку #{post['id']} ({post['cabinet_name']})")
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
    await _log(call, f"удалил рассылку #{post['id']} ({post['cabinet_name']})")
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
        await _log(call, f"группа {g['title']}: {'рассылка' if field == 'is_active' else 'видимость'} "
                         f"{'выкл' if g[field] else 'вкл'}")
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
    ), kb([hours[:4], hours[4:], [("⬅️ Назад", "a:set")]]))


@router.callback_query(F.data.startswith("a:ints:"))
async def interval_set(call: CallbackQuery) -> None:
    hours = _arg(call)
    if hours in config.INTERVAL_CHOICES:
        await db.set_setting("interval_hours", str(hours))
        await _log(call, f"общий интервал: {fmt_hours(hours)}")
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
    rows.append([("⬅️ Назад", "a:set")])
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
    await _log(message, f"добавил владельца {raw}")
    await message.answer(f"✅ Владелец <code>{raw}</code> добавлен. Ему нужно открыть бота и нажать /start.",
                         reply_markup=kb([[("⬅️ К владельцам", "a:own")]]))


@router.callback_query(F.data.startswith("a:odel:"))
async def owner_del(call: CallbackQuery, state: FSMContext) -> None:
    uid = _arg(call)
    if uid == call.from_user.id or not await db.remove_owner(uid):
        await call.answer("Этого владельца убрать нельзя.", show_alert=True)
        return
    await _log(call, f"убрал владельца {uid}")
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
    try:
        await backup.send_backup(bot, config.STORAGE_CHAT_ID)
    except Exception as exc:
        await call.message.answer(f"⚠️ Бэкап не удался: {esc(exc)}")
        return
    await call.message.answer("✅ Бэкап отправлен в хранилище.")


# ---------- журнал действий ----------

@router.callback_query(F.data == "a:log")
async def journal(call: CallbackQuery) -> None:
    items = await db.last_actions(20)
    lines = [f"<code>{fmt_dt(x['at'])[:5]} {fmt_dt(x['at'])[11:]}</code> "
             f"{esc(x['full_name'] or x['user_id'])}: {esc(x['text'])}" for x in items]
    await show(call, "📜 <b>Журнал действий</b>\nПоследние 20 записей\n\n"
                     + ("\n".join(lines) if lines else "Записей пока нет."),
               kb([[("⬅️ Назад", "a:set")]]))


# ---------- отчёт ----------

REPORT_HOURS = (7, 8, 9, 10, 12, 18, 21, 23)


@router.callback_query(F.data == "a:rep")
async def report_menu(call: CallbackQuery) -> None:
    on = await report.enabled()
    current = await report.hour()
    hours = [(("✅ " if h == current else "") + f"{h}:00", f"a:reph:{h}") for h in REPORT_HOURS]
    await show(call, (
        "📊 <b>Ежедневный отчёт</b>\n\n"
        f"Состояние: {'включён' if on else 'выключен'}\n"
        f"Время отправки: {current}:00 ({config.DISPLAY_TZ_LABEL})\n\n"
        "Отчёт получают все владельцы бота. Текст отчёта меняется в разделе Оформление."
    ), kb([
        hours[:4], hours[4:],
        [("⚪️ Выключить" if on else "🟢 Включить", "a:repon")],
        [("📨 Прислать сейчас", "a:repnow")],
        [("⬅️ Назад", "a:set")],
    ]))


@router.callback_query(F.data.startswith("a:reph:"))
async def report_hour(call: CallbackQuery) -> None:
    hour = _arg(call)
    if 0 <= hour <= 23:
        await db.set_setting("report_hour", str(hour))
    await report_menu(call)


@router.callback_query(F.data == "a:repon")
async def report_toggle(call: CallbackQuery) -> None:
    await db.set_setting("report_on", "0" if await report.enabled() else "1")
    await report_menu(call)


@router.callback_query(F.data == "a:repnow")
async def report_now(call: CallbackQuery) -> None:
    await call.answer()
    # пробный показ: счётчики не обнуляются
    await show(call.message, await report.build(), kb([[("⬅️ К отчёту", "a:rep")]]))


# ---------- оформление ----------

@router.callback_query(F.data == "a:lk")
async def look_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, (
        "🎨 <b>Оформление</b>\n\n"
        "Здесь меняется то, что видят партнёры: тексты экранов, подписи, цвета и значки кнопок. "
        "Здесь же шаблон ежедневного отчёта.\n\n"
        "Премиум-эмодзи в текстах и на кнопках работают, если у аккаунта, на котором создан бот, "
        "есть Telegram Premium."
    ), kb([[("📝 Тексты", "a:lt"), ("🔘 Кнопки", "a:lb")], [("⬅️ Назад", "a:set")]]))


@router.callback_query(F.data == "a:lt")
async def look_texts(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows = [[(("✏️ " if look.is_custom_text(key) else "") + title, f"a:lt:{key}")]
            for key, (title, _) in look.TEXTS.items()]
    rows.append([("⬅️ Назад", "a:lk")])
    await show(call, "📝 <b>Тексты</b>\n\nВыберите экран. Значком ✏️ отмечены изменённые.", kb(rows))


def _key(call: CallbackQuery) -> str:
    return call.data.split(":")[2]


async def _text_card(target, key: str) -> None:
    title = look.TEXTS[key][0]
    marks = ", ".join("{" + p + "}" for p in look.placeholders(key)) or "нет"
    await show(target, (
        f"📝 <b>{esc(title)}</b>\n"
        f"{'Изменён' if look.is_custom_text(key) else 'Стандартный'}\n"
        f"Подстановки: <code>{marks}</code>\n\n"
        "Так он выглядит сейчас (с примерными данными):\n"
        "➖➖➖➖➖➖\n" + look.sample(key)
    ), kb([
        [("✏️ Изменить", f"a:lte:{key}")],
        [("↩️ Вернуть стандартный", f"a:ltr:{key}")],
        [("⬅️ К текстам", "a:lt")],
    ]))


@router.callback_query(F.data.startswith("a:lt:"))
async def look_text_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if _key(call) in look.TEXTS:
        await _text_card(call, _key(call))


@router.callback_query(F.data.startswith("a:lte:"))
async def look_text_edit(call: CallbackQuery, state: FSMContext) -> None:
    key = _key(call)
    if key not in look.TEXTS:
        return
    await state.set_state(A.look_text)
    await state.set_data({"key": key})
    marks = "\n".join("<code>{" + p + "}</code>" for p in look.placeholders(key)) or "нет"
    await show(call, (
        f"✏️ <b>{esc(look.TEXTS[key][0])}</b>\n\n"
        "Пришлите новый текст одним сообщением. Жирный, курсив, ссылки и премиум-эмодзи сохранятся.\n\n"
        f"Подстановки, которые бот заменит на данные:\n{marks}\n\n"
        "Нажмите на подстановку, чтобы скопировать её."
    ), kb([[("⬅️ Назад", f"a:lt:{key}")]]))


@router.message(A.look_text)
async def look_text_save(message: Message, state: FSMContext) -> None:
    if not message.text or message.text.startswith("/"):
        await message.answer("Нужен текст. Пришлите ещё раз.")
        return
    key = (await state.get_data())["key"]
    await state.clear()
    await look.set_text(key, message.html_text)
    await _log(message, f"изменил текст: {look.TEXTS[key][0]}")
    await _text_card(message, key)


@router.callback_query(F.data.startswith("a:ltr:"))
async def look_text_reset(call: CallbackQuery) -> None:
    key = _key(call)
    if key in look.TEXTS:
        await look.set_text(key, None)
        await _text_card(call, key)


@router.callback_query(F.data == "a:lb")
async def look_buttons(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows, row = [], []
    for key in look.BUTTONS:
        row.append((look.button(key)["text"], f"a:lb:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Назад", "a:lk")])
    await show(call, "🔘 <b>Кнопки</b>\n\nВыберите кнопку, чтобы изменить подпись, цвет или значок.", kb(rows))


async def _button_card(target, key: str) -> None:
    view = look.button(key)
    styles = [(("✅ " if view["style"] == code else "") + name.capitalize(), f"a:lbs:{key}:{code or 'none'}")
              for code, name in look.STYLES.items()]
    await show(target, (
        "🔘 <b>Кнопка</b>\n\n"
        f"Подпись: {esc(view['text'])}\n"
        f"Цвет: {look.STYLES[view['style']]}\n"
        f"Значок: {'премиум-эмодзи' if view['icon'] else 'нет'}\n\n"
        "Верхняя кнопка показывает, как она выглядит у партнёров."
    ), kb([
        [look.btn(key, "a:noop")],
        [("✏️ Подпись", f"a:lbe:{key}")],
        styles[:2], styles[2:],
        [("😀 Значок", f"a:lbi:{key}")] + ([("❌ Убрать значок", f"a:lbx:{key}")] if view["icon"] else []),
        [("↩️ Вернуть стандартную", f"a:lbr:{key}")],
        [("⬅️ К кнопкам", "a:lb")],
    ]))


@router.callback_query(F.data.startswith("a:lb:"))
async def look_button_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if _key(call) in look.BUTTONS:
        await _button_card(call, _key(call))


@router.callback_query(F.data.startswith("a:lbs:"))
async def look_button_style(call: CallbackQuery) -> None:
    key, style = _key(call), call.data.split(":")[3]
    if key in look.BUTTONS and (style == "none" or style in look.STYLES):
        await look.set_button(key, style="" if style == "none" else style)
        await _button_card(call, key)


@router.callback_query(F.data.startswith("a:lbe:") | F.data.startswith("a:lbi:"))
async def look_button_edit(call: CallbackQuery, state: FSMContext) -> None:
    key = _key(call)
    if key not in look.BUTTONS:
        return
    icon = call.data.startswith("a:lbi:")
    await state.set_state(A.look_btn_icon if icon else A.look_btn_text)
    await state.set_data({"key": key})
    await show(call, (
        "😀 Пришлите один премиум-эмодзи. Он встанет значком перед подписью кнопки.\n\n"
        "Обычный эмодзи значком не станет: его можно просто вписать в подпись."
        if icon else
        "✏️ Пришлите новую подпись кнопки, до 30 символов. Обычные эмодзи можно писать прямо в подписи."
    ), kb([[("⬅️ Назад", f"a:lb:{key}")]]))


@router.message(A.look_btn_text)
async def look_button_text_save(message: Message, state: FSMContext) -> None:
    label = " ".join((message.text or "").split())
    if not label or label.startswith("/") or len(label) > 30:
        await message.answer("Подпись должна быть текстом до 30 символов. Пришлите ещё раз.")
        return
    key = (await state.get_data())["key"]
    await state.clear()
    await look.set_button(key, text=label)
    await _log(message, f"изменил кнопку: {label}")
    await _button_card(message, key)


@router.message(A.look_btn_icon)
async def look_button_icon_save(message: Message, state: FSMContext) -> None:
    emoji_id = next((e.custom_emoji_id for e in (message.entities or []) if e.type == "custom_emoji"), None)
    if not emoji_id:
        await message.answer("В сообщении нет премиум-эмодзи. Пришлите один премиум-эмодзи.")
        return
    key = (await state.get_data())["key"]
    await state.clear()
    await look.set_button(key, icon=emoji_id)
    await _button_card(message, key)


@router.callback_query(F.data.startswith("a:lbx:") | F.data.startswith("a:lbr:"))
async def look_button_reset(call: CallbackQuery) -> None:
    key = _key(call)
    if key not in look.BUTTONS:
        return
    if call.data.startswith("a:lbx:"):
        await look.set_button(key, icon="")
    else:
        await look.reset_button(key)
    await _button_card(call, key)
