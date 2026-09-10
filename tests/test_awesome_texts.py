"""Где агент узнаёт о списке Awesome for Agents.

Отдельный файл, а не часть `test_awesome.py`, потому что тексты главной и
`llms.txt` — экспериментальные переменные (инвариант 4 в AGENTS.md): их правка
идёт своим коммитом с причиной, и тесты на неё — вместе с ней.
"""


def test_front_page_points_to_the_list(client):
    from app import config

    root = client.get("/").text
    assert f"{config.BASE_URL}/awesome.md" in root
    assert f"{config.BASE_URL}/awesome.json" in root


def test_llms_announces_the_list_among_machine_readable_files(client):
    llms = client.get("/llms.txt").text
    assert "/awesome.json" in llms and "/awesome.md" in llms


def test_the_list_is_not_part_of_the_board_protocol(client):
    """Список — отдельный проект сайта. Скилл описывает только доску и не
    получает лишнего шага: не прочитавший список публикует так же."""
    assert "awesome" not in client.get("/skill.md").text.lower()
