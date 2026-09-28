"""
Вся работа с базой данных (SQLite, через aiosqlite).
Плоский модуль без ORM - простые функции-обёртки над SQL-запросами.
"""

import contextlib
import secrets
import string
from datetime import datetime, timedelta

import aiosqlite

import config
import texts

_KEY_ALPHABET = string.ascii_uppercase + string.digits


def _now() -> str:
    return datetime.utcnow().isoformat()


@contextlib.asynccontextmanager
async def _connect():
    """Открывает соединение с ОСНОВНОЙ базой (adbot.db) - операционные данные:
    ключи, рассылки, целевые чаты, статистика, баны. Это то, что чистит
    /factory_reset. Настройки - см. docstring ниже, общий для обеих БД.

    WAL + busy_timeout устраняют 'database is locked': раньше каждая функция
    открывала обычное соединение SQLite (журнал по умолчанию - DELETE), а
    бот одновременно пишет в базу из нескольких мест - обработчики апдейтов
    aiogram (могут выполняться параллельно) и фоновый планировщик рассылок
    (scheduler.py). Как только два таких соединения одновременно пытались
    писать - вторая транзакция сразу падала с OperationalError: database is
    locked, вместо того чтобы просто немного подождать.
      - WAL (Write-Ahead Logging) - позволяет читать базу, пока идёт запись,
        и не блокирует читателей писателем (и наоборот).
      - busy_timeout - если база всё же занята другой транзакцией, драйвер
        ждёт до 30 секунд, прежде чем поднять ошибку, вместо мгновенного отказа.
    """
    db = await aiosqlite.connect(config.DB_PATH)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        yield db
    finally:
        await db.close()


