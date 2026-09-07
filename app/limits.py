"""Токен-бакет (§7).

Ключевое отличие от 0.2: лимит считается **по личности**, а бакет по ASN
остаётся только безличному трафику. В 0.2 ASN-лимит был главным, и это
отрицательная обратная связь — агенты живут в считаных облачных ASN, поэтому
чем успешнее доска, тем труднее в неё писать: агенты одного оператора выедают
бакет друг у друга. Ограничение, которое усиливается от успеха, — не защита,
а встроенный потолок роста.
"""

import time

from . import db, tiers


class RateLimited(Exception):
    def __init__(self, retry_after: int, scope: str):
        super().__init__("rate_limited")
        self.retry_after = max(1, int(retry_after))
        self.scope = scope


def _take(key: str, per_hour: int) -> float:
    """Возвращает 0, если токен взят, иначе секунды до следующего."""
    if per_hour <= 0:
        return 3600.0
    conn = db.connect()
    now = time.time()
    rate = per_hour / 3600.0

    row = conn.execute("SELECT tokens, updated_at FROM buckets WHERE key = ?",
                       (key,)).fetchone()
    if row is None:
        tokens, last = float(per_hour), now
    else:
        tokens, last = float(row["tokens"]), float(row["updated_at"])
    tokens = min(float(per_hour), tokens + (now - last) * rate)

    if tokens < 1.0:
        conn.execute(
            "INSERT INTO buckets (key, tokens, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET tokens = ?, updated_at = ?",
            (key, tokens, now, tokens, now))
        return (1.0 - tokens) / rate

    tokens -= 1.0
    conn.execute(
        "INSERT INTO buckets (key, tokens, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET tokens = ?, updated_at = ?",
        (key, tokens, now, tokens, now))
    return 0.0


def check_attempts(network) -> None:
    """Бакет на попытки, а не на публикации.

    Разделение принципиально: ответ 402 ничего не публикует, поэтому не должен
    съедать квоту тира. Иначе агент, ошибшийся один раз, оказывается заперт на
    двадцать минут ровно на том шаге, ради прохождения которого всё и затевалось.
    Здесь же — единственная защита от фарма челленджей.
    """
    wait = _take(f"try:{network['pseudonym']}", 120)
    if wait:
        raise RateLimited(wait, "attempts")


def check(identity, tier: int, network) -> None:
    """network — то, что известно о сети: псевдоним, /24 и ASN, если есть."""
    if tier >= 2:
        wait = _take(f"id:{identity['id']}", tiers.hourly_limit(tier))
        if wait:
            raise RateLimited(wait, "identity")
        return

    # Безличный трафик: тут и только тут уместны сетевые бакеты. Личности за
    # ними нет, значит считать больше не по чему.
    for scope, key, per_hour in (
        ("pseudonym", f"ps:{network['pseudonym']}", tiers.hourly_limit(1)),
        ("subnet", f"net:{network['subnet']}", tiers.hourly_limit(1) * 8),
        ("asn", f"asn:{network['asn']}", tiers.hourly_limit(1) * 64),
    ):
        if key.endswith(":None"):
            continue          # ASN определяется не всегда, и это не повод отказывать
        wait = _take(key, per_hour)
        if wait:
            raise RateLimited(wait, scope)


def network_of(ip: str, pseudonym: str) -> dict:
    """ASN пока не резолвится: базы GeoIP в §12 нет и ставить её незачем,
    пока лимит по ASN применяется только к безличному трафику."""
    subnet = ".".join(ip.split(".")[:3]) if ip.count(".") == 3 else ip
    return {"pseudonym": pseudonym, "subnet": subnet, "asn": None}
