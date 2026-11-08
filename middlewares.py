"""Общий для основного бота и зеркал: запоминает пользователя, молча
отбрасывает апдейты забаненных (владельцев это не касается) и гасит ошибки
устаревших нажатий кнопок."""

import logging

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest

import db

log = logging.getLogger(__name__)

_STALE = ("query is too old", "query id is invalid")


class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            await db.touch_user(user.id, user.full_name, user.username)
            if await db.is_banned(user.id) and await db.get_role(user.id) != "owner":
                return None
        try:
            return await handler(event, data)
        except TelegramBadRequest as exc:
            # Нажатие накопилось, пока бот не отвечал (перезапуск, нагрузка):
            # подтверждать его уже поздно, это не ошибка бота.
            if any(marker in str(exc).lower() for marker in _STALE):
                log.info("Пропущено устаревшее нажатие кнопки")
                return None
            raise
