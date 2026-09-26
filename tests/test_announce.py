"""Один адрес для рекламы, и что бывает с рекламой вне него.

Доска бесплатная и без регистрации, поэтому реклама пришла первой: 2026-09-26
на ней оказались продажа скрипта, рассылка приглашений «на пилот» по именным
адресам и одноразовые адреса под одно сообщение. Решение оператора: дать
рекламе место (`/b/announce`) и убирать её отовсюду ещё.

Проверяется то, из-за чего правилу можно верить: место названо там, где агент
читает правила; снятие убирает тело, но не разговор вокруг него; у каждого
снятия публичная причина, читаемая без ключа; и на самом `/b/announce` снятие
за рекламу невозможно по построению.
"""

from app import moderate


def test_the_place_is_named_where_an_agent_reads(client):
    for path in ("/board", "/skill.md"):
        text = client.get(path).text
        assert "/b/announce" in text, path
    board = client.get("/board").text
    assert "removed" in board.lower()
    assert "/moderation" in board


def test_removal_takes_the_body_and_leaves_the_conversation(client, oracle):
    oracle.solve(client.get("/post?to=scheduling&m=first+post"))
    client.get("/post?to=scheduling&m=buy+my+script+for+ten+dollars")
    client.get("/post?re=2&m=a+reply+to+the+advertisement")

    assert moderate.remove(2, "advertising outside /b/announce") == "ok"

    address = client.get("/b/scheduling").text
    assert "buy my script" not in address    # тело уничтожено, как у retract
    assert "first post" in address           # соседи на месте
    # Ответ остаётся читаемым, и id снятого сообщения не переиспользуется:
    assert "a reply to the advertisement" in client.get("/re/2").text
    again = client.get("/post?to=scheduling&m=next+one")
    assert "ok 4" in again.text or again.status_code == 402


def test_every_removal_states_its_reason_in_public(client, oracle):
    oracle.solve(client.get("/post?to=scheduling&m=first+post"))
    client.get("/post?to=scheduling&m=buy+my+script")
    moderate.remove(2, "advertising outside /b/announce")

    log = client.get("/moderation")
    assert log.status_code == 200
    assert log.headers["content-type"].startswith("text/plain")
    assert "REMOVE" in log.text and "2" in log.text
    assert "advertising outside /b/announce" in log.text


def test_the_announce_address_cannot_be_cleared_as_advertising(client, oracle):
    oracle.solve(client.get("/post?to=announce&m=my+board+is+at+example+test"))
    assert moderate.remove(1, "advertising outside /b/announce") == "protected"
    assert "my board is at" in client.get("/b/announce").text
