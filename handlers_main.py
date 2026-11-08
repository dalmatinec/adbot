"""Основной бот для обычных пользователей: приветствие, создание зеркала,
подключение своего бота по токену, учёт групп, куда добавили основного бота.

Создание зеркала - через Managed Bots: пользователь открывает ссылку с готовыми
случайными именем и юзернеймом, подтверждает в окне Telegram, а основной бот
получает событие managed_bot, забирает токен и запускает зеркало сам.
"""

import logging
import random
import re
import secrets
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import applications
import posts
import config
import crypto
import db
import texts
import tg_raw
from notify import notify_owners
from ui import esc, kb, show, user_link

log = logging.getLogger(__name__)
router = Router()

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
NAME_WORDS = ["Pulse", "Orbit", "Echo", "Nova", "Spark", "Wave", "Flux", "Prime", "Vista", "Lumen"]


class UserStates(StatesGroup):
    waiting_token = State()
    waiting_post = State()


# ---------- главное меню ----------

async def _home(target: Message | CallbackQuery) -> None:
    uid = target.from_user.id
    mirror = await db.mirror_for_staff(uid)
    app = await db.get_application(uid)
    rows = []
    if mirror:
        is_owner = mirror["owner_id"] == uid
        if mirror["status"] == "revoked" and is_owner:
            text = (f"Токен зеркала @{esc(mirror['username'])} отозван, зеркало отключено. "
                    "Посты сохранены и находятся на паузе.\n\n"
                    "Создайте новое зеркало или отправьте токен нового бота: посты и слоты сохранятся.")
            rows.append([("Создать новое зеркало", "m:create")])
            rows.append([("Подключить бота по токену", "m:token")])
        else:
            text = f"Ваше зеркало: @{esc(mirror['username'])}\nНастройка рассылки выполняется в нём."
            if mirror["username"]:
                rows.append([("Открыть зеркало", f"https://t.me/{mirror['username']}")])
        if is_owner:
            rows.append([("Удалить зеркало", "m:del")])
    elif app:
        text = ("Ваша заявка на зеркало находится на рассмотрении. Ответ будет направлен "
                f"в этот чат в течение {config.APPLICATION_TTL_HOURS} ч.")
        rows.append([("Отозвать заявку", "m:cancelapp")])
    else:
        text = texts.WELCOME
        rows.append([("🪞 Создать зеркало", "m:create")])
        rows.append([("🔑 Подключить своего бота", "m:token")])
    if await db.get_role(uid):
        rows.append([("⚙️ Админка", "a:menu")])
    await show(target, text, kb(rows))


async def _blocked(user_id: int) -> bool:
    """Нельзя создавать новое зеркало, если есть рабочее зеркало или заявка.
    Отозванное зеркало (бот удалён) заменить можно."""
    mirror = await db.get_mirror_by_owner(user_id)
    if mirror and mirror["status"] != "revoked":
        return True
    return await db.get_application(user_id) is not None


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, state: FSMContext, command: CommandObject) -> None:
    await state.clear()
    if command.args == "post":
        await _ask_post(message, state)
        return
    await _home(message)


async def _ask_post(message: Message, state: FSMContext) -> None:
    """Приём поста основным ботом: так сохраняются премиум-эмодзи."""
    mirror = await db.mirror_for_staff(message.from_user.id)
    if not mirror or mirror["status"] != "active":
        await message.answer("Отправка постов доступна владельцам и админам активного зеркала.")
        return
    await state.set_state(UserStates.waiting_post)
    await message.answer(
        "Отправьте пост одним сообщением: текст, фото, видео, GIF или файл. "
        "Пост сохраняется без изменений, включая премиум-эмодзи и кнопки-ссылки.\n\n"
        "Для отмены отправьте /cancel.")


@router.message(UserStates.waiting_post, F.chat.type == "private")
async def post_received(message: Message, state: FSMContext, bot: Bot) -> None:
    mirror = await db.mirror_for_staff(message.from_user.id)
    if not mirror or mirror["status"] != "active":
        await state.clear()
        return
    try:
        st_chat, st_msg, buttons, title = await posts.ingest_direct(bot, message)
    except posts.IngestError as exc:
        await message.answer(esc(exc))
        return
    await state.clear()
    await db.save_draft(message.from_user.id, mirror["id"], st_chat, st_msg, buttons, title)
    await message.answer(
        f"Пост сохранён. Вернитесь в зеркало @{esc(mirror['username'])}, нажмите «Новый пост» "
        "и выберите группу.",
        reply_markup=kb([[("🪞 Открыть зеркало", f"https://t.me/{mirror['username']}")]]))


