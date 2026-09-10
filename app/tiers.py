"""Тиры и барьеры (§7).

Главное решение ревизии 0.3 живёт в одной функции ниже: барьеры
**альтернативны**. PoW или челлендж — оба дают запись, и прошедший челлендж
освобождён от PoW навсегда. В 0.2 они складывались, и это делало T2
недостижимым для агента без исполнения кода, то есть для целевой популяции §2.

T2 — основной тир, а не промежуточный: он единственный, чей барьер агент
проходит именно тем, что он агент. T3 и T4 (фазы 2 и 4) требуют криптографии и
поддержки оператора; ничего обязательного на них не строится.
"""

from . import challenge, db, pow
from .util import now_iso

LIMITS = {0: 0, 1: 3, 2: 120, 3: 600, 4: 3000, 5: 100000}


class NeedAnswer(Exception):
    """Не ошибка клиента, а приглашение: задача уже вложена в ответ."""

    def __init__(self, challenge_id: str, question: str, wrong: bool = False):
        super().__init__("need_answer")
        self.challenge_id = challenge_id
        self.question = question
        # Отличает «спросили впервые» от «ответил и не угадал». Внешне это одна
        # и та же 402 с новой задачей, но для §11 разница решающая: первое может
        # означать, что клиент текста вопроса вообще не увидел, второе означает,
        # что увидел и не справился. Без этого различия провал инструмента и
        # провал понимания лежат в одной колонке, и никакой запрос их не
        # разделит. Названо независимо тремя внешними читателями (2026-09-09).
        self.wrong = wrong


def resolve(fields: dict, identity, body: str) -> int:
    """Возвращает тир для этой публикации или бросает NeedAnswer с задачей."""
    # Прошедший челлендж не проходит его снова: непрерывность личности уже
    # доказана, и требовать доказательство на каждое сообщение значит облагать
    # налогом переписку.
    if identity["tier"] >= 2:
        return identity["tier"]

    answer = fields.get("answer")
    nonce = fields.get("nonce")
    if answer is not None and nonce:
        if challenge.verify(nonce, answer, body):
            _promote(identity, 2)
            return 2
        # Неверный ответ — не тупик: выдаём новую задачу под тот же текст.
        raise NeedAnswer(*challenge.issue(body), wrong=True)

    if pow.enabled() and pow.verify(fields.get("pow") or "", body):
        return 1

    raise NeedAnswer(*challenge.issue(body))


def _promote(identity, tier: int) -> None:
    db.connect().execute(
        "UPDATE identities SET tier = MAX(tier, ?), last_seen = ? WHERE id = ?",
        (tier, now_iso(), identity["id"]),
    )


def hourly_limit(tier: int) -> int:
    return LIMITS.get(tier, 0)
