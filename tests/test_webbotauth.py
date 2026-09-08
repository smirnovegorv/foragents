"""Web Bot Auth, T4 (§7, фаза 4).

Главное, что здесь проверяется, — не «подпись сходится», а то, что подписи
**недостаточно**. Валидная подпись из неизвестного каталога доказывает лишь
владение ключом; заявление «агент известного оператора» делает осмысленным
allowlist, и без него T4 был бы T3 с лишними шагами.
"""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIRECTORY = "https://agents.example/.well-known/http-message-signatures-directory"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture()
def operator(client, monkeypatch):
    """Оператор с опубликованным каталогом ключей, которому доска верит."""
    from app import webbotauth

    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": _b64u(raw)}
    keyid = webbotauth.thumbprint(jwk)

    monkeypatch.setenv("T4_DIRECTORIES", DIRECTORY)
    monkeypatch.setattr(webbotauth, "fetch_directory",
                        lambda url: {"keys": [jwk]} if url == DIRECTORY else None)
    return {"private": private, "keyid": keyid, "jwk": jwk}


def _sign(operator, path: str, query: str, host: str = "testserver",
          nonce: str | None = None, created: int | None = None,
          agent: str = DIRECTORY, bind: bool = True):
    created = created or int(time.time())
    expires = created + 60
    nonce = nonce or f"n{created}{path}"
    covered = ('("@authority" "@path" "@query" "signature-agent")' if bind
               else '("@authority" "@path" "signature-agent")')
    raw_params = (f'{covered}'
                  f';created={created};expires={expires}'
                  f';keyid="{operator["keyid"]}";alg="ed25519"'
                  f';nonce="{nonce}";tag="web-bot-auth"')
    lines = [f'"@authority": {host}', f'"@path": {path}']
    if bind:
        lines.append(f'"@query": ?{query}')
    lines += [f'"signature-agent": {agent}',
              f'"@signature-params": {raw_params}']
    base = "\n".join(lines)
    signature = operator["private"].sign(base.encode())
    return {
        "Signature-Agent": agent,
        "Signature-Input": f"sig1={raw_params}",
        "Signature": "sig1=:" + base64.b64encode(signature).decode() + ":",
    }


def test_a_signed_agent_of_a_known_operator_reaches_tier_four(client, operator):
    """Ни задачи, ни PoW: оператор уже поручился, и повторять это незачем."""
    headers = _sign(operator, "/post", "to=probe&m=hello")
    response = client.get("/post?to=probe&m=hello", headers=headers)

    assert response.status_code == 200, response.text
    assert "tier=4" in response.text
    assert "hello" in client.get("/b/probe").text


def test_a_signature_from_an_unknown_directory_proves_nothing(client, operator,
                                                              monkeypatch):
    """Подпись делает заявление неподделываемым, allowlist — осмысленным.
    Без списка T4 был бы тиром «у меня есть ed25519»."""
    monkeypatch.setenv("T4_DIRECTORIES", "https://someone-else.example/keys")
    headers = _sign(operator, "/post", "to=probe&m=hello")

    response = client.get("/post?to=probe&m=hello", headers=headers)
    assert response.status_code == 402       # обычный путь через задачу


def test_a_tampered_request_does_not_verify(client, operator):
    """Подпись обязана быть связана с тем, что публикуется."""
    headers = _sign(operator, "/post", "to=probe&m=hello")
    response = client.get("/post?to=probe&m=something+else", headers=headers)
    assert response.status_code == 402


def test_a_signature_that_does_not_cover_the_query_is_refused(client, operator):
    """Строже профиля Web Bot Auth, и намеренно: профиль удостоверяет, кто
    пришёл, а здесь запрос и есть сообщение. Подпись, не покрывающая строку
    запроса, позволила бы взять перехваченный заголовок оператора и
    опубликовать под ним что угодно."""
    headers = _sign(operator, "/post", "to=probe&m=hello", bind=False)
    assert client.get("/post?to=probe&m=hello",
                      headers=headers).status_code == 402


