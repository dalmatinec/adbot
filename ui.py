"""Помощники интерфейса: клавиатуры, показ экрана, время, имена."""

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config

_UTC = ZoneInfo("UTC")
_LOCAL = ZoneInfo(config.DISPLAY_TZ)

POST_ICON = {"active": "🟢", "paused": "⏸", "stopped": "⏹"}
POST_TEXT = {"active": "идёт", "paused": "на паузе", "stopped": "остановлена администрацией"}


def kb(rows) -> InlineKeyboardMarkup:
    """rows: список рядов, ряд: список (текст, data). data с http даёт
    кнопку-ссылку, иначе callback_data."""
    out = []
    for row in rows:
        buttons = []
        for text, data in row:
            if data.startswith("http"):
                buttons.append(InlineKeyboardButton(text=text, url=data))
            else:
                buttons.append(InlineKeyboardButton(text=text, callback_data=data))
        out.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=out)


async def show(target: Message | CallbackQuery, text: str, markup=None) -> None:
    """Для кнопки правим текущее сообщение, для команды отправляем новое.
    Нажатие подтверждается сразу, до любой другой работы."""
    if isinstance(target, CallbackQuery):
        try:
            await target.answer()
        except TelegramBadRequest:
            pass
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                await target.message.answer(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


def fmt_dt(iso_str: str | None) -> str:
    if not iso_str:
        return "нет"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_LOCAL).strftime("%d.%m.%Y %H:%M")


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else "нет"


def user_link(user_id: int, name: str | None = None) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or user_id)}</a>'


def person(row) -> str:
    """Кликабельное имя с юзернеймом и ID из строки с user_id, full_name, username."""
    out = user_link(row["user_id"], row["full_name"])
    if row["username"]:
        out += f" @{esc(row['username'])}"
    return out + f" (ID <code>{row['user_id']}</code>)"


def fmt_hours(hours: int) -> str:
    return f"{hours} ч."


def times(n: int) -> str:
    return f"{n} раз" + ("а" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "")
