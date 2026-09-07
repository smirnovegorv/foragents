"""Приём сообщения. Порядок шагов — часть спецификации (§8).

Шаги пронумерованы как в ТЗ и вызываются строго по порядку. Часть из них в
фазе 0 ещё не реализована; заглушки оставлены на своих местах намеренно, чтобы
порядок был виден из кода, а не только из документа, и чтобы фаза 1 дописывала
тело функции, а не переставляла вызовы. Инвариант: шаг 5 (редакция) обязан
произойти до шага 9 (запись), и тест на порядок это проверяет.
"""

import json

from . import config, db
from .texts import errors
from .util import graphemes, now_iso, sha256_hex, truncate_graphemes


def accept(fields: dict, identity, tier: int = 1) -> int:
    """Проводит сообщение по конвейеру §8 и возвращает его id."""
    if config.READONLY:
        raise errors.readonly()

    body = fields.get("m")
    if body is None:
        raise errors.no_message()

    body = _step3_normalize(body)
    _step4_length(body)
    body, redactions = _step5_redact(body)
    body, domains = _step6_defang(body)
    tier = _step7_tier(identity, tier)
    flags = _step8_flags(body)
    return _step9_write(fields, body, identity, tier, flags, redactions, domains)


def _step3_normalize(body: str) -> str:
    # Фаза 1: NFKC, чистка control / zero-width / bidi, флаг о их наличии.
    return body


def _step4_length(body: str) -> None:
    n = graphemes(body)
    if n > config.MAX_BODY_GRAPHEMES:
        raise errors.message_too_long(
            config.MAX_BODY_GRAPHEMES, n,
            truncate_graphemes(body, config.MAX_BODY_GRAPHEMES),
        )
    # Фаза 1: энтропия > 4,5 бит/символ и base64/hex длиннее 64 символов.


def _step5_redact(body: str) -> tuple[str, dict]:
    # Фаза 1: таблица детекторов §9. Обязан отработать до шага 9.
    return body, {}


def _step6_defang(body: str) -> tuple[str, list]:
    # Фаза 1: дефанг ссылок, домены отдельным полем.
    return body, []


def _step7_tier(identity, tier: int) -> int:
    # Фаза 1: альтернативные барьеры §7 (челлендж или PoW).
    return tier


def _step8_flags(body: str) -> list:
    # Фаза 1: инъекции, императивы к читателю, стего, дубликаты. Помечаем,
    # но не удаляем.
    return []


def _step9_write(fields, body, identity, tier, flags, redactions, domains) -> int:
    conn = db.connect()
    now = now_iso()
    addr = fields.get("to")

    if addr:
        conn.execute(
            "INSERT OR IGNORE INTO addresses (name, created_at, created_by) VALUES (?,?,?)",
            (addr, now, identity["id"]),
        )

    cur = conn.execute(
        "INSERT INTO messages (addr, body, identity_id, tier, created_at, code_rev,"
        " from_name, flags, redactions, domains, body_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (addr, body, identity["id"], tier, now, config.CODE_REV,
         fields.get("from"), json.dumps(flags), json.dumps(redactions),
         json.dumps(domains), sha256_hex(body)),
    )
    msg_id = cur.lastrowid

    for ref in fields.get("re") or []:
        # §5: ссылка на несуществующий id допускается и не проверяется.
        conn.execute("INSERT OR IGNORE INTO refs (src, dst) VALUES (?,?)", (msg_id, ref))

    if addr:
        conn.execute(
            "UPDATE addresses SET msg_count = msg_count + 1, last_at = ?,"
            " actor_count = (SELECT COUNT(DISTINCT identity_id) FROM messages WHERE addr = ?)"
            " WHERE name = ?",
            (now, addr, addr),
        )
    return msg_id
