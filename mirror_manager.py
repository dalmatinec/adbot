"""Запуск и остановка ботов-зеркал внутри одного процесса.

У каждого зеркала свой Bot и свой Dispatcher с long polling: домен и HTTPS
не нужны, работает из Docker как есть. Кабинет у всех зеркал одинаковый
(mirror_cabinet.make_router), а какое именно зеркало отвечает, определяется
по id бота.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage

import crypto
import db
import mirror_cabinet
import texts
from middlewares import GuardMiddleware

log = logging.getLogger(__name__)


class MirrorManager:
    def __init__(self, main_bot: Bot):
        self.main_bot = main_bot
        self.bots: dict[int, Bot] = {}              # mirror_id -> Bot
        self._tasks: dict[int, asyncio.Task] = {}   # mirror_id -> задача polling
        self._dps: dict[int, Dispatcher] = {}
        self._pending: dict[int, tuple[Bot, Dispatcher, asyncio.Task]] = {}  # owner_id -> бот с заявкой

    async def start_all(self) -> None:
        for row in await db.list_runnable_mirrors():
            await self.start(row)
        for app in await db.list_applications():
            await self.start_pending(app)

    async def start(self, mirror) -> None:
        mid = mirror["id"]
        if mid in self._tasks and not self._tasks[mid].done():
            return
        token = crypto.decrypt(mirror["token_enc"])
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher(storage=MemoryStorage(), main_bot=self.main_bot, mirror_id=mid, manager=self)
        dp.update.outer_middleware(GuardMiddleware())
        dp.include_router(mirror_cabinet.make_router())
        self.bots[mid] = bot
        self._dps[mid] = dp
        self._tasks[mid] = asyncio.create_task(self._run(mid, bot, dp), name=f"mirror-{mid}")
        asyncio.create_task(self.apply_description(bot))
        log.info("Зеркало #%s запущено", mid)

    async def start_pending(self, app) -> None:
        """Бот с неодобренной заявкой: сразу получает описание и отвечает на
        /start, что заявка ждёт одобрения. Полноценным зеркалом станет после
        принятия."""
        owner_id = app["user_id"]
        if owner_id in self._pending and not self._pending[owner_id][2].done():
            return
        bot = Bot(token=crypto.decrypt(app["token_enc"]),
                  default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher(storage=MemoryStorage(), owner_id=owner_id)
        dp.update.outer_middleware(GuardMiddleware())
        dp.include_router(mirror_cabinet.make_pending_router())
        task = asyncio.create_task(self._run_pending(owner_id, bot, dp), name=f"pending-{owner_id}")
        self._pending[owner_id] = (bot, dp, task)
        asyncio.create_task(self.apply_description(bot))
        log.info("Бот заявки пользователя %s запущен в режиме ожидания", owner_id)

    async def stop_pending(self, owner_id: int) -> None:
        entry = self._pending.pop(owner_id, None)
        if entry is None:
            return
        bot, dp, task = entry
        try:
            await dp.stop_polling()
        except Exception:
            pass
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await bot.session.close()
        except Exception:
            pass

    async def _run_pending(self, owner_id: int, bot: Bot, dp: Dispatcher) -> None:
        delay = 5
        while True:
            try:
                await dp.start_polling(bot, allowed_updates=["message"],
                                       handle_signals=False, close_bot_session=False)
                return
            except TelegramUnauthorizedError:
                log.warning("Токен заявки пользователя %s отозван", owner_id)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Бот заявки %s упал, перезапуск через %s с", owner_id, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    async def apply_description(self, bot: Bot) -> None:
        """Описание бота (видно до нажатия Старт) и краткое описание. Telegram
        ограничивает частоту смены, поэтому меняем только при расхождении."""
        desc = await db.get_setting("mirror_description", texts.DEFAULT_DESCRIPTION)
        short = await db.get_setting("mirror_short_description", texts.DEFAULT_SHORT_DESCRIPTION)
        try:
            current = await bot.get_my_description()
            if (current.description or "") != desc:
                await bot.set_my_description(description=desc)
            current_short = await bot.get_my_short_description()
            if (current_short.short_description or "") != short:
                await bot.set_my_short_description(short_description=short)
        except Exception:
            log.warning("Не удалось обновить описание зеркала", exc_info=True)

    async def apply_descriptions_all(self) -> int:
        n = 0
        for bot in list(self.bots.values()):
            await self.apply_description(bot)
            n += 1
            await asyncio.sleep(0.3)
        return n

    async def stop(self, mirror_id: int) -> None:
        dp = self._dps.pop(mirror_id, None)
        task = self._tasks.pop(mirror_id, None)
        bot = self.bots.pop(mirror_id, None)
        if dp is not None:
            try:
                await dp.stop_polling()
            except Exception:
                pass
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass

    async def stop_all(self) -> None:
        for mid in list(self._tasks):
            await self.stop(mid)
        for owner_id in list(self._pending):
            await self.stop_pending(owner_id)

    async def _run(self, mirror_id: int, bot: Bot, dp: Dispatcher) -> None:
        delay = 5
        while True:
            try:
                await dp.start_polling(
                    bot, allowed_updates=["message", "callback_query"],
                    handle_signals=False, close_bot_session=False,
                )
                return  # штатная остановка
            except TelegramUnauthorizedError:
                # токен отозван (бот удалён или токен перевыпущен)
                await db.set_mirror_status(mirror_id, "revoked")
                await db.pause_mirror_posts(mirror_id)
                mirror = await db.get_mirror(mirror_id)
                if mirror:
                    await self._notify_main(
                        mirror["owner_id"],
                        "Токен вашего зеркала отозван, зеркало отключено. "
                        "Посты сохранены и поставлены на паузу. Создайте зеркало заново или отправьте новый токен.",
                    )
                log.warning("Зеркало #%s: токен отозван", mirror_id)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Зеркало #%s упало, перезапуск через %s с", mirror_id, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    async def _notify_main(self, user_id: int, text: str) -> None:
        try:
            await self.main_bot.send_message(user_id, text)
        except Exception:
            pass

    async def notify_staff(self, mirror_id: int, text: str) -> None:
        """Сообщение создателю и админам зеркала от имени самого зеркала.
        Получат те, кто уже писал этому зеркалу."""
        bot = self.bots.get(mirror_id)
        if bot is None:
            return
        for uid in await db.mirror_staff_ids(mirror_id):
            try:
                await bot.send_message(uid, text)
            except Exception:
                pass

    async def broadcast_staff(self, text: str, entities=None) -> tuple[int, int]:
        """Текст всем создателям и админам всех зеркал. Возвращает (дошло, не дошло)."""
        ok = fail = 0
        for mirror in await db.list_runnable_mirrors():
            bot = self.bots.get(mirror["id"])
            if bot is None:
                continue
            for uid in await db.mirror_staff_ids(mirror["id"]):
                try:
                    await bot.send_message(uid, text, entities=entities, parse_mode=None)
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.05)
        return ok, fail
