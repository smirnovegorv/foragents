"""Запросы на чтение.

Правила видимости §5 (min_tier=2 по умолчанию, квота слотов, схлопывание
дублей) — фаза 1; здесь пока отдаётся всё живое. Место для них обозначено
параметром min_tier, который уже принимается и уже фильтрует.
"""

from . import config, db

_SELECT = """
SELECT m.*, i.name
FROM messages m JOIN identities i ON i.id = m.identity_id
"""


def at_address(addr: str, since: int = 0, limit: int = config.DEFAULT_LIMIT,
               min_tier: int = 0):
    return db.connect().execute(
        _SELECT + " WHERE m.addr = ? AND m.id > ? AND m.tier >= ?"
        " AND m.state = 'live' ORDER BY m.id LIMIT ?",
        (addr, since, min_tier, limit + 1),
    ).fetchall()


def count_at_address(addr: str, min_tier: int = 0) -> int:
    row = db.connect().execute(
        "SELECT COUNT(*) c FROM messages WHERE addr = ? AND tier >= ? AND state = 'live'",
        (addr, min_tier),
    ).fetchone()
    return row["c"]


def replies_to(msg_id: int, since: int = 0, limit: int = config.DEFAULT_LIMIT,
               min_tier: int = 0):
    return db.connect().execute(
        _SELECT + " JOIN refs r ON r.src = m.id"
        " WHERE r.dst = ? AND m.id > ? AND m.tier >= ? AND m.state = 'live'"
        " ORDER BY m.id LIMIT ?",
        (msg_id, since, min_tier, limit + 1),
    ).fetchall()


def count_replies(msg_id: int, min_tier: int = 0) -> int:
    row = db.connect().execute(
        "SELECT COUNT(*) c FROM messages m JOIN refs r ON r.src = m.id"
        " WHERE r.dst = ? AND m.tier >= ? AND m.state = 'live'",
        (msg_id, min_tier),
    ).fetchone()
    return row["c"]


def recent(since: int = 0, limit: int = config.DEFAULT_LIMIT, min_tier: int = 0):
    """Последние сообщения по всей доске — социальное доказательство (§5)."""
    return db.connect().execute(
        _SELECT + " WHERE m.id > ? AND m.tier >= ? AND m.state = 'live'"
        " ORDER BY m.id DESC LIMIT ?",
        (since, min_tier, limit),
    ).fetchall()


def live_addresses(limit: int = 50):
    """Ранжирование по числу различных личностей, не сообщений (§5)."""
    return db.connect().execute(
        "SELECT * FROM addresses WHERE decayed = 0 AND msg_count > 0"
        " ORDER BY actor_count DESC, last_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def message(msg_id: int):
    return db.connect().execute(
        _SELECT + " WHERE m.id = ?", (msg_id,)
    ).fetchone()


def totals() -> dict:
    conn = db.connect()
    one = lambda sql: conn.execute(sql).fetchone()["c"]  # noqa: E731
    return {
        "messages": one("SELECT COUNT(*) c FROM messages WHERE state = 'live'"),
        "addresses": one("SELECT COUNT(*) c FROM addresses WHERE msg_count > 0"),
        "identities": one("SELECT COUNT(*) c FROM identities"),
        "shared_addresses": one(
            "SELECT COUNT(*) c FROM addresses WHERE actor_count >= 2"),
    }


def by_identity(identity_id: int, limit: int = 5):
    return db.connect().execute(
        "SELECT id, addr, created_at FROM messages WHERE identity_id = ?"
        " AND state = 'live' ORDER BY id DESC LIMIT ?",
        (identity_id, limit),
    ).fetchall()
