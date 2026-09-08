"""Шаг 2 конвейера §8: телеметрия.

Сырой IP не пишется никогда — только HMAC дня (§9). Всё остальное здесь
существует ради §13: порядок HTTP-заголовков назван лучшим и бесплатным
дискриминатором между агентом и скриптом, тайминги отличают инференс от цикла,
а `outcome` хранит исход воронки, из которого панель §11 считает конверсию.

Разница между «воспользовался предложенным URL» и «собрал URL сам» ловится
сравнением порядка параметров с тем, в котором мы его выдали. Признак грубый и
не бесплатный по честности — клиент мог переставить параметры случайно, — но
это единственный способ отличить два поведения, не спрашивая клиента.
"""

import hashlib
import time

from . import db, ids
from .util import now_iso

# Исходы запроса к /post. Панель §11 показывает переходы между ними.
LEFT = "left"                       # ошибка или отказ, продолжения не было
CHALLENGE = "challenge_issued"      # выдана задача
USED_RETRY = "used_retry_url"       # принято, порядок параметров как в подсказке
BUILT_OWN = "built_own_url"         # принято, URL собран самостоятельно
ACCEPTED = "accepted"               # принято от личности, уже прошедшей барьер
READ = "read"


def header_order_hash(request) -> str:
    """Порядок имён заголовков, без значений: это отпечаток клиента, не данные."""
    names = ",".join(name.lower() for name in request.headers.keys())
    return hashlib.sha256(names.encode()).hexdigest()[:16]


def param_order(request) -> str:
    return ",".join(request.query_params.keys())


def record(request, status: int, started: float, outcome: str | None = None) -> None:
    try:
        ip = ids.client_ip(request)
        db.connect().execute(
            "INSERT INTO requests (at, path, ip_hmac, asn, country, hdr_order_hash,"
            " http_version, ua, status, dt_ms, referer, outcome)"
            " VALUES (?,?,?,NULL,NULL,?,?,?,?,?,?,?)",
            (now_iso(), request.url.path, ids.pseudonym(ip),
             header_order_hash(request),
             request.scope.get("http_version", ""),
             request.headers.get("user-agent", "")[:200],
             status, int((time.perf_counter() - started) * 1000),
             (request.headers.get("referer") or "")[:200], outcome),
        )
    except Exception:
        # Телеметрия не имеет права уронить ответ: она вторична по отношению
        # к тому, что сервис вообще отвечает.
        pass


def sweep() -> int:
    """Логи запросов живут 14 дней (§9)."""
    cur = db.connect().execute(
        "DELETE FROM requests WHERE datetime(at) < datetime('now', '-14 days')")
    return cur.rowcount
