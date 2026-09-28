"""
Всё время в БД хранится в UTC (database._now() = datetime.utcnow().isoformat()).
Этот модуль отвечает только за ПОКАЗ времени человеку — в часовом поясе
config.DISPLAY_TZ (по умолчанию Алматы, UTC+5, без перевода стрелок).

Важно: перевод не зависит от часового пояса, выставленного на сервере —
мы всегда явно трактуем сохранённое время как UTC и уже от него считаем
локальное. Поэтому даже если системные часы сервера показывают время в
другом поясе (или сам сервер работает в UTC), результат будет верным —
лишь бы системные часы были синхронизированы (NTP) и показывали
правильный момент времени.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import config

_UTC = ZoneInfo("UTC")
_LOCAL = ZoneInfo(config.DISPLAY_TZ)


def fmt_dt(iso_str: str | None, with_tz_label: bool = True) -> str:
    """'2026-09-06T20:08:21.502676' (UTC, из БД) -> '07.09.2026 01:08 (Алматы)'."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    local = dt.astimezone(_LOCAL)
    text = local.strftime("%d.%m.%Y %H:%M")
    if with_tz_label:
        text += f" ({config.DISPLAY_TZ_LABEL})"
    return text
