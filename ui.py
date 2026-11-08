"""Мелкие помощники для интерфейса: клавиатуры, показ экрана, время, имена."""

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config

_UTC = ZoneInfo("UTC")
_LOCAL = ZoneInfo(config.DISPLAY_TZ)


def kb(rows) -> InlineKeyboardMarkup:
    """rows: список рядов, ряд - список (текст, data). Если data начинается
    с http - это кнопка-ссылка, иначе callback_data."""
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
    """Для кнопки правим текущее сообщение, для команды отправляем новое."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                await target.message.answer(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


def fmt_dt(iso_str: str | None) -> str:
    """UTC из базы -> '07.09.2026 01:08 (Алматы)'."""
    if not iso_str:
        return "нет"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_LOCAL).strftime("%d.%m.%Y %H:%M") + f" ({config.DISPLAY_TZ_LABEL})"


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else "нет"


def user_link(user_id: int, name: str | None = None) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or user_id)}</a>'


def fmt_interval(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60} ч."
    if minutes > 60:
        return f"{minutes // 60} ч. {minutes % 60} мин."
    return f"{minutes} мин."


STATUS_ICON = {"active": "🟢", "pending": "🕓", "paused": "⏸", "blocked": "⛔", "revoked": "❌"}
STATUS_TEXT = {
    "active": "активна", "pending": "на модерации", "paused": "на паузе",
    "blocked": "заблокировано", "revoked": "токен отозван",
}
