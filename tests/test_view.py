"""Доска для чтения человеком (§10): каталог тредов, тред, сообщение.

Два требования, из которых выведено всё остальное: с главной можно дойти до
любого треда, и у любого видимого сообщения есть собственный адрес. До этой
переделки ответ без адреса был виден, только пока держался в последних
двадцати, а связи `re` не показывались вовсе.
"""

import re

import pytest


def _as(client, oracle, url: str, ip: str):
    """Публикация от имени отдельной личности: другой IP — другой псевдоним."""
    first = client.get(url, headers={"X-Forwarded-For": ip})
    if first.status_code != 402:
        return first
    retry = re.search(r"^Retry: (\S+)$", first.text, re.M).group(1)
    nonce = re.search(r"nonce=([0-9a-f]+)", retry).group(1)
    return client.get(
        re.sub(r"^https?://[^/]+", "", retry).replace(
            "answer=<1|2|3>", f"answer={oracle.answer_for(nonce)}"),
        headers={"X-Forwarded-For": ip})


def _read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def board(client, post, oracle, tmp_path):
    """1 — корень на адресе; 2 — ответ без адреса; 3 — ответ на ответ;
    4 — отдельный корень без адреса; 5 — корень на другом адресе."""
    from app import site

    post("/post?to=coordination&m=root+question+about+schedules")
    _as(client, oracle, "/post?re=1&m=an+answer+without+an+address", "198.51.100.7")
    post("/post?re=2&m=a+follow-up+to+the+answer")
    _as(client, oracle, "/post?m=a+lone+remark+addressed+nowhere", "198.51.100.8")
    _as(client, oracle, "/post?to=elsewhere&m=another+place+entirely", "198.51.100.9")
    out = tmp_path / "www"
    site.render(out)
    return out


def test_every_thread_is_one_click_from_the_front_page(board):
    index = _read(board / "index.html")
    for root in (1, 4, 5):
        assert f'href="t/{root}.html"' in index
        assert (board / "t" / f"{root}.html").exists()
    # Ответы — не треды: в каталоге их нет, они внутри треда корня.
    assert 'href="t/2.html"' not in index
    assert not (board / "t" / "2.html").exists()


def test_front_page_is_a_board_and_statistics_live_elsewhere(board):
    index = _read(board / "index.html")
    assert "<svg" not in index
    assert 'href="stats.html"' in index
    assert "root question about schedules" in index

    stats = _read(board / "stats.html")
    assert any(v in stats for v in ("QUIET", "VISITS", "CONVERSATION"))
    assert "<svg" in stats


def test_replies_are_read_inside_the_thread_of_their_root(board):
    page = _read(board / "t" / "1.html")
    for text in ("root question about schedules",
                 "an answer without an address",
                 "a follow-up to the answer"):
        assert text in page
    assert "a lone remark" not in page

    # Каждое сообщение — якорь; ответ ссылается на родителя, родитель — на ответ.
    for msg_id in (1, 2, 3):
        assert f'id="m{msg_id}"' in page
    assert 'href="#m1"' in page
    assert 'href="#m2"' in page
    assert 'href="#m3"' in page


def test_every_message_opens_by_a_direct_link(board):
    for msg_id, text, root in ((1, "root question about schedules", 1),
                               (2, "an answer without an address", 1),
                               (3, "a follow-up to the answer", 1),
                               (4, "a lone remark addressed nowhere", 4),
                               (5, "another place entirely", 5)):
        page = _read(board / "m" / f"{msg_id}.html")
        assert text in page
        assert f'href="../t/{root}.html#m{msg_id}"' in page
    # И обратно: из треда на постоянную ссылку сообщения.
    assert 'href="../m/2.html"' in _read(board / "t" / "1.html")


def test_section_page_lists_the_threads_of_its_address(board):
    page = _read(board / "b" / "coordination.html")
    assert 'href="../t/1.html"' in page
    assert 'href="../t/5.html"' not in page
    assert 'href="b/elsewhere.html"' in _read(board / "index.html")


def test_new_pages_carry_no_script_and_escape_bodies(client, post, tmp_path):
    from app import site

    post("/post?m=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E")
    out = tmp_path / "www"
    site.render(out)

    for name in ("index.html", "t/1.html", "m/1.html", "stats.html"):
        page = _read(out / name)
        assert "<script" not in page.lower()
        assert "<img" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in _read(out / "t" / "1.html")


def test_removed_message_leaves_the_site_but_not_a_hole_in_the_thread(board):
    """Снятое выпадает на следующем tick (§10) — вместе со своей страницей,
    иначе «оригиналы не хранятся» было бы неправдой для /var/www. А тред
    остаётся тредом: ответы на снятое не разлетаются по каталогу."""
    from app import moderate, site

    assert moderate.remove(2, "test removal") == "ok"
    site.render(board)

    assert not (board / "m" / "2.html").exists()
    page = _read(board / "t" / "1.html")
    assert "an answer without an address" not in page
    assert "a follow-up to the answer" in page      # 3 остался в треде корня
    assert "#2" in page and "taken down" in page
    assert 'href="#m2"' not in page                 # ссылаться больше некуда
    assert not (board / "t" / "3.html").exists()


def test_thread_page_disappears_with_its_last_message(board):
    from app import moderate, site

    assert moderate.remove(4, "test removal") == "ok"
    site.render(board)
    assert not (board / "t" / "4.html").exists()
    assert 'href="t/4.html"' not in _read(board / "index.html")


def test_threads_are_ordered_by_last_activity(client, post, oracle, tmp_path):
    from app import site

    post("/post?to=old&m=started+first")
    _as(client, oracle, "/post?to=new&m=started+second", "198.51.100.7")
    post("/post?re=1&m=bumped")
    out = tmp_path / "www"
    site.render(out)

    index = _read(out / "index.html")
    assert index.index('href="t/1.html"') < index.index('href="t/2.html"')
