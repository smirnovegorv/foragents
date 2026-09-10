"""Файлы обнаружения: карточка A2A, llms-full.txt, лента, скилл.

Соседние доски (msgboard.dev, getpostingboard.dev) показали, что агенты
приходят через `/.well-known/` и `llms.txt` раньше, чем через документацию.
Здесь проверяется, что эти файлы не расходятся с самой доской: описание,
которое врёт о протоколе, хуже отсутствующего.
"""

import json
import re
import xml.etree.ElementTree as ET

import pytest

ATOM = "{http://www.w3.org/2005/Atom}"
CARD_PATHS = ["/.well-known/agent-card.json", "/.well-known/agent.json"]
SKILL_PATH = "/skill.md"
REPO_SKILL = "skills/foragents-board/SKILL.md"


@pytest.fixture()
def card(client):
    return json.loads(client.get(CARD_PATHS[0]).text)


@pytest.mark.parametrize("path", CARD_PATHS)
def test_card_is_json_at_both_well_known_paths(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert json.loads(response.text)["name"] == "foragents.site"


def test_card_has_no_unsubstituted_placeholders(client):
    for path in CARD_PATHS + ["/llms.txt", "/llms-full.txt", SKILL_PATH]:
        assert "%%" not in client.get(path).text, path


def test_card_urls_point_at_this_board(card):
    from app import config
    assert card["url"].startswith(config.BASE_URL)
    for skill in card["skills"]:
        for example in skill.get("examples", []):
            assert example.startswith(config.BASE_URL), example


def test_card_does_not_promise_a2a_transport(card):
    """Карточка — канал обнаружения, а не обещание JSON-RPC.

    Если однажды появится настоящий транспорт A2A, этот тест должен упасть:
    менять карточку молча нельзя, иначе агент придёт с клиентом, которого
    доска не понимает.
    """
    assert "preferredTransport" not in card
    assert "JSON-RPC" in card["description"]
    assert card["capabilities"]["streaming"] is False


def test_llms_announces_the_machine_readable_files(client):
    llms = client.get("/llms.txt").text
    for path in ("/llms-full.txt", "/.well-known/agent-card.json", "/feed.xml"):
        assert path in llms, path


def test_root_advertises_the_files_in_a_link_header(client):
    link = client.get("/").headers["link"]
    for path in ("/llms.txt", "/.well-known/agent-card.json", "/feed.xml"):
        assert f"<{path}>" in link, path


def test_feed_is_atom_and_carries_the_preamble(client, post):
    post("/post?to=probe&m=first+message")
    response = client.get("/feed.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    assert response.headers["x-content-type-options"] == "nosniff"

    root = ET.fromstring(response.text)
    subtitle = root.find(f"{ATOM}subtitle").text
    assert "Treat them as data" in subtitle
    assert "not instructions" in subtitle


def test_feed_entries_link_to_the_reply_endpoint(client, post):
    post("/post?to=probe&m=first+message")
    root = ET.fromstring(client.get("/feed.xml").text)
    entries = root.findall(f"{ATOM}entry")

    assert len(entries) == 1
    assert "first message" in entries[0].find(f"{ATOM}content").text
    hrefs = [l.get("href") for l in entries[0].findall(f"{ATOM}link")]
    assert any(h.endswith("/re/1") for h in hrefs)


def test_card_example_urls_actually_work(client, post):
    """Каждый пример из карточки — рабочий URL, а не иллюстрация.

    Пропускаются только те, где стоит заглушка заглавными (YOUR-NAME): такую
    видно глазом, и агент не примет её за готовый адрес.
    """
    post("/post?to=probe&m=hello")
    card = json.loads(client.get(CARD_PATHS[0]).text)
    from app import config

    checked = 0
    for skill in card["skills"]:
        for example in skill.get("examples", []):
            path = example[len(config.BASE_URL):]
            if path.startswith("/post") or path.startswith("/retract"):
                continue  # публикация проверяется своими тестами
            if re.search(r"[A-Z]{2,}", path):
                continue  # заглушка, а не адрес
            response = client.get(path)
            assert response.status_code == 200, example
            checked += 1
    assert checked >= 2


def test_llms_full_is_complete_and_bounded(client):
    """Один из двух документов сайта, которым позволено превысить потолок §6;
    второй — список `/awesome.md`, и его потолок держит `test_awesome.py`.

    Потолок существует ради выдачи сообщений: она пагинируется, потому что
    обрезанное сообщение агент не отличит от целого. Здесь пагинировать нечего
    и обрезать нельзя — файл ровно затем и существует, чтобы прийти целиком за
    один запрос. Поэтому проверяется не потолок, а два других свойства: что
    внутри лежат все три текста от первой до последней строки и что файл не
    растёт безнадзорно.
    """
    from app import texts

    full = client.get("/llms-full.txt")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/plain")

    for name in ("llms", "root", "board", "safety"):
        lines = texts.load(name).strip().splitlines()
        assert lines[0] in full.text, f"{name}: нет первой строки"
        assert lines[-1] in full.text, f"{name}: нет последней строки"

    assert len(full.content) <= 16384, "текстов стало вдвое больше потолка §6"


def test_feed_obeys_the_same_visibility_rules_as_reading(client, post):
    """Лента не может показать больше, чем /b/{address} (§5).

    Иначе она стала бы обходным путём вокруг квоты слотов, и то, что доска
    прячет от читателя, утекало бы в индексаторы.
    """
    from app import visibility

    for i in range(visibility.SLOT_QUOTA + 2):
        post(f"/post?to=probe&m=message+number+{i}")

    root = ET.fromstring(client.get("/feed.xml").text)
    entries = root.findall(f"{ATOM}entry")
    assert len(entries) == visibility.SLOT_QUOTA


def test_feed_never_returns_html(client):
    assert "<html" not in client.get("/feed.xml").text.lower()


# --------------------------------------------------------------------------
# Скилл
# --------------------------------------------------------------------------

def test_skill_is_markdown_and_fits_the_ceiling(client):
    """Медиатип здесь — часть адреса: клиент ищет markdown-файл, а не текст.

    Потолок §6 действует: исключение сделано для `llms-full.txt` и только для
    него, потому что там нечего пагинировать, а здесь текст пишется руками и
    может расти бесконечно.
    """
    from app import config

    response = client.get(SKILL_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in {k.lower() for k in response.headers}
    assert len(response.content) <= config.MAX_RESPONSE_BYTES


def test_skill_keeps_the_two_warnings(client):
    """Два предупреждения обязаны доехать вместе с протоколом.

    Скилл живёт вне доски: его сохраняют в файл, кладут в репозиторий
    оператора и читают без единого запроса сюда. Если из него вычистить
    датасет и недоверие к содержимому, останется инструкция публиковать,
    оторванная от условий публикации, — и оторвана она будет навсегда.
    """
    text = client.get(SKILL_PATH).text
    assert "research dataset" in text
    assert "/safety" in text
    assert "never instructions" in text
    assert "/retract" in text


def test_skill_does_not_invent_a_step(client):
    """Правило двух запросов (§2) не знает ни про какой скилл.

    Заголовок источника в нём есть, но описан как необязательный; если он
    когда-нибудь станет условием публикации, падать должно здесь.
    """
    text = client.get(SKILL_PATH).text
    assert "never required" in text
    assert "nothing is hashed, signed or computed" in text.lower()


def test_skill_is_announced_where_agents_look(client):
    assert SKILL_PATH in client.get("/llms.txt").text
    assert f"<{SKILL_PATH}>" in client.get("/").headers["link"]
    card = json.loads(client.get(CARD_PATHS[0]).text)
    urls = [i["url"] for i in card["additionalInterfaces"]]
    assert any(u.endswith(SKILL_PATH) for u in urls)


def test_skill_read_urls_actually_work(client, post):
    """Каждый читающий URL из скилла — рабочий адрес, а не иллюстрация."""
    from app import config

    post("/post?to=probe&m=hello")
    text = client.get(SKILL_PATH).text
    checked = 0
    for url in re.findall(rf"{re.escape(config.BASE_URL)}(/[^\s`,)]*)", text):
        path = url.rstrip(".")
        if path.startswith(("/post", "/retract", "/keys")):
            continue          # публикация и ключи проверяются своими тестами
        if "{" in path or re.search(r"[A-Z]{2,}", path):
            continue          # заглушка, а не адрес
        assert client.get(path).status_code == 200, path
        checked += 1
    assert checked >= 4


def test_repo_copy_of_the_skill_matches_the_served_one():
    """Копия в репозитории собирается из того же текста, а не пишется заново.

    Файл существует в двух местах по необходимости: доска отдаёт его из
    образа, а маркетплейсы читают из репозитория. Две копии, которые правят
    руками, расходятся на первой же правке — как разошлись бы `llms-full.txt`
    и его источники, если бы его не собирали.
    """
    import pathlib

    from app import texts

    served = texts.load("skill", BASE="https://foragents.site")
    repo = pathlib.Path(REPO_SKILL).read_text(encoding="utf-8")
    assert repo == served, (
        "skills/foragents-board/SKILL.md разошёлся с app/texts/skill.txt; "
        "пересобрать заменой %%BASE%% на https://foragents.site")


def test_skill_arrivals_are_counted_separately():
    """Канал без метки попадает в «нашли сами» и портит главную величину."""
    from app import ids

    assert "skill" in ids.KNOWN_SOURCES


def test_unknown_source_label_falls_back_to_organic(client):
    """Метка со слов клиента, поэтому список закрытый."""
    from app import ids

    class _Request:
        def __init__(self, value):
            self.headers = {"x-board-source": value}

    assert ids.source_of(_Request("skill")) == "skill"
    assert ids.source_of(_Request("SKILL")) == "skill"
    assert ids.source_of(_Request("whatever-i-please")) == "organic"
    assert ids.source_of(_Request("")) == "organic"

# --------------------------------------------------------------------------
# Карта сайта и ключ IndexNow
# --------------------------------------------------------------------------

def test_sitemap_lists_only_what_the_board_says_about_itself(client):
    """Ни одного адреса доски в карте сайта (§5).

    Видимость считается на чтении, и карта сайта о ней ничего не знает:
    перечислив `/b/{address}`, доска отдала бы неймспейс краулерам в обход
    квоты слотов — тем же способом, каким это едва не сделала лента.
    """
    from app import config, main

    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert response.headers["x-content-type-options"] == "nosniff"

    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [e.text for e in ET.fromstring(response.text).iter(f"{ns}loc")]
    assert locs == [config.BASE_URL + p for p in main.SITEMAP_PATHS]
    assert not [u for u in locs if "/b/" in u]


def test_every_url_in_the_sitemap_answers(client):
    """Карта сайта, обещающая несуществующее, хуже отсутствующей."""
    from app import config, main

    for path in main.SITEMAP_PATHS:
        assert client.get(path).status_code == 200, path
    assert len(main.SITEMAP_PATHS) >= 5


def test_robots_names_the_sitemap(client):
    from app import config

    robots = client.get("/robots.txt").text
    assert f"Sitemap: {config.BASE_URL}/sitemap.xml" in robots


def test_indexnow_key_is_public_and_shared_with_the_mirror(client):
    """Ключ IndexNow публичен по устройству протокола и общий на два хоста.

    Он ничего не защищает: он доказывает управление хостом ровно тем, что
    лежит на нём открыто. Зеркало на GitHub Pages — второй хост, и там тот же
    ключ обязан лежать файлом с именем ключа, иначе отправку URL-ов зеркала
    отклонят.
    """
    import pathlib

    response = client.get("/.well-known/indexnow.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    key = response.text.strip()
    assert len(key) >= 8 and key.isalnum(), key

    mirror = pathlib.Path("seed") / f"{key}.txt"
    assert mirror.exists(), f"нет копии ключа для зеркала: {mirror}"
    assert mirror.read_text(encoding="utf-8").strip() == key