@contextlib.asynccontextmanager
async def _connect_content():
    """Отдельная база для "творческой" части - тексты (bot_texts) и
    оформление кнопок (button_settings), которые вы настраиваете через
    /admin1. Живёт в отдельном файле (config.CONTENT_DB_PATH), поэтому
    /factory_reset - который чистит основную базу под нового клиента -
    её не трогает: тексты/кнопки остаются как настроили."""
    db = await aiosqlite.connect(config.CONTENT_DB_PATH)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    async with _connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS super_admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_code TEXT UNIQUE NOT NULL,
                owner_id INTEGER,
                created_at TEXT NOT NULL,
                activated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id INTEGER NOT NULL REFERENCES keys(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT 'Без названия',
                source_chat_id INTEGER,
                source_message_id INTEGER,
                interval_hours INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 0,
                next_send_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                title TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER,
                key_id INTEGER,
                chat_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                banned_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS key_admins (
                key_id INTEGER NOT NULL REFERENCES keys(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (key_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS seen_users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS send_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER,
                key_id INTEGER,
                chat_id INTEGER NOT NULL,
                error TEXT,
                occurred_at TEXT NOT NULL
            );
            """
        )
        # Индексы. IF NOT EXISTS - безопасно вызывать на каждом старте, на
        # существующей базе ничего не пересоздаётся. Раньше часть индексов
        # была только в файле adbot.db, а в коде их не было - на чистой базе
        # (после переезда/пересоздания) они бы пропали.
        await db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_keys_owner ON keys(owner_id);
            CREATE INDEX IF NOT EXISTS idx_broadcasts_key ON broadcasts(key_id);
            CREATE INDEX IF NOT EXISTS idx_broadcasts_active ON broadcasts(is_active);
            CREATE INDEX IF NOT EXISTS idx_key_admins_key ON key_admins(key_id);
            CREATE INDEX IF NOT EXISTS idx_key_admins_user ON key_admins(user_id);
            -- статистика по чатам/дням: WHERE chat_id = ? AND sent_at >= ?
            CREATE INDEX IF NOT EXISTS idx_send_log_chat_sent ON send_log(chat_id, sent_at);
            CREATE INDEX IF NOT EXISTS idx_send_log_key ON send_log(key_id);
            CREATE INDEX IF NOT EXISTS idx_send_log_sent ON send_log(sent_at);
            """
        )
        await db.commit()


async def init_content_db() -> None:
    async with _connect_content() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS button_settings (
                button_key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                style TEXT NOT NULL DEFAULT 'primary',
                emoji_id TEXT,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bot_texts (
                text_key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                entities TEXT
            );
            """
        )
        await db.commit()
        # Миграция для баз, созданных до появления колонки entities
        # (иначе на старой базе бот упадёт с "no such column: entities").
        cur = await db.execute("PRAGMA table_info(bot_texts)")
        cols = [r[1] for r in await cur.fetchall()]
        if "entities" not in cols:
            await db.execute("ALTER TABLE bot_texts ADD COLUMN entities TEXT")
            await db.commit()


async def migrate_legacy_content() -> None:
    """Разовый перенос текстов/кнопок из старой единой базы (adbot.db,
    как было ДО этого обновления) в отдельную content.db. На новых
    установках (или уже перенесённых) в adbot.db этих таблиц нет - функция
    сразу выходит и ничего не делает, так что её безопасно вызывать при
    каждом старте бота."""
    async with _connect() as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('bot_texts', 'button_settings')"
        )
        legacy_tables = {r[0] for r in await cur.fetchall()}
        if not legacy_tables:
            return

        db.row_factory = aiosqlite.Row
        legacy_texts: list = []
        legacy_buttons: list = []
        if "bot_texts" in legacy_tables:
            cur = await db.execute("PRAGMA table_info(bot_texts)")
            has_entities = "entities" in [r[1] for r in await cur.fetchall()]
            if has_entities:
                cur = await db.execute("SELECT text_key, content, entities FROM bot_texts")
            else:
                cur = await db.execute("SELECT text_key, content, NULL AS entities FROM bot_texts")
            legacy_texts = await cur.fetchall()
        if "button_settings" in legacy_tables:
            cur = await db.execute(
                "SELECT button_key, label, style, emoji_id, position FROM button_settings"
            )
            legacy_buttons = await cur.fetchall()

    if legacy_texts or legacy_buttons:
        async with _connect_content() as cdb:
            for r in legacy_texts:
                await cdb.execute(
                    "INSERT OR IGNORE INTO bot_texts (text_key, content, entities) VALUES (?, ?, ?)",
                    (r["text_key"], r["content"], r["entities"]),
                )
            for r in legacy_buttons:
                await cdb.execute(
                    "INSERT OR IGNORE INTO button_settings "
                    "(button_key, label, style, emoji_id, position) VALUES (?, ?, ?, ?, ?)",
                    (r["button_key"], r["label"], r["style"], r["emoji_id"], r["position"]),
                )
            await cdb.commit()

    async with _connect() as db:
        await db.execute("DROP TABLE IF EXISTS bot_texts")
        await db.execute("DROP TABLE IF EXISTS button_settings")
        await db.commit()


async def factory_reset() -> None:
    """Полная очистка ОПЕРАЦИОННЫХ данных перед передачей боту нового
    клиента: ключи, рассылки, целевые чаты, статистика отправок, баны.
    НЕ трогает: тексты и оформление кнопок (они в content.db) и список
    супер-админов (чтобы после сброса не остаться без доступа к /adm)."""
    async with _connect() as db:
        await db.executescript(
            """
            DELETE FROM broadcasts;
            DELETE FROM keys;
            DELETE FROM target_chats;
            DELETE FROM send_log;
            DELETE FROM send_errors;
            DELETE FROM banned_users;
            DELETE FROM key_admins;
            DELETE FROM seen_users;
            """
        )
        await db.commit()


# ---------- супер-админы ----------

async def is_admin(user_id: int) -> bool:
    if user_id in config.OWNER_ID:
        return True
    async with _connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM super_admins WHERE user_id = ?", (user_id,)
        )
        return await cur.fetchone() is not None


async def list_admins() -> list[int]:
    async with _connect() as db:
        cur = await db.execute("SELECT user_id FROM super_admins ORDER BY added_at")
        rows = await cur.fetchall()
    admins = [r[0] for r in rows]
    for oid in config.OWNER_ID:
        if oid not in admins:
            admins.insert(0, oid)
    return admins


async def add_admin(user_id: int) -> bool:
    admins = await list_admins()
    if len(admins) >= config.MAX_SUPER_ADMINS:
        return False
    if user_id in admins:
        return False
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO super_admins (user_id, added_at) VALUES (?, ?)",
            (user_id, _now()),
        )
        await db.commit()
    return True


async def remove_admin(user_id: int) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM super_admins WHERE user_id = ?", (user_id,))
        await db.commit()


# ---------- ключи ----------

def _generate_key_code() -> str:
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(10))
    return f"{config.KEY_PREFIX}-{body}"


async def create_key() -> str:
    code = _generate_key_code()
    async with _connect() as db:
        await db.execute(
            "INSERT INTO keys (key_code, owner_id, created_at) VALUES (?, NULL, ?)",
            (code, _now()),
        )
        await db.commit()
    return code


async def get_key_by_code(code: str) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM keys WHERE key_code = ?", (code,))
        return await cur.fetchone()


async def activate_key(key_id: int, owner_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE keys SET owner_id = ?, activated_at = ? WHERE id = ?",
            (owner_id, _now(), key_id),
        )
        await db.commit()


async def get_key_by_id(key_id: int) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM keys WHERE id = ?", (key_id,))
        return await cur.fetchone()


async def get_key_by_owner(owner_id: int) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM keys WHERE owner_id = ? AND activated_at IS NOT NULL",
            (owner_id,),
        )
        return await cur.fetchone()


async def delete_key(key_id: int) -> None:
    """Удаляет ключ и все его рассылки (истёкшие/отозванные ключи не архивируются)."""
    async with _connect() as db:
        await db.execute("DELETE FROM broadcasts WHERE key_id = ?", (key_id,))
        await db.execute("DELETE FROM keys WHERE id = ?", (key_id,))
        await db.commit()


async def deactivate_all_broadcasts(key_id: int) -> int:
    """Выключает ВСЕ рассылки владельца ключа, не трогая сам ключ (в отличие
    от delete_key - рассылки не удаляются, просто is_active=0, как при
    ручном выключении каждой по кнопке). Владелец сам сможет включить их
    заново себе в меню. Возвращает число реально выключенных рассылок."""
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE broadcasts SET is_active = 0, next_send_at = NULL "
            "WHERE key_id = ? AND is_active = 1",
            (key_id,),
        )
        await db.commit()
        return cur.rowcount


async def list_all_keys() -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM keys ORDER BY created_at DESC")
        return await cur.fetchall()


# ---------- рассылки ----------

async def count_broadcasts(key_id: int) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM broadcasts WHERE key_id = ?", (key_id,)
        )
        row = await cur.fetchone()
        return row[0]


async def list_broadcasts(key_id: int) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM broadcasts WHERE key_id = ? ORDER BY created_at",
            (key_id,),
        )
        return await cur.fetchall()


async def get_broadcast(broadcast_id: int) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)
        )
        return await cur.fetchone()


async def create_broadcast(key_id: int, title: str) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO broadcasts (key_id, title, interval_hours, is_active, created_at) "
            "VALUES (?, ?, 1, 0, ?)",
            (key_id, title, _now()),
        )
        await db.commit()
        return cur.lastrowid


async def set_broadcast_post(broadcast_id: int, chat_id: int, message_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcasts SET source_chat_id = ?, source_message_id = ? WHERE id = ?",
            (chat_id, message_id, broadcast_id),
        )
        await db.commit()


async def set_broadcast_interval(broadcast_id: int, hours: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcasts SET interval_hours = ? WHERE id = ?",
            (hours, broadcast_id),
        )
        await db.commit()


async def set_broadcast_title(broadcast_id: int, title: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcasts SET title = ? WHERE id = ?", (title, broadcast_id)
        )
        await db.commit()


async def toggle_broadcast(broadcast_id: int, active: bool) -> None:
    """Включает/выключает рассылку.

    При включении next_send_at намеренно НЕ выставляется здесь: первую
    отправку делает сам хендлер (handlers_user.cb_toggle) сразу, синхронно,
    в момент нажатия кнопки - а сразу после неё вызывает reschedule_broadcast,
    который и ставит next_send_at = сейчас + интервал. Так рассылка уходит
    мгновенно при включении, а не только после ожидания полного интервала.
    (due_broadcasts() ниже подхватит и NULL-строки - это подстраховка на
    случай сбоя между этими двумя вызовами.)
    """
    async with _connect() as db:
        if active:
            await db.execute(
                "UPDATE broadcasts SET is_active = 1 WHERE id = ?", (broadcast_id,)
            )
        else:
            await db.execute(
                "UPDATE broadcasts SET is_active = 0, next_send_at = NULL WHERE id = ?",
                (broadcast_id,),
            )
        await db.commit()


async def delete_broadcast(broadcast_id: int) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM broadcasts WHERE id = ?", (broadcast_id,))
        await db.commit()


async def due_broadcasts() -> list[aiosqlite.Row]:
    """Активные рассылки, которым пора отправляться.
    next_send_at IS NULL тоже считается "пора" - это подстраховка для
    рассылок, включённых до того, как для них успели проставить время
    следующей отправки (см. toggle_broadcast).

    JOIN c keys - рассылка без живого ключа (осиротевшая, см.
    find_orphan_broadcasts) сюда не попадёт, даже если у неё is_active=1 -
    её отдельно гасит deactivate_orphan_broadcasts() в scheduler._tick()."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT b.* FROM broadcasts b JOIN keys k ON k.id = b.key_id "
            "WHERE b.is_active = 1 AND (b.next_send_at IS NULL OR b.next_send_at <= ?)",
            (_now(),),
        )
        return await cur.fetchall()


async def find_orphan_broadcasts() -> list[aiosqlite.Row]:
    """Рассылки, чей ключ удалён не через delete_key (например, правкой базы
    напрямую) - key_id смотрит в никуда. В обычной работе бота таких быть не
    должно (delete_key всегда удаляет рассылки вместе с ключом одной
    транзакцией), но раздел /adm -> Рассылки -> "Без ключа" должен уметь их
    найти и показать, если всё-таки завелись."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT b.* FROM broadcasts b LEFT JOIN keys k ON k.id = b.key_id "
            "WHERE k.id IS NULL ORDER BY b.id"
        )
        return await cur.fetchall()


async def deactivate_orphan_broadcasts() -> list[aiosqlite.Row]:
    """Гасит все ВКЛЮЧЁННЫЕ осиротевшие рассылки и возвращает те, что реально
    выключила (для уведомления админов) - вызывается планировщиком на
    каждом тике, ДО отправки due_broadcasts(). Не трогает уже выключенные
    сироты - их админ видит и разбирает вручную через /adm."""
    orphans = await find_orphan_broadcasts()
    active = [b for b in orphans if b["is_active"]]
    if not active:
        return []
    async with _connect() as db:
        await db.executemany(
            "UPDATE broadcasts SET is_active = 0, next_send_at = NULL WHERE id = ?",
            [(b["id"],) for b in active],
        )
        await db.commit()
    return active


async def reschedule_broadcast(broadcast_id: int, interval_hours: int) -> None:
    next_send_at = (datetime.utcnow() + timedelta(hours=interval_hours)).isoformat()
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcasts SET next_send_at = ? WHERE id = ?",
            (next_send_at, broadcast_id),
        )
        await db.commit()


# ---------- целевые чаты ----------

async def list_target_chats(active_only: bool = True) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if active_only:
            cur = await db.execute(
                "SELECT * FROM target_chats WHERE is_active = 1"
            )
        else:
            cur = await db.execute("SELECT * FROM target_chats")
        return await cur.fetchall()


async def list_target_chats_with_stats() -> list[dict]:
    """Список целевых чатов вместе с числом отправленных в них рассылок -
    для раздела /adm -> "Целевые чаты"."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tc.*, "
            "(SELECT COUNT(*) FROM send_log sl WHERE sl.chat_id = tc.chat_id) AS total_sent "
            "FROM target_chats tc ORDER BY tc.added_at"
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_target_chat(chat_id: int) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM target_chats WHERE chat_id = ?", (chat_id,))
        return await cur.fetchone()


async def add_target_chat(chat_id: int, title: str | None = None) -> None:
    """Добавляет чат в список целевых (не трогая остальные - в отличие от
    старого set_target_chat, рассылки теперь идут сразу во все активные
    целевые чаты)."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO target_chats (chat_id, title, is_active, added_at) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET is_active = 1, title = excluded.title",
            (chat_id, title, _now()),
        )
        await db.commit()


async def remove_target_chat(chat_id: int) -> None:
    """Полностью удаляет чат из списка целевых. Статистика по уже
    отправленным туда рассылкам (send_log) не удаляется - так что счётчики
    в старых карточках рассылок останутся верными."""
    async with _connect() as db:
        await db.execute("DELETE FROM target_chats WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def log_send(broadcast_id: int, key_id: int | None, chat_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO send_log (broadcast_id, key_id, chat_id, sent_at) VALUES (?, ?, ?, ?)",
            (broadcast_id, key_id, chat_id, _now()),
        )
        await db.commit()


async def chat_stats_summary(chat_id: int) -> dict:
    """Короткая сводка по целевому чату для карточки в /adm: сколько всего
    рассылок сюда ушло и сколько из них - сегодня. Без разбивки по ключам и
    пользователям - с ростом числа рекламодателей такая простыня текста не
    поместится на экран, поэтому карточка группы теперь показывает только
    сводные цифры."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) AS c FROM send_log WHERE chat_id = ?", (chat_id,))
        total_sent = (await cur.fetchone())["c"]
        today = datetime.utcnow().date().isoformat()
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM send_log WHERE chat_id = ? AND sent_at >= ?", (chat_id, today)
        )
        sent_today = (await cur.fetchone())["c"]
    return {"total_sent": total_sent, "sent_today": sent_today}


