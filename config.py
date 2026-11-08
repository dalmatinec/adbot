"""Настройки из переменных окружения (см. .env.example). В Docker их задаёт
docker-compose из файла .env, поэтому config.py руками править не нужно."""

import os


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(";", ",").split(",") if x.strip()]


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Стартовые владельцы основного бота. Остальных добавляют из админки.
OWNER_IDS = _ints(os.getenv("OWNER_IDS", ""))

DB_PATH = os.getenv("DB_PATH", "data/adbot.db")

# Приватный канал-хранилище постов: основной бот - админ. Посты лежат там и
# переживают смену основного бота (нового бота достаточно сделать админом).
STORAGE_CHAT_ID = _opt_int("STORAGE_CHAT_ID")
# Приватный канал для бэкапов (чтобы не засорять личку). Можно не задавать.
BACKUP_CHAT_ID = _opt_int("BACKUP_CHAT_ID")
BACKUP_EVERY_HOURS = int(os.getenv("BACKUP_EVERY_HOURS", "24"))

# Ключ шифрования токенов зеркал (Fernet). Сгенерировать:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Хранить отдельно от базы и бэкапов - иначе шифрование бессмысленно.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

DISPLAY_TZ = os.getenv("DISPLAY_TZ", "Asia/Almaty")
DISPLAY_TZ_LABEL = os.getenv("DISPLAY_TZ_LABEL", "Алматы")

# Лимиты
MAX_MIRROR_ADMINS = 3            # вместе с создателем зеркала
DEFAULT_SLOTS = 3                # постов на один токен по умолчанию
MAX_SLOTS = 10                   # потолок, выше слоты не поднять
MIN_INTERVAL_MINUTES = int(os.getenv("MIN_INTERVAL_MINUTES", "5"))
DEFAULT_INTERVAL_MINUTES = int(os.getenv("DEFAULT_INTERVAL_MINUTES", "60"))
APPLICATION_TTL_HOURS = int(os.getenv("APPLICATION_TTL_HOURS", "24"))  # без ответа заявка отклоняется
MAX_FILE_MB = 20                # Bot API не отдаёт ботам файлы больше 20 МБ

SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "1.5"))
SCHEDULER_TICK_SECONDS = int(os.getenv("SCHEDULER_TICK_SECONDS", "15"))
GROUP_ERRORS_BEFORE_DISABLE = 3

# Ссылка создания зеркала (Managed Bots). Если формат у Telegram поменяется -
# правится здесь, без правки кода. Подставляются {manager}, {username}, {name}.
MIRROR_LINK_TEMPLATE = os.getenv(
    "MIRROR_LINK_TEMPLATE",
    "https://t.me/newbot/{manager}/{username}?name={name}",
)


def validate() -> None:
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN не задан")
    if not OWNER_IDS:
        problems.append("OWNER_IDS не задан (хотя бы один Telegram ID)")
    if not ENCRYPTION_KEY:
        problems.append("ENCRYPTION_KEY не задан (см. .env.example)")
    if not STORAGE_CHAT_ID:
        problems.append("STORAGE_CHAT_ID не задан (канал-хранилище постов)")
    if problems:
        raise SystemExit("Ошибка настройки:\n - " + "\n - ".join(problems))
