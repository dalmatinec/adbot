"""Фоновая отправка рекламы. Все рассылки делает основной бот: он состоит в
группах админом и копирует пост из канала-хранилища (copy_message), поэтому
в группе нет пометки о пересылке, а посты не зависят от зеркал.

Каждый активный пост уходит в свою группу раз в интервал (интервал группы
или общий). Отправки идут по одной с паузой, чтобы не упереться в лимиты
Telegram для групп (около 20 сообщений в минуту).
"""

import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import config
import db
import posts
from notify import notify_owners

log = logging.getLogger(__name__)


async def tick(bot) -> int:
    """Один проход: отправляет всё, что пора. Возвращает число успешных отправок."""
    sent = 0
    for post in await db.due_posts():
        interval = await db.effective_interval(post["group_interval"])
        ids = (post["id"], interval, post["group_id"], post["mirror_id"])
        try:
            await bot.copy_message(
                chat_id=post["group_chat_id"],
                from_chat_id=post["storage_chat_id"],
                message_id=post["storage_message_id"],
                reply_markup=posts.buttons_markup(post["buttons"]),
            )
        except TelegramRetryAfter as exc:
            await db.delay_post(post["id"], int(exc.retry_after) + 1)
            await asyncio.sleep(min(int(exc.retry_after), 30))
            continue
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            await db.mark_failed(*ids, str(exc))
            await _handle_send_error(bot, post, str(exc))
            continue
        except Exception as exc:  # сеть и прочее: к группе претензий нет
            log.exception("Сбой отправки поста %s", post["id"])
            await db.mark_failed(*ids, str(exc))
            continue
        await db.mark_sent(*ids)
        await db.group_ok(post["group_id"])
        sent += 1
        await asyncio.sleep(config.SEND_DELAY_SECONDS)
    return sent


async def _handle_send_error(bot, post, error: str) -> None:
    low = error.lower()
    if "message to copy not found" in low or "message_id_invalid" in low:
        # пост удалили из хранилища: ставим на паузу, чтобы не долбить впустую
        await db.set_post_status(post["id"], "paused")
        await notify_owners(
            bot, f"Пост #{post['id']} ({post['title']}) не найден в хранилище и поставлен на паузу.")
        return
    streak = await db.group_fail(post["group_id"])
    if streak >= config.GROUP_ERRORS_BEFORE_DISABLE:
        await db.set_group_active(post["group_id"], False)
        await notify_owners(
            bot, f"Группа {post['group_title']} отключена после {streak} ошибок подряд: {error}\n"
                 "Проверьте, что бот состоит в чате и имеет право отправлять сообщения, затем включите группу в /adm.")


async def run_scheduler(bot) -> None:
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(config.SCHEDULER_TICK_SECONDS)
