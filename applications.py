"""Заявки на создание зеркала.

Чтобы зеркала не плодили все подряд, новое зеркало сначала попадает в заявку:
владельцам и модераторам приходит карточка с данными человека и кнопками
«Принять» / «Отклонить». Пока решения нет, токен лежит в базе зашифрованным
и бот зеркала не запускается. Отклонили или никто не ответил за
APPLICATION_TTL_HOURS - заявка удаляется вместе с токеном.
"""

import asyncio
import logging

import config
import crypto
import db
from notify import notify_owners
from ui import esc, fmt_dt, kb, user_link

log = logging.getLogger(__name__)

SOURCE_TEXT = {"quick": "создан быстро (случайные имя и юзернейм)", "token": "подключён по токену"}


def card_text(app, user) -> str:
    name = user["full_name"] if user else None
    uname = f"@{esc(user['username'])}" if user and user["username"] else "—"
    first_seen = fmt_dt(user["first_seen"]) if user else "—"
    return (
        "📥 <b>Заявка на зеркало</b>\n\n"
        f"Пользователь: {user_link(app['user_id'], name)}\n"
        f"ID: <code>{app['user_id']}</code>\n"
        f"Username: {uname}\n"
        f"В боте с: {first_seen}\n"
        f"Заявка подана: {fmt_dt(app['created_at'])}\n"
        f"Бот: @{esc(app['username'])}, {SOURCE_TEXT.get(app['source'], app['source'])}\n"
        f"Истекает: {fmt_dt(app['expires_at'])} (потом отклонится само)"
    )


def card_buttons(user_id: int):
    return kb([[("✅ Принять", f"a:ap:{user_id}"), ("❌ Отклонить", f"a:ar:{user_id}")]])


async def submit(bot, owner_id: int, me: dict, token: str, source: str,
                 user_info: dict | None = None) -> str:
    """Создаёт заявку и зовёт владельцев. Возвращает статус из
    db.create_application."""
    if user_info:
        await db.touch_user(owner_id, user_info.get("full_name"), user_info.get("username"))
    result = await db.create_application(
        owner_id, me["id"], me.get("username"), crypto.encrypt(token), source)
    if result != "ok":
        return result
    app = await db.get_application(owner_id)
    user = await db.get_user(owner_id)
    await notify_owners(bot, card_text(app, user), card_buttons(owner_id))
    await _tell_user(
        bot, owner_id,
        f"📨 Заявка на зеркало @{esc(me.get('username'))} отправлена владельцам. "
        f"Ответ придёт сюда. Если за {config.APPLICATION_TTL_HOURS} ч ответа не будет, "
        "заявка отклонится сама, и её можно подать заново.")
    return "ok"


async def approve(bot, manager, user_id: int) -> str | None:
    """Принимает заявку и запускает зеркало. Возвращает @username или None,
    если заявки уже нет."""
    app = await db.get_application(user_id)
    if not app:
        return None
    mid = await db.approve_application(user_id)
    if mid is None:
        return None
    await manager.start(await db.get_mirror(mid))
    await _tell_user(
        bot, user_id,
        f"✅ Заявка одобрена. Зеркало @{esc(app['username'])} готово: откройте его и нажмите /start, "
        "там настраивается рассылка.",
        kb([[("🪞 Открыть зеркало", f"https://t.me/{app['username']}")]]))
    return app["username"]


async def reject(bot, user_id: int, reason: str = "отклонена владельцами") -> bool:
    """Удаляет заявку вместе с токеном и сообщает пользователю."""
    app = await db.get_application(user_id)
    if not app:
        return False
    await db.delete_application(user_id)
    await _tell_user(
        bot, user_id,
        f"❌ Заявка на зеркало {reason}. Данные бота у нас удалены. "
        "При необходимости можно подать новую.")
    return True


async def expire_once(bot) -> int:
    n = 0
    for app in await db.expired_applications():
        user = await db.get_user(app["user_id"])
        if await reject(bot, app["user_id"],
                        f"не рассмотрена за {config.APPLICATION_TTL_HOURS} ч и отклонена"):
            n += 1
            await notify_owners(
                bot, f"⌛ Заявка {user_link(app['user_id'], user['full_name'] if user else None)} "
                     f"на @{esc(app['username'])} истекла и отклонена, токен удалён.")
    return n


async def run_expiry(bot, every_seconds: int = 600) -> None:
    while True:
        try:
            await expire_once(bot)
        except Exception:
            log.exception("Ошибка очистки заявок")
        await asyncio.sleep(every_seconds)


async def _tell_user(bot, user_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=markup)
    except Exception:
        pass
