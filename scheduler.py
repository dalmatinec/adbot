"""Фоновая рассылка. Каждый активный пост пересылается в свою группу раз в
интервал (свой у группы или общий), интервал задают только владельцы бота.

Отправки идут строго по одной. Между любыми двумя отправками пауза не меньше
SEND_GAP_SECONDS, между постами в одну группу не меньше GROUP_GAP_SECONDS,
даже если десятки постов подошли одновременно.
"""

import asyncio
import logging
import time

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import config
import db
import notify
import posts

log = logging.getLogger(__name__)

_last_any = 0.0
_last_group: dict[int, float] = {}

_GONE = ("message to forward not found", "message_id_invalid", "messages to forward not found",
         "message to copy not found")


_GROUP_FAULT = ("forbidden", "chat not found", "not enough rights", "have no rights",
                "chat_write_forbidden", "administrator", "kicked", "not a member")


async def _wait_turn(group_id: int) -> None:
    global _last_any
    now = time.monotonic()
    wait = max(_last_any + config.SEND_GAP_SECONDS - now,
               _last_group.get(group_id, 0.0) + config.GROUP_GAP_SECONDS - now)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_any = _last_group[group_id] = time.monotonic()


async def tick(bot) -> int:
    """Один проход: отправляет всё, что пора. Возвращает число успешных отправок."""
    sent = 0
    for post in await db.due_posts():
        await _wait_turn(post["group_id"])
        try:
            await posts.send_post(bot, post["group_chat_id"], post)
        except TelegramRetryAfter as exc:
            pause = int(exc.retry_after) + 1
            await db.delay_post(post["id"], pause)
            await asyncio.sleep(min(pause, 30))
            continue
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            await _on_error(bot, post, str(exc))
            continue
        except Exception as exc:  # сеть и прочее: к группе претензий нет
            log.exception("Сбой отправки поста %s", post["id"])
            await db.mark_failed(post["id"], str(exc), retry_seconds=120)
            continue
        await db.mark_sent(post["id"], await db.effective_interval(post["group_interval"]))
        await db.group_ok(post["group_id"])
        sent += 1
    return sent


async def _on_error(bot, post, error: str) -> None:
    await db.mark_failed(post["id"], error)
    if any(marker in error.lower() for marker in _GONE):
        # поста больше нет в хранилище: рассылать нечего
        await db.set_post_status(post["id"], "paused")
        text = (f"⚠️ Рассылка #{post['id']} поставлена на паузу: пост не найден в хранилище. "
                "Замените пост в карточке рассылки.")
        await notify.cabinet(bot, post["cabinet_id"], text)
        await notify.owners(bot, f"⚠️ Рассылка #{post['id']} кабинета <b>{post['cabinet_name']}</b> "
                                 "поставлена на паузу: пост не найден в хранилище.")
        return
    if not any(marker in error.lower() for marker in _GROUP_FAULT):
        return  # дело в самом посте, группа ни при чём: повтор позже
    streak = await db.group_fail(post["group_id"])
    if streak >= config.GROUP_ERRORS_BEFORE_DISABLE:
        await db.set_group_flag(post["group_id"], "is_active", False)
        await notify.owners(
            bot, f"⚠️ Группа <b>{post['group_title']}</b> отключена после {streak} ошибок подряд.\n"
                 f"Причина: {error}\n\n"
                 "Проверьте, что бот состоит в группе и может отправлять сообщения, "
                 "затем включите группу в админке.")


async def run_scheduler(bot) -> None:
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(config.SCHEDULER_TICK_SECONDS)
