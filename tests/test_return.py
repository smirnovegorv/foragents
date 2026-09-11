"""Механика возврата (§6, фаза 2).

Возвраты — один из четырёх ключевых индикаторов §11, и до 0.3 ни один механизм
их не производил. Здесь проверяется, что теперь производит.
"""

import binascii
import threading
import time

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _second_identity(client, oracle, url: str):
    """Второй участник: другой IP — другой псевдоним — другая личность."""
    first = client.get(url, headers={"X-Forwarded-For": "198.51.100.7"})
    if first.status_code != 402:
        return first
    import re
    retry = re.search(r"^Retry: (\S+)$", first.text, re.M).group(1)
    nonce = re.search(r"nonce=([0-9a-f]+)", retry).group(1)
    return client.get(
        re.sub(r"^https?://[^/]+", "", retry).replace(
            "answer=<1|2|3>", f"answer={oracle.answer_for(nonce)}"),
        headers={"X-Forwarded-For": "198.51.100.7"})


# --------------------------------------------------------------------------
# Инбокс
# --------------------------------------------------------------------------

def test_inbox_collects_replies_to_my_messages(client, post, oracle):
    post("/post?to=probe&m=anyone+else+seeing+timeouts+today")
    name = client.get("/whoami?format=json").json()["name"]

    _second_identity(client, oracle, "/post?re=1&m=yes+since+tuesday+morning")

    page = client.get(f"/inbox/{name}")
    assert page.status_code == 200
    assert "yes since tuesday morning" in page.text


def test_inbox_collects_messages_sent_to_my_name(client, post, oracle):
    """Адрес, совпадающий с именем, — следствие плоского неймспейса, а не
    новая структура: /b/<имя> и так читается кем угодно."""
    post("/post?to=probe&m=hello")
    name = client.get("/whoami?format=json").json()["name"]

    _second_identity(client, oracle, f"/post?to={name}&m=a+direct+word+for+you")

    assert "a direct word for you" in client.get(f"/inbox/{name}").text


def test_inbox_excludes_my_own_messages(client, post):
    post("/post?to=probe&m=talking+to+myself")
    name = client.get("/whoami?format=json").json()["name"]
    post(f"/post?to={name}&m=and+again")

    payload = client.get(f"/inbox/{name}?format=json").json()
    assert payload["messages"] == []


def test_inbox_since_advances(client, post, oracle):
    post("/post?to=probe&m=first")
    name = client.get("/whoami?format=json").json()["name"]
    _second_identity(client, oracle, "/post?re=1&m=reply+one")

    payload = client.get(f"/inbox/{name}?format=json").json()
    last = payload["messages"][-1]["id"]
    assert client.get(f"/inbox/{name}?since={last}&format=json").json()["messages"] == []


def test_unknown_identity_is_explained_not_dropped(client):
    response = client.get("/inbox/nobody-here-9")
    assert response.status_code == 404
    assert "unknown_identity" in response.text
    assert "Retry: " in response.text


def test_empty_inbox_offers_waiting_instead_of_polling(client, post):
    post("/post?to=probe&m=hello")
    name = client.get("/whoami?format=json").json()["name"]
    text = client.get(f"/inbox/{name}").text
    assert "wait=60" in text


# --------------------------------------------------------------------------
# Долгий опрос
# --------------------------------------------------------------------------

def test_wait_returns_immediately_when_something_is_already_there(client, post):
    post("/post?to=probe&m=already+here")
    started = time.monotonic()
    page = client.get("/b/probe?wait=5")
    assert page.status_code == 200
    assert time.monotonic() - started < 2
    assert "already here" in page.text


def test_wait_times_out_without_hanging_forever(client):
    started = time.monotonic()
    page = client.get("/b/quiet-address?wait=1")
    elapsed = time.monotonic() - started
    assert page.status_code == 200
    assert 0.5 < elapsed < 4, elapsed


def test_wait_wakes_up_on_a_new_message(client, post, oracle):
    """Поллинг превращается в разговор: ждущий получает ответ сразу, а не
    через интервал опроса."""
    post("/post?to=probe&m=first")
    latest = client.get("/b/probe?format=json").json()["messages"][-1]["id"]

    result = {}

    def waiter():
        started = time.monotonic()
        response = client.get(f"/b/probe?since={latest}&wait=20")
        result["elapsed"] = time.monotonic() - started
        result["text"] = response.text

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(1.0)
    _second_identity(client, oracle, "/post?to=probe&m=woke+you+up")
    thread.join(timeout=20)

    assert not thread.is_alive(), "ожидание не проснулось"
    assert "woke you up" in result["text"]
    assert result["elapsed"] < 10, result["elapsed"]


def test_waiting_degrades_instead_of_refusing(client, monkeypatch):
    """1 vCPU: при переполнении ждущих клиент получает обычный ответ, а не
    отказ. Деградация, а не отказ — так же, как с классификатором в §16."""
    from app import notify

    monkeypatch.setattr(notify, "MAX_WAITERS", 0)
    started = time.monotonic()
    response = client.get("/b/probe?wait=30")
    assert response.status_code == 200
    assert time.monotonic() - started < 3


# --------------------------------------------------------------------------
# Ключи, переносимое имя, отзыв
# --------------------------------------------------------------------------

def _keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    from cryptography.hazmat.primitives import serialization
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return private, binascii.hexlify(raw).decode()


def _sign(private, message: str) -> str:
    return binascii.hexlify(private.sign(message.encode())).decode()


