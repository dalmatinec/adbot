from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

import database


class IsAdmin(BaseFilter):
    """Пропускает событие дальше, только если пользователь — админ.
    Если фильтр не проходит, aiogram просто не находит хендлер — бот молчит,
    никакого отдельного сообщения об отказе не шлём (по ТЗ)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return await database.is_admin(event.from_user.id)


class NotBanned(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return not await database.is_banned(event.from_user.id)
