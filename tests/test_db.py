"""Проверка слоя данных без Telegram: python3 tests/test_db.py"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OWNER_IDS", "1")

import config  # noqa: E402
import db  # noqa: E402


async def main() -> None:
    config.OWNER_IDS[:] = [1]
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    await db.init(path)

    assert await db.get_role(1) == "owner"
    assert not await db.remove_owner(1)          # стартового владельца не убрать
    await db.add_owner(2, "moderator")
    assert await db.get_role(2) == "moderator"

    # зеркала: одно на пользователя и один бот один раз
    m1 = await db.create_mirror(10, 1001, "a_bot", "enc")
    assert m1
    assert await db.create_mirror(10, 1002, "b_bot", "enc") is None   # тот же владелец
    assert await db.create_mirror(11, 1001, "c_bot", "enc") is None   # тот же бот
    m2 = await db.create_mirror(11, 1003, "d_bot", "enc")
    assert m2 and (await db.get_mirror(m1))["slots"] == config.DEFAULT_SLOTS

    # админы зеркала: максимум 3 вместе с создателем
    assert await db.add_mirror_admin(m1, 20) == "ok"
    assert await db.add_mirror_admin(m1, 20) == "exists"
    assert await db.add_mirror_admin(m1, 10) == "owner"
    assert await db.add_mirror_admin(m1, 21) == "ok"
    assert await db.add_mirror_admin(m1, 22) == "limit"
    assert await db.is_mirror_staff(m1, 21) and not await db.is_mirror_staff(m1, 22)
    assert (await db.mirror_for_staff(21))["id"] == m1
    assert sorted(await db.mirror_staff_ids(m1)) == [10, 20, 21]

    # слоты не выше потолка
    assert await db.set_mirror_slots(m1, 99) == config.MAX_SLOTS
    assert await db.set_mirror_slots(m1, 0) == 1
    await db.set_mirror_slots(m1, 3)

    # замена бота отозванного зеркала: посты и слоты сохраняются, занятый бот не принимается
    await db.set_mirror_status(m2, "revoked")
    assert not await db.replace_mirror_bot(m2, 1001, "x_bot", "enc2")      # 1001 занят зеркалом m1
    assert await db.replace_mirror_bot(m2, 1099, "new_bot", "enc2")
    row = await db.get_mirror(m2)
    assert row["bot_id"] == 1099 and row["status"] == "active" and row["owner_id"] == 11

    # группы и посты
    g = await db.add_group(-100500, "Чат", active=True)
    g2 = await db.add_group(-100501, "Чат 2", active=False)
    assert [r["id"] for r in await db.list_groups(active_only=True)] == [g]
    p = await db.create_post(m1, g, 10, "Реклама", -100999, 5, None, pending=False)
    due = await db.due_posts()
    assert [d["id"] for d in due] == [p] and due[0]["group_chat_id"] == -100500
    iv = await db.effective_interval(due[0]["group_interval"])
    assert iv == config.DEFAULT_INTERVAL_MINUTES
    await db.mark_sent(p, iv, g, m1)
    assert await db.due_posts() == []
    assert (await db.get_post(p))["sent_count"] == 1

    # выключенная группа и заблокированное зеркало не отправляют
    await db.delay_post(p, -5)
    assert len(await db.due_posts()) == 1
    await db.set_group_active(g, False)
    assert await db.due_posts() == []
    await db.set_group_active(g, True)
    await db.set_mirror_status(m1, "blocked")
    assert await db.due_posts() == []
    await db.set_mirror_status(m1, "active")

    # модерация: пост ждёт одобрения, отчёт стартует с одобрения
    pp = await db.create_post(m1, g2, 10, "На модерации", -100999, 6, None, pending=True)
    assert (await db.get_post(pp))["started_at"] is None
    await db.set_post_status(pp, "active")
    assert (await db.get_post(pp))["started_at"] is not None

    # бан блокирует зеркало, разбан возвращает
    await db.ban_user(11)
    assert (await db.get_mirror(m2))["status"] == "blocked"
    await db.unban_user(11)
    assert (await db.get_mirror(m2))["status"] == "active"

    # ошибки группы
    assert await db.group_fail(g) == 1 and await db.group_fail(g) == 2
    await db.group_ok(g)
    assert await db.group_fail(g) == 1

    stats = await db.overall_stats()
    assert stats["mirrors"] == 2 and stats["posts_active"] == 2

    # удаление зеркала каскадом удаляет админов и посты
    await db.delete_mirror(m1)
    assert await db.list_posts(m1) == [] and await db.list_mirror_admins(m1) == []

    # снимок базы
    snap = path + ".snap"
    db.snapshot(snap)
    assert os.path.getsize(snap) > 0
    print("db: все проверки пройдены")


asyncio.run(main())
