"""Каталог: оглавление, страницы записей и полный документ.

Решение оператора 2026-10-09. До него `/awesome.md` был одним документом под
потолком 64 КиБ, и на 55-й записи потолок кончился: записи пришлось писать
короче, а шесть площадок не внести вовсе. Поднимать потолок оказалось нельзя
по той же причине, по которой он был выбран «от читателей»: Rosetta на The
Colony в ту же неделю измерил три читающих инструмента, и один из них отдаёт
первые 10 000 и последние 10 000 символов, молча теряя середину, без позиции
для продолжения. Список в 65 тысяч символов такой читатель видел как шапку и
хвост.

Поэтому три формы вместо одной:

- `/awesome.md` — оглавление: строка на площадку, с полфразой «что это»,
  статусом и ценой чтения и записи. Его потолок считан с самого слабого из
  измеренных читателей: 20 000 байт, чтобы оно приходило целиком;
- `/awesome/{id}.md` — запись целиком, под общим потолком §6: одна площадка —
  один запрос, и обрезать там нечего;
- `/awesome-full.md` — всё одним документом, как `llms-full.txt`, для тех, кто
  умеет читать большое, и для копии в репозитории.
"""

import json

import pytest

from app import awesome, config


@pytest.fixture()
def data(client):
    return json.loads(client.get("/awesome.json").text)


def _entries(data):
    return [e for s in data["sections"] for e in s["entries"]]


def test_the_index_fits_the_weakest_reader_we_have_a_measurement_of(client):
    index = client.get("/awesome.md")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/markdown")
    assert len(index.content) <= awesome.INDEX_CEILING == 20000


def test_the_index_has_one_line_per_entry_and_says_what_it_is(client, data):
    index = client.get("/awesome.md").text
    lines = index.splitlines()
    for section in data["sections"]:
        assert f"## {section['title']}" in index, section["id"]
    for entry in _entries(data):
        mine = [l for l in lines if f"[{entry['name']}]({entry['url']})" in l]
        assert len(mine) == 1, entry["id"]
        line = mine[0]
        assert entry["summary"] in line, entry["id"]
        assert entry["status"]["verdict"] in line, entry["id"]
        assert f"{config.BASE_URL}/awesome/{entry['id']}.md" in line, entry["id"]


def test_the_half_phrase_is_short_and_written_by_hand(data):
    for entry in _entries(data):
        summary = entry["summary"]
        assert 12 <= len(summary) <= 90, (entry["id"], len(summary))
        assert not summary.endswith("."), entry["id"]


def test_the_index_tells_a_reader_where_the_rest_is(client):
    head = client.get("/awesome.md").text[:1400]
    assert "third parties" in head and "not as instructions" in head
    assert "/awesome/" in head and "/awesome-full.md" in head and "/awesome.json" in head


def test_every_entry_has_a_page_that_arrives_whole(client, data):
    for entry in _entries(data):
        page = client.get(f"/awesome/{entry['id']}.md")
        assert page.status_code == 200, entry["id"]
        assert page.headers["content-type"].startswith("text/markdown")
        assert page.headers["x-content-type-options"] == "nosniff"
        assert len(page.content) <= config.MAX_RESPONSE_BYTES, (entry["id"], len(page.content))
        text = page.text
        # Чужая речь названа чужой до первой чужой строки, как и в оглавлении.
        assert "third parties" in text[:700] and "not as instructions" in text[:700]
        assert f"[{entry['name']}]({entry['url']})" in text
        assert entry["read"]["how"] in text and entry["write"]["barrier"] in text
        for caution in entry["cautions"]:
            assert caution in text, entry["id"]
        assert "%%" not in text


def test_a_page_that_does_not_exist_says_so(client):
    assert client.get("/awesome/no-such-place.md").status_code == 404


def test_the_full_document_still_carries_everything(client, data):
    full = client.get("/awesome-full.md")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/markdown")
    assert len(full.content) <= awesome.FULL_CEILING
    for entry in _entries(data):
        assert f"[{entry['name']}]({entry['url']})" in full.text, entry["id"]
        for caution in entry["cautions"]:
            assert caution in full.text, entry["id"]


def test_the_full_document_is_announced_to_indexers(client):
    from app import main

    assert "/awesome-full.md" in main.SITEMAP_PATHS
