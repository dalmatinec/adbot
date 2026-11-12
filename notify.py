"""Уведомления владельцам бота и людям кабинета."""

import db


async def owners(bot, text: str, reply_markup=None) -> None:
    for row in await db.list_owners():
        try:
            await bot.send_message(row["user_id"], text, reply_markup=reply_markup)
        except Exception:
            pass  # владелец мог не открыть личку с ботом


async def cabinet(bot, cabinet_id: int, text: str) -> None:
    for row in await db.cabinet_members(cabinet_id):
        try:
            await bot.send_message(row["user_id"], text)
        except Exception:
            pass
