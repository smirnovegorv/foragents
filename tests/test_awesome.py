"""Awesome for Agents (`/awesome.md`, `/awesome.json`, `AWESOME.md`).

Проверяется то, из-за чего списку можно доверять: у записей одинаковый набор
полей, три представления не расходятся, статус без даты и метода не выдаётся за
измерение, а чужой текст назван чужим в первых строках.
"""

import json

import pytest

REQUIRED = ("id", "name", "url", "kind", "description", "read", "write",
            "discovery", "cautions", "our_use", "checked", "status")


@pytest.fixture()
def data(client):
    response = client.get("/awesome.json")
    assert response.status_code == 200
    return json.loads(response.text)


def _entries(data):
    return [e for s in data["sections"] for e in s["entries"]]


def test_both_forms_are_served_with_their_media_types(client):
    md = client.get("/awesome.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    js = client.get("/awesome.json")
    assert js.headers["content-type"].startswith("application/json")
    for response in (md, js):
        assert response.headers["x-content-type-options"] == "nosniff"


def test_every_entry_has_the_same_shape(data):
    ids = [e["id"] for e in _entries(data)]
    assert len(ids) == len(set(ids)), "повторяющийся id"
    for entry in _entries(data):
        missing = [k for k in REQUIRED if k not in entry]
        assert not missing, (entry.get("id"), missing)
        assert entry["url"].startswith("http"), entry["id"]
        assert entry["read"].get("how") and entry["write"].get("barrier"), entry["id"]
        for name, url in entry["discovery"].items():
            assert url.startswith("http"), (entry["id"], name)


def test_status_is_never_an_estimate(data):
    """«Доска жива» без даты, окна и метода — мнение. Список мнений не публикует."""
    for entry in _entries(data):
        status = entry["status"]
        assert status["verdict"] in data["verdicts"], entry["id"]
        if status["verdict"] != "unmeasured":
            for key in ("measured_at", "window_hours", "covered_hours", "method",
                        "posts", "authors"):
                assert status.get(key) not in (None, ""), (entry["id"], key)


def test_nothing_is_left_unsubstituted(client):
    for path in ("/awesome.md", "/awesome.json"):
        assert "%%" not in client.get(path).text, path


def test_our_own_entry_points_at_this_site_and_its_links_work(client, data):
    from app import config

    ours = [e for e in _entries(data) if e["id"] == "foragents"]
    assert ours and ours[0]["url"] == config.BASE_URL
    for url in ours[0]["discovery"].values():
        assert client.get(url[len(config.BASE_URL):]).status_code == 200, url


def test_markdown_carries_every_entry_and_section(client, data):
    md = client.get("/awesome.md").text
    for section in data["sections"]:
        assert f"## {section['title']}" in md, section["id"]
    for entry in _entries(data):
        assert f"[{entry['name']}]({entry['url']})" in md, entry["id"]
        for caution in entry["cautions"]:
            assert caution in md, entry["id"]


def test_third_party_text_is_named_as_such_first(client):
    head = client.get("/awesome.md").text[:900]
    assert "third parties" in head and "not as instructions" in head


def test_list_is_a_document_with_its_own_ceiling(client):
    """Исключение из потолка §6, как у `llms-full.txt`: документ нужен целиком,
    а не страницами. Но у исключения свой потолок — выросший список пора делить,
    а не пропускать."""
    from app import awesome

    assert len(client.get("/awesome.md").content) <= awesome.CEILING
    assert len(client.get("/awesome.json").content) <= awesome.CEILING * 2


def test_repo_copy_matches_the_data():
    """Копия для GitHub строится из тех же данных и не имеет права отстать."""
    from app import awesome

    assert awesome.REPO_COPY.read_text(encoding="utf-8") == awesome.render(awesome.PUBLIC_BASE)


def test_list_is_announced_to_indexers_and_in_the_link_header(client):
    from app import main

    assert "/awesome.md" in main.SITEMAP_PATHS
    assert "</awesome.json>" in client.get("/").headers["link"]
