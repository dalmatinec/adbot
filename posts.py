"""Приём поста от рекламодателя и хранение его в канале-хранилище.

Почему так: зеркало получает пост в личке со своим ботом, а рассылает
основной бот, который в этой личке не состоит и скопировать оттуда ничего не
может. Поэтому зеркало скачивает содержимое, основной бот заливает его в
приватный канал-хранилище, а рассылка потом идёт copy_message из этого канала.
Побочный плюс: пост не зависит ни от зеркала, ни от смены основного бота.

Поддерживается: текст, фото, видео, анимация (GIF), документ, аудио.
Кнопки-ссылки (inline url) сохраняются. Альбомы не поддерживаются.
"""

import json
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db

log = logging.getLogger(__name__)


class IngestError(Exception):
    """Понятная пользователю причина, почему пост не принят."""


def extract_buttons(message: Message) -> str | None:
    """Только кнопки-ссылки: остальные в рассылке работать не будут."""
    markup = message.reply_markup
    if not markup:
        return None
    rows = []
    for row in markup.inline_keyboard:
        out = [{"text": b.text, "url": b.url} for b in row if b.url]
        if out:
            rows.append(out)
    return json.dumps(rows, ensure_ascii=False) if rows else None


def buttons_markup(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    if not buttons_json:
        return None
    rows = json.loads(buttons_json)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row] for row in rows
    ])


def post_title(message: Message) -> str:
    text = (message.text or message.caption or "").strip().replace("\n", " ")
    if text:
        return text[:40] + ("…" if len(text) > 40 else "")
    kind = ("фото" if message.photo else "видео" if message.video else
            "GIF" if message.animation else "файл" if message.document else "аудио")
    return f"Пост ({kind})"


async def _stopword_hit(text: str) -> str | None:
    raw = await db.get_setting("stopwords", "")
    low = text.lower()
    for word in [w.strip().lower() for w in (raw or "").split(",") if w.strip()]:
        if word in low:
            return word
    return None


async def ingest(mirror_bot: Bot, main_bot: Bot, message: Message) -> tuple[int, int, str | None, str]:
    """Возвращает (storage_chat_id, storage_message_id, buttons_json, title)."""
    if message.media_group_id:
        raise IngestError("Альбомы не поддерживаются. Отправьте один пост: текст, фото, видео, GIF или файл.")

    text = message.text or message.caption or ""
    hit = await _stopword_hit(text)
    if hit:
        raise IngestError(f"Пост содержит запрещённое слово: {hit}.")

    target = config.STORAGE_CHAT_ID
    try:
        if message.text:
            saved = await main_bot.send_message(
                target, message.text, entities=message.entities, parse_mode=None,
            )
        else:
            if message.photo:
                file_id, size, kind = message.photo[-1].file_id, message.photo[-1].file_size, "photo"
            elif message.video:
                file_id, size, kind = message.video.file_id, message.video.file_size, "video"
            elif message.animation:
                file_id, size, kind = message.animation.file_id, message.animation.file_size, "animation"
            elif message.document:
                file_id, size, kind = message.document.file_id, message.document.file_size, "document"
            elif message.audio:
                file_id, size, kind = message.audio.file_id, message.audio.file_size, "audio"
            else:
                raise IngestError("Данный тип сообщения не поддерживается. Допустимы текст, фото, видео, GIF и файлы.")
            if size and size > config.MAX_FILE_MB * 1024 * 1024:
                raise IngestError(f"Размер файла превышает {config.MAX_FILE_MB} МБ, Telegram не позволяет ботам загрузить такой файл.")
            buf = await mirror_bot.download(file_id)
            upload = BufferedInputFile(buf.getvalue(), filename=f"{kind}_{message.message_id}")
            send = {
                "photo": main_bot.send_photo, "video": main_bot.send_video,
                "animation": main_bot.send_animation, "document": main_bot.send_document,
                "audio": main_bot.send_audio,
            }[kind]
            saved = await send(
                target, upload, caption=message.caption,
                caption_entities=message.caption_entities, parse_mode=None,
            )
    except IngestError:
        raise
    except Exception as exc:
        log.exception("Не удалось сохранить пост в хранилище")
        raise IngestError(
            "Не удалось принять пост. Повторите попытку. Если ошибка повторяется, обратитесь к владельцам."
        ) from exc
    return target, saved.message_id, extract_buttons(message), post_title(message)


async def ingest_direct(main_bot: Bot, message: Message) -> tuple[int, int, str | None, str]:
    """Пост прислан самому основному боту. Копируем его в хранилище один в один
    (copy_message сохраняет все сущности, включая премиум-эмодзи, и не требует
    скачивать файл), а рассылка потом идёт пересылкой из хранилища."""
    if message.media_group_id:
        raise IngestError("Альбомы не поддерживаются. Отправьте один пост: текст, фото, видео, GIF или файл.")
    if not (message.text or message.photo or message.video or message.animation
            or message.document or message.audio):
        raise IngestError("Данный тип сообщения не поддерживается. Допустимы текст, фото, видео, GIF и файлы.")
    hit = await _stopword_hit(message.text or message.caption or "")
    if hit:
        raise IngestError(f"Пост содержит запрещённое слово: {hit}.")
    try:
        saved = await main_bot.copy_message(
            config.STORAGE_CHAT_ID, from_chat_id=message.chat.id, message_id=message.message_id)
    except Exception as exc:
        log.exception("Не удалось сохранить пост в хранилище (direct)")
        raise IngestError(
            "Не удалось принять пост. Повторите попытку. Если ошибка повторяется, обратитесь к владельцам."
        ) from exc
    return config.STORAGE_CHAT_ID, saved.message_id, extract_buttons(message), post_title(message)
