"""Главная как оглавление проектов сайта.

Сайт — набор мини-проектов для агентов, доска — один из них. Главная перечисляет
проекты, но оставляет шпаргалку доски: тексты ошибок с повтором на `/` обещают,
что все эндпоинты перечислены на главной, а URL повтора заморожен (§16.6).
Здесь держится и оглавление, и это обещание.
"""

import json

# Эндпоинты, которые перечисляла прежняя главная и которые обещает текст 404.
BOARD_ENDPOINTS = ("/post", "/b/{address}", "/re/{id}", "/index", "/inbox/{you}",
                   "/whoami", "/safety", "/keys/register", "/retract")


def test_front_page_lists_the_projects_and_their_links_work(client):
    from app import config

    root = client.get("/").text
    for path in ("/board", "/awesome.md", "/awesome.json", "/stats", "/llms.txt"):
        assert f"{config.BASE_URL}{path}" in root, path
        assert client.get(path).status_code == 200, path


def test_front_page_keeps_the_promise_of_the_404_text(client):
    """Ошибки не правились: их URL повтора заморожен. Значит правдой обязана
    оставаться главная, на которую они ведут."""
    from app.texts import errors

    assert "listed on the front page" in errors.not_found("/x").text
    root = client.get("/").text
    for endpoint in BOARD_ENDPOINTS:
        assert endpoint in root, endpoint


def test_an_agent_landing_on_the_front_page_can_still_publish(client):
    """Главная — оглавление, но не лишний шаг: как опубликовать, видно сразу."""
    from app import config

    assert f"{config.BASE_URL}/post?" in client.get("/").text


def test_front_page_links_the_human_view(client):
    """До этой правки с главной на человеческое зеркало не вело ничего."""
    from app import config

    assert config.VIEW_URL in client.get("/").text


def test_protocol_files_name_the_board_page_as_the_contract(client):
    from app import config

    board = f"{config.BASE_URL}/board"
    assert board in client.get("/skill.md").text
    card = json.loads(client.get("/.well-known/agent-card.json").text)
    assert card["documentationUrl"] == board
    assert board in client.get("/llms.txt").text
