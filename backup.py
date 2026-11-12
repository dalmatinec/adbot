"""Бэкап базы в хранилище постов (раз в неделю и по кнопке).

Копия делается через SQLite backup API (согласованный снимок при работающем
боте) и сжимается.

Восстановление и перенос: остановить контейнер, распаковать файл (gunzip) в
data/adbot.db, запустить заново. Подробности в README.
"""

import asyncio
import gzip
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import FSInputFile

import config
import db

log = logging.getLogger(__name__)


async def send_backup(bot: Bot, chat_id: int) -> None:
    tmp = tempfile.mkdtemp(prefix="adbot-backup-")
    try:
        raw = os.path.join(tmp, "adbot.db")
        await asyncio.to_thread(db.snapshot, raw)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        packed = os.path.join(tmp, f"adbot-{stamp}.db.gz")

        def _pack() -> None:
            with open(raw, "rb") as src, gzip.open(packed, "wb") as dst:
                shutil.copyfileobj(src, dst)

        await asyncio.to_thread(_pack)
        size_mb = os.path.getsize(packed) / 1024 / 1024
        if size_mb > 49:
            raise RuntimeError(f"Бэкап {size_mb:.0f} МБ больше лимита Telegram для ботов (50 МБ)")
        await bot.send_document(chat_id, FSInputFile(packed), caption=f"💾 Бэкап {stamp} UTC")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run_backups(bot: Bot) -> None:
    """Раз в BACKUP_EVERY_HOURS шлёт бэкап в хранилище и чистит истёкшие ссылки."""
    if not config.STORAGE_CHAT_ID or config.BACKUP_EVERY_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(config.BACKUP_EVERY_HOURS * 3600)
        try:
            await db.cleanup_invites()
            await send_backup(bot, config.STORAGE_CHAT_ID)
        except Exception:
            log.exception("Автоматический бэкап не удался")
