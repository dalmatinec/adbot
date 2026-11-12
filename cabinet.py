"""Кабинет партнёра: вход по одноразовой ссылке, рассылки, превью, замена
поста, пауза, удаление. Интервал и группы задают владельцы бота, партнёр их
только видит.
"""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import db
import notify
import posts
from ui import POST_ICON, POST_TEXT, esc, fmt_dt, fmt_hours, kb, person, show, times

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

ALBUM_WAIT_SECONDS = 1.5
_albums: dict[tuple[int, str], list[int]] = {}


class S(StatesGroup):
    post = State()      # ждём пост (новый или на замену)
    group = State()     # ждём выбор группы
    name = State()      # ждём новое название существующей рассылки
    new_name = State()  # шаг 1: название новой рассылки
    confirm = State()   # шаг 3: подтверждение запуска


async def _cabinet(user_id: int):
    cid = db.cabinet_of(user_id)
    return await db.get_cabinet(cid) if cid else None


def _frozen_text(cabinet) -> str:
    return (f"🏪 <b>{esc(cabinet['name'])}</b>\n\n"
            "❄️ Кабинет заморожен администрацией. Рассылки приостановлены.\n"
            "Для уточнения обратитесь к администрации.")


# ---------- главный экран ----------

async def home(target: Message | CallbackQuery) -> None:
    uid = target.from_user.id
    cabinet = await _cabinet(uid)
    if not cabinet:
        if db.is_owner(uid):
            import admin
            await admin.menu(target)
        return
    rows = []
    if cabinet["status"] != "active":
        text = _frozen_text(cabinet)
        rows.append([("📋 Мои рассылки", "c:posts")])
    else:
        items = await db.list_posts(cabinet["id"])
        live = sum(1 for p in items if p["status"] == "active")
        text = (
            f"🏪 <b>{esc(cabinet['name'])}</b>\n\n"
            f"📦 Рассылок: {len(items)} из {cabinet['slots']}\n"
            f"🟢 Идёт сейчас: {live}\n"
            f"⏱ Интервал: {fmt_hours(await db.global_interval())}\n\n"
            "Выберите действие."
        )
        rows.append([("➕ Новая рассылка", "c:new")])
        rows.append([("📋 Мои рассылки", "c:posts")])
        rows.append([("👥 Доступ", "c:people")])
        rows.append([("ℹ️ Как это работает", "c:help")])
    if db.is_owner(uid):
        rows.append([("⚙️ Админка", "a:menu")])
    await show(target, text, kb(rows))


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject, bot: Bot) -> None:
    await state.clear()
    if command.args:
        await _enter(message, bot, command.args.strip())
        return
    await home(message)


async def _enter(message: Message, bot: Bot, token: str) -> None:
    uid = message.from_user.id
    status, cabinet, prev = await db.use_invite(token, uid)
    if status == "bad":
        if db.is_known(uid):
            await message.answer("Ссылка недействительна или уже использована.")
            await home(message)
        return  # постороннему с неверной ссылкой бот не отвечает
    if status == "busy":
        await message.answer(
            "Вы уже привязаны к другому кабинету. Один человек работает только с одним кабинетом. "
            "Обратитесь к администрации.")
        return
    await db.touch_user(uid, message.from_user.full_name, message.from_user.username)
    if prev:
        try:
            await bot.send_message(
                prev, f"Ваш доступ к кабинету <b>{esc(cabinet['name'])}</b> прекращён: "
                      "управление передано другому человеку.")
        except Exception:
            pass
    user = await db.get_user(uid)
    await notify.owners(
        bot, f"✅ Вход в кабинет <b>{esc(cabinet['name'])}</b>\n\n👤 {person(user)}"
             + ("\n\nПрежний доступ отключён." if prev else ""))
    await message.answer(f"✅ Доступ к кабинету <b>{esc(cabinet['name'])}</b> открыт.")
    await home(message)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await home(message)


