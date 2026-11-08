"""SQLite: схема, индексы и вся работа с данными.

Один файл базы, одно соединение, все вызовы идут через поток (asyncio.to_thread)
под общим замком - для одного процесса бота этого достаточно, а резервная копия
и перенос сводятся к копированию одного файла. Время хранится в UTC (ISO).
Всё привязано к Telegram user_id, а не к боту, поэтому смена основного бота
ничего не теряет.
"""

import asyncio
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import config

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    user_id  INTEGER PRIMARY KEY,
    role     TEXT NOT NULL DEFAULT 'owner',      -- owner | moderator
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    full_name  TEXT,
    username   TEXT,
    is_banned  INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          INTEGER UNIQUE NOT NULL,
    title            TEXT,
    is_active        INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER,                    -- NULL = общий интервал
    error_streak     INTEGER NOT NULL DEFAULT 0,
    added_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirrors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL UNIQUE,          -- одно зеркало на пользователя
    bot_id     INTEGER NOT NULL UNIQUE,
    username   TEXT,
    token_enc  TEXT NOT NULL,
    slots      INTEGER NOT NULL DEFAULT 3,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | paused | blocked | revoked
    created_at TEXT NOT NULL
);

-- заявки на зеркало: токен лежит тут только до решения владельцев
CREATE TABLE IF NOT EXISTS applications (
    user_id    INTEGER PRIMARY KEY,              -- одна заявка на человека
    bot_id     INTEGER NOT NULL UNIQUE,
    username   TEXT,
    token_enc  TEXT NOT NULL,
    source     TEXT NOT NULL,                    -- quick | token
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirror_admins (
    mirror_id INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (mirror_id, user_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    mirror_id          INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
    group_id           INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    created_by         INTEGER NOT NULL,
    title              TEXT NOT NULL,
    storage_chat_id    INTEGER NOT NULL,
    storage_message_id INTEGER NOT NULL,
    buttons            TEXT,                     -- JSON: [[{"text","url"}]]
    status             TEXT NOT NULL DEFAULT 'active',  -- pending | active | paused
    created_at         TEXT NOT NULL,
    started_at         TEXT,                     -- с него идёт отчёт
    next_send_at       TEXT,
    last_sent_at       TEXT,
    sent_count         INTEGER NOT NULL DEFAULT 0,
    error_count        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS send_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   INTEGER,
    mirror_id INTEGER,
    group_id  INTEGER,
    sent_at   TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    error     TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_banned     ON users(is_banned);
CREATE INDEX IF NOT EXISTS idx_mirrors_status   ON mirrors(status);
CREATE INDEX IF NOT EXISTS idx_madmins_user     ON mirror_admins(user_id);
CREATE INDEX IF NOT EXISTS idx_posts_mirror     ON posts(mirror_id);
CREATE INDEX IF NOT EXISTS idx_posts_group      ON posts(group_id);
CREATE INDEX IF NOT EXISTS idx_posts_due        ON posts(status, next_send_at);
CREATE INDEX IF NOT EXISTS idx_apps_expires     ON applications(expires_at);
CREATE INDEX IF NOT EXISTS idx_log_post         ON send_log(post_id);
CREATE INDEX IF NOT EXISTS idx_log_sent         ON send_log(sent_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def in_minutes(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


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
    """Возвращает число затронутых строк."""
    return await asyncio.to_thread(_run, sql, params, "exec")


async def insert(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(_run, sql, params, "insert")


async def init(path: str | None = None) -> None:
    global _conn
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.execute("PRAGMA busy_timeout=30000")
    _conn.executescript(SCHEMA)
    _conn.commit()
    for uid in config.OWNER_IDS:
        _conn.execute(
            "INSERT OR IGNORE INTO owners(user_id, role, added_at) VALUES (?, 'owner', ?)",
            (uid, now()),
        )
    _conn.commit()


def snapshot(dest_path: str) -> None:
    """Согласованная копия базы (SQLite backup API), безопасна при работе бота."""
    with _lock:
        target = sqlite3.connect(dest_path)
        try:
            _conn.backup(target)
        finally:
            target.close()


# ---------- настройки ----------

async def get_setting(key: str, default: str | None = None) -> str | None:
    row = await one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


async def global_interval() -> int:
    v = await get_setting("interval_minutes")
    return int(v) if v else config.DEFAULT_INTERVAL_MINUTES


async def moderation_on() -> bool:
    return (await get_setting("moderation", "0")) == "1"


async def effective_interval(group_interval: int | None) -> int:
    """Интервал группы, а если он не задан - общий; не ниже минимального."""
    value = group_interval or await global_interval()
    return max(value, config.MIN_INTERVAL_MINUTES)


# ---------- владельцы ----------

async def get_role(user_id: int) -> str | None:
    row = await one("SELECT role FROM owners WHERE user_id=?", (user_id,))
    return row["role"] if row else None


async def list_owners():
    return await all_("SELECT * FROM owners ORDER BY role, user_id")


async def add_owner(user_id: int, role: str = "owner") -> None:
    await execute(
        "INSERT INTO owners(user_id, role, added_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role",
        (user_id, role, now()),
    )


async def remove_owner(user_id: int) -> bool:
    if user_id in config.OWNER_IDS:
        return False  # стартовых владельцев из .env из админки не убрать
    return await execute("DELETE FROM owners WHERE user_id=?", (user_id,)) > 0


# ---------- пользователи и баны ----------

async def touch_user(user_id: int, full_name: str | None, username: str | None) -> None:
    ts = now()
    await execute(
        "INSERT INTO users(user_id, full_name, username, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
        "full_name=excluded.full_name, username=excluded.username, last_seen=excluded.last_seen",
        (user_id, full_name, username, ts, ts),
    )


async def get_user(user_id: int):
    return await one("SELECT * FROM users WHERE user_id=?", (user_id,))


async def is_banned(user_id: int) -> bool:
    row = await one("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    return bool(row and row["is_banned"])


async def ban_user(user_id: int) -> None:
    ts = now()
    await execute(
        "INSERT INTO users(user_id, is_banned, first_seen, last_seen) VALUES (?, 1, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET is_banned=1",
        (user_id, ts, ts),
    )
    # зеркало забаненного блокируется, его посты перестают уходить
    await execute("UPDATE mirrors SET status='blocked' WHERE owner_id=?", (user_id,))
    await execute("DELETE FROM applications WHERE user_id=?", (user_id,))


async def unban_user(user_id: int) -> None:
    await execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
    await execute(
        "UPDATE mirrors SET status='active' WHERE owner_id=? AND status='blocked'", (user_id,)
    )


async def list_user_ids(include_banned: bool = False) -> list[int]:
    sql = "SELECT user_id FROM users" + ("" if include_banned else " WHERE is_banned=0")
    return [r["user_id"] for r in await all_(sql)]


# ---------- группы ----------

async def add_group(chat_id: int, title: str | None, active: bool = False) -> int:
    row = await one("SELECT id FROM groups WHERE chat_id=?", (chat_id,))
    if row:
        await execute("UPDATE groups SET title=? WHERE id=?", (title, row["id"]))
        return row["id"]
    return await insert(
        "INSERT INTO groups(chat_id, title, is_active, added_at) VALUES (?, ?, ?, ?)",
        (chat_id, title, int(active), now()),
    )


async def get_group(group_id: int):
    return await one("SELECT * FROM groups WHERE id=?", (group_id,))


async def get_group_by_chat(chat_id: int):
    return await one("SELECT * FROM groups WHERE chat_id=?", (chat_id,))


async def list_groups(active_only: bool = False):
    sql = "SELECT * FROM groups" + (" WHERE is_active=1" if active_only else "") + " ORDER BY id"
    return await all_(sql)


async def set_group_active(group_id: int, active: bool) -> None:
    await execute(
        "UPDATE groups SET is_active=?, error_streak=0 WHERE id=?", (int(active), group_id)
    )


async def set_group_interval(group_id: int, minutes: int | None) -> None:
    await execute("UPDATE groups SET interval_minutes=? WHERE id=?", (minutes, group_id))


async def delete_group(group_id: int) -> None:
    await execute("DELETE FROM groups WHERE id=?", (group_id,))


async def group_ok(group_id: int) -> None:
    await execute("UPDATE groups SET error_streak=0 WHERE id=?", (group_id,))


async def group_fail(group_id: int) -> int:
    await execute("UPDATE groups SET error_streak=error_streak+1 WHERE id=?", (group_id,))
    row = await one("SELECT error_streak FROM groups WHERE id=?", (group_id,))
    return row["error_streak"] if row else 0


# ---------- зеркала ----------

async def create_mirror(owner_id: int, bot_id: int, username: str | None, token_enc: str):
    """Возвращает id или None, если у пользователя уже есть зеркало
    или этот бот уже зарегистрирован."""
    slots = int(await get_setting("default_slots", str(config.DEFAULT_SLOTS)))
    try:
        return await insert(
            "INSERT INTO mirrors(owner_id, bot_id, username, token_enc, slots, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, bot_id, username, token_enc, slots, now()),
        )
    except sqlite3.IntegrityError:
        return None


async def get_mirror(mirror_id: int):
    return await one("SELECT * FROM mirrors WHERE id=?", (mirror_id,))


async def get_mirror_by_bot(bot_id: int):
    return await one("SELECT * FROM mirrors WHERE bot_id=?", (bot_id,))


async def get_mirror_by_owner(owner_id: int):
    return await one("SELECT * FROM mirrors WHERE owner_id=?", (owner_id,))


async def mirror_for_staff(user_id: int):
    """Зеркало, где пользователь создатель или админ."""
    row = await get_mirror_by_owner(user_id)
    if row:
        return row
    return await one(
        "SELECT m.* FROM mirrors m JOIN mirror_admins a ON a.mirror_id=m.id WHERE a.user_id=?",
        (user_id,),
    )


async def list_mirrors(offset: int = 0, limit: int = 8):
    return await all_(
        "SELECT * FROM mirrors ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    )


async def list_runnable_mirrors():
    return await all_("SELECT * FROM mirrors WHERE status IN ('active', 'paused', 'blocked')")


async def count_mirrors() -> int:
    return (await one("SELECT COUNT(*) AS c FROM mirrors"))["c"]


async def set_mirror_status(mirror_id: int, status: str) -> None:
    await execute("UPDATE mirrors SET status=? WHERE id=?", (status, mirror_id))


async def set_mirror_slots(mirror_id: int, slots: int) -> int:
    slots = max(1, min(slots, config.MAX_SLOTS))
    await execute("UPDATE mirrors SET slots=? WHERE id=?", (slots, mirror_id))
    return slots


async def set_mirror_token(mirror_id: int, token_enc: str) -> None:
    await execute(
        "UPDATE mirrors SET token_enc=?, status='active' WHERE id=?", (token_enc, mirror_id)
    )


async def delete_mirror(mirror_id: int) -> None:
    await execute("DELETE FROM mirrors WHERE id=?", (mirror_id,))


# ---------- заявки на зеркало ----------

async def create_application(user_id: int, bot_id: int, username: str | None,
                             token_enc: str, source: str) -> str:
    """'ok' | 'has_mirror' | 'has_app' | 'bot_taken'."""
    if await get_mirror_by_owner(user_id):
        return "has_mirror"
    if await get_application(user_id):
        return "has_app"
    taken = await one("SELECT 1 AS x FROM mirrors WHERE bot_id=?", (bot_id,)) or \
        await one("SELECT 1 AS x FROM applications WHERE bot_id=?", (bot_id,))
    if taken:
        return "bot_taken"
    try:
        await insert(
            "INSERT INTO applications(user_id, bot_id, username, token_enc, source, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, bot_id, username, token_enc, source, now(),
             in_minutes(config.APPLICATION_TTL_HOURS * 60)),
        )
    except sqlite3.IntegrityError:
        return "bot_taken"
    return "ok"


async def get_application(user_id: int):
    return await one("SELECT * FROM applications WHERE user_id=?", (user_id,))


async def list_applications():
    return await all_("SELECT * FROM applications ORDER BY created_at")


async def count_applications() -> int:
    return (await one("SELECT COUNT(*) AS c FROM applications"))["c"]


async def delete_application(user_id: int) -> bool:
    """Удаляет заявку вместе с токеном."""
    return await execute("DELETE FROM applications WHERE user_id=?", (user_id,)) > 0


async def expired_applications():
    return await all_("SELECT * FROM applications WHERE expires_at <= ?", (now(),))


async def approve_application(user_id: int):
    """Превращает заявку в зеркало. Возвращает id зеркала или None."""
    app = await get_application(user_id)
    if not app:
        return None
    mid = await create_mirror(user_id, app["bot_id"], app["username"], app["token_enc"])
    await delete_application(user_id)
    return mid


# ---------- админы зеркала ----------

async def list_mirror_admins(mirror_id: int):
    return await all_(
        "SELECT user_id FROM mirror_admins WHERE mirror_id=? ORDER BY added_at", (mirror_id,)
    )


async def add_mirror_admin(mirror_id: int, user_id: int) -> str:
    """'ok' | 'limit' | 'exists' | 'owner'."""
    mirror = await get_mirror(mirror_id)
    if not mirror:
        return "limit"
    if mirror["owner_id"] == user_id:
        return "owner"
    admins = await list_mirror_admins(mirror_id)
    if any(a["user_id"] == user_id for a in admins):
        return "exists"
    if len(admins) + 1 >= config.MAX_MIRROR_ADMINS:  # +1 - сам создатель
        return "limit"
    await execute(
        "INSERT INTO mirror_admins(mirror_id, user_id, added_at) VALUES (?, ?, ?)",
        (mirror_id, user_id, now()),
    )
    return "ok"


async def remove_mirror_admin(mirror_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM mirror_admins WHERE mirror_id=? AND user_id=?", (mirror_id, user_id)
    )


async def is_mirror_staff(mirror_id: int, user_id: int) -> bool:
    mirror = await get_mirror(mirror_id)
    if not mirror:
        return False
    if mirror["owner_id"] == user_id:
        return True
    row = await one(
        "SELECT 1 AS x FROM mirror_admins WHERE mirror_id=? AND user_id=?", (mirror_id, user_id)
    )
    return row is not None


async def mirror_staff_ids(mirror_id: int) -> list[int]:
    mirror = await get_mirror(mirror_id)
    if not mirror:
        return []
    ids = [mirror["owner_id"]]
    ids += [a["user_id"] for a in await list_mirror_admins(mirror_id)]
    return ids


# ---------- посты ----------

async def count_posts(mirror_id: int) -> int:
    return (await one("SELECT COUNT(*) AS c FROM posts WHERE mirror_id=?", (mirror_id,)))["c"]


async def create_post(mirror_id: int, group_id: int, created_by: int, title: str,
                      storage_chat_id: int, storage_message_id: int,
                      buttons_json: str | None, pending: bool) -> int:
    ts = now()
    return await insert(
        "INSERT INTO posts(mirror_id, group_id, created_by, title, storage_chat_id, "
        "storage_message_id, buttons, status, created_at, started_at, next_send_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mirror_id, group_id, created_by, title, storage_chat_id, storage_message_id,
         buttons_json, "pending" if pending else "active", ts,
         None if pending else ts, None if pending else ts),
    )


async def get_post(post_id: int):
    return await one("SELECT * FROM posts WHERE id=?", (post_id,))


async def list_posts(mirror_id: int):
    return await all_("SELECT * FROM posts WHERE mirror_id=? ORDER BY id", (mirror_id,))


async def set_post_status(post_id: int, status: str) -> None:
    if status == "active":
        # старт отчёта - момент первого включения, дальше не сбрасывается
        await execute(
            "UPDATE posts SET status='active', next_send_at=?, "
            "started_at=COALESCE(started_at, ?) WHERE id=?",
            (now(), now(), post_id),
        )
    else:
        await execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))


async def delete_post(post_id: int) -> None:
    await execute("DELETE FROM posts WHERE id=?", (post_id,))


async def pause_mirror_posts(mirror_id: int) -> int:
    return await execute(
        "UPDATE posts SET status='paused' WHERE mirror_id=? AND status='active'", (mirror_id,)
    )


async def due_posts(limit: int = 50):
    """Активные посты, которым пора уходить: пост активен, группа включена,
    зеркало не заблокировано."""
    return await all_(
        "SELECT p.*, g.chat_id AS group_chat_id, g.title AS group_title, "
        "g.interval_minutes AS group_interval "
        "FROM posts p JOIN groups g ON g.id=p.group_id JOIN mirrors m ON m.id=p.mirror_id "
        "WHERE p.status='active' AND g.is_active=1 AND m.status='active' "
        "AND p.next_send_at <= ? ORDER BY p.next_send_at LIMIT ?",
        (now(), limit),
    )


async def mark_sent(post_id: int, interval_minutes: int, group_id: int, mirror_id: int) -> None:
    ts = now()
    await execute(
        "UPDATE posts SET sent_count=sent_count+1, last_sent_at=?, next_send_at=? WHERE id=?",
        (ts, in_minutes(interval_minutes), post_id),
    )
    await execute(
        "INSERT INTO send_log(post_id, mirror_id, group_id, sent_at, ok) VALUES (?, ?, ?, ?, 1)",
        (post_id, mirror_id, group_id, ts),
    )


async def mark_failed(post_id: int, interval_minutes: int, group_id: int,
                      mirror_id: int, error: str) -> None:
    ts = now()
    await execute(
        "UPDATE posts SET error_count=error_count+1, next_send_at=? WHERE id=?",
        (in_minutes(interval_minutes), post_id),
    )
    await execute(
        "INSERT INTO send_log(post_id, mirror_id, group_id, sent_at, ok, error) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (post_id, mirror_id, group_id, ts, error[:300]),
    )


async def delay_post(post_id: int, seconds: int) -> None:
    when = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    await execute("UPDATE posts SET next_send_at=? WHERE id=?", (when, post_id))


async def mirror_stats(mirror_id: int) -> dict:
    row = await one(
        "SELECT COUNT(*) AS posts, COALESCE(SUM(sent_count),0) AS sent, "
        "COALESCE(SUM(error_count),0) AS errors FROM posts WHERE mirror_id=?",
        (mirror_id,),
    )
    return dict(row)


async def overall_stats() -> dict:
    out = {}
    out["mirrors"] = (await one("SELECT COUNT(*) AS c FROM mirrors"))["c"]
    out["mirrors_active"] = (
        await one("SELECT COUNT(*) AS c FROM mirrors WHERE status='active'"))["c"]
    out["posts_active"] = (
        await one("SELECT COUNT(*) AS c FROM posts WHERE status='active'"))["c"]
    out["posts_pending"] = (
        await one("SELECT COUNT(*) AS c FROM posts WHERE status='pending'"))["c"]
    out["users"] = (await one("SELECT COUNT(*) AS c FROM users"))["c"]
    out["banned"] = (await one("SELECT COUNT(*) AS c FROM users WHERE is_banned=1"))["c"]
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    out["sent_24h"] = (
        await one("SELECT COUNT(*) AS c FROM send_log WHERE ok=1 AND sent_at>=?", (day_ago,)))["c"]
    out["errors_24h"] = (
        await one("SELECT COUNT(*) AS c FROM send_log WHERE ok=0 AND sent_at>=?", (day_ago,)))["c"]
    return out


async def recent_errors(limit: int = 10):
    return await all_(
        "SELECT * FROM send_log WHERE ok=0 ORDER BY id DESC LIMIT ?", (limit,)
    )


async def cleanup_log(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return await execute("DELETE FROM send_log WHERE sent_at < ?", (cutoff,))
