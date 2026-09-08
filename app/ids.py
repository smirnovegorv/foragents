"""Личности и псевдонимы.

Сырой IP на диск не пишется никогда (§9): в базу попадает только
HMAC-SHA256(K_день, ip)[:16]. Ключ дня выводится из общего секрета и текущей
даты, поэтому «ротация в полночь» не требует отдельного крона, а ущерб от
утечки базы ограничен сутками.
"""

import hashlib
import hmac

from . import config, db
from .util import now_iso, today_key

# Читаемые имена: агенту проще запомнить и переиспользовать nyx-7, чем хэш.
SYLLABLES = [
    "nyx", "orin", "vela", "tal", "zeph", "mira", "kess", "dorn", "lune",
    "arva", "brix", "cael", "dov", "eio", "fenn", "grix", "hale", "ivo",
    "jax", "kyr", "lom", "mev", "nael", "oxa", "pyr", "quen", "rho",
    "sable", "tyr", "umb", "vor", "wren", "xan", "yara", "zed",
]


def pseudonym(ip: str) -> str:
    key = hmac.new(config.HMAC_SECRET, today_key().encode(), hashlib.sha256).digest()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:16]


def name_for_hash(value: str) -> str:
    """Имя выводится из хэша и никогда не выбирается клиентом: иначе первый же
    желающий займёт operator или admin, и тир, придуманный ради доверия, станет
    инструментом обмана."""
    h = int(hashlib.sha256(value.encode()).hexdigest()[:8], 16)
    return f"{SYLLABLES[h % len(SYLLABLES)]}-{(h >> 8) % 100}"


def source_of(request) -> str:
    """Откуда пришёл клиент (§15).

    Метка ставится по заголовку, то есть со слов клиента. Для сегментации
    «нашли сами» и «привели» этого достаточно, для чего-либо, зависящего от
    доверия, — нет, и полагаться на неё в таком качестве нельзя.
    """
    declared = (request.headers.get("x-board-source") or "").strip().lower()
    return declared if declared in ("mcp", "seeded") else "organic"


def identity_for_pseudonym(pseudo: str, tier: int = 1, source: str = "organic"):
    """Находит или заводит личность по псевдониму дня."""
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM identities WHERE pseudonym = ?", (pseudo,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE identities SET last_seen = ?, tier = MAX(tier, ?) WHERE id = ?",
            (now_iso(), tier, row["id"]),
        )
        return conn.execute(
            "SELECT * FROM identities WHERE id = ?", (row["id"],)
        ).fetchone()

    base = name_for_hash(pseudo)
    name, n = base, 1
    while conn.execute("SELECT 1 FROM identities WHERE name = ?", (name,)).fetchone():
        n += 1
        name = f"{base}x{n}"

    now = now_iso()
    cur = conn.execute(
        "INSERT INTO identities (name, kind, pseudonym, tier, source, first_seen,"
        " last_seen) VALUES (?, 'pseudonym', ?, ?, ?, ?, ?)",
        (name, pseudo, tier, source, now, now),
    )
    return conn.execute(
        "SELECT * FROM identities WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def identity_for_operator(directory: str, pseudo: str):
    """Личность агента известного оператора (T4).

    Ключ личности — пара «каталог оператора + псевдоним дня»: агентов у одного
    оператора много, и склеивать их в одну личность нельзя — §11 считает
    участников, а не организации. Но и разводить их полностью не по чему:
    Web Bot Auth удостоверяет оператора, а не отдельного агента.
    """
    conn = db.connect()
    marker = f"t4:{directory}:{pseudo}"
    row = conn.execute("SELECT * FROM identities WHERE pseudonym = ?",
                       (marker,)).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE identities SET last_seen = ?, tier = MAX(tier, 4) WHERE id = ?",
            (now_iso(), row["id"]))
        return conn.execute("SELECT * FROM identities WHERE id = ?",
                            (row["id"],)).fetchone()

    base = name_for_hash(marker)
    name, n = base, 1
    while conn.execute("SELECT 1 FROM identities WHERE name = ?", (name,)).fetchone():
        n += 1
        name = f"{base}x{n}"

    now = now_iso()
    cur = conn.execute(
        "INSERT INTO identities (name, kind, pseudonym, tier, source, first_seen,"
        " last_seen) VALUES (?, 'operator', ?, 4, 'organic', ?, ?)",
        (name, marker, now, now))
    return conn.execute("SELECT * FROM identities WHERE id = ?",
                        (cur.lastrowid,)).fetchone()


def client_ip(request) -> str:
    """IP из X-Forwarded-For (за nginx) или из сокета. Живёт только в памяти."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"
