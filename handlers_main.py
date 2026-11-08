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
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import applications
import config
import db
import texts
import tg_raw
from notify import notify_owners
from ui import esc, kb, show

log = logging.getLogger(__name__)
router = Router()

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
NAME_WORDS = ["Pulse", "Orbit", "Echo", "Nova", "Spark", "Wave", "Flux", "Prime", "Vista", "Lumen"]


class UserStates(StatesGroup):
    waiting_token = State()


# ---------- главное меню ----------

async def _home(target: Message | CallbackQuery) -> None:
    uid = target.from_user.id
    mirror = await db.mirror_for_staff(uid)
    rows = []
    if mirror:
        text = f"🪞 Ваше зеркало: @{esc(mirror['username'])}\nВ нём вы настраиваете рассылку."
        if mirror["username"]:
            rows.append([("🪞 Открыть зеркало", f"https://t.me/{mirror['username']}")])
    elif await db.get_application(uid):
        text = ("📨 Ваша заявка на зеркало на рассмотрении. Ответ придёт сюда, "
                f"обычно в течение {config.APPLICATION_TTL_HOURS} ч.")
    else:
        text = texts.WELCOME
        rows.append([("🪞 Создать зеркало", "m:create")])
        rows.append([("🔑 Подключить своего бота", "m:token")])
    if await db.get_role(uid):
        rows.append([("⚙️ Админка", "a:menu")])
    await show(target, text, kb(rows))


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _home(message)


@router.message(Command("cancel"), F.chat.type == "private")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _home(message)


@router.callback_query(F.data == "m:home")
async def home_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _home(call)


# ---------- создание зеркала через Managed Bots ----------

@router.callback_query(F.data == "m:create")
async def create_mirror(call: CallbackQuery, bot: Bot) -> None:
    if await db.get_mirror_by_owner(call.from_user.id) or await db.get_application(call.from_user.id):
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


async def register_managed_bot(bot: Bot, mb: dict) -> None:
    """Событие managed_bot: пользователь подтвердил создание зеркала.
    Бот не запускается сразу, а уходит в заявку владельцам."""
    user = mb.get("user") or {}
    owner_id = user.get("id")
    bot_info = mb.get("bot") or {}
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
                         "Бот создан, но подключить его автоматически не получилось. "
                         "Нажмите «Подключить своего бота» и пришлите его токен из @BotFather.")
        await notify_owners(bot, f"⚠️ Ошибка подключения зеркала {bot_id} пользователя {owner_id}: {esc(exc)}")
        return
    full_name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or None
    await _submit(bot, owner_id, me, token, "quick",
                  {"full_name": full_name, "username": user.get("username")})


_SUBMIT_ERRORS = {
    "has_mirror": "У вас уже есть зеркало. Один пользователь - одно зеркало.",
    "has_app": "Ваша заявка уже на рассмотрении.",
    "bot_taken": "Этот бот уже подключён или ожидает рассмотрения.",
}


async def _submit(bot: Bot, owner_id: int, me: dict, token: str, source: str,
                  user_info: dict | None = None) -> bool:
    result = await applications.submit(bot, owner_id, me, token, source, user_info)
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
                await register_managed_bot(bot, mb)
            except Exception:
                log.exception("Ошибка обработки managed_bot")
            return True
        return await super().feed_update(bot, update, **kwargs)


# ---------- подключение бота по токену ----------

@router.callback_query(F.data == "m:token")
async def token_start(call: CallbackQuery, state: FSMContext) -> None:
    if await db.get_mirror_by_owner(call.from_user.id) or await db.get_application(call.from_user.id):
        await call.answer("У вас уже есть зеркало или заявка", show_alert=True)
        return
    await state.set_state(UserStates.waiting_token)
    await show(call, texts.HOWTO_BOTFATHER, kb([[("⬅️ Назад", "m:home")]]))


@router.message(UserStates.waiting_token, F.chat.type == "private")
async def token_received(message: Message, state: FSMContext, bot: Bot) -> None:
    raw = (message.text or "").strip()
    try:
        await message.delete()  # токен не должен оставаться в переписке
    except Exception:
        pass
    if not TOKEN_RE.match(raw):
        await message.answer("Это не похоже на токен. Он выглядит как <code>123456789:AA…</code>. "
                             "Пришлите ещё раз или /cancel.")
        return
    try:
        me = await tg_raw.get_me(raw)
    except tg_raw.TgError:
        await message.answer("Telegram не принял этот токен. Проверьте его и пришлите заново.")
        return
    if me["id"] == bot.id:
        await message.answer("Это токен основного бота, нужен токен отдельного бота.")
        return
    await state.clear()
    await _submit(bot, message.from_user.id, me, raw, "token",
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
            warn = "" if status == "administrator" else "\n⚠️ Бот не админ: без прав писать в чат он не сможет."
            await notify_owners(
                bot, f"➕ Бот добавлен в «{esc(chat.title)}» ({chat.id}).{warn}\n"
                     "Включите группу: /adm → Группы.")
    elif status in ("left", "kicked"):
        group = await db.get_group_by_chat(chat.id)
        if group and group["is_active"]:
            await db.set_group_active(group["id"], False)
            await notify_owners(bot, f"➖ Бота убрали из «{esc(chat.title)}». Группа отключена.")
