"""Приём и пересылка постов.

Партнёр присылает боту пост: свой или пересланный из канала, группы, от
человека или бота. Бот ничего не пересобирает, а пересылает сообщение как
есть в хранилище, и оттуда же пересылает его в группы. Пересылка сохраняет
всё: премиум-эмодзи, медиа, альбомы и исходную шапку Переслано от.
Хранилище нужно, чтобы пост не зависел от того, удалил ли партнёр переписку.
"""

import logging

from aiogram.types import Message

import config

log = logging.getLogger(__name__)


class IngestError(Exception):
    """Понятная партнёру причина, почему пост не принят."""


def post_title(message: Message) -> str:
    text = (message.text or message.caption or "").strip().replace("\n", " ")
    if text:
        return text[:40] + ("…" if len(text) > 40 else "")
    kind = ("фото" if message.photo else "видео" if message.video else
            "GIF" if message.animation else "файл" if message.document else
            "аудио" if message.audio else "голосовое" if message.voice else
            "стикер" if message.sticker else "сообщение")
    return f"Пост ({kind})"


async def forward(bot, to_chat_id: int, from_chat_id: int, ids: list[int]) -> list[int]:
    """Пересылает одно сообщение или альбом. Возвращает id новых сообщений."""
    ids = sorted(ids)
    if len(ids) == 1:
        sent = await bot.forward_message(chat_id=to_chat_id, from_chat_id=from_chat_id, message_id=ids[0])
        return [sent.message_id]
    sent = await bot.forward_messages(chat_id=to_chat_id, from_chat_id=from_chat_id, message_ids=ids)
    return [m.message_id for m in sent]


async def store(bot, chat_id: int, message_ids: list[int]) -> list[int]:
    """Кладёт присланный пост в хранилище."""
    try:
        return await forward(bot, config.STORAGE_CHAT_ID, chat_id, message_ids)
    except Exception as exc:
        log.exception("Не удалось сохранить пост в хранилище")
        low = str(exc).lower()
        if "can't be forwarded" in low or "protected" in low or "noforwards" in low:
            raise IngestError("Этот пост нельзя переслать: в источнике запрещена пересылка.") from exc
        raise IngestError(
            "Не удалось принять пост. Повторите попытку. Если ошибка повторяется, "
            "обратитесь к администрации.") from exc


def ids_of(post) -> list[int]:
    return [int(x) for x in post["storage_ids"].split(",") if x]


async def send_post(bot, to_chat_id: int, post) -> None:
    await forward(bot, to_chat_id, post["storage_chat_id"], ids_of(post))


async def drop_stored(bot, storage_chat_id: int, ids: list[int]) -> None:
    """Убирает старое содержимое из хранилища. Не получилось, не страшно."""
    try:
        await bot.delete_messages(chat_id=storage_chat_id, message_ids=ids)
    except Exception:
        pass
