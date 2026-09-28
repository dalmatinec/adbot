"""
Уведомляет всех супер-админов, когда бота добавляют в новую группу/канал
(или возвращают права после кика) - чтобы не приходилось гадать, добавлен
ли он куда-то, и сразу видеть название/ID для "Целевые чаты".

Отдельный модуль/роутер - подключается в bots.py двумя строками, как и
остальные обработчики.
"""

from aiogram import Router, Bot
from aiogram.types import ChatMemberUpdated

import database

router = Router()

_ACTIVE_STATUSES = {"member", "administrator"}
_INACTIVE_STATUSES = {"left", "kicked"}


@router.my_chat_member()
async def on_bot_chat_member_update(event: ChatMemberUpdated, bot: Bot) -> None:
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    if old_status in _ACTIVE_STATUSES or new_status not in _ACTIVE_STATUSES:
        return  # интересует только переход "не было доступа" -> "появился"

    chat = event.chat
    title = chat.title or chat.full_name or str(chat.id)
    text = (
        f"✅ Бот добавлен в группу: {title}\n"
        f"ID: {chat.id}\n\n"
        f"Чтобы рассылки шли сюда - добавьте этот ID в \"🎯 Целевые чаты\" через /adm."
    )
    for admin_id in await database.list_admins():
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass  # админ мог не открыть ЛС с ботом - не критично
