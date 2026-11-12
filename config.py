"""Настройки из переменных окружения (см. .env.example)."""

import os


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(";", ",").split(",") if x.strip()]


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Стартовые владельцы бота. Остальных добавляют из админки.
OWNER_IDS = _ints(os.getenv("OWNER_IDS", ""))

DB_PATH = os.getenv("DB_PATH", "data/adbot.db")

# Приватный канал или группа, где лежат принятые посты. Бот там админ.
STORAGE_CHAT_ID = _opt_int("STORAGE_CHAT_ID")
# Приватный канал для бэкапов. Можно не задавать.
BACKUP_CHAT_ID = _opt_int("BACKUP_CHAT_ID")
BACKUP_EVERY_HOURS = int(os.getenv("BACKUP_EVERY_HOURS", "24"))

DISPLAY_TZ = os.getenv("DISPLAY_TZ", "Asia/Almaty")
DISPLAY_TZ_LABEL = os.getenv("DISPLAY_TZ_LABEL", "Алматы")

DEFAULT_SLOTS = 3                 # постов на кабинет по умолчанию
MAX_SLOTS = 10                    # потолок слотов
DEFAULT_INTERVAL_HOURS = int(os.getenv("DEFAULT_INTERVAL_HOURS", "2"))
INTERVAL_CHOICES = (1, 2, 3, 4, 6, 8, 12, 24)   # часы, кнопки в админке
INVITE_TTL_HOURS = int(os.getenv("INVITE_TTL_HOURS", "48"))

# Антифлуд рассылки: пауза между любыми двумя отправками и между постами
# в одну и ту же группу.
SEND_GAP_SECONDS = float(os.getenv("SEND_GAP_SECONDS", "1"))
GROUP_GAP_SECONDS = float(os.getenv("GROUP_GAP_SECONDS", "3"))
SCHEDULER_TICK_SECONDS = int(os.getenv("SCHEDULER_TICK_SECONDS", "10"))
GROUP_ERRORS_BEFORE_DISABLE = 3
# Антифлуд кнопок: чаще этого нажатия одного человека не обрабатываются.
CLICK_GAP_SECONDS = 0.4


def validate() -> None:
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN не задан")
    if not OWNER_IDS:
        problems.append("OWNER_IDS не задан (хотя бы один Telegram ID)")
    if not STORAGE_CHAT_ID:
        problems.append("STORAGE_CHAT_ID не задан (хранилище постов)")
    if problems:
        raise SystemExit("Ошибка настройки:\n - " + "\n - ".join(problems))
