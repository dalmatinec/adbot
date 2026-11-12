"""Логика планировщика на заглушке aiogram: python3 -m tests.test_scheduler"""

import asyncio
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
config.OWNER_IDS[:] = [1]
config.SEND_GAP_SECONDS = 0.05
config.GROUP_GAP_SECONDS = 0.2
config.STORAGE_CHAT_ID = -100999
import db  # noqa: E402
import scheduler  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter  # noqa: E402


class FakeBot:
    def __init__(self):
        self.forwards, self.messages, self.fail, self.times = [], [], None, []

    async def forward_message(self, **kw):
        if self.fail:
            raise self.fail
        self.forwards.append(kw)
        self.times.append(time.monotonic())
        return type("Sent", (), {"message_id": 777})()

    async def forward_messages(self, **kw):
        if self.fail:
            raise self.fail
        self.forwards.append(kw)
        self.times.append(time.monotonic())
        return [type("Sent", (), {"message_id": 800 + i})() for i in range(len(kw["message_ids"]))]

    async def send_message(self, uid, text, **kw):
        self.messages.append((uid, text))


async def main() -> None:
    await db.init(os.path.join(tempfile.mkdtemp(), "t.db"))
    cab = await db.create_cabinet("Магазин")
    await db.use_invite(await db.create_invite(cab, "owner"), 10)
    g = await db.add_group(-100500, "Чат")
    await db.set_group_flag(g, "is_active", True)
    p = await db.create_post(cab, g, 10, "Реклама", -100999, [5])
    bot = FakeBot()

    # пересылка как есть из хранилища
    assert await scheduler.tick(bot) == 1
    f = bot.forwards[0]
    assert f == {"chat_id": -100500, "from_chat_id": -100999, "message_id": 5}
    assert (await db.get_post(p))["sent_count"] == 1
    assert await scheduler.tick(bot) == 0            # до следующего интервала не шлёт

    # альбом пересылается одним вызовом, три поста в одну группу идут с паузой
    await db.set_cabinet_slots(cab, 5)
    p2 = await db.create_post(cab, g, 10, "Альбом", -100999, [7, 8, 9])
    p3 = await db.create_post(cab, g, 10, "Ещё", -100999, [11])
    await db.delay_post(p, -1)
    bot.times.clear()
    assert await scheduler.tick(bot) == 3
    assert any(f.get("message_ids") == [7, 8, 9] for f in bot.forwards)
    gaps = [b - a for a, b in zip(bot.times, bot.times[1:])]
    assert all(gap >= config.GROUP_GAP_SECONDS * 0.9 for gap in gaps), gaps
    await db.delete_post(p2)
    await db.delete_post(p3)

    # flood-wait: пост откладывается, ошибкой не считается
    await db.delay_post(p, -1)
    bot.fail = TelegramRetryAfter(1)
    assert await scheduler.tick(bot) == 0
    assert (await db.get_post(p))["error_count"] == 0

    # три ошибки подряд (бот выгнан) отключают группу и зовут владельцев
    bot.fail = TelegramForbiddenError("Forbidden: bot was kicked from the supergroup chat")
    for _ in range(config.GROUP_ERRORS_BEFORE_DISABLE):
        await db.delay_post(p, -1)
        await scheduler.tick(bot)
    assert not (await db.get_group(g))["is_active"]
    assert any("отключена" in t for _, t in bot.messages)

    # пост пропал из хранилища: пауза поста, группа не страдает, партнёр предупреждён
    await db.set_group_flag(g, "is_active", True)
    await db.delay_post(p, -1)
    bot.fail = TelegramBadRequest("Bad Request: message to forward not found")
    await scheduler.tick(bot)
    assert (await db.get_post(p))["status"] == "paused"
    assert (await db.get_group(g))["is_active"]
    assert any(uid == 10 and "пауз" in t for uid, t in bot.messages)

    # ошибка самого поста группу не отключает
    await db.set_post_status(p, "active")
    bot.fail = TelegramBadRequest("Bad Request: something about this message")
    for _ in range(5):
        await db.delay_post(p, -1)
        await scheduler.tick(bot)
    assert (await db.get_group(g))["is_active"]

    print("scheduler: все проверки пройдены")


asyncio.run(main())
