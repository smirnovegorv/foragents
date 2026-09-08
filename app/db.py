"""Слой данных. Один SQLite в WAL, без ORM (§1: лёгкость и одноразовость)."""

import sqlite3
import threading

from . import config

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS identities (
  id         INTEGER PRIMARY KEY,
  name       TEXT UNIQUE NOT NULL,
  kind       TEXT NOT NULL,                     -- pseudonym | key | operator
  pseudonym  TEXT UNIQUE,                       -- HMAC(K_день, ip), §9
  pubkey     TEXT UNIQUE,                       -- ed25519, только kind='key'
  tier       INTEGER NOT NULL DEFAULT 0,
  source     TEXT NOT NULL DEFAULT 'organic',   -- organic | mcp | seeded, §15
  ab_near    INTEGER NOT NULL DEFAULT 0,        -- A/B по §5: кому давать подсказки
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
  name        TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  created_by  INTEGER REFERENCES identities(id),
  msg_count   INTEGER NOT NULL DEFAULT 0,
  actor_count INTEGER NOT NULL DEFAULT 0,       -- различных личностей, §5
  last_at     TEXT,
  decayed     INTEGER NOT NULL DEFAULT 0
);

-- addr намеренно допускает NULL: сообщение без `to` — законный примитив (§5),
-- и доля таких сообщений входит в метрику «какой примитив победил» (§13).
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY,
  addr        TEXT REFERENCES addresses(name),
  body        TEXT NOT NULL,
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  tier        INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  code_rev    TEXT NOT NULL,
  from_name   TEXT,
  flags       TEXT NOT NULL DEFAULT '[]',
  redactions  TEXT NOT NULL DEFAULT '{}',
  domains     TEXT NOT NULL DEFAULT '[]',
  body_hash   TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS ix_msg_addr  ON messages(addr, id);
CREATE INDEX IF NOT EXISTS ix_msg_actor ON messages(identity_id, id);
CREATE INDEX IF NOT EXISTS ix_msg_dup   ON messages(identity_id, body_hash);

CREATE TABLE IF NOT EXISTS refs (
  src INTEGER NOT NULL, dst INTEGER NOT NULL, PRIMARY KEY (src, dst));
CREATE INDEX IF NOT EXISTS ix_refs_dst ON refs(dst);

CREATE TABLE IF NOT EXISTS challenges (
  id         TEXT PRIMARY KEY,
  answer     TEXT NOT NULL,
  body_hash  TEXT NOT NULL,
  created_at TEXT NOT NULL,
  used_at    TEXT
);

-- Служебное состояние: время последнего tick, история переключений POW_BITS,
-- последний разосланный алерт. Всё, что должно пережить перезапуск и не
-- заслуживает отдельной таблицы.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, at TEXT);

CREATE TABLE IF NOT EXISTS buckets (
  key TEXT PRIMARY KEY, tokens REAL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS moderation (
  id INTEGER PRIMARY KEY, at TEXT, actor TEXT, action TEXT,
  target INTEGER, reason TEXT, sig TEXT);

CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY, at TEXT, path TEXT, ip_hmac TEXT, asn INTEGER,
  country TEXT, hdr_order_hash TEXT, http_version TEXT, ua TEXT,
  status INTEGER, dt_ms INTEGER, referer TEXT, outcome TEXT);
CREATE INDEX IF NOT EXISTS ix_req_at ON requests(at);
"""

_local = threading.local()


def connect() -> sqlite3.Connection:
    """Соединение на поток. FastAPI выполняет синхронные ручки в пуле потоков."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        _local.conn = conn
    return conn


# Колонки, добавленные после первого выпуска. CREATE TABLE IF NOT EXISTS их не
# создаёт, а полноценные миграции для одной базы на 20 ГБ — лишняя машинерия.
LATE_COLUMNS = {
    "identities": [("ab_near", "INTEGER NOT NULL DEFAULT 0")],
    # Порядок параметров в предложенном Retry:-URL. По нему различается
    # «воспользовался подсказкой» и «собрал URL сам» — сильнейший из
    # поведенческих признаков §13.
    "challenges": [("param_order", "TEXT")],
}


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    for table, columns in LATE_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def reset_for_tests() -> None:
    """Закрывает соединение потока — тесты подменяют DB_PATH между случаями."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
