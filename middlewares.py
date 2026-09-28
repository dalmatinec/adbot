"""
Middleware, который на каждом апдейте (сообщение или нажатие кнопки)
сохраняет ID/имя/username отправителя — чтобы потом показывать его как
кликабельное имя (см. mentions.py), а не голый ID.

Подключается в bots.py ОДНОЙ строкой: setup_middlewares(dp).
"""

from aiogram import BaseMiddleware, Dispatcher

import database


class TrackUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None:
            await database.upsert_seen_user(user.id, user.full_name, user.username)
        return await handler(event, data)


def setup_middlewares(dp: Dispatcher) -> None:
    dp.update.outer_middleware(TrackUserMiddleware())
