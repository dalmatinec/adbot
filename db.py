"""SQLite: схема, индексы и вся работа с данными.

Один файл, одно соединение, запросы идут в отдельном потоке под замком.
Права доступа (владельцы бота и участники кабинетов) держатся в памяти и
перечитываются только при изменении, поэтому на обычное нажатие кнопки
база для проверки доступа не трогается. Время хранится в UTC (ISO).
"""

import asyncio
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import config

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

# кэш доступа: user_id -> cabinet_id, и множество владельцев бота
_owners: set[int] = set()
_members: dict[int, int] = {}
_names: dict[int, tuple] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    user_id  INTEGER PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    full_name  TEXT,
    username   TEXT,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        INTEGER UNIQUE NOT NULL,
    title          TEXT,
    is_active      INTEGER NOT NULL DEFAULT 0,   -- рассылка в группу включена
    is_public      INTEGER NOT NULL DEFAULT 0,   -- группа видна партнёрам
    interval_hours INTEGER,                      -- NULL = общий интервал
    error_streak   INTEGER NOT NULL DEFAULT 0,
    added_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cabinets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    slots      INTEGER NOT NULL DEFAULT 3,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | frozen
    all_groups INTEGER NOT NULL DEFAULT 0,       -- видит и скрытые группы
    created_at TEXT NOT NULL
);

