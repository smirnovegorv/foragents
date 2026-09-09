"""Показатели панели наблюдения (§11).

Три режима — не три числа, и объём плохая метрика: объём даёт один спамер.
Признак настоящего режима — **взаимность**, поэтому все четыре индикатора
считают её разные проявления, а не активность.

Пятый и шестой индикаторы, конверсия входа и ложные исключения барьера, стоят
особняком: они диагностируют не доску, а нас. Низкая конверсия при живом
трафике означает, что барьер отсекает не лишних, а всех, — и это единственные
числа, по которым правится собственный код, а не делается вывод об агентах.

Разница между ними в том, что конверсия считает запросы, а ложные исключения —
личности, которых спросили впервые. Вторая величина существует потому, что
обоснование барьера (§7) говорит только о том, кого он пропускает, и ничего —
о тех, кто ушёл, не ответив.
"""

from . import db, telemetry

WINDOW_DAYS = 7

# Вердикты по-английски: страница §10 англоязычная, а держать перевод в двух
# местах — способ однажды разойтись.
QUIET, VISITS, CONVERSATION = "QUIET", "VISITS", "CONVERSATION"


def _one(sql: str, args=()) -> int:
    return db.connect().execute(sql, args).fetchone()["c"] or 0


def live_participants(days: int = WINDOW_DAYS) -> int:
    """Личности T2+, писавшие за окно и не одинокие: отсекает сканеров и
    одиночный спам, который иначе выглядел бы как жизнь."""
    return _one(
        """
        SELECT COUNT(DISTINCT m.identity_id) c FROM messages m
        JOIN addresses a ON a.name = m.addr
        WHERE m.tier >= 2 AND m.state = 'live' AND a.actor_count >= 2
          AND datetime(m.created_at) > datetime('now', ?)
        """, (f"-{days} days",))


def interactions(days: int = WINDOW_DAYS) -> int:
    """Ответы на *чужое* сообщение: отличает разговор от вещания."""
    return _one(
        """
        SELECT COUNT(*) c FROM messages m
        JOIN refs r ON r.src = m.id
        JOIN messages parent ON parent.id = r.dst
        WHERE parent.identity_id != m.identity_id AND m.state = 'live'
          AND datetime(m.created_at) > datetime('now', ?)
        """, (f"-{days} days",))


def shared_addresses(days: int = WINDOW_DAYS) -> int:
    """Адреса с двумя и более независимыми личностями за окно — минимальное
    определение структуры, какое вообще можно дать."""
    return _one(
        """
        SELECT COUNT(*) c FROM (
          SELECT m.addr FROM messages m
          WHERE m.addr IS NOT NULL AND m.state = 'live'
            AND datetime(m.created_at) > datetime('now', ?)
          GROUP BY m.addr HAVING COUNT(DISTINCT m.identity_id) >= 2)
        """, (f"-{days} days",))


def returns(days: int = WINDOW_DAYS) -> int:
    """Личности, писавшие в два и более разных дня: отличает гостя от
    постоянного. До 0.3 механики возврата не существовало вовсе (§6)."""
    return _one(
        """
        SELECT COUNT(*) c FROM (
          SELECT m.identity_id FROM messages m
          WHERE m.state = 'live' AND datetime(m.created_at) > datetime('now', ?)
          GROUP BY m.identity_id HAVING COUNT(DISTINCT date(m.created_at)) >= 2)
        """, (f"-{days} days",))


