"""Кабинет рекламодателя внутри бота-зеркала.

Здесь нет ни групп, ни интервалов: рекламодатель присылает пост, выбирает
одну из разрешённых владельцами групп и смотрит статистику. Всё остальное
(интервал, группы, слоты) задают владельцы в админке основного бота.

Роутер создаётся функцией make_router() отдельно для каждого зеркала: один
объект Router нельзя подключить к двум диспетчерам.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import db
import posts
import texts
from ui import STATUS_ICON, STATUS_TEXT, esc, fmt_dt, fmt_interval, kb, show, user_link

log = logging.getLogger(__name__)


class CabinetStates(StatesGroup):
    waiting_post = State()
    choosing_group = State()
    adding_admin = State()


async def _staff_mirror(user_id: int, mirror_id: int):
    """Зеркало, если пользователь в нём создатель или админ; иначе None
    (посторонним бот не отвечает вовсе)."""
    if not await db.is_mirror_staff(mirror_id, user_id):
        return None
    return await db.get_mirror(mirror_id)


def _is_active(mirror) -> bool:
    return mirror["status"] == "active"


async def _menu(target, mirror) -> None:
    used = await db.count_posts(mirror["id"])
    live = sum(1 for p in await db.list_posts(mirror["id"]) if p["status"] == "active")
    if not _is_active(mirror):
        text = f"Доступ к рассылке ограничен: {STATUS_TEXT[mirror['status']]}. Обратитесь к владельцам сети."
    else:
        text = (
            "<b>Кабинет рассылки</b>\n\n"
            f"Слоты: {used}/{mirror['slots']}\n"
            f"Активных рассылок: {live}"
        )
    await show(target, text, kb([
        [("➕ Новый пост", "c:new")],
        [("📋 Мои посты", "c:posts"), ("📊 Статистика", "c:stats")],
        [("👥 Админы", "c:admins"), ("ℹ️ Инструкция", "c:help")],
    ]))


def make_router() -> Router:
    r = Router()
    r.message.filter(F.chat.type == "private")
    r.callback_query.filter(F.message.chat.type == "private")

    # ---------- меню ----------

    @r.message(CommandStart())
    async def start(message: Message, state: FSMContext, mirror_id: int) -> None:
        await state.clear()
        mirror = await _staff_mirror(message.from_user.id, mirror_id)
        if mirror:
            await _menu(message, mirror)

    @r.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext, mirror_id: int) -> None:
        await state.clear()
        mirror = await _staff_mirror(message.from_user.id, mirror_id)
        if mirror:
            await _menu(message, mirror)

    @r.callback_query(F.data == "c:menu")
    async def menu(call: CallbackQuery, state: FSMContext, mirror_id: int) -> None:
        await state.clear()
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if mirror:
            await _menu(call, mirror)

    @r.callback_query(F.data == "c:help")
    async def help_(call: CallbackQuery, mirror_id: int) -> None:
        if await _staff_mirror(call.from_user.id, mirror_id):
            await show(call, texts.CABINET_HELP, kb([[("⬅️ Назад", "c:menu")]]))

    # ---------- новый пост ----------

    @r.callback_query(F.data == "c:new")
    async def new_post(call: CallbackQuery, state: FSMContext, main_bot: Bot, mirror_id: int) -> None:
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if not mirror:
            return
        if not _is_active(mirror):
            await call.answer("Доступ ограничен", show_alert=True)
            return
        used = await db.count_posts(mirror_id)
        if used >= mirror["slots"]:
            await call.answer(
                f"Все слоты заняты ({used}/{mirror['slots']}). Удалите ненужный пост "
                "или попросите владельцев расширить слоты.", show_alert=True)
            return
        if not await db.list_groups(active_only=True):
            await call.answer("Пока нет доступных групп для рассылки.", show_alert=True)
            return
        draft = await db.take_draft(call.from_user.id, mirror_id)
        if draft:
            data = {"st_chat": draft["storage_chat_id"], "st_msg": draft["storage_message_id"],
                    "buttons": draft["buttons"], "title": draft["title"]}
            await state.update_data(**data)
            await call.answer()
            await _choose_group(call.message, state, main_bot, mirror, call.from_user.id)
            return
        await state.set_state(CabinetStates.waiting_post)
        me = await main_bot.me()
        await show(call, "Чтобы сохранить премиум-эмодзи, отправьте пост через основного бота: "
                         "нажмите кнопку ниже, пришлите пост и вернитесь сюда.\n\n"
                         "Можно отправить пост и прямо здесь, но премиум-эмодзи в этом случае "
                         "заменятся обычными.\n\nДля отмены отправьте /cancel.",
                   kb([[("📨 Отправить пост через основного бота", f"https://t.me/{me.username}?start=post")],
                       [("⬅️ Назад", "c:menu")]]))

    @r.message(CabinetStates.waiting_post)
    async def got_post(message: Message, state: FSMContext, bot: Bot,
                       main_bot: Bot, mirror_id: int) -> None:
        mirror = await _staff_mirror(message.from_user.id, mirror_id)
        if not mirror or not _is_active(mirror):
            return
        wait = await message.answer("Пост принимается.")
        try:
            st_chat, st_msg, buttons, title = await posts.ingest(bot, main_bot, message)
        except posts.IngestError as exc:
            await wait.edit_text(esc(exc))
            return
        await wait.delete()
        await state.update_data(st_chat=st_chat, st_msg=st_msg, buttons=buttons, title=title)

        await _choose_group(message, state, main_bot, mirror, message.from_user.id)

    async def _choose_group(message: Message, state: FSMContext, main_bot: Bot, mirror, user_id: int) -> None:
        groups = await db.list_groups(active_only=True)
        if not groups:
            await state.clear()
            await message.answer("Сейчас нет доступных групп. Попробуйте позже.")
            return
        if len(groups) == 1:
            await _finalize(message, state, main_bot, mirror, user_id, groups[0]["id"])
            return
        await state.set_state(CabinetStates.choosing_group)
        rows = [[(f"💬 {g['title'] or g['chat_id']}", f"c:pg:{g['id']}")] for g in groups]
        rows.append([("✖️ Отмена", "c:menu")])
        await message.answer("Выберите группу для рассылки. На один пост выбирается одна группа.", reply_markup=kb(rows))

    @r.callback_query(F.data.startswith("c:pg:"), CabinetStates.choosing_group)
    async def pick_group(call: CallbackQuery, state: FSMContext, main_bot: Bot, mirror_id: int) -> None:
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if not mirror:
            return
        await call.answer()
        await _finalize(call.message, state, main_bot, mirror, call.from_user.id,
                        int(call.data.split(":")[2]))

    async def _finalize(message: Message, state: FSMContext, main_bot: Bot,
                        mirror, user_id: int, group_id: int) -> None:
        data = await state.get_data()
        await state.clear()
        group = await db.get_group(group_id)
        if not group or not group["is_active"] or "st_msg" not in data:
            await message.answer("Эта группа сейчас недоступна, начните заново.")
            return
        if await db.count_posts(mirror["id"]) >= mirror["slots"]:
            await message.answer("Все слоты заняты, пост не создан.")
            return
        pending = await db.moderation_on()
        post_id = await db.create_post(
            mirror["id"], group_id, user_id, data["title"], data["st_chat"], data["st_msg"],
            data["buttons"], pending,
        )
        if pending:
            await _notify_moderators(main_bot, post_id, mirror, group, data)
            text = "Пост принят и направлен на проверку. После одобрения рассылка будет запущена."
        else:
            interval = await db.effective_interval(group["interval_minutes"])
            text = (f"Пост принят. Рассылка в группу <b>{esc(group['title'])}</b> "
                    f"будет выполняться каждые {fmt_interval(interval)}. Первая отправка в течение минуты.")
        await message.answer(text, reply_markup=kb([[("📋 Мои посты", "c:posts"), ("🏠 Меню", "c:menu")]]))

    # ---------- мои посты ----------

    @r.callback_query(F.data == "c:posts")
    async def posts_list(call: CallbackQuery, mirror_id: int) -> None:
        if not await _staff_mirror(call.from_user.id, mirror_id):
            return
        items = await db.list_posts(mirror_id)
        rows = [[(f"{STATUS_ICON[p['status']]} {p['title']}", f"c:post:{p['id']}")] for p in items]
        rows.append([("⬅️ Назад", "c:menu")])
        await show(call, "<b>Ваши посты</b>" if items else "У вас пока нет постов.", kb(rows))

    async def _post_card(call: CallbackQuery, post) -> None:
        group = await db.get_group(post["group_id"])
        interval = await db.effective_interval(group["interval_minutes"]) if group else None
        lines = [
            f"{STATUS_ICON[post['status']]} <b>{esc(post['title'])}</b>",
            f"Статус: {STATUS_TEXT.get(post['status'], post['status'])}",
            f"Группа: {esc(group['title']) if group else 'нет'}",
            f"Интервал: {fmt_interval(interval) if interval else 'нет'} (устанавливается владельцами)",
            f"Создан: {fmt_dt(post['created_at'])}",
            f"Отчёт с: {fmt_dt(post['started_at'])}",
            f"Отправлено: {post['sent_count']}, ошибок: {post['error_count']}",
            f"Последняя отправка: {fmt_dt(post['last_sent_at'])}",
        ]
        rows = []
        if post["status"] == "active":
            rows.append([("⏸ Пауза", f"c:pp:{post['id']}")])
        elif post["status"] == "paused":
            rows.append([("▶️ Возобновить", f"c:pp:{post['id']}")])
        rows.append([("🗑 Удалить", f"c:pd:{post['id']}")])
        rows.append([("⬅️ К постам", "c:posts")])
        await show(call, "\n".join(lines), kb(rows))

    async def _own_post(call: CallbackQuery, mirror_id: int):
        if not await _staff_mirror(call.from_user.id, mirror_id):
            return None
        post = await db.get_post(int(call.data.split(":")[2]))
        if not post or post["mirror_id"] != mirror_id:
            await call.answer("Пост не найден", show_alert=True)
            return None
        return post

    @r.callback_query(F.data.startswith("c:post:"))
    async def post_open(call: CallbackQuery, mirror_id: int) -> None:
        post = await _own_post(call, mirror_id)
        if post:
            await _post_card(call, post)

    @r.callback_query(F.data.startswith("c:pp:"))
    async def post_toggle(call: CallbackQuery, mirror_id: int) -> None:
        post = await _own_post(call, mirror_id)
        if not post:
            return
        mirror = await db.get_mirror(mirror_id)
        if post["status"] == "active":
            await db.set_post_status(post["id"], "paused")
        elif post["status"] == "paused":
            if not _is_active(mirror):
                await call.answer("Доступ ограничен", show_alert=True)
                return
            await db.set_post_status(post["id"], "active")
        await _post_card(call, await db.get_post(post["id"]))

    @r.callback_query(F.data.startswith("c:pd:"))
    async def post_delete_ask(call: CallbackQuery, mirror_id: int) -> None:
        post = await _own_post(call, mirror_id)
        if post:
            await show(call, f"Удалить пост {esc(post['title'])}? Статистика поста будет удалена.",
                       kb([[("🗑 Да, удалить", f"c:pdc:{post['id']}")],
                           [("⬅️ Отмена", f"c:post:{post['id']}")]]))

    @r.callback_query(F.data.startswith("c:pdc:"))
    async def post_delete(call: CallbackQuery, mirror_id: int) -> None:
        post = await _own_post(call, mirror_id)
        if post:
            await db.delete_post(post["id"])
            await call.answer("Удалено")
            await posts_list(call, mirror_id)

    # ---------- статистика ----------

    @r.callback_query(F.data == "c:stats")
    async def stats(call: CallbackQuery, mirror_id: int) -> None:
        if not await _staff_mirror(call.from_user.id, mirror_id):
            return
        items = await db.list_posts(mirror_id)
        total = await db.mirror_stats(mirror_id)
        lines = ["<b>Статистика</b> (с момента создания каждой рассылки)\n"]
        for p in items:
            lines.append(
                f"{STATUS_ICON[p['status']]} {esc(p['title'])}\n"
                f"   отправок: {p['sent_count']}, с {fmt_dt(p['started_at'])}"
            )
        if not items:
            lines.append("Постов пока нет.")
        lines.append(f"\nВсего отправок: {total['sent']}, ошибок: {total['errors']}")
        await show(call, "\n".join(lines), kb([[("⬅️ Назад", "c:menu")]]))

    # ---------- админы зеркала ----------

    async def _admins_screen(target, mirror, is_owner: bool) -> None:
        admins = await db.list_mirror_admins(mirror["id"])
        lines = [f"<b>Администраторы</b> (не более {config.MAX_MIRROR_ADMINS} включая создателя)\n",
                 f"Создатель: {user_link(mirror['owner_id'])}"]
        rows = []
        for a in admins:
            lines.append(f"• {user_link(a['user_id'])}")
            if is_owner:
                rows.append([(f"❌ Убрать {a['user_id']}", f"c:adel:{a['user_id']}")])
        if is_owner and len(admins) + 1 < config.MAX_MIRROR_ADMINS:
            rows.append([("➕ Добавить админа", "c:aadd")])
        rows.append([("⬅️ Назад", "c:menu")])
        await show(target, "\n".join(lines), kb(rows))

    @r.callback_query(F.data == "c:admins")
    async def admins(call: CallbackQuery, mirror_id: int) -> None:
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if mirror:
            await _admins_screen(call, mirror, mirror["owner_id"] == call.from_user.id)

    @r.callback_query(F.data == "c:aadd")
    async def admin_add(call: CallbackQuery, state: FSMContext, mirror_id: int) -> None:
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if not mirror or mirror["owner_id"] != call.from_user.id:
            return
        await state.set_state(CabinetStates.adding_admin)
        await show(call, "Отправьте Telegram ID нового администратора или перешлите любое его сообщение. "
                         "Пользователь должен хотя бы один раз открыть этого бота.\n\nДля отмены отправьте /cancel.",
                   kb([[("⬅️ Назад", "c:admins")]]))

    @r.message(CabinetStates.adding_admin)
    async def admin_add_msg(message: Message, state: FSMContext, mirror_id: int) -> None:
        mirror = await _staff_mirror(message.from_user.id, mirror_id)
        if not mirror or mirror["owner_id"] != message.from_user.id:
            return
        uid = None
        origin = message.forward_origin
        if origin is not None and getattr(origin, "type", "") == "user":
            uid = origin.sender_user.id
        elif message.text and message.text.strip().isdigit():
            uid = int(message.text.strip())
        if uid is None:
            await message.answer("Требуется числовой ID или пересланное сообщение пользователя.")
            return
        result = await db.add_mirror_admin(mirror_id, uid)
        await state.clear()
        note = {"ok": "Администратор добавлен.", "exists": "Пользователь уже является администратором.",
                "owner": "Вы являетесь создателем зеркала.",
                "limit": f"Достигнут лимит: не более {config.MAX_MIRROR_ADMINS} человек, включая создателя."}[result]
        await message.answer(note)
        await _admins_screen(message, await db.get_mirror(mirror_id), True)

    @r.callback_query(F.data.startswith("c:adel:"))
    async def admin_del(call: CallbackQuery, mirror_id: int) -> None:
        mirror = await _staff_mirror(call.from_user.id, mirror_id)
        if not mirror or mirror["owner_id"] != call.from_user.id:
            return
        await db.remove_mirror_admin(mirror_id, int(call.data.split(":")[2]))
        await _admins_screen(call, mirror, True)

    return r


async def _notify_moderators(main_bot: Bot, post_id: int, mirror, group, data: dict) -> None:
    """Предпросмотр поста и кнопки одобрения всем владельцам и модераторам."""
    text = (f"<b>Новый пост на проверку</b> #{post_id}\n"
            f"Зеркало: @{esc(mirror['username'])} (владелец {user_link(mirror['owner_id'])})\n"
            f"Группа: {esc(group['title'])}")
    markup = kb([[("✅ Одобрить", f"a:ok:{post_id}"), ("❌ Отклонить", f"a:no:{post_id}")]])
    for owner in await db.list_owners():
        try:
            await main_bot.copy_message(
                owner["user_id"], data["st_chat"], data["st_msg"],
                reply_markup=posts.buttons_markup(data["buttons"]),
            )
            await main_bot.send_message(owner["user_id"], text, reply_markup=markup)
        except Exception:
            log.warning("Не удалось отправить пост на проверку %s", owner["user_id"])


def make_pending_router() -> Router:
    """Бот, чья заявка ещё не рассмотрена: на любое сообщение от заявителя
    отвечает, что заявка ожидает одобрения. Остальным не отвечает."""
    r = Router()
    r.message.filter(F.chat.type == "private")

    @r.message()
    async def pending(message: Message, owner_id: int) -> None:
        if message.from_user.id == owner_id and await db.get_application(owner_id):
            await message.answer(texts.PENDING_NOTICE)

    return r
