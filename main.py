"""Точка входа: один бот, планировщик и бэкапы в одном процессе."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import admin
import backup
import cabinet
import config
import db
import look
import report
from middlewares import GuardMiddleware
from scheduler import run_scheduler

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    config.validate()
    await db.init()
    await look.load()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    guard = GuardMiddleware()
    dp.message.outer_middleware(guard)
    dp.callback_query.outer_middleware(guard)
    dp.include_router(admin.events)
    # кабинет первым: в нём /start и /cancel, они должны срабатывать и во время ввода в админке
    dp.include_router(cabinet.router)
    dp.include_router(admin.router)

    me = await bot.get_me()
    logging.info("Бот запущен: @%s", me.username)

    background = [
        asyncio.create_task(run_scheduler(bot), name="scheduler"),
        asyncio.create_task(backup.run_backups(bot), name="backups"),
        asyncio.create_task(report.run(bot), name="report"),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        for task in background:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
