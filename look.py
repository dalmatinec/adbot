"""Оформление, которое владелец меняет из админки: тексты экранов партнёра,
подписи, цвета и значки кнопок, шаблон отчёта.

Значения лежат в таблице settings и держатся в памяти. Текст хранится в HTML
так, как его прислал владелец, поэтому форматирование и премиум-эмодзи
сохраняются. В фигурных скобках стоят подстановки: {name}, {slots} и т. д.
"""

import json

import db

# ключ: (название в админке, текст по умолчанию)
TEXTS = {
    "home": ("Главный экран кабинета",
             "🏪 <b>{name}</b>\n\n"
             "📦 Рассылок: {used} из {slots}\n"
             "🟢 Идёт сейчас: {live}\n\n"
             "Выберите действие."),
    "frozen": ("Кабинет заморожен",
               "🏪 <b>{name}</b>\n\n"
               "❄️ Кабинет заморожен администрацией. Рассылки приостановлены.\n"
               "Для уточнения обратитесь к администрации."),
    "help": ("Как это работает",
             "ℹ️ <b>Как это работает</b>\n\n"
             "1. Нажмите <b>Новая рассылка</b> и задайте название.\n"
             "2. Пришлите пост. Можно написать его здесь или переслать из канала, группы "
             "или от другого бота.\n"
             "3. Выберите группу, проверьте пост и подтвердите запуск.\n\n"
             "Пост пересылается как есть, без изменений, с заданным интервалом.\n\n"
             "В карточке рассылки доступны превью, замена поста, пауза и удаление.\n"
             "Интервал и список групп задаёт администрация."),
    "step1": ("Новая рассылка, шаг 1: название",
              "➕ <b>Новая рассылка</b>\n"
              "Шаг 1 из 3. Название\n\n"
              "Пришлите название рассылки, до 40 символов. По нему вы будете находить её в списке. "
              "В группу название не попадает."),
    "step2": ("Новая рассылка, шаг 2: пост",
              "➕ <b>Новая рассылка</b>\n"
              "Шаг 2 из 3. Пост\n\n"
              "Название: {title}\n\n"
              "Пришлите пост одним сообщением. Его можно написать здесь или переслать "
              "из канала, группы или от бота. Пост будет пересылаться без изменений."),
    "choose": ("Новая рассылка: выбор группы",
               "✅ Пост принят.\n\nВыберите группу для рассылки."),
    "step3": ("Новая рассылка, шаг 3: подтверждение",
              "➕ <b>Новая рассылка</b>\n"
              "Шаг 3 из 3. Подтверждение\n\n"
              "🏷 Название: {title}\n"
              "💬 Группа: {group}\n"
              "⏱ Интервал: {interval}\n\n"
              "Выше показано, как пост будет выглядеть в группе. Запустить рассылку?"),
    "launched": ("Рассылка запущена",
                 "✅ <b>Рассылка #{id} запущена</b>\n\n"
                 "🏷 Название: {title}\n"
                 "💬 Группа: {group}\n"
                 "⏱ Интервал: {interval}\n\n"
                 "Первая отправка в течение минуты."),
    "post": ("Карточка рассылки",
             "📄 <b>Рассылка #{id}</b>\n"
             "{title}\n\n"
             "💬 Группа: {group}\n"
             "{status}\n"
             "⏱ Интервал: {interval}\n\n"
             "📊 Отправлено: {sent}\n"
             "🕓 Последняя отправка: {last}\n"
             "⏭ Следующая отправка: {next}\n"
             "📅 Создана: {created}"),
    "list": ("Список рассылок",
             "📋 <b>Мои рассылки</b>\n\nЗанято мест: {used} из {slots}"),
    "report": ("Ежедневный отчёт владельцам",
               "📊 <b>Отчёт</b>\n"
               "{date}\n\n"
               "📤 Отправок с прошлого отчёта: {sent}\n"
               "⚠️ Ошибок: {errors}\n\n"
               "🏪 Кабинетов: {cabinets}\n"
               "❄️ Заморожено: {frozen}\n\n"
               "📄 Рассылок: {posts}\n"
               "🟢 Идёт: {active}\n"
               "⏸ На паузе: {paused}\n"
               "⏹ Остановлено: {stopped}\n\n"
               "💬 Групп включено: {groups}\n\n"
               "{problems}"),
}