@router.message(Command("cancel"), F.chat.type == "private")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _home(message)


@router.callback_query(F.data == "m:home")
async def home_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _home(call)


# ---------- удаление зеркала и отзыв заявки ----------

@router.callback_query(F.data == "m:del")
async def delete_ask(call: CallbackQuery) -> None:
    mirror = await db.get_mirror_by_owner(call.from_user.id)
    if not mirror:
        await _home(call)
        return
    await show(call, (
        f"Удалить зеркало @{esc(mirror['username'])}? Все его посты, слоты и статистика будут удалены. "
        "Сам бот в Telegram останется, при необходимости удалите его через @BotFather. "
        "После удаления можно создать новое зеркало (потребуется одобрение владельцев)."),
        kb([[("Да, удалить", "m:delc")], [("Отмена", "m:home")]]))


@router.callback_query(F.data == "m:delc")
async def delete_do(call: CallbackQuery, bot: Bot, manager) -> None:
    mirror = await db.get_mirror_by_owner(call.from_user.id)
    if mirror:
        await manager.stop(mirror["id"])
        await db.delete_mirror(mirror["id"])
        await notify_owners(
            bot, f"Пользователь {user_link(call.from_user.id, call.from_user.full_name)} "
                 f"удалил зеркало @{esc(mirror['username'])}.")
    await _home(call)


@router.callback_query(F.data == "m:cancelapp")
async def cancel_application(call: CallbackQuery, bot: Bot, manager) -> None:
    if await applications.reject(bot, call.from_user.id, "отозвана заявителем", manager=manager):
        await notify_owners(
            bot, f"Пользователь {user_link(call.from_user.id, call.from_user.full_name)} отозвал заявку на зеркало.")
    await _home(call)


# ---------- создание зеркала через Managed Bots ----------

@router.callback_query(F.data == "m:create")
async def create_mirror(call: CallbackQuery, bot: Bot) -> None:
    if await _blocked(call.from_user.id):
        await call.answer("У вас уже есть зеркало или заявка", show_alert=True)
        await _home(call)
        return
    me = await bot.me()
    username = f"ads_{secrets.token_hex(4)}_bot"
    name = f"{random.choice(NAME_WORDS)} {random.randint(100, 999)}"
    link = config.MIRROR_LINK_TEMPLATE.format(
        manager=me.username, username=username, name=quote(name))
    await show(call, texts.CREATE_MIRROR, kb([
        [("🪞 Создать зеркало", link)],
        [("🔑 Подключить своего бота", "m:token")],
        [("⬅️ Назад", "m:home")],
    ]))


async def register_managed_bot(bot: Bot, manager, mb: dict) -> None:
    """Событие managed_bot: пользователь подтвердил создание зеркала.
    Бот не запускается сразу, а уходит в заявку владельцам."""
    user, bot_info = tg_raw.parse_managed_bot(mb)
    owner_id = user.get("id")
    bot_id = bot_info.get("id")
    if not owner_id or not bot_id:
        log.warning("managed_bot без user/bot: %s", mb)
        return
    if await db.is_banned(owner_id):
        return
    existing = await db.get_mirror_by_owner(owner_id)
    if existing and existing["bot_id"] == bot_id:
        return  # повторное событие о том же боте
    try:
        token = await tg_raw.get_managed_bot_token(config.BOT_TOKEN, bot_id)
        me = await tg_raw.get_me(token)
    except tg_raw.TgError as exc:
        log.error("Не удалось получить токен зеркала %s: %s", bot_id, exc)
        await _safe_send(bot, owner_id,
                         "Бот создан, но автоматическое подключение не удалось. "
                         "Нажмите Подключить своего бота и отправьте токен из @BotFather.")
        await notify_owners(bot, f"Ошибка подключения зеркала {bot_id} пользователя {owner_id}: {esc(exc)}")
        return
    if existing and existing["status"] == "revoked":
        await _replace_mirror(bot, manager, existing, me, token)
        return
    full_name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or None
    await _submit(bot, manager, owner_id, me, token, "quick",
                  {"full_name": full_name, "username": user.get("username")})


async def _replace_mirror(bot: Bot, manager, mirror, me: dict, token: str) -> None:
    """Владелец уже одобрен ранее, поэтому новый бот взамен отозванного
    подключается без повторной заявки. Посты остаются на паузе."""
    if not await db.replace_mirror_bot(mirror["id"], me["id"], me.get("username"), crypto.encrypt(token)):
        await _safe_send(bot, mirror["owner_id"], "Этот бот уже подключён к другому зеркалу.")
        return
    await manager.stop(mirror["id"])
    await manager.start(await db.get_mirror(mirror["id"]))
    await _safe_send(
        bot, mirror["owner_id"],
        f"Зеркало обновлено: @{esc(me.get('username'))}. Посты находятся на паузе, "
        "возобновите их в кабинете зеркала.",
        kb([[("Открыть зеркало", f"https://t.me/{me.get('username')}")]]))
    await notify_owners(bot, f"Зеркало пользователя {mirror['owner_id']} заменено на @{esc(me.get('username'))}.")


