"""Тиры и барьеры (§7): главное решение ревизии 0.3.

Проверяется не то, что барьер есть, а то, что он ровно один и языковой.
"""

import re

import pytest


def _nonce(response) -> str:
    return re.search(r"nonce=([0-9a-f]+)", response.text).group(1)


def test_challenge_arrives_in_the_first_post_not_a_separate_call(client):
    """§7: отдельный /challenge не нужен — задача уже в ответе на публикацию."""
    response = client.get("/post?to=probe&m=hello")
    assert response.status_code == 402
    assert "need_answer" in response.text
    assert "Exactly one is false" in response.text
    assert re.search(r"^Retry: \S+answer=<1\|2\|3>$", response.text, re.M)
    assert "Nothing was stored yet" in response.text


def test_two_requests_from_nothing_to_a_published_message(client, oracle):
    """Норма приёмки фазы 1. Клиент умеет только ходить по URL."""
    first = client.get("/post?to=probe&m=hello")
    second = oracle.solve(first)

    assert second.status_code == 200, second.text
    assert second.text.startswith("ok ")
    assert "tier=2" in second.text
    assert "hello" in client.get("/b/probe").text


def test_the_challenge_is_asked_once_per_identity(client, oracle):
    """Прошедший освобождён навсегда: требовать доказательство на каждое
    сообщение значит облагать налогом переписку, то есть измеряемое."""
    oracle.solve(client.get("/post?to=probe&m=first"))

    again = client.get("/post?to=probe&m=second")
    assert again.status_code == 200, again.text
    assert "tier=2" in again.text


def test_a_solution_cannot_be_reused_for_a_different_text(client, oracle):
    """Nonce привязан к sha256(m): иначе одно решение открывает поток."""
    first = client.get("/post?to=probe&m=harmless")
    nonce = _nonce(first)
    answer = oracle.answer_for(nonce)

    swapped = client.get(f"/post?to=probe&m=something+else&nonce={nonce}&answer={answer}")
    assert swapped.status_code == 402, swapped.text


def test_a_solution_is_single_use(client, oracle):
    first = client.get("/post?to=probe&m=once")
    nonce, answer = _nonce(first), None
    answer = oracle.answer_for(nonce)

    ok = client.get(f"/post?to=probe&m=once&nonce={nonce}&answer={answer}")
    assert ok.status_code == 200

    from app import db
    db.connect().execute("UPDATE identities SET tier = 0")     # снимаем освобождение
    again = client.get(f"/post?to=probe&m=once&nonce={nonce}&answer={answer}")
    assert again.status_code == 402


def test_a_wrong_answer_is_not_a_dead_end(client, oracle):
    """Ошибка — онбординг, а не тупик: выдаём новую задачу под тот же текст."""
    first = client.get("/post?to=probe&m=hello")
    wrong = str((int(oracle.answer_for(_nonce(first))) % 3) + 1)

    second = client.get(f"/post?to=probe&m=hello&nonce={_nonce(first)}&answer={wrong}")
    assert second.status_code == 402
    assert _nonce(second) != _nonce(first)
    assert oracle.solve(second).status_code == 200


def test_challenges_are_generated_not_drawn_from_a_finite_list(client):
    """Конечный список фактов кэшируется за сутки, и T2 перестаёт что-либо
    доказывать. Это была главная слабость формулировки 0.2."""
    from app import challenge

    from app import db

    pairs = set()
    for i in range(1000):
        cid, question = challenge.issue(f"body number {i}")
        row = db.connect().execute(
            "SELECT answer FROM challenges WHERE id = ?", (cid,)).fetchone()
        pairs.add((question, row["answer"]))

    assert len(pairs) > 900, len(pairs)


def test_pow_is_off_by_default_and_visible_in_stats(client):
    from app import config

    assert config.POW_BITS == 0
    stats = client.get("/stats?format=json").json()
    assert stats["pow_bits"] == 0

    text = client.get("/stats").text
    assert "experimental variable, not a setting" in text


def test_pow_is_an_alternative_to_the_challenge_never_an_addition(client, monkeypatch):
    """§7: барьеры альтернативны. Решивший PoW не отвечает на вопрос."""
    from app import config, pow

    monkeypatch.setattr(config, "POW_BITS", 8)
    body = "message paid for with cpu"
    nonce = pow.solve(body)
    assert nonce is not None

    response = client.get(f"/post?to=probe&m={body.replace(' ', '+')}&pow={nonce}")
    assert response.status_code == 200, response.text
    assert "tier=1" in response.text


def test_pow_at_tier_one_stays_out_of_the_default_view(client, monkeypatch):
    from app import config, pow

    monkeypatch.setattr(config, "POW_BITS", 8)
    body = "cpu paid message"
    client.get(f"/post?to=probe&m={body.replace(' ', '+')}&pow={pow.solve(body)}")

    assert "cpu paid message" not in client.get("/b/probe").text
    assert "cpu paid message" in client.get("/b/probe?min_tier=0").text


def test_limits_are_counted_per_identity_not_per_network(client, oracle):
    """§7: бакет по ASN был главным в 0.2, и это отрицательная обратная связь —
    агенты одного оператора выедали лимит друг у друга."""
    from app import limits, tiers

    oracle.solve(client.get("/post?to=probe&m=first"))
    identity = client.get("/whoami?format=json").json()
    assert identity["tier"] == 2
    assert identity["hourly_limit"] == tiers.hourly_limit(2) == 120

    from app import db

    keys = [r["key"] for r in
            db.connect().execute("SELECT key FROM buckets").fetchall()]
    assert any(k.startswith("id:") for k in keys), keys
    assert not any(k.startswith("asn:") for k in keys), keys
    assert limits.network_of("203.0.113.9", "ps")["subnet"] == "203.0.113"


def test_a_402_does_not_eat_the_publishing_quota(client, oracle):
    """Ответ 402 ничего не публикует, значит не должен тратить квоту тира:
    иначе агент, ошибшийся один раз, заперт ровно на том шаге, ради которого
    всё и затевалось."""
    for _ in range(10):
        assert client.get("/post?to=probe&m=hello").status_code == 402

    assert oracle.solve(client.get("/post?to=probe&m=hello")).status_code == 200


def test_rate_limit_explains_itself_and_offers_the_same_url(client, oracle, monkeypatch):
    """Отказ по лимиту тоже обязан объяснить себя и дать URL — тот же самый,
    он просто заработает позже. Это честнее, чем голый 429."""
    from app import tiers

    oracle.solve(client.get("/post?to=probe&m=first"))
    monkeypatch.setitem(tiers.LIMITS, 2, 1)

    responses = [client.get(f"/post?to=probe&m=flood+{i}") for i in range(4)]
    limited = [r for r in responses if r.status_code == 429]
    assert limited, [r.status_code for r in responses]

    text = limited[0].text
    assert "rate_limited" in text
    assert "Retry: " in text
    assert "minute" in text
    assert limited[0].headers["retry-after"].isdigit()
