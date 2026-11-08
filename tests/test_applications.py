"""Заявки на зеркала: python3 tests/test_applications.py"""

import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

from cryptography.fernet import Fernet  # noqa: E402

import config  # noqa: E402
config.OWNER_IDS[:] = [1]
config.ENCRYPTION_KEY = Fernet.generate_key().decode()
import applications  # noqa: E402
import crypto  # noqa: E402
import db  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, reply_markup=None, **kw):
        self.sent.append((uid, text, reply_markup))

    def to(self, uid):
        return [t for u, t, _ in self.sent if u == uid]


class FakeManager:
    def __init__(self):
        self.started = []

    async def start(self, mirror):
        self.started.append(mirror["id"])


async def main() -> None:
    await db.init(os.path.join(tempfile.mkdtemp(), "t.db"))
    await db.add_owner(2, "moderator")
    bot, mgr = FakeBot(), FakeManager()
    me = {"id": 1001, "username": "ads_x_bot"}

    # заявка: владелец и модератор получают карточку с кнопками, пользователь - подтверждение
    r = await applications.submit(bot, 10, me, "123456:SECRET_TOKEN", "quick",
                                  {"full_name": "Иван <b>", "username": "ivan"})
    assert r == "ok"
    for admin in (1, 2):
        text = bot.to(admin)[0]
        assert "tg://user?id=10" in text and "<code>10</code>" in text and "@ivan" in text
        assert "&lt;b&gt;" in text  # имя экранировано
    buttons = [b.callback_data for u, _, m in bot.sent if u == 1 and m for row in m.inline_keyboard for b in row]
    assert buttons == ["a:ap:10", "a:ar:10"]
    assert "отправлена" in bot.to(10)[0]

    # токен в базе только в зашифрованном виде
    app = await db.get_application(10)
    assert "SECRET_TOKEN" not in app["token_enc"]
    assert crypto.decrypt(app["token_enc"]) == "123456:SECRET_TOKEN"
    # до решения зеркала нет
    assert await db.get_mirror_by_owner(10) is None

    # повторная заявка и тот же бот отсекаются
    assert await applications.submit(bot, 10, {"id": 1002, "username": "b_bot"}, "t", "token") == "has_app"
    assert await applications.submit(bot, 11, me, "t", "token") == "bot_taken"

    # принять: создаётся зеркало, запускается, заявка и токен заявки удалены
    assert await applications.approve(bot, mgr, 10) == "ads_x_bot"
    mirror = await db.get_mirror_by_owner(10)
    assert mirror and mgr.started == [mirror["id"]]
    assert await db.get_application(10) is None
    assert any("одобрена" in t for t in bot.to(10))
    assert await applications.approve(bot, mgr, 10) is None      # второй раз - уже нет

    # у владельца зеркала новая заявка не принимается
    assert await applications.submit(bot, 10, {"id": 1003, "username": "c_bot"}, "t", "token") == "has_mirror"

    # отклонить: заявка и токен удалены, человеку сообщили
    await applications.submit(bot, 12, {"id": 1004, "username": "d_bot"}, "t", "token")
    assert await applications.reject(bot, 12)
    assert await db.get_application(12) is None
    assert any("отклонена" in t for t in bot.to(12))
    assert not await applications.reject(bot, 12)
    # после отклонения можно подать заново
    assert await applications.submit(bot, 12, {"id": 1004, "username": "d_bot"}, "t", "token") == "ok"

    # без ответа заявка отклоняется сама, владельцам сообщают
    await db.execute("UPDATE applications SET expires_at=? WHERE user_id=12", ("2000-01-01T00:00:00+00:00",))
    before = len(bot.to(1))
    assert await applications.expire_once(bot) == 1
    assert await db.get_application(12) is None
    assert any("отклонена" in t for t in bot.to(12))
    assert len(bot.to(1)) == before + 1 and "истекла" in bot.to(1)[-1]
    assert await applications.expire_once(bot) == 0

    # свежие заявки не трогаются, бан удаляет заявку
    await applications.submit(bot, 13, {"id": 1005, "username": "e_bot"}, "t", "token")
    assert await applications.expire_once(bot) == 0
    await db.ban_user(13)
    assert await db.get_application(13) is None
    assert await db.count_applications() == 0

    print("applications: все проверки пройдены")


asyncio.run(main())
