"""Логика планировщика на заглушке aiogram: python3 tests/test_scheduler.py"""

import asyncio
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
config.SEND_DELAY_SECONDS = 0
config.OWNER_IDS[:] = [1]
import db  # noqa: E402
import scheduler  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter  # noqa: E402


class FakeBot:
    def __init__(self):
        self.copies, self.messages, self.fail = [], [], None

    async def copy_message(self, **kw):
        if self.fail:
            raise self.fail
        self.copies.append(kw)

    async def send_message(self, uid, text, **kw):
        self.messages.append((uid, text))


async def main() -> None:
    await db.init(os.path.join(tempfile.mkdtemp(), "t.db"))
    m = await db.create_mirror(10, 1001, "a_bot", "enc")
    g = await db.add_group(-100500, "Чат", active=True)
    btn = json.dumps([[{"text": "Сайт", "url": "https://example.com"}]])
    p = await db.create_post(m, g, 10, "Реклама", -100999, 5, btn, pending=False)
    bot = FakeBot()

    # обычная отправка: пост копируется из хранилища, кнопки сохраняются
    assert await scheduler.tick(bot) == 1
    c = bot.copies[0]
    assert c["chat_id"] == -100500 and c["from_chat_id"] == -100999 and c["message_id"] == 5
    assert c["reply_markup"].inline_keyboard[0][0].url == "https://example.com"
    post = await db.get_post(p)
    assert post["sent_count"] == 1 and post["next_send_at"] > db.now()

    # до следующего интервала повторно не шлёт
    assert await scheduler.tick(bot) == 0

    # flood-wait: пост откладывается, ошибкой не считается
    await db.delay_post(p, -1)
    bot.fail = TelegramRetryAfter(1)
    assert await scheduler.tick(bot) == 0
    assert (await db.get_post(p))["error_count"] == 0

    # три ошибки подряд (бот выгнан) отключают группу и зовут владельцев
    bot.fail = TelegramForbiddenError("bot was kicked")
    for _ in range(config.GROUP_ERRORS_BEFORE_DISABLE):
        await db.delay_post(p, -1)
        await scheduler.tick(bot)
    assert not (await db.get_group(g))["is_active"]
    assert any("отключена" in t for _, t in bot.messages)
    assert (await db.get_post(p))["error_count"] == config.GROUP_ERRORS_BEFORE_DISABLE

    # пост пропал из хранилища: пауза поста, группа не страдает
    await db.set_group_active(g, True)
    await db.delay_post(p, -1)
    bot.fail = TelegramBadRequest("Bad Request: message to copy not found")
    await scheduler.tick(bot)
    assert (await db.get_post(p))["status"] == "paused"
    assert (await db.get_group(g))["is_active"]

    print("scheduler: все проверки пройдены")


asyncio.run(main())
