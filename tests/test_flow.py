"""Сквозной сценарий на заглушке aiogram: обработчики вызываются напрямую.
python3 -m tests.test_flow"""

import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
config.OWNER_IDS[:] = [1]
config.STORAGE_CHAT_ID = -100999
config.SEND_GAP_SECONDS = config.GROUP_GAP_SECONDS = 0
config.CLICK_GAP_SECONDS = 0
import db  # noqa: E402
import admin  # noqa: E402
import look  # noqa: E402
import report  # noqa: E402
import ui  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
import cabinet  # noqa: E402
import main as _main  # noqa: E402,F401  проверка, что точка входа импортируется
import scheduler  # noqa: E402
from middlewares import GuardMiddleware  # noqa: E402
from aiogram import types  # noqa: E402
from aiogram.filters import CommandObject  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402

cabinet.ALBUM_WAIT_SECONDS = 0.05
BAD = ("«", "»", "—", "–")


class FakeBot:
    def __init__(self):
        self.sent, self.forwards, self.deleted, self._id = [], [], [], 500

    def _check(self, text):
        assert not any(ch in text for ch in BAD), text

    chats = {-1009: ("supergroup", "Новая группа", "administrator"),
             -1008: ("supergroup", "Без прав", "member"), -1007: ("channel", "Канал", "administrator")}

    async def me(self):
        return type("Me", (), {"username": "main_bot", "id": 999})()

    async def get_chat(self, chat_id):
        if chat_id not in self.chats:
            raise TelegramBadRequest("Bad Request: chat not found")
        kind, title, _ = self.chats[chat_id]
        return type("Chat", (), {"id": chat_id, "type": kind, "title": title})()

    async def get_chat_member(self, chat_id, user_id):
        return type("Member", (), {"status": self.chats[chat_id][2]})()

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self._check(text)
        self.sent.append((chat_id, text))

    async def forward_message(self, chat_id, from_chat_id, message_id):
        self._id += 1
        self.forwards.append((chat_id, from_chat_id, [message_id]))
        return type("M", (), {"message_id": self._id})()

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        self.forwards.append((chat_id, from_chat_id, list(message_ids)))
        out = []
        for _ in message_ids:
            self._id += 1
            out.append(type("M", (), {"message_id": self._id})())
        return out

    async def delete_messages(self, chat_id, message_ids):
        self.deleted.append((chat_id, list(message_ids)))

    async def copy_message(self, **kw):
        self.sent.append((kw["chat_id"], "copy"))

    async def leave_chat(self, chat_id):
        self.sent.append((chat_id, "left"))


BOT = FakeBot()
SCREEN: dict[int, tuple] = {}     # user_id -> (текст, кнопки) последнего экрана
_mid = 100


class User:
    def __init__(self, uid):
        self.id, self.is_bot, self.full_name, self.username = uid, False, f"Имя {uid}", f"user{uid}"


class Msg(types.Message):
    def __init__(self, uid, text=None, media_group_id=None, photo=None, caption=None, chat_type="private"):
        global _mid
        _mid += 1
        self.message_id = _mid
        self.from_user = User(uid)
        self.chat = type("Chat", (), {"id": uid, "type": chat_type})()
        self.text, self.caption, self.media_group_id, self.photo = text, caption, media_group_id, photo
        self.html_text, self.entities = text, None
        self.video = self.animation = self.document = self.audio = self.voice = self.sticker = None

    async def answer(self, text, reply_markup=None):
        BOT._check(text)
        SCREEN[self.chat.id] = (text, reply_markup)

    async def edit_text(self, text, reply_markup=None):
        await self.answer(text, reply_markup)


class Call(types.CallbackQuery):
    def __init__(self, uid, data):
        self.from_user, self.data, self.message, self.alert = User(uid), data, Msg(uid), None

    async def answer(self, text=None, show_alert=False):
        if text:
            BOT._check(text)
            self.alert = text


def buttons(uid) -> list[str]:
    markup = SCREEN[uid][1]
    return [b.callback_data or b.url for row in markup.inline_keyboard for b in row] if markup else []


STATES: dict[int, FSMContext] = {}