# значения для примера в админке
SAMPLE = {
    "name": "Магазин Ромашка", "used": "2", "slots": "3", "live": "2", "interval": "2 ч.",
    "title": "Акция ноября", "group": "Наша площадка", "id": "15", "status": "🟢 Статус: идёт",
    "sent": "14 раз", "last": "13.11.2026 02:00", "next": "13.11.2026 04:00", "created": "10.11.2026 12:30",
    "date": "13.11.2026", "errors": "0", "cabinets": "90", "frozen": "1", "posts": "210",
    "active": "200", "paused": "8", "stopped": "2", "groups": "3", "problems": "Рассылок с ошибками нет.",
}

BUTTONS = {
    "new": "➕ Новая рассылка",
    "posts": "📋 Мои рассылки",
    "people": "👥 Доступ",
    "help": "ℹ️ Как это работает",
    "back": "⬅️ Назад",
    "home": "🏠 В кабинет",
    "cancel": "✖️ Отмена",
    "launch": "✅ Запустить",
    "open": "📄 Открыть рассылку",
    "preview": "👁 Превью",
    "replace": "🔄 Заменить пост",
    "pause": "⏸ Пауза",
    "resume": "▶️ Возобновить",
    "rename": "✏️ Название",
    "delete": "🗑 Удалить",
    "to_list": "⬅️ К списку",
    "confirm_delete": "🗑 Да, удалить",
}

STYLES = {"": "стандартный", "primary": "синий", "success": "зелёный", "danger": "красный"}

_texts: dict[str, str] = {}
_buttons: dict[str, dict] = {}


async def load() -> None:
    _texts.clear()
    _buttons.clear()
    for key, value in (await db.settings_like("look:")).items():
        _, kind, name = key.split(":", 2)
        if kind == "t" and name in TEXTS:
            _texts[name] = value
        elif kind == "b" and name in BUTTONS:
            _buttons[name] = json.loads(value)


def placeholders(key: str) -> list[str]:
    """Подстановки, которые понимает этот текст (по стандартному варианту)."""
    import re
    return sorted(set(re.findall(r"\{(\w+)\}", TEXTS[key][1])), key=TEXTS[key][1].index)


def text(key: str, **values) -> str:
    """Текст экрана с подстановками. Значения должны быть уже экранированы."""
    out = _texts.get(key) or TEXTS[key][1]
    for name, value in values.items():
        out = out.replace("{" + name + "}", str(value))
    return out


def sample(key: str) -> str:
    return text(key, **SAMPLE)


def is_custom_text(key: str) -> bool:
    return key in _texts


async def set_text(key: str, html: str | None) -> None:
    if html is None:
        _texts.pop(key, None)
        await db.delete_setting(f"look:t:{key}")
    else:
        _texts[key] = html
        await db.set_setting(f"look:t:{key}", html)


def button(key: str) -> dict:
    """Текущий вид кнопки: text, style, icon."""
    custom = _buttons.get(key, {})
    return {"text": custom.get("text") or BUTTONS[key],
            "style": custom.get("style") or "", "icon": custom.get("icon") or ""}


def btn(key: str, data: str) -> tuple:
    """Кнопка для ui.kb с учётом оформления."""
    view = button(key)
    extra = {k: view[k] for k in ("style", "icon") if view[k]}
    return (view["text"], data, extra) if extra else (view["text"], data)


async def set_button(key: str, **changes) -> None:
    """changes: text, style, icon. Пустое значение возвращает стандартное."""
    custom = dict(_buttons.get(key, {}))
    for name, value in changes.items():
        if value:
            custom[name] = value
        else:
            custom.pop(name, None)
    if custom:
        _buttons[key] = custom
        await db.set_setting(f"look:b:{key}", json.dumps(custom, ensure_ascii=False))
    else:
        await reset_button(key)


async def reset_button(key: str) -> None:
    _buttons.pop(key, None)
    await db.delete_setting(f"look:b:{key}")