# ---------- пользователи / бан ----------

async def is_banned(user_id: int) -> bool:
    async with _connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)
        )
        return await cur.fetchone() is not None


async def ban_user(user_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO banned_users (user_id, banned_at) VALUES (?, ?)",
            (user_id, _now()),
        )
        # у забаненного отключаем все его рассылки
        cur = await db.execute("SELECT id FROM keys WHERE owner_id = ?", (user_id,))
        rows = await cur.fetchall()
        for (key_id,) in rows:
            await db.execute(
                "UPDATE broadcasts SET is_active = 0 WHERE key_id = ?", (key_id,)
            )
        await db.commit()


async def unban_user(user_id: int) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_owners() -> list[aiosqlite.Row]:
    """Список всех пользователей, у которых есть активированный ключ (для раздела 'Пользователи')."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM keys WHERE owner_id IS NOT NULL ORDER BY activated_at DESC"
        )
        return await cur.fetchall()


# ---------- оформление кнопок (для /admin1) ----------

_DEFAULT_BUTTONS = {
    "bc_title": ("📝 Название рассылки", "primary", 0),
    "bc_post": ("📩 Загрузить / заменить пост", "primary", 1),
    "bc_interval": ("⏱ Интервал", "primary", 2),
    "bc_toggle_on": ("▶️ Включить", "success", 3),
    "bc_toggle_off": ("⏸ Выключить", "danger", 3),
    "bc_status": ("📊 Статус", "primary", 4),
    "bc_delete": ("🗑 Удалить рассылку", "danger", 5),
    "bc_new": ("➕ Новая рассылка", "success", 6),
    "adv_mykey": ("🆔 Мой ключ", "primary", 7),
    "adv_help": ("ℹ️ Помощь", "primary", 8),
    "adv_broadcasts": ("📋 Мои рассылки", "primary", 9),
    "adv_admins": ("👥 Админы", "primary", 10),
    "btn_back": ("🔙 Назад", "danger", 100),
    "btn_cancel": ("✖️ Отмена", "danger", 101),
}


async def ensure_default_buttons() -> None:
    async with _connect_content() as db:
        for key, (label, style, position) in _DEFAULT_BUTTONS.items():
            await db.execute(
                "INSERT OR IGNORE INTO button_settings "
                "(button_key, label, style, emoji_id, position) VALUES (?, ?, ?, NULL, ?)",
                (key, label, style, position),
            )
        await db.commit()


async def get_button(button_key: str) -> aiosqlite.Row:
    async with _connect_content() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM button_settings WHERE button_key = ?", (button_key,)
        )
        row = await cur.fetchone()
        if row:
            return row
    label, style, position = _DEFAULT_BUTTONS[button_key]
    return {"button_key": button_key, "label": label, "style": style,
            "emoji_id": None, "position": position}


async def list_editable_buttons() -> list[aiosqlite.Row]:
    async with _connect_content() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM button_settings WHERE button_key LIKE 'bc_%' "
            "OR button_key LIKE 'adv_%' ORDER BY position"
        )
        return await cur.fetchall()


async def update_button(
    button_key: str,
    label: str | None = None,
    style: str | None = None,
    emoji_id: str | None = None,
) -> None:
    current = await get_button(button_key)
    label = label if label is not None else current["label"]
    style = style if style is not None else current["style"]
    emoji_id = emoji_id if emoji_id is not None else current["emoji_id"]
    async with _connect_content() as db:
        await db.execute(
            "INSERT INTO button_settings (button_key, label, style, emoji_id, position) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT position FROM button_settings WHERE button_key = ?), 0)) "
            "ON CONFLICT(button_key) DO UPDATE SET label = excluded.label, "
            "style = excluded.style, emoji_id = excluded.emoji_id",
            (button_key, label, style, emoji_id, button_key),
        )
        await db.commit()


async def swap_button_position(button_key: str, direction: str) -> None:
    """direction: 'up' или 'down' - меняет местами позицию с соседней кнопкой."""
    buttons = await list_editable_buttons()
    idx = next((i for i, b in enumerate(buttons) if b["button_key"] == button_key), None)
    if idx is None:
        return
    target_idx = idx - 1 if direction == "up" else idx + 1
    if target_idx < 0 or target_idx >= len(buttons):
        return
    a, b = buttons[idx], buttons[target_idx]
    async with _connect_content() as db:
        await db.execute(
            "UPDATE button_settings SET position = ? WHERE button_key = ?",
            (b["position"], a["button_key"]),
        )
        await db.execute(
            "UPDATE button_settings SET position = ? WHERE button_key = ?",
            (a["position"], b["button_key"]),
        )
        await db.commit()


# ---------- редактируемые тексты (для /admin1 -> "Тексты") ----------

async def ensure_default_texts() -> None:
    async with _connect_content() as db:
        for key, default in texts.TEXT_DEFAULTS.items():
            await db.execute(
                "INSERT OR IGNORE INTO bot_texts (text_key, content) VALUES (?, ?)",
                (key, default),
            )
        await db.commit()


async def get_text(key: str) -> str:
    """Возвращает текущий текст: из БД, если он там переопределён,
    иначе - значение по умолчанию из texts.py."""
    async with _connect_content() as db:
        cur = await db.execute("SELECT content FROM bot_texts WHERE text_key = ?", (key,))
        row = await cur.fetchone()
    if row:
        return row[0]
    return texts.TEXT_DEFAULTS.get(key, f"[[{key}]]")


async def get_text_with_entities(key: str) -> tuple[str, list[dict] | None]:
    """Как get_text(), но дополнительно возвращает разметку сущностей
    (list[dict], формат aiogram MessageEntity.model_dump()) - нужно, чтобы
    кастомные эмодзи, вставленные через /admin1 -> "Тексты", реально
    отображались как кастомные эмодзи, а не терялись при сохранении текста
    (Telegram хранит кастомный эмодзи не в самом тексте, а отдельной
    сущностью entity поверх обычного символа-заглушки)."""
    import json
    async with _connect_content() as db:
        cur = await db.execute(
            "SELECT content, entities FROM bot_texts WHERE text_key = ?", (key,)
        )
        row = await cur.fetchone()
    if row:
        content, raw_entities = row
        entities = json.loads(raw_entities) if raw_entities else None
        return content, entities
    return texts.TEXT_DEFAULTS.get(key, f"[[{key}]]"), None


async def t(key: str, **kwargs) -> str:
    """get_text() + безопасная подстановка плейсхолдеров.
    Если админ через /admin1 случайно испортил плейсхолдер (например,
    оставил незакрытую фигурную скобку), .format() не должен ронять бота -
    в этом случае просто возвращаем текст как есть, без подстановки."""
    text = await get_text(key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


async def render_text(key: str, **kwargs) -> tuple[str, list | None]:
    """Готовит текст к отправке пользователю вместе с его сущностями
    (aiogram MessageEntity) - то есть с сохранением кастомных эмодзи и
    прочего форматирования, вставленного через /admin1 -> "Тексты".

    Если у текста есть плейсхолдеры ({count}, {hours}...), entities не
    возвращаются: после .format() длина и смещения символов меняются, и
    сохранённая разметка станет указывать не на те символы. В этом случае
    текст всё равно подставится корректно - просто без кастомного эмодзи.
    Отправлять результат нужно с parse_mode=None (см. keyboards/ и хендлеры) -
    иначе глобальный ParseMode.HTML бота будет конфликтовать с entities.
    """
    from aiogram.types import MessageEntity

    content, raw_entities = await get_text_with_entities(key)
    if kwargs:
        try:
            content = content.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
        return content, None
    if not raw_entities:
        return content, None
    return content, [MessageEntity(**e) for e in raw_entities]


async def update_text(key: str, content: str, entities: list[dict] | None = None) -> None:
    """entities - сериализованные MessageEntity (см. get_text_with_entities).
    Передавайте None, если в тексте нет спецформатирования/кастомных эмодзи -
    тогда старая разметка (если была) стирается, что и нужно при обычном
    редактировании текста заново."""
    import json
    entities_json = json.dumps(entities) if entities else None
    async with _connect_content() as db:
        await db.execute(
            "INSERT INTO bot_texts (text_key, content, entities) VALUES (?, ?, ?) "
            "ON CONFLICT(text_key) DO UPDATE SET content = excluded.content, "
            "entities = excluded.entities",
            (key, content, entities_json),
        )
        await db.commit()


async def reset_text(key: str) -> None:
    """Удаляет переопределение - get_text() снова начнёт возвращать значение
    по умолчанию из texts.py."""
    async with _connect_content() as db:
        await db.execute("DELETE FROM bot_texts WHERE text_key = ?", (key,))
        await db.commit()


async def list_texts_by_group(group: str) -> list[dict]:
    keys = [k for k, meta in texts.TEXT_META.items() if meta["group"] == group]
    async with _connect_content() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT text_key, content FROM bot_texts")
        overrides = {r["text_key"]: r["content"] for r in await cur.fetchall()}
    result = []
    for key in keys:
        content = overrides.get(key, texts.TEXT_DEFAULTS[key])
        result.append({"key": key, "label": texts.TEXT_META[key]["label"], "content": content})
    return result


# ---------- админы конкретного ключа (сабадмины рекламодателя) ----------

async def get_key_for_user(user_id: int) -> aiosqlite.Row | None:
    """Ключ, к которому у пользователя есть доступ к меню рекламодателя:
    либо он владелец (owner_id), либо добавлен владельцем как админ ключа
    (key_admins). Именно эту функцию нужно использовать вместо
    get_key_by_owner() везде, где рекламодателю показывается меню - иначе
    админы ключа не смогут в него попасть."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM keys WHERE owner_id = ? AND activated_at IS NOT NULL", (user_id,)
        )
        row = await cur.fetchone()
        if row:
            return row
        cur = await db.execute(
            "SELECT k.* FROM keys k JOIN key_admins ka ON ka.key_id = k.id "
            "WHERE ka.user_id = ? AND k.activated_at IS NOT NULL",
            (user_id,),
        )
        return await cur.fetchone()


