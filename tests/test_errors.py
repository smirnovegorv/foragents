"""Инвариант §6: ни одного 4xx/5xx без слов и без работающего URL.

Проверяется не наличие строки Retry:, а её пригодность — предложенный URL
запрашивается, и он обязан привести к успеху. Подсказка, которая сама
отвергается сервером, хуже её отсутствия: она уводит агента в цикл.
"""

import re

import pytest

CASES = [
    ("no_message",        "/post?to=probe"),
    ("empty_address",     "/b/"),
    ("bad_number",        "/b/probe?limit=soon"),
    ("bad_number",        "/b/probe?since=yesterday"),
    ("not_found",         "/no-such-endpoint"),
    ("address_too_long",  "/post?m=hi&to=" + "z" * 100),
    ("query_too_large",   "/post?m=" + "b" * 3000),
]


def _retry(response):
    match = re.search(r"^Retry: (\S+)$", response.text, re.M)
    assert match, f"ошибка без Retry:\n{response.text}"
    return re.sub(r"^https?://[^/]+", "", match.group(1))


@pytest.mark.parametrize("code,url", CASES)
def test_error_explains_itself_and_retry_works(client, code, url):
    response = client.get(url)

    assert response.status_code >= 400, url
    assert code in response.text.splitlines()[0], response.text
    # объяснение словами, а не только код
    assert len(response.text.splitlines()[1].split()) >= 4, response.text

    retried = client.get(_retry(response))
    # 402 — не ошибка, а следующий шаг воронки: подсказка довела клиента до
    # челленджа, что и требуется. Любой другой 4xx означал бы, что мы
    # предложили URL, который сами же отвергаем.
    assert retried.status_code in (200, 402), (
        f"предложенный URL сам вернул ошибку:\n{retried.text}")


def test_message_too_long_is_reachable_only_through_a_body(client, post):
    """Через GET раньше срабатывает лимит запроса — так и написано в §8:
    реальным ограничителем длины остаётся query 2 КБ, а не 2000 графем.
    Проверяем оба исхода явно, чтобы разница была зафиксирована, а не
    обнаружена потом на живом трафике."""
    from app import config

    through_get = client.get("/post?to=probe&m=" + "a" * 2500)
    assert "query_too_large" in through_get.text

    through_body = client.post("/post?to=probe", data={"m": "a" * 2500})
    assert through_body.status_code == 400
    assert "message_too_long" in through_body.text

    url = _retry(through_body)
    query = url.split("?", 1)[1] if "?" in url else ""
    assert len(query.encode()) <= config.MAX_QUERY_BYTES

    # предложенный URL несёт текст, урезанный ровно до лимита, и проходит
    retried = post(url)
    assert retried.status_code == 200, retried.text
    stored = client.get("/b/probe?format=json").json()["messages"][-1]["m"]
    assert len(stored) == config.MAX_BODY_GRAPHEMES


def test_method_not_allowed_is_explained(client):
    response = client.post("/index")
    assert response.status_code == 405
    assert "method_not_allowed" in response.text
    assert client.get(_retry(response)).status_code == 200


def test_readonly_switch_refuses_writes_but_not_reads(client, post, monkeypatch):
    from app import config

    post("/post?to=probe&m=before")
    monkeypatch.setattr(config, "READONLY", True)

    blocked = client.get("/post?to=probe&m=during")
    assert blocked.status_code == 503
    assert "readonly" in blocked.text
    assert client.get(_retry(blocked)).status_code == 200

    assert client.get("/b/probe").status_code == 200
    assert "during" not in client.get("/b/probe").text
