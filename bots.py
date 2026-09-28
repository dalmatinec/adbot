"""
Точка входа. Только: инициализация БД, подключение middleware и роутеров,
запуск планировщика и polling. Бизнес-логика — в handlers_*.py.

Чтобы добавить новый модуль хендлеров: создать handlers_XXX.py со своим
`router = Router()`, импортировать его здесь и добавить одну строку
`dp.include_router(handlers_XXX.router)` в нужном месте по порядку ниже.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import database
import handlers_admin
import handlers_admin1
import handlers_chatmember
import handlers_user
import handlers_key
from middlewares import setup_middlewares
from scheduler import run_scheduler


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    await database.init_db()
    await database.init_content_db()
    await database.migrate_legacy_content()
    await database.ensure_default_buttons()
    await database.ensure_default_texts()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    setup_middlewares(dp)

    # Порядок важен: сначала админ-роутеры и роутер рекламодателя (там команды
    # и конкретные callback_data/состояния — они "перехватывают" апдейт и
    # дальше он не идёт), и только ПОСЛЕДНИМ — handlers_key, который ловит
    # "любой текст" и либо активирует ключ, либо молчит.
    dp.include_router(handlers_admin.router)
    dp.include_router(handlers_admin1.router)
    dp.include_router(handlers_chatmember.router)
    dp.include_router(handlers_user.router)
    dp.include_router(handlers_key.router)

    asyncio.create_task(run_scheduler(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