def st(uid) -> FSMContext:
    return STATES.setdefault(uid, FSMContext())


async def passes(event) -> bool:
    """Пропустит ли фильтр событие до обработчика."""
    hit = []

    async def handler(ev, data):
        hit.append(1)
    await GuardMiddleware()(handler, event, {})
    return bool(hit)


async def main() -> None:
    await db.init(os.path.join(tempfile.mkdtemp(), "t.db"))
    g1 = await db.add_group(-1001, "Открытая")
    g2 = await db.add_group(-1002, "Скрытая")
    g3 = await db.add_group(-1003, "Вторая открытая")
    for g in (g1, g2, g3):
        await db.set_group_flag(g, "is_active", True)
    await db.set_group_flag(g1, "is_public", True)

    # посторонним бот не отвечает, кроме входа по ссылке; группы не слушает
    assert not await passes(Msg(50, "/start"))
    assert not await passes(Msg(50, "привет"))
    assert not await passes(Call(50, "c:home"))
    assert await passes(Msg(50, "/start abc"))
    assert not await passes(Msg(1, "текст", chat_type="supergroup"))
    assert await passes(Msg(1, "/adm"))

    # владелец создаёт кабинет и получает ссылку
    await admin.adm(Msg(1, "/adm"), st(1))
    assert "Админка" in SCREEN[1][0]
    await admin.cabinet_new(Call(1, "a:cnew"), st(1))
    await admin.cabinet_new_name(Msg(1, "Магазин Ромашка"), st(1), BOT)
    cab = (await db.list_cabinets())[0]["id"]
    token = (await db.get_invites(cab))[0]["token"]
    assert "Магазин Ромашка" in SCREEN[1][0] and "не вошёл" in SCREEN[1][0]

    # неверная ссылка: постороннему тишина
    SCREEN.pop(50, None)
    await cabinet.start(Msg(50, "/start zzz"), st(50), CommandObject("zzz"), BOT)
    assert 50 not in SCREEN and not db.is_known(50)

    # партнёр входит по ссылке и видит кабинет
    await cabinet.start(Msg(50, f"/start {token}"), st(50), CommandObject(token), BOT)
    assert db.cabinet_of(50) == cab and "Магазин Ромашка" in SCREEN[50][0]
    assert "c:new" in buttons(50) and "a:menu" not in buttons(50)
    assert any(uid == 1 and "Вход в кабинет" in t for uid, t in BOT.sent)
    assert await passes(Msg(50, "/start")) and await passes(Call(50, "c:home"))

    # новая рассылка: название, пост, подтверждение. Группа одна, выбор пропускается
    await cabinet.new_post(Call(50, "c:new"), st(50))
    assert await st(50).get_state() == cabinet.S.new_name.state and "Шаг 1" in SCREEN[50][0]
    await cabinet.new_name(Msg(50, "Скидки"), st(50))
    assert await st(50).get_state() == cabinet.S.post.state and "Шаг 2" in SCREEN[50][0]
    post_msg = Msg(50, "Скидки на всё 🔥")
    await cabinet.got_post(post_msg, st(50), BOT)
    assert BOT.forwards[-2] == (-100999, 50, [post_msg.message_id])
    assert BOT.forwards[-1][0] == 50                      # превью перед запуском
    assert "Шаг 3" in SCREEN[50][0] and "c:ok" in buttons(50)
    assert await db.count_posts(cab) == 0                 # без подтверждения ничего не создаётся
    await cabinet.new_confirm(Call(50, "c:ok"), st(50))
    posts_ = await db.list_posts(cab)
    assert len(posts_) == 1 and posts_[0]["group_id"] == g1 and posts_[0]["title"] == "Скидки"
    assert "запущена" in SCREEN[50][0]
    p1 = posts_[0]["id"]

    # планировщик пересылает из хранилища в группу
    assert await scheduler.tick(BOT) == 1
    assert BOT.forwards[-1] == (-1001, -100999, [int(posts_[0]["storage_ids"])])

    # отмена на подтверждении: рассылки нет, пост убран из хранилища
    await cabinet.new_post(Call(50, "c:new"), st(50))
    await cabinet.new_name(Msg(50, "Передумал"), st(50))
    await cabinet.got_post(Msg(50, "Текст"), st(50), BOT)
    await cabinet.new_cancel(Call(50, "c:no"), st(50), BOT)
    assert await db.count_posts(cab) == 1 and BOT.deleted[-1][0] == -100999

    # две открытые группы: альбом из трёх сообщений, выбор группы, подтверждение
    await db.set_group_flag(g3, "is_public", True)
    await cabinet.new_post(Call(50, "c:new"), st(50))
    await cabinet.new_name(Msg(50, "Альбом"), st(50))
    parts = [Msg(50, media_group_id="alb", photo=[1], caption="Альбом" if i == 0 else None) for i in range(3)]
    await asyncio.gather(*(cabinet.got_post(m, st(50), BOT) for m in parts))
    assert BOT.forwards[-1] == (-100999, 50, [m.message_id for m in parts])
    assert f"c:g:{g1}" in buttons(50) and f"c:g:{g3}" in buttons(50) and f"c:g:{g2}" not in buttons(50)
    await cabinet.pick_group(Call(50, f"c:g:{g3}"), st(50), BOT)
    assert "Вторая открытая" in SCREEN[50][0]
    await cabinet.new_confirm(Call(50, "c:ok"), st(50))
    posts_ = await db.list_posts(cab)
    assert len(posts_) == 2 and posts_[1]["group_id"] == g3 and posts_[1]["storage_ids"].count(",") == 2
    p2 = posts_[1]["id"]

    # в скрытую группу партнёр попасть не может даже подделкой кнопки
    await cabinet.new_post(Call(50, "c:new"), st(50))
    await cabinet.new_name(Msg(50, "Третий"), st(50))
    await cabinet.got_post(Msg(50, "Третий"), st(50), BOT)
    await cabinet.pick_group(Call(50, f"c:g:{g2}"), st(50), BOT)
    assert await db.count_posts(cab) == 2 and "недоступна" in SCREEN[50][0]
    await cabinet.new_cancel(Call(50, "c:no"), st(50), BOT)

    # лимит мест
    await db.set_cabinet_slots(cab, 2)
    call = Call(50, "c:new")
    await cabinet.new_post(call, st(50))
    assert call.alert and "заняты" in call.alert

    # карточка, превью, пауза и возобновление
    await cabinet.post_open(Call(50, f"c:p:{p1}"), st(50))
    assert "Рассылка #" in SCREEN[50][0] and f"c:pt:{p1}" in buttons(50)
    await cabinet.post_preview(Call(50, f"c:pv:{p1}"), BOT)
    assert BOT.forwards[-1][0] == 50
    await cabinet.post_toggle(Call(50, f"c:pt:{p1}"))
    assert (await db.get_post(p1))["status"] == "paused"
    await cabinet.post_toggle(Call(50, f"c:pt:{p1}"))
    assert (await db.get_post(p1))["status"] == "active"

    # замена поста: счётчик и группа на месте, старое убрано из хранилища
    old_ids = (await db.get_post(p1))["storage_ids"]
    await cabinet.replace_start(Call(50, f"c:rep:{p1}"), st(50))
    await cabinet.got_post(Msg(50, "Новый текст"), st(50), BOT)
    post = await db.get_post(p1)
    assert post["title"] == "Скидки" and post["sent_count"] == 1 and post["storage_ids"] != old_ids
    assert BOT.deleted[-1] == (-100999, [int(old_ids)])

    # название: задаётся вручную и переживает замену поста
    await cabinet.post_rename(Call(50, f"c:pn:{p1}"), st(50))
    await cabinet.post_rename_done(Msg(50, "Акция ноября"), st(50))
    assert (await db.get_post(p1))["title"] == "Акция ноября" and "Акция ноября" in SCREEN[50][0]
    await cabinet.replace_start(Call(50, f"c:rep:{p1}"), st(50))
    await cabinet.got_post(Msg(50, "Совсем другой текст"), st(50), BOT)
    assert (await db.get_post(p1))["title"] == "Акция ноября"

    # чужую рассылку открыть нельзя
    cab_b = await db.create_cabinet("Чужой")
    await db.use_invite(await db.create_invite(cab_b, "owner"), 60)
    call = Call(60, f"c:p:{p1}")
    await cabinet.post_open(call, st(60))
    assert call.alert == "Рассылка не найдена."

    # владелец останавливает рассылку: партнёр уведомлён и сам возобновить не может
    await admin.post_open(Call(1, f"a:p:{p1}"))
    assert "Акция ноября" in SCREEN[1][0] and "tg://user?id=50" in SCREEN[1][0]   # название партнёра и ссылка на него
    await admin.cabinet_posts(Call(1, f"a:cps:{cab}"))
    assert "tg://user?id=50" in SCREEN[1][0] and "a:menu" not in buttons(1)
    await admin.post_stop(Call(1, f"a:pst:{p1}"), BOT)
    assert (await db.get_post(p1))["status"] == "stopped"
    assert any(uid == 50 and "остановлена" in t for uid, t in BOT.sent)
    call = Call(50, f"c:pt:{p1}")
    await cabinet.post_toggle(call)
    assert call.alert and (await db.get_post(p1))["status"] == "stopped"
    await cabinet.post_open(Call(50, f"c:p:{p1}"), st(50))
    assert f"c:pt:{p1}" not in buttons(50)
    await admin.post_stop(Call(1, f"a:pst:{p1}"), BOT)
    assert (await db.get_post(p1))["status"] == "active"

    # помощник
    await admin.cabinet_link(Call(1, f"a:clh:{cab}"), BOT)
    helper_token = next(i["token"] for i in await db.get_invites(cab) if i["role"] == "helper")
    await cabinet.start(Msg(51, "/start x"), st(51), CommandObject(helper_token), BOT)
    assert db.cabinet_of(51) == cab
    await cabinet.people(Call(50, "c:people"))
    assert "Помощник" in SCREEN[50][0]

    # смена владельца: рассылки остаются, прежний теряет доступ
    await admin.cabinet_link(Call(1, f"a:clo:{cab}"), BOT)
    owner_token = next(i["token"] for i in await db.get_invites(cab) if i["role"] == "owner")
    await cabinet.start(Msg(70, "/start x"), st(70), CommandObject(owner_token), BOT)
    assert db.cabinet_of(70) == cab and db.cabinet_of(50) is None and db.cabinet_of(51) == cab
    assert await db.count_posts(cab) == 2
    assert any(uid == 50 and "прекращён" in t for uid, t in BOT.sent)
    assert not await passes(Call(50, "c:home"))
    await cabinet.posts_list(Call(70, "c:posts"), st(70))
    assert f"c:p:{p1}" in buttons(70) and f"c:p:{p2}" in buttons(70)

    # заморозка: рассылки стоят, новые создать нельзя
    await admin.cabinet_freeze(Call(1, f"a:cfz:{cab}"), BOT)
    await db.delay_post(p1, -1)
    assert await scheduler.tick(BOT) == 0
    call = Call(70, "c:new")
    await cabinet.new_post(call, st(70))
    assert call.alert and "заморожен" in call.alert
    await cabinet.home(Msg(70, "/start"))
    assert "заморожен" in SCREEN[70][0]
    await admin.cabinet_freeze(Call(1, f"a:cfz:{cab}"), BOT)

    # поиск: по части названия без учёта регистра, по ID человека, по названию рассылки
    await admin.find(Call(1, "a:find"), st(1))
    await admin.find_run(Msg(1, "ромаш"), st(1))
    assert f"a:cab:{cab}" in buttons(1)
    await admin.find_run(Msg(1, "70"), st(1))
    assert f"a:cab:{cab}" in buttons(1)
    await admin.find_run(Msg(1, "АКЦИЯ"), st(1))
    assert f"a:p:{p1}" in buttons(1)
    await admin.find_run(Msg(1, "нет такого"), st(1))
    assert "ничего не найдено" in SCREEN[1][0]
    await st(1).clear()

    # оформление: владелец меняет текст и кнопку, партнёр видит изменения
    await admin.look_text_edit(Call(1, "a:lte:home"), st(1))
    await admin.look_text_save(Msg(1, "Добро пожаловать, {name}. Мест: {used}/{slots}"), st(1))
    assert "Изменён" in SCREEN[1][0]
    await cabinet.home(Msg(70, "/start"))
    assert SCREEN[70][0] == "Добро пожаловать, Магазин Ромашка. Мест: 2/2"
    await admin.look_button_edit(Call(1, "a:lbe:new"), st(1))
    await admin.look_button_text_save(Msg(1, "Создать"), st(1))
    await admin.look_button_style(Call(1, "a:lbs:new:success"))
    icon_msg = Msg(1, "x")
    icon_msg.entities = [type("E", (), {"type": "custom_emoji", "custom_emoji_id": "555"})()]
    await admin.look_button_edit(Call(1, "a:lbi:new"), st(1))
    await admin.look_button_icon_save(icon_msg, st(1))
    await cabinet.home(Msg(70, "/start"))
    first = SCREEN[70][1].inline_keyboard[0][0]
    assert (first.text, first.style, first.icon_custom_emoji_id) == ("Создать", "success", "555")
    await look.load()                                    # оформление переживает перезапуск
    assert look.button("new") == {"text": "Создать", "style": "success", "icon": "555"}

    # Telegram не принял цвет или значок: экран всё равно показан, с обычными кнопками
    class Picky(Msg):
        async def answer(self, text, reply_markup=None):
            if any(b.style or b.icon_custom_emoji_id for row in reply_markup.inline_keyboard for b in row):
                raise TelegramBadRequest("Bad Request: BUTTON_STYLE_INVALID")
            await super().answer(text, reply_markup)
    await cabinet.home(Picky(70, "/start"))
    first = SCREEN[70][1].inline_keyboard[0][0]
    assert first.text == "Создать" and first.style is None and first.callback_data == "c:new"

    await admin.look_button_reset(Call(1, "a:lbr:new"))
    await admin.look_text_reset(Call(1, "a:ltr:home"))
    await cabinet.home(Msg(70, "/start"))
    assert "Рассылок: 2 из 2" in SCREEN[70][0] and SCREEN[70][1].inline_keyboard[0][0].style is None

    # отчёт и журнал
    text = await report.build()
    assert "Кабинетов: 2" in text and "Отправок" in text
    BOT.sent.clear()
    await report.send(BOT)
    assert BOT.sent and BOT.sent[0][0] == 1 and await db.get_setting("cnt:sent") == "0"
    await admin.journal(Call(1, "a:log"))
    assert "создал кабинет Магазин Ромашка" in SCREEN[1][0] and "остановил рассылку" in SCREEN[1][0]

    # заметка и сообщение от бота в кабинет
    await admin.cabinet_note(Call(1, f"a:cnote:{cab}"), st(1))
    await admin.cabinet_note_done(Msg(1, "Оплата до 20 числа"), st(1))
    assert "Оплата до 20 числа" in SCREEN[1][0] and "https://t.me/user70" in buttons(1)
    BOT.sent.clear()
    await admin.cabinet_message(Call(1, f"a:cmsg:{cab}"), st(1))
    await admin.cabinet_message_send(Msg(1, "Проверьте пост"), st(1), BOT)
    assert {uid for uid, _ in BOT.sent} == {70, 51} and "2 из 2" in SCREEN[1][0]
    await admin.cabinet_note(Call(1, f"a:cnote:{cab}"), st(1))
    await admin.cabinet_note_done(Msg(1, "-"), st(1))
    assert "Заметка" not in SCREEN[1][0].split("Создан")[1]

    # группы добавляются по ID с проверкой прав бота
    await admin.group_new(Call(1, "a:gnew"), st(1))
    for bad, why in ((-1, "не видит"), (-1008, "не администратор"), (-1007, "не группа"), (-100999, "хранилище")):
        await admin.group_new_id(Msg(1, str(bad)), st(1), BOT)
        assert why in SCREEN[1][0], (bad, SCREEN[1][0])
        assert await db.get_group_by_chat(bad) is None
    await admin.group_new_id(Msg(1, "-1009"), st(1), BOT)
    added = await db.get_group_by_chat(-1009)
    assert added and added["is_active"] == 1 and added["is_public"] == 0 and "Новая группа" in SCREEN[1][0]
    await db.delete_group(added["id"])

    # все экраны админки открываются
    for handler, data, extra in [
        (admin.cabinets, "a:cabs:0", (st(1),)), (admin.cabinet_open, f"a:cab:{cab}", (st(1),)),
        (admin.cabinet_posts, f"a:cps:{cab}", ()),
        (admin.post_preview, f"a:pv:{p1}", (BOT,)), (admin.post_delete_ask, f"a:pdel:{p1}", ()),
        (admin.groups, "a:groups", (st(1),)), (admin.group_new, "a:gnew", (st(1),)), (admin.group_open, f"a:g:{g1}", ()),
        (admin.group_toggle, f"a:gpub:{g2}", ()), (admin.group_interval, f"a:gint:{g1}", ()),
        (admin.group_interval_set, f"a:gis:{g1}:4", ()), (admin.group_delete_ask, f"a:gdel:{g2}", ()),
        (admin.owners, "a:own", (st(1),)), (admin.owner_add, "a:oadd", (st(1),)),
        (admin.announce, "a:bc", (st(1),)), (admin.cabinet_slots, f"a:csl:{cab}:1", ()),
        (admin.cabinet_all_groups, f"a:cag:{cab}", ()), (admin.cabinet_rename, f"a:crn:{cab}", (st(1),)),
        (admin.cabinet_delete_ask, f"a:cdel:{cab}", ()), (admin.menu_cb, "a:menu", (st(1),)),
        (admin.settings, "a:set", (st(1),)), (admin.report_menu, "a:rep", ()),
        (admin.report_hour, "a:reph:10", ()), (admin.report_toggle, "a:repon", ()),
        (admin.report_now, "a:repnow", ()), (admin.look_menu, "a:lk", (st(1),)),
        (admin.look_texts, "a:lt", (st(1),)), (admin.look_buttons, "a:lb", (st(1),)),
        (admin.look_button_open, "a:lb:pause", (st(1),)), (admin.look_text_open, "a:lt:report", (st(1),)),
        (cabinet.help_, "c:help", ()), (cabinet.post_delete_ask, f"c:pd:{p1}", ()),
    ]:
        uid = 70 if data.startswith("c:") else 1
        await handler(Call(uid, data), *extra)
        assert SCREEN[uid][0], data
    for key in look.TEXTS:                                # каждый текст открывается в редакторе
        await admin.look_text_open(Call(1, f"a:lt:{key}"), st(1))
        assert "{" not in SCREEN[1][0].split("➖➖➖➖➖➖")[1], key
    assert (await db.get_group(g1))["interval_hours"] == 4
    assert await db.effective_interval(None) == config.DEFAULT_INTERVAL_HOURS
    assert (await db.get_group(g2))["is_public"] == 1

    # владелец бота со своим кабинетом видит кнопку админки
    await admin.owner_add_done(Msg(1, "70"), st(1))
    await cabinet.home(Msg(70, "/start"))
    assert "a:menu" in buttons(70)

    # объявление, удаление рассылок и кабинета
    await admin.announce(Call(1, "a:bc"), st(1))
    await admin.announce_send(Msg(1, "Важно"), st(1), BOT)
    assert "2 из" in SCREEN[1][0] or "3 из" in SCREEN[1][0]
    await cabinet.post_delete(Call(70, f"c:pdc:{p2}"), BOT, st(70))
    assert await db.count_posts(cab) == 1
    await admin.post_delete(Call(1, f"a:pdelc:{p1}"), BOT)
    assert await db.count_posts(cab) == 0
    await admin.cabinet_remove_helper(Call(1, f"a:crh:{cab}"), BOT)
    assert db.cabinet_of(51) is None
    await admin.cabinet_delete(Call(1, f"a:cdelc:{cab}"), BOT, st(1))
    assert await db.get_cabinet(cab) is None and db.cabinet_of(70) is None

    print("flow: все проверки пройдены")


asyncio.run(main())
