"""Уведомления владельцам основного бота."""

import db


async def notify_owners(bot, text: str, reply_markup=None) -> None:
    for owner in await db.list_owners():
        try:
            await bot.send_message(owner["user_id"], text, reply_markup=reply_markup)
        except Exception:
            pass  # владелец мог не открыть личку с ботом