@router.callback_query(F.data == "c:home")
async def home_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await home(call)


@router.callback_query(F.data == "c:help")
async def help_(call: CallbackQuery) -> None:
    await show(call, (
        "ℹ️ <b>Как это работает</b>\n\n"
        "1. Нажмите <b>Новая рассылка</b> и задайте название.\n"
        "2. Пришлите пост. Можно написать его здесь или переслать из канала, группы "
        "или от другого бота.\n"
        "3. Выберите группу, проверьте пост и подтвердите запуск.\n\n"
        "Пост пересылается как есть, без изменений, с заданным интервалом.\n\n"
        "В карточке рассылки доступны превью, замена поста, пауза и удаление.\n"
        "Интервал и список групп задаёт администрация."
    ), kb([[("⬅️ Назад", "c:home")]]))


@router.callback_query(F.data == "c:people")
async def people(call: CallbackQuery) -> None:
    cabinet = await _cabinet(call.from_user.id)
    if not cabinet:
        return
    lines = []
    for m in await db.cabinet_members(cabinet["id"]):
        role = "Владелец кабинета" if m["role"] == "owner" else "Помощник"
        lines.append(f"{'👤' if m['role'] == 'owner' else '🤝'} {role}: {person(m)}")
    await show(call, (
        f"👥 <b>Доступ к кабинету</b>\n\n" + "\n".join(lines) +
        "\n\nИзменить состав может только администрация."
    ), kb([[("⬅️ Назад", "c:home")]]))


# ---------- новая рассылка и замена поста ----------

async def _active_cabinet(call: CallbackQuery):
    cabinet = await _cabinet(call.from_user.id)
    if not cabinet:
        return None
    if cabinet["status"] != "active":
        await call.answer("Кабинет заморожен администрацией.", show_alert=True)
        return None
    return cabinet


@router.callback_query(F.data == "c:new")
async def new_post(call: CallbackQuery, state: FSMContext) -> None:
    cabinet = await _active_cabinet(call)
    if not cabinet:
        return
    used = await db.count_posts(cabinet["id"])
    if used >= cabinet["slots"]:
        await call.answer(
            f"Все места заняты: {used} из {cabinet['slots']}. Удалите ненужную рассылку "
            "или замените в ней пост.", show_alert=True)
        return
    if not await db.groups_for_cabinet(cabinet):
        await call.answer("Сейчас нет доступных групп. Обратитесь к администрации.", show_alert=True)
        return
    await state.set_state(S.new_name)
    await show(call, (
        "➕ <b>Новая рассылка</b>\n"
        "Шаг 1 из 3. Название\n\n"
        "Пришлите название рассылки, до 40 символов. По нему вы будете находить её в списке. "
        "В группу название не попадает.\n\n"
        "Для отмены нажмите Назад."
    ), kb([[("⬅️ Назад", "c:home")]]))