_SUBMIT_ERRORS = {
    "has_mirror": "У вас уже есть зеркало. Допускается одно зеркало на пользователя.",
    "has_app": "Ваша заявка уже на рассмотрении.",
    "bot_taken": "Этот бот уже подключён или ожидает рассмотрения.",
}


async def _submit(bot: Bot, manager, owner_id: int, me: dict, token: str, source: str,
                  user_info: dict | None = None) -> bool:
    result = await applications.submit(bot, manager, owner_id, me, token, source, user_info)
    if result != "ok":
        await _safe_send(bot, owner_id, _SUBMIT_ERRORS[result])
    return result == "ok"


async def _safe_send(bot: Bot, user_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=markup)
    except Exception:
        pass


class MainDispatcher(Dispatcher):
    """Диспетчер основного бота. Перехватывает событие managed_bot до обычной
    маршрутизации: так оно не зависит от того, знает ли установленная версия
    aiogram этот тип обновления."""

    async def feed_update(self, bot: Bot, update, **kwargs):
        extra = getattr(update, "model_extra", None) or {}
        mb = extra.get("managed_bot") or getattr(update, "managed_bot", None)
        if mb is not None:
            if hasattr(mb, "model_dump"):
                mb = mb.model_dump()
            try:
                await register_managed_bot(bot, self.workflow_data["manager"], mb)
            except Exception:
                log.exception("Ошибка обработки managed_bot")
            return True
        return await super().feed_update(bot, update, **kwargs)


# ---------- подключение бота по токену ----------

@router.callback_query(F.data == "m:token")
async def token_start(call: CallbackQuery, state: FSMContext) -> None:
    if await _blocked(call.from_user.id):
        await call.answer("У вас уже есть зеркало или заявка", show_alert=True)
        return
    await state.set_state(UserStates.waiting_token)
    await show(call, texts.HOWTO_BOTFATHER, kb([[("⬅️ Назад", "m:home")]]))


@router.message(UserStates.waiting_token, F.chat.type == "private")
async def token_received(message: Message, state: FSMContext, bot: Bot, manager) -> None:
    raw = (message.text or "").strip()
    try:
        await message.delete()  # токен не должен оставаться в переписке
    except Exception:
        pass
    if not TOKEN_RE.match(raw):
        await message.answer("Введённые данные не похожи на токен. Токен имеет вид <code>123456789:AA...</code>. "
                             "Отправьте токен повторно или /cancel.")
        return
    try:
        me = await tg_raw.get_me(raw)
    except tg_raw.TgError:
        await message.answer("Telegram не принял токен. Проверьте его и отправьте повторно.")
        return
    if me["id"] == bot.id:
        await message.answer("Указан токен основного бота. Требуется токен отдельного бота.")
        return
    await state.clear()
    existing = await db.get_mirror_by_owner(message.from_user.id)
    if existing and existing["status"] == "revoked":
        await _replace_mirror(bot, manager, existing, me, raw)
    else:
        await _submit(bot, manager, message.from_user.id, me, raw, "token",
                      {"full_name": message.from_user.full_name, "username": message.from_user.username})
    await _home(message)


# ---------- в какие группы добавили основного бота ----------

@router.my_chat_member(F.chat.type.in_({"group", "supergroup", "channel"}))
async def on_my_member(event: ChatMemberUpdated, bot: Bot) -> None:
    status = event.new_chat_member.status
    chat = event.chat
    if status in ("administrator", "member"):
        group = await db.get_group_by_chat(chat.id)
        await db.add_group(chat.id, chat.title)
        if group is None:
            warn = "" if status == "administrator" else "\nБот не является администратором, отправка сообщений может быть недоступна."
            await notify_owners(
                bot, f"Бот добавлен в группу <b>{esc(chat.title)}</b> ({chat.id}).{warn}\n"
                     "Для включения откройте /adm, раздел Группы.")
    elif status in ("left", "kicked"):
        group = await db.get_group_by_chat(chat.id)
        if group and group["is_active"]:
            await db.set_group_active(group["id"], False)
            await notify_owners(bot, f"Бот удалён из группы <b>{esc(chat.title)}</b>. Группа отключена.")