async def is_key_owner(key_id: int, user_id: int) -> bool:
    async with _connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM keys WHERE id = ? AND owner_id = ?", (key_id, user_id)
        )
        return await cur.fetchone() is not None


async def list_key_admins(key_id: int) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM key_admins WHERE key_id = ? ORDER BY added_at", (key_id,)
        )
        return await cur.fetchall()


async def add_key_admin(key_id: int, user_id: int, limit: int) -> bool:
    admins = await list_key_admins(key_id)
    if len(admins) >= limit:
        return False
    if any(a["user_id"] == user_id for a in admins):
        return False
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO key_admins (key_id, user_id, added_at) VALUES (?, ?, ?)",
            (key_id, user_id, _now()),
        )
        await db.commit()
    return True


async def remove_key_admin(key_id: int, user_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "DELETE FROM key_admins WHERE key_id = ? AND user_id = ?", (key_id, user_id)
        )
        await db.commit()


# ---------- учёт имён пользователей (для кликабельных упоминаний) ----------

async def upsert_seen_user(user_id: int, full_name: str | None, username: str | None) -> None:
    """Вызывается middleware на каждом апдейте - так у бота появляется
    имя/username человека, даже если он никогда не был "в системе" отдельной
    записью (нужно, чтобы показывать кликабельное имя вместо голого ID)."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO seen_users (user_id, full_name, username, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET full_name = excluded.full_name, "
            "username = excluded.username, last_seen = excluded.last_seen",
            (user_id, full_name, username, _now(), _now()),
        )
        await db.commit()


async def get_seen_user(user_id: int) -> aiosqlite.Row | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM seen_users WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


# ---------- лог ошибок отправки (для статистики) ----------

async def log_send_error(broadcast_id: int | None, key_id: int | None, chat_id: int, error: str) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO send_errors (broadcast_id, key_id, chat_id, error, occurred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (broadcast_id, key_id, chat_id, error[:300], _now()),
        )
        await db.commit()


async def recent_send_errors(limit: int = 10) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM send_errors ORDER BY id DESC LIMIT ?", (limit,)
        )
        return await cur.fetchall()


# ---------- общая статистика (для /adm -> "Статистика") ----------

async def overall_stats() -> dict:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) AS c FROM keys")
        total_keys = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM keys WHERE owner_id IS NOT NULL")
        activated_keys = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(DISTINCT owner_id) AS c FROM keys WHERE owner_id IS NOT NULL")
        total_users = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM broadcasts WHERE is_active = 1")
        active_broadcasts = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM target_chats WHERE is_active = 1")
        active_chats = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM send_log")
        total_sent = (await cur.fetchone())["c"]
        today = datetime.utcnow().date().isoformat()
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM send_log WHERE sent_at >= ?", (today,)
        )
        sent_today = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM send_errors")
        total_errors = (await cur.fetchone())["c"]
    return {
        "total_keys": total_keys,
        "activated_keys": activated_keys,
        "total_users": total_users,
        "active_broadcasts": active_broadcasts,
        "active_chats": active_chats,
        "total_sent": total_sent,
        "sent_today": sent_today,
        "total_errors": total_errors,
    }


async def top_advertisers(limit: int = 5) -> list[dict]:
    """Топ рекламодателей по числу отправленных сообщений (все чаты суммарно)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT sl.key_id AS key_id, k.key_code AS key_code, k.owner_id AS owner_id, "
            "COUNT(*) AS cnt "
            "FROM send_log sl LEFT JOIN keys k ON k.id = sl.key_id "
            "GROUP BY sl.key_id ORDER BY cnt DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
