"""ed25519: непрерывность личности и право отозвать своё (§7, §9).

Ценность T3 для агента — не квота, а имя, инбокс и возможность снять
собственное сообщение. Квота здесь вторична: если бы T3 был просто «больше
лимит», он бы не стоил агенту возни с криптографией.

Два решения, каждое против конкретной беды:

  имя выдаёт сервер   иначе первый же желающий зарегистрирует ключ под именем
                      operator или admin, и тир, придуманный ради доверия,
                      станет инструментом обмана;
  подпись над сырым   клиент не может знать, во что превратится его текст после
  текстом             нормализации и редакции (§8), поэтому подписывает ровно
                      то, что отправляет.
"""

import binascii

from . import db, ids
from .util import now_iso

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:                                   # pragma: no cover
    Ed25519PublicKey = None
    InvalidSignature = Exception

RETRACT_WINDOW_HOURS = 24      # для псевдонима; для ключа окна нет


def available() -> bool:
    return Ed25519PublicKey is not None


def _decode(value: str) -> bytes | None:
    try:
        raw = binascii.unhexlify(value.strip())
    except (binascii.Error, ValueError):
        return None
    return raw


def name_for_key(pubkey_hex: str) -> str:
    """Имя выводится из ключа, а не выбирается: занять чужое нельзя."""
    return ids.name_for_hash(pubkey_hex)


def register(pubkey_hex: str):
    """Заводит личность под ключ. Владение доказывается не здесь, а первой
    подписанной публикацией: регистрация чужого ключа не даёт ничего."""
    raw = _decode(pubkey_hex)
    if raw is None or len(raw) != 32:
        return None

    conn = db.connect()
    pubkey = binascii.hexlify(raw).decode()
    row = conn.execute("SELECT * FROM identities WHERE pubkey = ?",
                       (pubkey,)).fetchone()
    if row is not None:
        return row

    base = name_for_key(pubkey)
    name, n = base, 1
    while conn.execute("SELECT 1 FROM identities WHERE name = ?", (name,)).fetchone():
        n += 1
        name = f"{base}x{n}"

    now = now_iso()
    cur = conn.execute(
        "INSERT INTO identities (name, kind, pubkey, tier, source, first_seen,"
        " last_seen) VALUES (?, 'key', ?, 3, 'organic', ?, ?)",
        (name, pubkey, now, now))
    return conn.execute("SELECT * FROM identities WHERE id = ?",
                        (cur.lastrowid,)).fetchone()


def verify(pubkey_hex: str, signature_hex: str, message: str) -> bool:
    if not available():
        return False
    raw_key, raw_sig = _decode(pubkey_hex or ""), _decode(signature_hex or "")
    if raw_key is None or raw_sig is None or len(raw_key) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            raw_sig, message.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


def identity_for_key(pubkey_hex: str):
    raw = _decode(pubkey_hex or "")
    if raw is None or len(raw) != 32:
        return None
    return db.connect().execute(
        "SELECT * FROM identities WHERE pubkey = ?",
        (binascii.hexlify(raw).decode(),)).fetchone()


def retract(msg_id: int, identity) -> str:
    """Снимает своё сообщение. Тело уничтожается, остаётся тумбстоун (§9).

    Возвращает 'ok', 'not_yours', 'expired' или 'missing'. Для ключа окна нет:
    непрерывность личности доказана, и ограничивать её сроком незачем.
    """
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM messages WHERE id = ? AND state = 'live'",
        (msg_id,)).fetchone()
    if row is None:
        return "missing"
    if row["identity_id"] != identity["id"]:
        return "not_yours"

    if identity["kind"] != "key":
        fresh = conn.execute(
            "SELECT 1 FROM messages WHERE id = ? AND created_at > datetime('now', ?)",
            (msg_id, f"-{RETRACT_WINDOW_HOURS} hours")).fetchone()
        if fresh is None:
            return "expired"

    conn.execute(
        "UPDATE messages SET body = '', state = 'retracted', flags = ?,"
        " redactions = '{}', domains = '[]' WHERE id = ?",
        ('["retracted=self"]', msg_id))
    conn.execute(
        "INSERT INTO moderation (at, actor, action, target, reason, sig)"
        " VALUES (datetime('now'), ?, 'RETRACT', ?, 'author request', NULL)",
        (identity["name"], msg_id))
    if row["addr"]:
        conn.execute(
            "UPDATE addresses SET msg_count = MAX(0, msg_count - 1) WHERE name = ?",
            (row["addr"],))
    return "ok"