@router.message(S.new_name)
async def new_name(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not name or name.startswith("/") or len(name) > 40:
        await message.answer("Название должно быть текстом до 40 символов. Пришлите ещё раз.")
        return
    await state.set_state(S.post)
    await state.set_data({"mode": "new", "name": name})
    await message.answer((
        "➕ <b>Новая рассылка</b>\n"
        "Шаг 2 из 3. Пост\n\n"
        f"Название: {esc(name)}\n\n"
        "Пришлите пост одним сообщением. Его можно написать здесь или переслать "
        "из канала, группы или от бота. Пост будет пересылаться без изменений."
    ), reply_markup=kb([[("✖️ Отмена", "c:home")]]))


@router.callback_query(F.data.startswith("c:rep:"))
async def replace_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _active_cabinet(call):
        return
    post = await _own_post(call)
    if not post:
        return
    await state.set_state(S.post)
    await state.set_data({"mode": "replace", "post_id": post["id"]})
    await show(call, (
        f"🔄 <b>Замена поста в рассылке #{post['id']}</b>\n\n"
        "Пришлите новый пост одним сообщением. Группа, расписание и счётчик отправок сохранятся.\n\n"
        "Для отмены нажмите Назад."
    ), kb([[("⬅️ Назад", f"c:p:{post['id']}")]]))


@router.message(S.post)
async def got_post(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        return
    ids = [message.message_id]
    if message.media_group_id:
        # альбом приходит несколькими сообщениями: собираем их и принимаем разом
        key = (message.from_user.id, message.media_group_id)
        if key in _albums:
            _albums[key].append(message.message_id)
            return
        _albums[key] = ids
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        ids = _albums.pop(key, ids)
    cabinet = await _cabinet(message.from_user.id)
    data = await state.get_data()
    if not cabinet or cabinet["status"] != "active" or await state.get_state() != S.post.state:
        return
    try:
        stored = await posts.store(bot, message.chat.id, ids)
    except posts.IngestError as exc:
        text = f"⚠️ {esc(exc)}"
        reason = getattr(exc, "reason", None)
        if reason and db.is_owner(message.from_user.id):
            text += f"\n\nПричина (видна только владельцам бота):\n<code>{esc(reason)}</code>"
        await message.answer(text)
        if reason:
            await notify.owners(bot, f"⚠️ Пост не принят: бот не смог переслать его в хранилище.\n<code>{esc(reason)}</code>")
        return
    title = posts.post_title(message)

    if data.get("mode") == "replace":
        post = await db.get_post(data["post_id"])
        await state.clear()
        if not post or post["cabinet_id"] != cabinet["id"]:
            await message.answer("Рассылка не найдена.")
            return
        await db.replace_post_content(post["id"], title, config.STORAGE_CHAT_ID, stored)
        await posts.drop_stored(bot, post["storage_chat_id"], posts.ids_of(post))
        await message.answer("✅ Пост заменён. Расписание сохранено.")
        await _post_card(message, await db.get_post(post["id"]))
        return

    groups = await db.groups_for_cabinet(cabinet)
    if not groups:
        await state.clear()
        await message.answer("Сейчас нет доступных групп. Обратитесь к администрации.")
        return
    await state.update_data(stored=stored)
    if len(groups) == 1:
        await _confirm(message, state, bot, groups[0])
        return
    await state.set_state(S.group)
    rows = [[(f"💬 {g['title'] or g['chat_id']}", f"c:g:{g['id']}")] for g in groups]
    rows.append([("✖️ Отмена", "c:no")])
    await message.answer("✅ Пост принят.\n\nВыберите группу для рассылки.", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("c:g:"), S.group)
async def pick_group(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    cabinet = await _active_cabinet(call)
    if not cabinet:
        return
    await call.answer()
    allowed = {g["id"]: g for g in await db.groups_for_cabinet(cabinet)}
    group = allowed.get(int(call.data.split(":")[2]))
    if not group:
        await call.message.answer("Эта группа сейчас недоступна. Выберите другую.")
        return
    await _confirm(call.message, state, bot, group)


async def _confirm(message: Message, state: FSMContext, bot: Bot, group) -> None:
    """Шаг 3: показываем пост и просим подтвердить запуск."""
    data = await state.get_data()
    await state.set_state(S.confirm)
    await state.update_data(group_id=group["id"])
    try:
        await posts.forward(bot, message.chat.id, config.STORAGE_CHAT_ID, data["stored"])
    except Exception:
        log.warning("Не удалось показать превью перед запуском", exc_info=True)
    interval = await db.effective_interval(group["interval_hours"])
    await message.answer((
        "➕ <b>Новая рассылка</b>\n"
        "Шаг 3 из 3. Подтверждение\n\n"
        f"🏷 Название: {esc(data['name'])}\n"
        f"💬 Группа: {esc(group['title'])}\n"
        f"⏱ Интервал: {fmt_hours(interval)}\n\n"
        "Выше показано, как пост будет выглядеть в группе. Запустить рассылку?"
    ), reply_markup=kb([[("✅ Запустить", "c:ok")], [("✖️ Отмена", "c:no")]]))


@router.callback_query(F.data == "c:no")
async def new_cancel(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    if data.get("stored"):
        await posts.drop_stored(bot, config.STORAGE_CHAT_ID, data["stored"])
    await home(call)


@router.callback_query(F.data == "c:ok", S.confirm)
async def new_confirm(call: CallbackQuery, state: FSMContext) -> None:
    cabinet = await _active_cabinet(call)
    if not cabinet:
        return
    data = await state.get_data()
    await state.clear()
    allowed = {g["id"]: g for g in await db.groups_for_cabinet(cabinet)}
    group = allowed.get(data.get("group_id"))
    if not group:
        await show(call, "Эта группа сейчас недоступна. Начните заново.", kb([[("🏠 В кабинет", "c:home")]]))
        return
    if await db.count_posts(cabinet["id"]) >= cabinet["slots"]:
        await show(call, "Все места заняты, рассылка не создана.", kb([[("🏠 В кабинет", "c:home")]]))
        return
    post_id = await db.create_post(cabinet["id"], group["id"], call.from_user.id, data["name"],
                                   config.STORAGE_CHAT_ID, data["stored"], named=True)
    interval = await db.effective_interval(group["interval_hours"])
    await show(call, (
        f"✅ <b>Рассылка #{post_id} запущена</b>\n\n"
        f"🏷 Название: {esc(data['name'])}\n"
        f"💬 Группа: {esc(group['title'])}\n"
        f"⏱ Интервал: {fmt_hours(interval)}\n\n"
        "Первая отправка в течение минуты."
    ), kb([[("📄 Открыть рассылку", f"c:p:{post_id}")], [("🏠 В кабинет", "c:home")]]))


# ---------- мои рассылки ----------

@router.callback_query(F.data == "c:posts")
async def posts_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    cabinet = await _cabinet(call.from_user.id)
    if not cabinet:
        return
    items = await db.list_posts(cabinet["id"])
    rows = [[(f"{POST_ICON[p['status']]} #{p['id']} {p['title']}", f"c:p:{p['id']}")] for p in items]
    rows.append([("⬅️ Назад", "c:home")])
    text = (f"📋 <b>Мои рассылки</b>\n\nЗанято мест: {len(items)} из {cabinet['slots']}"
            if items else "📋 <b>Мои рассылки</b>\n\nРассылок пока нет.")
    await show(call, text, kb(rows))


async def _own_post(call: CallbackQuery):
    post = await db.get_post(int(call.data.split(":")[2]))
    if not post or post["cabinet_id"] != db.cabinet_of(call.from_user.id):
        await call.answer("Рассылка не найдена.", show_alert=True)
        return None
    return post


async def post_text(post) -> str:
    interval = await db.effective_interval(post["group_interval"])
    nxt = fmt_dt(post["next_send_at"]) if post["status"] == "active" else "нет"
    return (
        f"📄 <b>Рассылка #{post['id']}</b>\n"
        f"{esc(post['title'])}\n\n"
        f"💬 Группа: {esc(post['group_title'])}\n"
        f"{POST_ICON[post['status']]} Статус: {POST_TEXT[post['status']]}\n"
        f"⏱ Интервал: {fmt_hours(interval)}\n\n"
        f"📊 Отправлено: {times(post['sent_count'])}\n"
        f"🕓 Последняя отправка: {fmt_dt(post['last_sent_at'])}\n"
        f"⏭ Следующая отправка: {nxt}\n"
        f"📅 Создана: {fmt_dt(post['created_at'])}"
    )


async def _post_card(target, post) -> None:
    rows = [[("👁 Превью", f"c:pv:{post['id']}")], [("🔄 Заменить пост", f"c:rep:{post['id']}")]]
    if post["status"] == "active":
        rows.append([("⏸ Пауза", f"c:pt:{post['id']}")])
    elif post["status"] == "paused":
        rows.append([("▶️ Возобновить", f"c:pt:{post['id']}")])
    rows.append([("✏️ Название", f"c:pn:{post['id']}")])
    rows.append([("🗑 Удалить", f"c:pd:{post['id']}")])
    rows.append([("⬅️ К списку", "c:posts")])
    text = await post_text(post)
    if post["status"] == "stopped":
        text += "\n\nРассылка остановлена администрацией. Для возобновления свяжитесь с администрацией."
    await show(target, text, kb(rows))


@router.callback_query(F.data.startswith("c:p:"))
async def post_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    post = await _own_post(call)
    if post:
        await _post_card(call, post)


@router.callback_query(F.data.startswith("c:pn:"))
async def post_rename(call: CallbackQuery, state: FSMContext) -> None:
    post = await _own_post(call)
    if not post:
        return
    await state.set_state(S.name)
    await state.set_data({"post_id": post["id"]})
    await show(call, (
        f"✏️ <b>Название рассылки #{post['id']}</b>\n\n"
        f"Сейчас: {esc(post['title'])}\n\n"
        "Пришлите новое название, до 40 символов. Его видите только вы и администрация, "
        "в группу оно не попадает."
    ), kb([[("⬅️ Назад", f"c:p:{post['id']}")]]))


@router.message(S.name)
async def post_rename_done(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())
    if not name or name.startswith("/") or len(name) > 40:
        await message.answer("Название должно быть текстом до 40 символов. Пришлите ещё раз.")
        return
    data = await state.get_data()
    await state.clear()
    post = await db.get_post(data["post_id"])
    if not post or post["cabinet_id"] != db.cabinet_of(message.from_user.id):
        await message.answer("Рассылка не найдена.")
        return
    await db.rename_post(post["id"], name)
    await _post_card(message, await db.get_post(post["id"]))


@router.callback_query(F.data.startswith("c:pv:"))
async def post_preview(call: CallbackQuery, bot: Bot) -> None:
    post = await _own_post(call)
    if not post:
        return
    await call.answer()
    try:
        await posts.send_post(bot, call.message.chat.id, post)
    except Exception:
        await call.message.answer("⚠️ Не удалось показать пост. Попробуйте заменить его.")
        return
    await call.message.answer(
        "👆 Так пост выглядит в группе.", reply_markup=kb([[("⬅️ К рассылке", f"c:p:{post['id']}")]]))


@router.callback_query(F.data.startswith("c:pt:"))
async def post_toggle(call: CallbackQuery) -> None:
    if not await _active_cabinet(call):
        return
    post = await _own_post(call)
    if not post:
        return
    if post["status"] == "stopped":
        await call.answer("Рассылка остановлена администрацией.", show_alert=True)
        return
    await db.set_post_status(post["id"], "paused" if post["status"] == "active" else "active")
    await _post_card(call, await db.get_post(post["id"]))


@router.callback_query(F.data.startswith("c:pd:"))
async def post_delete_ask(call: CallbackQuery) -> None:
    post = await _own_post(call)
    if not post:
        return
    await show(call, (
        f"🗑 <b>Удалить рассылку #{post['id']}?</b>\n{esc(post['title'])}\n\n"
        "Рассылка остановится, место освободится. Отменить удаление нельзя."
    ), kb([[("🗑 Да, удалить", f"c:pdc:{post['id']}")], [("⬅️ Отмена", f"c:p:{post['id']}")]]))


@router.callback_query(F.data.startswith("c:pdc:"))
async def post_delete(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    post = await _own_post(call)
    if not post:
        return
    await db.delete_post(post["id"])
    await posts.drop_stored(bot, post["storage_chat_id"], posts.ids_of(post))
    await call.answer("Рассылка удалена.")
    await posts_list(call, state)
