"""Заявки на создание зеркала.

Чтобы зеркала не плодили все подряд, новое зеркало сначала попадает в заявку:
владельцам и модераторам приходит карточка с данными человека и кнопками
Принять / Отклонить. Пока решения нет, токен лежит в базе зашифрованным
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

SOURCE_TEXT = {"quick": "создан быстрым способом (случайные имя и юзернейм)", "token": "подключён по токену"}


def card_text(app, user) -> str:
    name = user["full_name"] if user else None
    uname = f"@{esc(user['username'])}" if user and user["username"] else "нет"
    first_seen = fmt_dt(user["first_seen"]) if user else "нет"
    return (
        "<b>Заявка на зеркало</b>\n\n"
        f"Пользователь: {user_link(app['user_id'], name)}\n"
        f"ID: <code>{app['user_id']}</code>\n"
        f"Username: {uname}\n"
        f"В боте с: {first_seen}\n"
        f"Заявка подана: {fmt_dt(app['created_at'])}\n"
        f"Бот: @{esc(app['username'])}, {SOURCE_TEXT.get(app['source'], app['source'])}\n"
        f"Срок рассмотрения до: {fmt_dt(app['expires_at'])} (по истечении заявка будет отклонена автоматически)"
    )


def card_buttons(user_id: int):
    return kb([[("✅ Принять", f"a:ap:{user_id}"), ("❌ Отклонить", f"a:ar:{user_id}")]])


async def submit(bot, manager, owner_id: int, me: dict, token: str, source: str,
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
    await manager.start_pending(app)
    await notify_owners(bot, card_text(app, user), card_buttons(owner_id))
    await _tell_user(
        bot, owner_id,
        f"Заявка на зеркало @{esc(me.get('username'))} направлена владельцам. "
        f"Ответ будет отправлен в этот чат. Если в течение {config.APPLICATION_TTL_HOURS} ч решение не будет принято, "
        "заявка будет отклонена автоматически, после чего её можно подать повторно.")
    return "ok"


async def approve(bot, manager, user_id: int) -> str | None:
    """Принимает заявку и запускает зеркало. Возвращает @username или None,
    если заявки уже нет."""
    app = await db.get_application(user_id)
    if not app:
        return None
    mid = await db.approve_application(user_id)
    await manager.stop_pending(user_id)
    if mid is None:
        return None
    await manager.start(await db.get_mirror(mid))
    await _tell_user(
        bot, user_id,
        f"Заявка одобрена. Зеркало @{esc(app['username'])} подключено. "
        "Откройте его и нажмите Старт для настройки рассылки.",
        kb([[("🪞 Открыть зеркало", f"https://t.me/{app['username']}")]]))
    return app["username"]


async def reject(bot, user_id: int, reason: str = "отклонена владельцами", manager=None) -> bool:
    """Удаляет заявку вместе с токеном и сообщает пользователю."""
    app = await db.get_application(user_id)
    if not app:
        return False
    await db.delete_application(user_id)
    if manager is not None:
        await manager.stop_pending(user_id)
    await _tell_user(
        bot, user_id,
        f"Заявка на зеркало {reason}. Данные бота удалены. "
        "При необходимости можно подать новую заявку.")
    return True


async def expire_once(bot, manager=None) -> int:
    n = 0
    for app in await db.expired_applications():
        user = await db.get_user(app["user_id"])
        if await reject(bot, app["user_id"],
                        f"отклонена автоматически: решение не принято в течение {config.APPLICATION_TTL_HOURS} ч", manager):
            n += 1
            await notify_owners(
                bot, f"Заявка {user_link(app['user_id'], user['full_name'] if user else None)} "
                     f"на @{esc(app['username'])} отклонена автоматически по истечении срока, токен удалён.")
    return n


async def run_expiry(bot, manager, every_seconds: int = 600) -> None:
    while True:
        try:
            await expire_once(bot, manager)
        except Exception:
            log.exception("Ошибка очистки заявок")
        await asyncio.sleep(every_seconds)


async def _tell_user(bot, user_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=markup)
    except Exception:
        pass
