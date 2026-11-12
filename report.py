"""Ежедневный отчёт владельцам бота. Шаблон меняется в админке (Оформление)."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import db
import look
import notify
from ui import esc, kb

log = logging.getLogger(__name__)
_LOCAL = ZoneInfo(config.DISPLAY_TZ)


async def build() -> str:
    s = await db.summary()
    bad = await db.posts_with_errors()
    if bad:
        problems = "⚠️ <b>Рассылки с ошибками</b>\n" + "\n".join(
            f"• #{p['id']} {esc(p['title'])} ({esc(p['cabinet_name'])})" for p in bad)
    else:
        problems = "✅ Рассылок с ошибками нет."
    return look.text(
        "report", date=datetime.now(_LOCAL).strftime("%d.%m.%Y %H:%M"),
        sent=await db.get_setting("cnt:sent", "0"), errors=await db.get_setting("cnt:errors", "0"),
        cabinets=s["cabinets"], frozen=s["frozen"], posts=s["posts"], active=s["active"],
        paused=s["paused"], stopped=s["stopped"], groups=s["groups"], problems=problems)


async def send(bot, reset: bool = True) -> None:
    await notify.owners(bot, await build(), kb([[("⚙️ Админка", "a:menu")]]))
    if reset:
        await db.set_setting("cnt:sent", "0")
        await db.set_setting("cnt:errors", "0")


async def enabled() -> bool:
    return await db.get_setting("report_on", "1") == "1"


async def hour() -> int:
    return int(await db.get_setting("report_hour", "9"))


async def run(bot) -> None:
    """Раз в минуту проверяет, не пора ли. Отчёт уходит один раз в сутки в
    заданный час по местному времени."""
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now(_LOCAL)
            today = now.strftime("%Y-%m-%d")
            if (await enabled() and now.hour == await hour()
                    and await db.get_setting("report_last") != today):
                await db.set_setting("report_last", today)
                await send(bot)
                await db.trim_actions()
        except Exception:
            log.exception("Ошибка отправки отчёта")
