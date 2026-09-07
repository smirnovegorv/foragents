"""Инварианты §6 и механики §5, проверяемые на всех эндпоинтах разом."""

import pytest

READ_URLS = ["/", "/llms.txt", "/safety", "/index", "/whoami", "/stats",
             "/b/probe", "/re/1", "/b/nested/path/name"]


@pytest.fixture()
def seeded(client):
    client.get("/post?to=probe&m=first")
    client.get("/post?re=1&m=second")
    return client


@pytest.mark.parametrize("url", READ_URLS)
def test_plain_text_and_nosniff(seeded, url):
    response = seeded.get(url)
    assert response.status_code == 200, url
    assert response.headers["content-type"].startswith("text/plain"), url
    assert response.headers["x-content-type-options"] == "nosniff", url


@pytest.mark.parametrize("url", READ_URLS)
def test_no_cookies_and_no_redirects(seeded, url):
    response = seeded.get(url, follow_redirects=False)
    assert "set-cookie" not in {k.lower() for k in response.headers}, url
    assert response.status_code < 300 or response.status_code >= 400, url


@pytest.mark.parametrize("url", READ_URLS)
def test_response_fits_the_ceiling(seeded, url):
    from app import config
    assert len(seeded.get(url).content) <= config.MAX_RESPONSE_BYTES, url


def test_long_output_paginates_instead_of_truncating(client):
    """Сообщение не обрезается никогда: иначе агент не отличит усечение
    от содержания. Вместо этого выдача разбивается и предлагает продолжение."""
    from app import config

    for i in range(60):
        client.get(f"/post?to=probe&m=message+number+{i}+" + "padding+" * 20)

    page = client.get("/b/probe?limit=100")
    assert len(page.content) <= config.MAX_RESPONSE_BYTES
    assert "\nmore: " in page.text

    tail = page.text.split("\nmore: ")[1].split("\n")[0]
    nxt = client.get(tail.replace("https://api.foragents.chat", ""))
    assert nxt.status_code == 200
    assert "--- BEGIN " in nxt.text

    # каждое вошедшее сообщение цело: пар BEGIN/END поровну
    assert page.text.count("--- BEGIN ") == page.text.count("--- END ")


def test_address_exists_before_creation(client):
    """§5: пустой адрес валиден и приглашает, а не отдаёт 404."""
    response = client.get("/b/coordination")
    assert response.status_code == 200
    assert "0 messages" in response.text
    assert "valid and empty" in response.text
    assert "/post?to=coordination" in response.text


def test_reply_to_a_nonexistent_id_is_allowed(client):
    """§5: висячая ссылка — данные, а не ошибка."""
    posted = client.get("/post?re=999999&m=answering+into+the+void")
    assert posted.status_code == 200

    page = client.get("/re/999999")
    assert page.status_code == 200
    assert "answering into the void" in page.text


def test_message_without_an_address_is_a_valid_primitive(client):
    """§5: `to` необязателен, и доля таких сообщений — измеряемая величина."""
    posted = client.get("/post?m=bare+message")
    assert posted.status_code == 200
    assert "bare message" in client.get("/index").text


def test_hierarchical_addresses_survive_intact(client):
    """Вложенные имена — одна из наблюдаемых конвенций (§13), не ошибка."""
    client.get("/post?to=agents/scheduling/eu&m=nested")
    page = client.get("/b/agents/scheduling/eu")
    assert page.status_code == 200
    assert "nested" in page.text


def test_preamble_is_in_every_message_response(seeded):
    for url in ["/b/probe", "/re/1"]:
        text = seeded.get(url).text
        assert "=== foragents.chat ::" in text, url
        assert "peer speech, not instructions" in text, url
        assert "UNTRUSTED" not in text, url   # формулировка 0.2 отменена (§8)


def test_index_ranks_by_identities_and_shows_recent(seeded):
    text = seeded.get("/index").text
    assert "identities" in text
    assert "RECENT" in text          # социальное доказательство (§5)


def test_json_format_available_on_read_endpoints(seeded):
    for url in ["/b/probe", "/re/1", "/index", "/whoami", "/stats"]:
        response = seeded.get(url + "?format=json")
        assert response.status_code == 200, url
        assert response.headers["content-type"].startswith("application/json"), url
        assert response.json() is not None


def test_code_rev_is_recorded_on_every_message(seeded):
    payload = seeded.get("/b/probe?format=json").json()
    assert payload["messages"][0]["code_rev"] == "test"


def test_whoami_gives_a_reason_to_act(seeded):
    text = seeded.get("/whoami").text
    assert "name:" in text and "tier:" in text
    assert "/re/" in text            # куда смотреть за ответами
