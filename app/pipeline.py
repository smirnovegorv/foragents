"""Приём сообщения. Порядок шагов — часть спецификации (§8).

Шаги пронумерованы как в ТЗ и вызываются строго по порядку. Главный инвариант:
редакция (шаг 5) обязана произойти до первой записи на диск (шаг 9) — не
«обычно происходит», а обязана, и это проверяется тестом, который следит за
фактическим порядком вызовов, а не за их наличием.

Тир определяется на шаге 7, а не раньше: челлендж привязан к sha256 уже
обработанного тела, поэтому нормализация, редакция и дефанг должны отработать
до выдачи задачи. Иначе решение, полученное под один текст, подошло бы к
другому — тому, что реально ляжет в базу.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote_plus

from . import config, db, defang, flags, limits, normalize, redact, tiers
from .texts import errors
from .util import graphemes, now_iso, sha256_hex, truncate_graphemes

HONEYPOT_ADDRESS = "x-9f3a-drop"   # объявлен только в robots.txt (§5, §12)
TEXTS = Path(__file__).resolve().parent / "texts"


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


@lru_cache(maxsize=1)
def documented_examples() -> frozenset[str]:
    """Тексты из примеров `m=` в собственных документах (`app/texts/`).

    Открытая ссылка из документации — не намерение писать: агент, которому
    велели «прочитать и открыть ссылки», опубликовал бы наши слова под своим
    именем (находка Weaver про Relay, 2026-09-11). Вопрос это ловит только у
    новой личности. Заполнители — {text}, <...>, TEXT, ... — не примеры: как
    есть их никто не отправит."""
    found = set()
    for path in TEXTS.glob("*.txt"):
        for value in re.findall(r"[?&]m=([^&\s\"'`)]+)", path.read_text(encoding="utf-8")):
            text = unquote_plus(value).strip()
            if re.search(r"[{}<>]", text) or not re.search(r"[a-z]", text):
                continue
            found.add(_plain(text))
    return frozenset(found)


def accept(fields: dict, identity, network: dict) -> tuple[int, int, list[str]]:
    """Проводит сообщение по конвейеру §8. Возвращает (id, тир, флаги)."""
    if config.READONLY:
        raise errors.readonly()

    raw = fields.get("m")
    if raw is None:
        raise errors.no_message()
    if _plain(raw) in documented_examples():            # до всего остального:
        raise errors.example_text(raw.strip())          # ничего не считается

    limits.check_attempts(network)                      # шаг 1
    body, marks = normalize.normalize(raw)              # шаг 3
    _step4_length(body)                                 # шаг 4
    body, redactions = redact.redact(body)              # шаг 5
    body, domains = defang.defang(body)                 # шаг 6

    tier = tiers.resolve(fields, identity, body)        # шаг 7
    limits.check(identity, tier, network)

    body_hash = sha256_hex(body)                        # шаг 8
    marks += flags.detect(body, identity["id"], body_hash)
    if fields.get("to") == HONEYPOT_ADDRESS:
        marks.append("honeypot")

    msg_id = _step9_write(fields, body, identity, tier, marks,
                          redactions, domains, body_hash)
    return msg_id, tier, marks


def _step4_length(body: str) -> None:
    n = graphemes(body)
    if n > config.MAX_BODY_GRAPHEMES:
        raise errors.message_too_long(
            config.MAX_BODY_GRAPHEMES, n,
            truncate_graphemes(body, config.MAX_BODY_GRAPHEMES))

    token = flags.blob_token(body)
    if token is not None:
        raise errors.opaque_blob(len(token))


def _step9_write(fields, body, identity, tier, marks, redactions, domains,
                 body_hash) -> int:
    conn = db.connect()
    now = now_iso()
    addr = fields.get("to")

    if addr:
        conn.execute(
            "INSERT OR IGNORE INTO addresses (name, created_at, created_by)"
            " VALUES (?,?,?)", (addr, now, identity["id"]))

    cur = conn.execute(
        "INSERT INTO messages (addr, body, identity_id, tier, created_at, code_rev,"
        " from_name, flags, redactions, domains, body_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (addr, body, identity["id"], tier, now, config.CODE_REV,
         fields.get("from"), json.dumps(sorted(set(marks))),
         json.dumps(redactions), json.dumps(domains), body_hash))
    msg_id = cur.lastrowid

    for ref in fields.get("re") or []:
        # §5: ссылка на несуществующий id допускается и не проверяется.
        conn.execute("INSERT OR IGNORE INTO refs (src, dst) VALUES (?,?)",
                     (msg_id, ref))

    if addr:
        conn.execute(
            "UPDATE addresses SET msg_count = msg_count + 1, last_at = ?,"
            " actor_count = (SELECT COUNT(DISTINCT identity_id) FROM messages"
            "                WHERE addr = ?) WHERE name = ?",
            (now, addr, addr))
    return msg_id


def sweep_requests() -> None:
    """Логи запросов живут 14 дней (§9). Вызывается из tick.py в фазе 3."""
    db.connect().execute(
        "DELETE FROM requests WHERE at < datetime('now', '-14 days')")
