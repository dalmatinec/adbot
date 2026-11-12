"""Проверка базы: python3 -m tests.test_db"""

import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
config.OWNER_IDS[:] = [1]
import db  # noqa: E402


async def main() -> None:
    await db.init(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert db.is_owner(1) and not db.is_known(50)

    # кабинет и вход по одноразовой ссылке
    cab = await db.create_cabinet("Магазин")
    token = await db.create_invite(cab, "owner")
    assert (await db.use_invite("wrong", 50))[0] == "bad"
    status, cabinet, prev = await db.use_invite(token, 50)
    assert status == "ok" and cabinet["name"] == "Магазин" and prev is None
    assert db.cabinet_of(50) == cab
    assert (await db.use_invite(token, 51))[0] == "bad"          # ссылка сгорела

    # новая ссылка той же роли отменяет прежнюю
    t1 = await db.create_invite(cab, "helper")
    t2 = await db.create_invite(cab, "helper")
    assert (await db.use_invite(t1, 60))[0] == "bad"
    assert (await db.use_invite(t2, 60))[0] == "ok" and db.cabinet_of(60) == cab

    # человек из одного кабинета не может войти в другой
    cab2 = await db.create_cabinet("Другой")
    t3 = await db.create_invite(cab2, "owner")
    assert (await db.use_invite(t3, 50))[0] == "busy"

    # группы: скрытая не видна партнёру, видна при all_groups
    g1 = await db.add_group(-1001, "Открытая")
    g2 = await db.add_group(-1002, "Скрытая")
    for g in (g1, g2):
        await db.set_group_flag(g, "is_active", True)
    await db.set_group_flag(g1, "is_public", True)
    c = await db.get_cabinet(cab)
    assert [g["id"] for g in await db.groups_for_cabinet(c)] == [g1]
    await db.toggle_cabinet_all_groups(cab)
    assert len(await db.groups_for_cabinet(await db.get_cabinet(cab))) == 2

    # пост, очередь, интервал
    p = await db.create_post(cab, g1, 50, "Реклама", -100999, [5, 6])
    assert [x["id"] for x in await db.due_posts()] == [p]
    await db.mark_sent(p, 2)
    assert await db.due_posts() == []
    post = await db.get_post(p)
    assert post["sent_count"] == 1 and post["storage_ids"] == "5,6"
    assert await db.effective_interval(None) == config.DEFAULT_INTERVAL_HOURS
    await db.set_setting("interval_hours", "4")
    assert await db.effective_interval(None) == 4 and await db.effective_interval(1) == 1

    # заморозка кабинета и остановка поста убирают его из очереди
    await db.delay_post(p, -1)
    await db.set_cabinet_status(cab, "frozen")
    assert await db.due_posts() == []
    await db.set_cabinet_status(cab, "active")
    await db.set_post_status(p, "stopped")
    assert await db.due_posts() == []
    await db.set_post_status(p, "active")
    assert len(await db.due_posts()) == 1

    # смена владельца: рассылки остаются, прежний теряет доступ
    t4 = await db.create_invite(cab, "owner")
    status, _, prev = await db.use_invite(t4, 70)
    assert status == "ok" and prev == 50
    assert db.cabinet_of(70) == cab and db.cabinet_of(50) is None
    assert await db.count_posts(cab) == 1

    # слоты в пределах 1..MAX
    assert await db.set_cabinet_slots(cab, 99) == config.MAX_SLOTS
    assert await db.set_cabinet_slots(cab, 0) == 1

    # последнего владельца бота убрать нельзя
    assert not await db.remove_owner(1)
    await db.add_owner(2)
    assert await db.remove_owner(2)

    # удаление кабинета уносит посты и доступы
    await db.delete_cabinet(cab)
    assert await db.count_all_posts() == 0 and db.cabinet_of(70) is None

    print("db: все проверки пройдены")


asyncio.run(main())
