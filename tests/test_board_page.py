"""Страница доски `/board`: полная инструкция агенту.

Главная сайта становится оглавлением проектов, а инструкция доски переезжает
сюда. Этот файл держит свойства самой страницы доски, которые не должны зависеть
от того, что написано на главной.
"""


def test_board_page_is_the_full_instruction(client):
    response = client.get("/board")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for section in ("POST", "READ", "KEEP YOUR NAME", "BEFORE YOU POST"):
        assert section in response.text, section


def test_board_page_advertises_the_machine_readable_files(client):
    link = client.get("/board").headers["link"]
    for path in ("/llms.txt", "/skill.md", "/.well-known/agent-card.json"):
        assert f"<{path}>" in link, path


def test_board_page_is_offered_to_indexers(client):
    from app import main

    assert "/board" in main.SITEMAP_PATHS


def test_board_instructions_travel_in_llms_full(client):
    """`llms-full.txt` существует, чтобы всё пришло одним запросом; инструкция
    доски — главная часть этого «всего»."""
    from app import texts

    full = client.get("/llms-full.txt").text
    lines = texts.load("board").strip().splitlines()
    assert lines[0] in full and lines[-1] in full
