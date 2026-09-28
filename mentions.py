"""
Кликабельные имена пользователей - через нативный Telegram text_mention.
В отличие от tg://user?id=... "ссылки", text_mention выглядит как обычный
текст (не подчёркнутый, не синий), но нажатие всё равно открывает профиль
человека. Работает даже у пользователей без username.

Если у нас нет сохранённого имени человека (он ещё ни разу не писал боту,
и его ID ввели вручную, например при добавлении админа ключа) - используем
голый ID как обычный текст, без спецформатирования (по требованию: "если
юзера нет - просто ID").
"""

from aiogram.types import MessageEntity, User

import database


def _utf16_len(s: str) -> int:
    """Длина строки в UTF-16 code units.

    MessageEntity.offset/length у Telegram считаются в UTF-16 code units,
    а не в питоновских символах. Для большинства текста они совпадают, но
    расходятся на любом символе вне Basic Multilingual Plane - а это почти
    все эмодзи: флаги (каждый - это ДВА regional-indicator символа, у
    каждого из них len()==1, а в UTF-16 каждый занимает 2 юнита), эмодзи с
    тоном кожи, многие лица и т.п.

    Если это не учитывать, offset/length у entity получаются меньше
    настоящих, Telegram API отклоняет edit_text/answer с ошибкой на любом
    имени с таким символом внутри - а имя берётся из профиля пользователя
    в Telegram, отредактировать его мы не можем. Внешне это выглядело как
    "кнопка не реагирует": callback падал с исключением до call.answer().
    """
    return len(s.encode("utf-16-le")) // 2


def _display_name(seen_row) -> str | None:
    if not seen_row:
        return None
    if seen_row["full_name"]:
        return seen_row["full_name"]
    if seen_row["username"]:
        return f"@{seen_row['username']}"
    return None


async def mention_line(prefix: str, user_id: int) -> tuple[str, list[MessageEntity]]:
    """'prefix' + кликабельное имя (или голый ID, если имя неизвестно)."""
    seen_row = await database.get_seen_user(user_id)
    name = _display_name(seen_row)
    if not name:
        return f"{prefix}{user_id}", []
    text = f"{prefix}{name}"
    offset = _utf16_len(prefix)
    entity = MessageEntity(
        type="text_mention",
        offset=offset,
        length=_utf16_len(name),
        user=User(id=user_id, is_bot=False, first_name=name[:64]),
    )
    return text, [entity]


async def mention_line_with_id(prefix: str, user_id: int) -> tuple[str, list[MessageEntity]]:
    """Как mention_line(), но всегда добавляет "(id N)" после имени - чтобы
    в карточках ключа/пользователя было видно и кликабельное имя, и голый
    ID рядом. Если имя неизвестно - просто голый ID (дублировать его не
    нужно)."""
    seen_row = await database.get_seen_user(user_id)
    name = _display_name(seen_row)
    if not name:
        return f"{prefix}{user_id}", []
    text = f"{prefix}{name} (id {user_id})"
    offset = _utf16_len(prefix)
    entity = MessageEntity(
        type="text_mention",
        offset=offset,
        length=_utf16_len(name),
        user=User(id=user_id, is_bot=False, first_name=name[:64]),
    )
    return text, [entity]


async def button_label(user_id: int, prefix: str = "") -> str:
    """Подпись для inline-кнопки (кнопки не поддерживают entities - просто
    имя вместо ID, если оно известно; сама кнопка и так кликабельна)."""
    seen_row = await database.get_seen_user(user_id)
    name = _display_name(seen_row)
    return f"{prefix}{name or user_id}"


def join_lines(parts: list[tuple[str, list[MessageEntity]]], sep: str = "\n") -> tuple[str, list[MessageEntity]]:
    """Склеивает несколько (текст, entities) в одно сообщение, пересчитывая
    смещения (offset) сущностей под их новое место в общем тексте."""
    text = ""
    all_entities: list[MessageEntity] = []
    for i, (line, ents) in enumerate(parts):
        if i > 0:
            text += sep
        base_offset = _utf16_len(text)
        for e in ents:
            all_entities.append(e.model_copy(update={"offset": e.offset + base_offset}))
        text += line
    return text, all_entities
