"""Точка входа: основной бот + зеркала + планировщик + бэкапы в одном процессе."""

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import applications
import backup
import config
import db
import handlers_admin
import handlers_main
from middlewares import GuardMiddleware
from mirror_manager import MirrorManager
from scheduler import run_scheduler

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member", "managed_bot"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.validate()
    await db.init()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    manager = MirrorManager(bot)

    dp = handlers_main.MainDispatcher(storage=MemoryStorage(), manager=manager)
    dp.update.outer_middleware(GuardMiddleware())
    dp.include_router(handlers_admin.router)
    dp.include_router(handlers_main.router)

    me = await bot.get_me()
    logging.info("Основной бот: @%s", me.username)

    await bot.delete_webhook(drop_pending_updates=False)
    await manager.start_all()
    background = [
        asyncio.create_task(run_scheduler(bot), name="scheduler"),
        asyncio.create_task(backup.run_backups(bot), name="backups"),
        asyncio.create_task(applications.run_expiry(bot), name="applications"),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        for task in background:
            task.cancel()
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