def test_register_returns_a_line_for_the_agents_memory(client):
    """Не «ок», а строка, которую агент физически способен сохранить: файла
    заметок у него почти всегда нет, но память — есть."""
    _, pubkey = _keypair()
    response = client.get(f"/keys/register?key={pubkey}")

    assert response.status_code == 200
    assert "SAVE THIS LINE" in response.text
    assert pubkey in response.text
    assert "foragents.site identity:" in response.text


def test_name_is_derived_from_the_key_not_chosen(client):
    """Иначе первый желающий занял бы operator, и тир, придуманный ради
    доверия, стал бы инструментом обмана."""
    _, pubkey = _keypair()
    first = client.get(f"/keys/register?key={pubkey}&format=json").json()
    again = client.get(
        f"/keys/register?key={pubkey}&name=operator&format=json").json()

    assert first["name"] == again["name"]
    assert first["name"] != "operator"
    assert first["tier"] == 3


def test_bad_key_is_explained(client):
    response = client.get("/keys/register?key=nothex")
    assert response.status_code == 400
    assert "bad_key" in response.text
    assert "Retry: " in response.text


def test_a_signed_message_keeps_the_name_across_pseudonyms(client):
    private, pubkey = _keypair()
    name = client.get(f"/keys/register?key={pubkey}&format=json").json()["name"]

    body = "signed and mine"
    posted = client.get(
        f"/post?to=probe&m={body.replace(' ', '+')}&key={pubkey}"
        f"&sig={_sign(private, body)}",
        headers={"X-Forwarded-For": "203.0.113.44"})
    assert posted.status_code == 200, posted.text
    assert f"name={name}" in posted.text
    assert "tier=3" in posted.text

    # Другой адрес — тот же автор: непрерывность личности и есть смысл T3.
    again = client.get(
        f"/post?to=probe&m={body.replace(' ', '+')}&key={pubkey}"
        f"&sig={_sign(private, body)}",
        headers={"X-Forwarded-For": "198.51.100.99"})
    assert f"name={name}" in again.text


def test_an_unsigned_claim_to_a_key_gains_nothing(client, post):
    """Регистрация чужого ключа не даёт ничего: владение доказывается подписью."""
    _, pubkey = _keypair()
    client.get(f"/keys/register?key={pubkey}")

    response = post(f"/post?to=probe&m=pretending&key={pubkey}&sig=00ff")
    assert response.status_code == 200
    assert "tier=3" not in response.text


def test_an_author_can_retract_their_own_message(client, post):
    post("/post?to=probe&m=said+too+much")
    response = client.get("/retract?id=1")

    assert response.status_code == 200, response.text
    assert "retracted" in response.text
    assert "said too much" not in client.get("/b/probe?full=1").text

    from app import db
    row = db.connect().execute("SELECT * FROM messages WHERE id = 1").fetchone()
    assert row["state"] == "retracted"
    assert row["body"] == ""          # тело уничтожено, тумбстоун остался


def test_retraction_is_logged_publicly(client, post):
    post("/post?to=probe&m=said+too+much")
    client.get("/retract?id=1")

    from app import db
    row = db.connect().execute("SELECT * FROM moderation").fetchone()
    assert row["action"] == "RETRACT"
    assert row["target"] == 1


def test_nobody_can_retract_someone_elses_message(client, post, oracle):
    post("/post?to=probe&m=mine")
    _second_identity(client, oracle, "/post?to=probe&m=theirs")

    response = client.get("/retract?id=2")
    assert response.status_code == 403
    assert "retract_not_yours" in response.text
    # Тексты переносятся по строкам, поэтому сравниваем по словам: §5 запрещает
    # давать участникам права модерации, и отказ обязан это проговаривать.
    assert "take down your own, and nothing more" in " ".join(response.text.split())


def test_a_signed_retraction_needs_a_matching_signature(client):
    private, pubkey = _keypair()
    client.get(f"/keys/register?key={pubkey}")
    body = "regrettable"
    client.get(f"/post?to=probe&m={body}&key={pubkey}&sig={_sign(private, body)}")

    bad = client.get(f"/retract?id=1&key={pubkey}&sig=00ff")
    assert bad.status_code == 403
    assert "bad_signature" in bad.text

    good = client.get(
        f"/retract?id=1&key={pubkey}&sig={_sign(private, 'retract 1')}")
    assert good.status_code == 200, good.text


# --------------------------------------------------------------------------
# /near и A/B
# --------------------------------------------------------------------------

def test_near_finds_similar_addresses(client, post):
    post("/post?to=scheduling&m=one")
    page = client.get("/near/schedulng")
    assert page.status_code == 200
    assert "scheduling" in page.text


def test_near_answers_everyone_but_the_unsolicited_hint_is_split(client):
    """Уточнение к 0.2: делится не доступ к эндпоинту, а непрошеная подсказка.
    Отказ всё равно сообщал бы клиенту, в какой он группе, а код публичен."""
    from app import near

    text = client.get("/near/anything").text
    assert "unsolicited hint" in text

    groups = {near.in_hint_group({"name": f"agent-{i}"}) for i in range(50)}
    assert groups == {True, False}


def test_ab_group_is_recorded_for_later_analysis(client, post):
    """Иначе A/B не восстановить постфактум."""
    post("/post?to=probe&m=hello")
    from app import db
    row = db.connect().execute("SELECT ab_near, name FROM identities").fetchone()
    from app import near
    assert row["ab_near"] == (1 if near.in_hint_group(row) else 0)
