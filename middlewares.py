"""Фильтр входящих событий.

Посторонним бот не отвечает вообще: единственное, что от них принимается,
это /start с кодом из ссылки входа. Сообщения из групп отбрасываются сразу,
без обращения к базе. Частые нажатия одного человека гасятся (антифлуд),
устаревшие нажатия не считаются ошибкой.
"""

import logging
import time

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

import config
import db

log = logging.getLogger(__name__)

_STALE = ("query is too old", "query id is invalid")
_last_click: dict[int, float] = {}


class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return None
        if isinstance(event, Message):
            if event.chat.type != "private":
                return None
            if not db.is_known(user.id):
                text = event.text or ""
                if not (text.startswith("/start ") and len(text) > 7):
                    return None
        elif isinstance(event, CallbackQuery):
            if not db.is_known(user.id):
                return None
            now = time.monotonic()
            if now - _last_click.get(user.id, 0.0) < config.CLICK_GAP_SECONDS:
                try:
                    await event.answer()
                except TelegramBadRequest:
                    pass
                return None
            _last_click[user.id] = now
            if len(_last_click) > 5000:
                _last_click.clear()
        if db.is_known(user.id):
            await db.touch_user(user.id, user.full_name, user.username)
        try:
            return await handler(event, data)
        except TelegramBadRequest as exc:
            if any(marker in str(exc).lower() for marker in _STALE):
                log.info("Пропущено устаревшее нажатие кнопки")
                return None
            raise
