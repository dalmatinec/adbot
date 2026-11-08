"""Общий для основного бота и зеркал: запоминает пользователя и молча
отбрасывает апдейты забаненных (владельцев это не касается)."""

from aiogram import BaseMiddleware

import db


class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            await db.touch_user(user.id, user.full_name, user.username)
            if await db.is_banned(user.id) and await db.get_role(user.id) != "owner":
                return None
        return await handler(event, data)