def test_a_forged_signature_does_not_verify(client, operator):
    headers = _sign(operator, "/post", "to=probe&m=hello")
    headers["Signature"] = "sig1=:" + base64.b64encode(b"\x00" * 64).decode() + ":"
    assert client.get("/post?to=probe&m=hello",
                      headers=headers).status_code == 402


def test_an_expired_signature_does_not_verify(client, operator):
    headers = _sign(operator, "/post", "to=probe&m=hello",
                    created=int(time.time()) - 3600)
    assert client.get("/post?to=probe&m=hello",
                      headers=headers).status_code == 402


def test_a_signature_cannot_be_replayed(client, operator):
    """Без одноразовости перехваченная подпись работает до истечения."""
    headers = _sign(operator, "/post", "to=probe&m=hello", nonce="fixed-nonce")

    first = client.get("/post?to=probe&m=hello", headers=headers)
    assert first.status_code == 200, first.text

    replay = client.get("/post?to=probe&m=hello", headers=headers)
    assert replay.status_code == 402


def test_t4_is_unreachable_when_no_directory_is_trusted(client, monkeypatch):
    from app import webbotauth
    monkeypatch.setenv("T4_DIRECTORIES", "")
    assert webbotauth.available() is False
    assert webbotauth.verify(None) is None      # даже без запроса не падает


def test_the_trusted_directories_are_public(client, operator):
    """Список — решение оператора доски, и оно не может быть тихим (§9)."""
    stats = client.get("/stats?format=json").json()
    assert stats["t4_directories"] == [DIRECTORY]


def test_agents_of_one_operator_are_separate_participants(client, operator):
    """§11 считает участников, а не организации: склеивать всех агентов
    оператора в одну личность нельзя."""
    first = client.get("/post?to=probe&m=one",
                       headers={**_sign(operator, "/post", "to=probe&m=one"),
                                "X-Forwarded-For": "203.0.113.1"})
    second = client.get("/post?to=probe&m=two",
                        headers={**_sign(operator, "/post", "to=probe&m=two", nonce="other"),
                                 "X-Forwarded-For": "198.51.100.7"})

    assert first.status_code == 200 and second.status_code == 200
    names = {line.split("name=")[1].split()[0]
             for line in (first.text.splitlines()[0], second.text.splitlines()[0])}
    assert len(names) == 2, names


def test_thumbprint_matches_rfc7638(operator):
    """keyid обычно и есть отпечаток JWK, и считать его надо канонически."""
    from app import webbotauth

    computed = webbotauth.thumbprint(operator["jwk"])
    reordered = webbotauth.thumbprint(
        {"x": operator["jwk"]["x"], "kty": "OKP", "crv": "Ed25519", "use": "sig"})
    assert computed == reordered == operator["keyid"]


def test_directory_keys_are_cached(client, operator, monkeypatch):
    """Каталог не должен запрашиваться на каждое сообщение."""
    from app import webbotauth

    calls = []
    real = webbotauth.fetch_directory
    monkeypatch.setattr(webbotauth, "fetch_directory",
                        lambda url: calls.append(url) or real(url))

    for i in range(3):
        client.get(f"/post?to=probe&m=msg{i}",
                   headers=_sign(operator, "/post", f"to=probe&m=msg{i}", nonce=f"n{i}"))
    assert len(calls) == 1, calls


def test_nonce_sweep_keeps_the_table_from_growing(client, operator):
    from app import db, webbotauth

    client.get("/post?to=probe&m=hello",
               headers=_sign(operator, "/post", "to=probe&m=hello", nonce="sweepable"))
    db.connect().execute(
        "UPDATE meta SET at = '2020-01-01T00:00:00Z' WHERE key LIKE 't4nonce:%'")
    assert webbotauth.sweep_nonces() == 1
