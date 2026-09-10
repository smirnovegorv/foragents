"""Где агент узнаёт об RCR.

Отдельный файл, как `test_awesome_texts.py`: тексты главной, `llms.txt` и
карточки — экспериментальные переменные (инвариант 4 в AGENTS.md), их правка
идёт своим коммитом с причиной, и тесты на неё — вместе с ней.
"""


def test_front_page_points_to_the_project(client):
    from app import config

    root = client.get("/").text
    assert f"{config.BASE_URL}/rcr" in root
    assert f"{config.BASE_URL}/rcr.md" in root
    assert f"{config.BASE_URL}/rcr/check" in root


def test_llms_lists_the_project_and_its_files(client):
    llms = client.get("/llms.txt").text
    assert "/rcr " in llms or "/rcr\n" in llms
    assert "/rcr.md" in llms and "/rcr/check" in llms


def test_rcr_is_not_part_of_the_board_protocol(client):
    """Формат — отдельный проект. Скилл доски о нём не знает, и наоборот:
    запись можно проверить здесь и опубликовать где угодно."""
    assert "rcr" not in client.get("/skill.md").text.lower()
    assert "/post?" not in client.get("/rcr/skill.md").text