-- один человек состоит только в одном кабинете
CREATE TABLE IF NOT EXISTS members (
    user_id    INTEGER PRIMARY KEY,
    cabinet_id INTEGER NOT NULL REFERENCES cabinets(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,                    -- owner | helper
    added_at   TEXT NOT NULL
);

-- одноразовые ссылки входа в кабинет
CREATE TABLE IF NOT EXISTS invites (
    token      TEXT PRIMARY KEY,
    cabinet_id INTEGER NOT NULL REFERENCES cabinets(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cabinet_id      INTEGER NOT NULL REFERENCES cabinets(id) ON DELETE CASCADE,
    group_id        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    created_by      INTEGER NOT NULL,
    title           TEXT NOT NULL,
    named           INTEGER NOT NULL DEFAULT 0,  -- 1 = название задано вручную
    storage_chat_id INTEGER NOT NULL,
    storage_ids     TEXT NOT NULL,               -- id сообщений через запятую (альбом = несколько)
    status          TEXT NOT NULL DEFAULT 'active',  -- active | paused | stopped
    created_at      TEXT NOT NULL,
    last_sent_at    TEXT,
    next_send_at    TEXT,
    sent_count      INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- журнал действий владельцев бота
CREATE TABLE IF NOT EXISTS actions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text    TEXT NOT NULL,
    at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_members_cabinet ON members(cabinet_id);
CREATE INDEX IF NOT EXISTS idx_invites_cabinet ON invites(cabinet_id);
CREATE INDEX IF NOT EXISTS idx_posts_cabinet   ON posts(cabinet_id);
CREATE INDEX IF NOT EXISTS idx_posts_group     ON posts(group_id);
CREATE INDEX IF NOT EXISTS idx_posts_due       ON posts(status, next_send_at);
"""


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def now() -> str:
    return _utc().isoformat(timespec="seconds")


def in_seconds(seconds: int) -> str:
    return (_utc() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


# ---------- низкоуровневое ----------

def _run(sql: str, params: tuple, mode: str):
    with _lock:
        cur = _conn.execute(sql, params)
        if mode == "one":
            return cur.fetchone()
        if mode == "all":
            return cur.fetchall()
        _conn.commit()
        return cur.lastrowid if mode == "insert" else cur.rowcount


async def one(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_run, sql, params, "one")


async def all_(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_run, sql, params, "all")


async def execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(_run, sql, params, "exec")


async def insert(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(_run, sql, params, "insert")


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # встроенный LOWER в SQLite не понимает кириллицу, для поиска нужен свой
    conn.create_function("lower_u", 1, lambda s: s.lower() if isinstance(s, str) else s)
    return conn


async def init(path: str | None = None) -> None:
    global _conn
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _conn = _open(path)
    # База прежней версии (с зеркалами) несовместима: откладываем её в сторону
    # и начинаем с чистой, чтобы бот не падал на старой схеме.
    old = _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mirrors'").fetchone()
    if old and path != ":memory:":
        _conn.close()
        stamp = _utc().strftime("%Y%m%d%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.replace(path + suffix, f"{path}.old-{stamp}{suffix}")
        _conn = _open(path)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.execute("PRAGMA busy_timeout=30000")
    _conn.execute("PRAGMA cache_size=-4000")   # до 4 МБ кэша страниц
    _conn.executescript(SCHEMA)
    columns = {r["name"] for r in _conn.execute("PRAGMA table_info(posts)")}
    if "named" not in columns:   # база, созданная до появления названий
        _conn.execute("ALTER TABLE posts ADD COLUMN named INTEGER NOT NULL DEFAULT 0")
    for uid in config.OWNER_IDS:
        _conn.execute("INSERT OR IGNORE INTO owners(user_id, added_at) VALUES (?, ?)", (uid, now()))
    _conn.commit()
    _reload_access()


def _reload_access() -> None:
    with _lock:
        owners = {r["user_id"] for r in _conn.execute("SELECT user_id FROM owners")}
        members = {r["user_id"]: r["cabinet_id"] for r in _conn.execute("SELECT user_id, cabinet_id FROM members")}
    _owners.clear()
    _owners.update(owners)
    _members.clear()
    _members.update(members)


def snapshot(dest_path: str) -> None:
    """Согласованная копия базы (SQLite backup API), безопасна при работе бота."""
    with _lock:
        target = sqlite3.connect(dest_path)
        try:
            _conn.backup(target)
        finally:
            target.close()


# ---------- доступ (из памяти, без запросов) ----------

def is_owner(user_id: int) -> bool:
    return user_id in _owners


def cabinet_of(user_id: int) -> int | None:
    return _members.get(user_id)


def is_known(user_id: int) -> bool:
    return user_id in _owners or user_id in _members


# ---------- настройки ----------

async def get_setting(key: str, default: str | None = None) -> str | None:
    row = await one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


async def settings_like(prefix: str) -> dict:
    rows = await all_("SELECT key, value FROM settings WHERE key LIKE ?", (prefix + "%",))
    return {r["key"]: r["value"] for r in rows}


async def delete_setting(key: str) -> None:
    await execute("DELETE FROM settings WHERE key=?", (key,))


async def bump(key: str) -> None:
    """Счётчик в настройках: +1 одним запросом."""
    await execute(
        "INSERT INTO settings(key, value) VALUES (?, '1') "
        "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1", (key,))


async def global_interval() -> int:
    return int(await get_setting("interval_hours", str(config.DEFAULT_INTERVAL_HOURS)))


async def effective_interval(group_interval: int | None) -> int:
    """Интервал в часах: свой у группы или общий."""
    return group_interval or await global_interval()


# ---------- пользователи и владельцы ----------

async def touch_user(user_id: int, full_name: str | None, username: str | None) -> None:
    """Запоминает имя. Пишет в базу только когда имя или юзернейм изменились."""
    if _names.get(user_id) == (full_name, username):
        return
    _names[user_id] = (full_name, username)
    await execute(
        "INSERT INTO users(user_id, full_name, username, first_seen) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username",
        (user_id, full_name, username, now()))


async def get_user(user_id: int):
    return await one("SELECT * FROM users WHERE user_id=?", (user_id,))


async def list_owners():
    return await all_(
        "SELECT o.user_id, u.full_name, u.username FROM owners o "
        "LEFT JOIN users u ON u.user_id=o.user_id ORDER BY o.added_at")


async def add_owner(user_id: int) -> None:
    await execute("INSERT OR IGNORE INTO owners(user_id, added_at) VALUES (?, ?)", (user_id, now()))
    _reload_access()


async def remove_owner(user_id: int) -> bool:
    """Последнего владельца удалить нельзя."""
    if len(_owners) <= 1 or user_id not in _owners:
        return False
    await execute("DELETE FROM owners WHERE user_id=?", (user_id,))
    _reload_access()
    return True


# ---------- группы ----------

async def add_group(chat_id: int, title: str | None) -> int:
    row = await one("SELECT id FROM groups WHERE chat_id=?", (chat_id,))
    if row:
        await execute("UPDATE groups SET title=? WHERE id=?", (title, row["id"]))
        return row["id"]
    return await insert(
        "INSERT INTO groups(chat_id, title, added_at) VALUES (?, ?, ?)", (chat_id, title, now()))


async def get_group(group_id: int):
    return await one("SELECT * FROM groups WHERE id=?", (group_id,))


async def get_group_by_chat(chat_id: int):
    return await one("SELECT * FROM groups WHERE chat_id=?", (chat_id,))


async def list_groups():
    return await all_(
        "SELECT g.*, (SELECT COUNT(*) FROM posts p WHERE p.group_id=g.id) AS posts "
        "FROM groups g ORDER BY g.id")


async def groups_for_cabinet(cabinet) -> list:
    """Включённые группы, доступные кабинету: открытые, а при all_groups все."""
    if cabinet["all_groups"]:
        return await all_("SELECT * FROM groups WHERE is_active=1 ORDER BY id")
    return await all_("SELECT * FROM groups WHERE is_active=1 AND is_public=1 ORDER BY id")


async def set_group_flag(group_id: int, field: str, value: bool) -> None:
    assert field in ("is_active", "is_public")
    extra = ", error_streak=0" if field == "is_active" and value else ""
    await execute(f"UPDATE groups SET {field}=?{extra} WHERE id=?", (int(value), group_id))


async def set_group_interval(group_id: int, hours: int | None) -> None:
    await execute("UPDATE groups SET interval_hours=? WHERE id=?", (hours, group_id))


async def delete_group(group_id: int) -> None:
    await execute("DELETE FROM groups WHERE id=?", (group_id,))


async def group_fail(group_id: int) -> int:
    await execute("UPDATE groups SET error_streak=error_streak+1 WHERE id=?", (group_id,))
    row = await one("SELECT error_streak FROM groups WHERE id=?", (group_id,))
    return row["error_streak"] if row else 0


async def group_ok(group_id: int) -> None:
    await execute("UPDATE groups SET error_streak=0 WHERE id=? AND error_streak>0", (group_id,))


# ---------- кабинеты ----------

async def create_cabinet(name: str) -> int:
    slots = int(await get_setting("default_slots", str(config.DEFAULT_SLOTS)))
    return await insert(
        "INSERT INTO cabinets(name, slots, created_at) VALUES (?, ?, ?)", (name, slots, now()))


async def get_cabinet(cabinet_id: int):
    return await one("SELECT * FROM cabinets WHERE id=?", (cabinet_id,))


async def list_cabinets(offset: int = 0, limit: int = 8):
    return await all_(
        "SELECT c.*, (SELECT COUNT(*) FROM posts p WHERE p.cabinet_id=c.id) AS posts, "
        "(SELECT COUNT(*) FROM members m WHERE m.cabinet_id=c.id) AS people "
        "FROM cabinets c ORDER BY c.id DESC LIMIT ? OFFSET ?", (limit, offset))


async def count_cabinets() -> int:
    return (await one("SELECT COUNT(*) AS n FROM cabinets"))["n"]


async def rename_cabinet(cabinet_id: int, name: str) -> None:
    await execute("UPDATE cabinets SET name=? WHERE id=?", (name, cabinet_id))


async def set_cabinet_status(cabinet_id: int, status: str) -> None:
    await execute("UPDATE cabinets SET status=? WHERE id=?", (status, cabinet_id))


async def set_cabinet_slots(cabinet_id: int, slots: int) -> int:
    slots = max(1, min(slots, config.MAX_SLOTS))
    await execute("UPDATE cabinets SET slots=? WHERE id=?", (slots, cabinet_id))
    return slots


async def toggle_cabinet_all_groups(cabinet_id: int) -> None:
    await execute("UPDATE cabinets SET all_groups=1-all_groups WHERE id=?", (cabinet_id,))


async def delete_cabinet(cabinet_id: int) -> None:
    await execute("DELETE FROM cabinets WHERE id=?", (cabinet_id,))
    _reload_access()


async def cabinet_members(cabinet_id: int):
    return await all_(
        "SELECT m.user_id, m.role, m.added_at, u.full_name, u.username FROM members m "
        "LEFT JOIN users u ON u.user_id=m.user_id WHERE m.cabinet_id=? "
        "ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END", (cabinet_id,))


async def remove_member(user_id: int) -> None:
    await execute("DELETE FROM members WHERE user_id=?", (user_id,))
    _reload_access()


async def all_member_ids() -> list[int]:
    return [r["user_id"] for r in await all_("SELECT user_id FROM members")]


# ---------- ссылки входа ----------

async def create_invite(cabinet_id: int, role: str) -> str:
    """Новая одноразовая ссылка. Прежняя ссылка той же роли перестаёт работать."""
    await execute("DELETE FROM invites WHERE cabinet_id=? AND role=?", (cabinet_id, role))
    token = secrets.token_urlsafe(16)
    await execute(
        "INSERT INTO invites(token, cabinet_id, role, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, cabinet_id, role, now(), in_seconds(config.INVITE_TTL_HOURS * 3600)))
    return token


async def get_invites(cabinet_id: int):
    return await all_("SELECT * FROM invites WHERE cabinet_id=? AND expires_at>?", (cabinet_id, now()))


async def revoke_invites(cabinet_id: int) -> None:
    await execute("DELETE FROM invites WHERE cabinet_id=?", (cabinet_id,))


async def use_invite(token: str, user_id: int) -> tuple[str, object, int | None]:
    """Вход по ссылке. Возвращает (статус, кабинет, id прежнего владельца или помощника).
    Статусы: ok, bad (нет или истекла), busy (человек уже в другом кабинете)."""
    inv = await one("SELECT * FROM invites WHERE token=? AND expires_at>?", (token, now()))
    if not inv:
        return "bad", None, None
    cabinet = await get_cabinet(inv["cabinet_id"])
    current = _members.get(user_id)
    if current is not None and current != inv["cabinet_id"]:
        return "busy", cabinet, None
    prev = await one(
        "SELECT user_id FROM members WHERE cabinet_id=? AND role=? AND user_id<>?",
        (inv["cabinet_id"], inv["role"], user_id))

    def _tx():
        with _lock:
            _conn.execute("DELETE FROM members WHERE cabinet_id=? AND role=?", (inv["cabinet_id"], inv["role"]))
            _conn.execute("DELETE FROM members WHERE user_id=?", (user_id,))
            _conn.execute(
                "INSERT INTO members(user_id, cabinet_id, role, added_at) VALUES (?, ?, ?, ?)",
                (user_id, inv["cabinet_id"], inv["role"], now()))
            _conn.execute("DELETE FROM invites WHERE token=?", (token,))
            _conn.commit()

    await asyncio.to_thread(_tx)
    _reload_access()
    return "ok", cabinet, prev["user_id"] if prev else None


async def cleanup_invites() -> None:
    await execute("DELETE FROM invites WHERE expires_at<=?", (now(),))


# ---------- посты ----------

_POST = (
    "SELECT p.*, g.title AS group_title, g.chat_id AS group_chat_id, g.interval_hours AS group_interval, "
    "g.is_active AS group_active, c.name AS cabinet_name, c.status AS cabinet_status "
    "FROM posts p JOIN groups g ON g.id=p.group_id JOIN cabinets c ON c.id=p.cabinet_id "
)


async def count_posts(cabinet_id: int) -> int:
    return (await one("SELECT COUNT(*) AS n FROM posts WHERE cabinet_id=?", (cabinet_id,)))["n"]


async def create_post(cabinet_id: int, group_id: int, created_by: int, title: str,
                      storage_chat_id: int, storage_ids: list[int], named: bool = False) -> int:
    ts = now()
    return await insert(
        "INSERT INTO posts(cabinet_id, group_id, created_by, title, named, storage_chat_id, storage_ids, "
        "created_at, next_send_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cabinet_id, group_id, created_by, title, int(named), storage_chat_id,
         ",".join(map(str, storage_ids)), ts, ts))


async def get_post(post_id: int):
    return await one(_POST + "WHERE p.id=?", (post_id,))


async def list_posts(cabinet_id: int):
    return await all_(_POST + "WHERE p.cabinet_id=? ORDER BY p.id", (cabinet_id,))


async def count_all_posts() -> int:
    return (await one("SELECT COUNT(*) AS n FROM posts"))["n"]


async def replace_post_content(post_id: int, title: str, storage_chat_id: int, storage_ids: list[int]) -> None:
    """Замена содержимого: расписание и счётчики остаются. Название, заданное
    вручную, тоже остаётся."""
    await execute(
        "UPDATE posts SET title=CASE WHEN named=1 THEN title ELSE ? END, "
        "storage_chat_id=?, storage_ids=?, last_error=NULL WHERE id=?",
        (title, storage_chat_id, ",".join(map(str, storage_ids)), post_id))


async def rename_post(post_id: int, title: str) -> None:
    await execute("UPDATE posts SET title=?, named=1 WHERE id=?", (title, post_id))


async def set_post_status(post_id: int, status: str) -> None:
    """При включении пост уходит в ближайший проход, если его время уже прошло."""
    if status == "active":
        await execute(
            "UPDATE posts SET status='active', last_error=NULL, "
            "next_send_at=CASE WHEN next_send_at IS NULL OR next_send_at<? THEN ? ELSE next_send_at END "
            "WHERE id=?", (now(), now(), post_id))
    else:
        await execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))


async def delete_post(post_id: int) -> None:
    await execute("DELETE FROM posts WHERE id=?", (post_id,))


async def due_posts(limit: int = 40):
    return await all_(
        _POST + "WHERE p.status='active' AND p.next_send_at<=? AND g.is_active=1 AND c.status='active' "
        "ORDER BY p.next_send_at, p.id LIMIT ?", (now(), limit))


async def mark_sent(post_id: int, interval_hours: int) -> None:
    await bump("cnt:sent")
    await execute(
        "UPDATE posts SET last_sent_at=?, next_send_at=?, sent_count=sent_count+1, last_error=NULL WHERE id=?",
        (now(), in_seconds(interval_hours * 3600), post_id))


async def mark_failed(post_id: int, error: str, retry_seconds: int = 600) -> None:
    await bump("cnt:errors")
    await execute(
        "UPDATE posts SET error_count=error_count+1, last_error=?, next_send_at=? WHERE id=?",
        (error[:300], in_seconds(retry_seconds), post_id))


async def delay_post(post_id: int, seconds: int) -> None:
    await execute("UPDATE posts SET next_send_at=? WHERE id=?", (in_seconds(seconds), post_id))


async def summary() -> dict:
    row = await one(
        "SELECT (SELECT COUNT(*) FROM cabinets) AS cabinets, "
        "(SELECT COUNT(*) FROM cabinets WHERE status='frozen') AS frozen, "
        "(SELECT COUNT(*) FROM posts) AS posts, "
        "(SELECT COUNT(*) FROM posts WHERE status='active') AS active, "
        "(SELECT COUNT(*) FROM posts WHERE status='paused') AS paused, "
        "(SELECT COUNT(*) FROM posts WHERE status='stopped') AS stopped, "
        "(SELECT COALESCE(SUM(sent_count), 0) FROM posts) AS sent, "
        "(SELECT COUNT(*) FROM groups WHERE is_active=1) AS groups")
    return dict(row)


async def posts_with_errors(limit: int = 10):
    return await all_(_POST + "WHERE p.last_error IS NOT NULL ORDER BY p.id DESC LIMIT ?", (limit,))


# ---------- поиск ----------

async def search(query: str, limit: int = 10) -> tuple[list, list]:
    """Ищет кабинеты (по названию, имени, юзернейму или ID человека) и рассылки
    (по названию или номеру). Регистр не важен."""
    q = query.strip().lstrip("@#").lower()
    like = f"%{q}%"
    num = int(q) if q.isdigit() else -1
    cabinets = await all_(
        "SELECT DISTINCT c.* FROM cabinets c LEFT JOIN members m ON m.cabinet_id=c.id "
        "LEFT JOIN users u ON u.user_id=m.user_id "
        "WHERE lower_u(c.name) LIKE ? OR lower_u(u.full_name) LIKE ? OR lower_u(u.username) LIKE ? "
        "OR m.user_id=? OR c.id=? ORDER BY c.id DESC LIMIT ?", (like, like, like, num, num, limit))
    found = await all_(
        _POST + "WHERE lower_u(p.title) LIKE ? OR p.id=? ORDER BY p.id DESC LIMIT ?", (like, num, limit))
    return cabinets, found


# ---------- журнал действий ----------

async def log_action(user_id: int, text: str) -> None:
    await execute("INSERT INTO actions(user_id, text, at) VALUES (?, ?, ?)", (user_id, text[:200], now()))


async def last_actions(limit: int = 20):
    return await all_(
        "SELECT a.*, u.full_name FROM actions a LEFT JOIN users u ON u.user_id=a.user_id "
        "ORDER BY a.id DESC LIMIT ?", (limit,))


async def trim_actions(keep: int = 500) -> None:
    await execute("DELETE FROM actions WHERE id <= (SELECT MAX(id) FROM actions) - ?", (keep,))