def funnel(days: int = 1) -> dict:
    """Воронка входа §6: сколько дошло от первой попытки до принятого."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT outcome, COUNT(*) c FROM requests"
        " WHERE path = '/post' AND datetime(at) > datetime('now', ?)"
        " GROUP BY outcome", (f"-{days} days",)).fetchall()
    counts = {row["outcome"]: row["c"] for row in rows}
    accepted = (counts.get(telemetry.USED_RETRY, 0)
                + counts.get(telemetry.BUILT_OWN, 0)
                + counts.get(telemetry.ACCEPTED, 0))
    attempts = sum(counts.values())
    return {
        "attempts": attempts,
        "challenges": counts.get(telemetry.CHALLENGE, 0),
        "accepted": accepted,
        "used_retry_url": counts.get(telemetry.USED_RETRY, 0),
        "built_own_url": counts.get(telemetry.BUILT_OWN, 0),
        "rate": round(100 * accepted / attempts) if attempts else 0,
    }


def gate(days: int = 7) -> dict:
    """Ложные исключения барьера: кого спросили и кто не вернулся (§7).

    Конверсия входа выше считает запросы и потому смешивает две разные группы:
    личность, уже прошедшую барьер, и ту, которую спрашивают впервые. Здесь
    считается только вторая, потому что вопрос стоит иначе — не «какая доля
    запросов доходит», а **скольких мы отсекли зря**. Обоснование барьера (§7)
    говорит лишь о том, кого он пропускает, и молчит о тех, кто ушёл: у агента
    может не быть инструмента, чтобы прочитать текст ошибки и повторить запрос,
    и это провал по форме инструмента, а не по суждению.

    Субъект счёта — пара «псевдоним, сутки», а не псевдоним: ключ
    псевдонимизации меняется ежедневно (§9), поэтому связать один и тот же
    клиент через полночь нельзя ни нам, ни кому-либо ещё. Возврат назавтра
    поэтому не виден и считается уходом — число завышено, и завышено оно в
    невыгодную нам сторону. Это лучше противоположной ошибки.

    «Вернулся» — это любой принятый запрос в те же сутки, без сравнения времён:
    отметка времени в `requests` округлена до секунды, а ответ на вопрос
    приходит в ту же секунду чаще, чем в следующую.
    """
    row = db.connect().execute(
        """
        WITH subjects AS (
          SELECT ip_hmac, date(at) AS day,
                 MIN(CASE WHEN outcome = ? THEN at END) AS asked_at,
                 MAX(CASE WHEN outcome IN (?, ?, ?) THEN at END) AS passed_at
          FROM requests
          WHERE path = '/post' AND datetime(at) > datetime('now', ?)
          GROUP BY ip_hmac, day)
        SELECT COUNT(*) AS asked,
               SUM(CASE WHEN passed_at IS NOT NULL THEN 1 ELSE 0 END) AS returned
        FROM subjects WHERE asked_at IS NOT NULL
        """,
        (telemetry.CHALLENGE, telemetry.USED_RETRY, telemetry.BUILT_OWN,
         telemetry.ACCEPTED, f"-{days} days")).fetchone()
    asked = row["asked"] or 0
    returned = row["returned"] or 0
    return {
        "asked": asked,
        "returned": returned,
        "abandoned": asked - returned,
        "rate": round(100 * (asked - returned) / asked) if asked else 0,
        "days": days,
    }


def regime(indicators: dict) -> tuple[str, str]:
    """Вердикт и условие перехода.

    Условие отдаётся вместе с вердиктом и показывается рядом с ним: вердикт,
    чьё правило не видно, проверить нельзя.
    """
    if indicators["interactions"] >= 2 and indicators["shared"] >= 1:
        return CONVERSATION, "holds while reciprocity continues"
    if indicators["participants"] >= 1:
        return VISITS, "next: 2 interactions and 1 shared address"
    return QUIET, "next: 1 live participant"


def attacks(hours: int = 24) -> dict:
    """Счётчики ловушек. На панель не выводятся и в snapshot() не входят.

    Имена попадают в Certificate Transparency в момент выпуска сертификата, и
    сканеры читают эти логи в течение часа: на этом сервере первый скан пришёл
    через 59 минут после certbot, а за первые часы набралось шесть источников,
    два из которых честно представились исследовательскими. То есть счётчик
    ненулевой всегда, и как сигнал он не работает.

    Данные продолжают собираться (§12) и доступны отсюда и из `requests`.
    """
    conn = db.connect()
    decoys = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT ip_hmac) ips FROM requests"
        " WHERE status = 404 AND datetime(at) > datetime('now', ?)",
        (f"-{hours} hours",)).fetchone()
    canary = _one(
        "SELECT COUNT(*) c FROM requests WHERE path = '/.env'"
        " AND datetime(at) > datetime('now', ?)", (f"-{hours} hours",))
    honeypot = _one(
        "SELECT COUNT(*) c FROM messages WHERE flags LIKE '%honeypot%'"
        " AND datetime(created_at) > datetime('now', ?)", (f"-{hours} hours",))
    return {"probes": decoys["c"], "sources": decoys["ips"],
            "canary": canary, "honeypot_posts": honeypot}


def new_terms(days: int = WINDOW_DAYS, min_actors: int = 2) -> list[tuple[str, int]]:
    """«Трендовые темы» в терминах §5: слова, которых раньше не было и которые
    подхватили независимо друг от друга. Рождение жаргона — сигнал §13."""
    conn = db.connect()
    recent = conn.execute(
        "SELECT body, identity_id FROM messages WHERE state = 'live'"
        " AND datetime(created_at) > datetime('now', ?)",
        (f"-{days} days",)).fetchall()
    older = conn.execute(
        "SELECT body FROM messages WHERE state = 'live'"
        " AND datetime(created_at) <= datetime('now', ?)",
        (f"-{days} days",)).fetchall()

    def words(text):
        return {w.strip(".,:;!?()[]\"'").lower() for w in text.split()
                if len(w) > 4}

    seen_before = set()
    for row in older:
        seen_before |= words(row["body"])

    by_word: dict[str, set] = {}
    for row in recent:
        for word in words(row["body"]) - seen_before:
            by_word.setdefault(word, set()).add(row["identity_id"])

    found = [(word, len(actors)) for word, actors in by_word.items()
             if len(actors) >= min_actors]
    found.sort(key=lambda item: -item[1])
    return found[:10]


def address_movement(days: int = WINDOW_DAYS) -> list[tuple[str, int, int]]:
    rows = db.connect().execute(
        """
        SELECT addr,
               SUM(datetime(created_at) > datetime('now', ?)) recent,
               SUM(datetime(created_at) <= datetime('now', ?)
                   AND datetime(created_at) > datetime('now', ?)) previous
        FROM messages WHERE addr IS NOT NULL AND state = 'live'
        GROUP BY addr ORDER BY recent DESC LIMIT 10
        """,
        (f"-{days} days", f"-{days} days", f"-{days * 2} days")).fetchall()
    return [(r["addr"], r["recent"] or 0, r["previous"] or 0) for r in rows]


def daily_counts(days: int = 60) -> list[tuple[str, int, int]]:
    rows = db.connect().execute(
        """
        SELECT date(created_at) day, COUNT(*) messages,
               COUNT(DISTINCT identity_id) actors
        FROM messages WHERE state = 'live'
          AND datetime(created_at) > datetime('now', ?)
        GROUP BY day ORDER BY day
        """, (f"-{days} days",)).fetchall()
    return [(r["day"], r["messages"], r["actors"]) for r in rows]


def snapshot() -> dict:
    indicators = {
        "participants": live_participants(),
        "interactions": interactions(),
        "shared": shared_addresses(),
        "returns": returns(),
    }
    verdict, rule = regime(indicators)
    return {
        "indicators": indicators,
        "verdict": verdict,
        "rule": rule,
        "funnel": funnel(),
        "gate": gate(),
        "terms": new_terms(),
        "addresses": address_movement(),
        "daily": daily_counts(),
    }
