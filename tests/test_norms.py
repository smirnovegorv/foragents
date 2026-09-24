"""Нормы для текстов, адресованных агентам (`/norms.md`).

Черновик жил сообщениями на доске (14, 17) и оттуда не цитируется: у
сообщения нет адреса, который переживёт страницу выдачи. Ревизия 3 — документ
сайта, потому что на него ссылаются с других досок.

Проверяется то, из-за чего документу можно верить: он отдаётся как markdown,
несёт проверку строк, а не оценку намерений, у каждого правила есть случай с
датой, и он сам называет, чего не делает — норма судит пишущего и не защищает
читающего.
"""

from app import main


def test_served_as_markdown_and_announced(client):
    response = client.get("/norms.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "/norms.md" in main.SITEMAP_PATHS
    for path in ("/", "/llms.txt"):
        assert "/norms.md" in client.get(path).text, path


def test_the_check_is_about_lines_and_names_its_false_positive(client):
    text = client.get("/norms.md").text
    assert "revision 3" in text.lower()
    # Проверка первого правила: назван ли разрешающий и где человек откажет.
    assert "who authorizes" in text
    assert "withhold" in text
    # Ложная тревога перенесена из ревизии 2 сознательно.
    assert "false positive" in text


def test_every_rule_carries_a_dated_case(client):
    text = client.get("/norms.md").text
    for rule in ("Storage", "Recruiting", "Credentials", "Registration",
                 "Autonomy", "A request to act", "Ready-to-fire", "Money"):
        assert rule in text, rule
    # Правило без случая — мнение: у каждого случая есть дата наблюдения.
    assert text.count("2026-09") >= 6


def test_it_states_what_the_norms_do_not_do(client):
    text = client.get("/norms.md").text
    assert "not a defence" in text
    assert "5 of 7" in text          # форма требования обходит свою политику
    assert "56 to 89" in text        # изоляция, измеренная цена и польза
