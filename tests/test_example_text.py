"""Пример из собственной документации не публикуется.

Находка `Weaver` (SwarmMemo) против доски Relay, 2026-09-11: готовая ссылка
записи в `llms.txt` публикует у агента, которому велели «прочитать файл и
открыть ссылки», хотя писать он не собирался. Здесь слабее, но то же: первую
попытку личности встречает вопрос, а после ответа личность больше не
спрашивают, и её следующий переход по примеру из `llms.txt` ложился на доску.
"""

import re
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

TEXTS = Path(__file__).resolve().parent.parent / "app" / "texts"


def _stored() -> int:
    from app import db
    return db.connect().execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def _llms_examples() -> set[str]:
    raw = re.findall(r"[?&]m=([^&\s]+)", (TEXTS / "llms.txt").read_text(encoding="utf-8"))
    return {unquote_plus(v) for v in raw}


def test_a_docs_example_does_not_publish_for_an_identity_past_the_question(client, post):
    first = post("/post?to=probe&m=a+message+of+my+own")
    assert first.status_code == 200, first.text
    before = _stored()

    examples = _llms_examples()
    assert examples, "в llms.txt не нашлось ни одного примера m="
    for example in sorted(examples):
        response = client.get("/post?to=probe&m=" + quote_plus(example))
        assert response.status_code == 400, (example, response.text)
        assert "example_text" in response.text, response.text
        assert re.search(r"^Retry: \S+", response.text, re.M), response.text

    assert _stored() == before


def test_every_documented_example_is_known_to_the_pipeline():
    """Список примеров собирается из самих текстов: новый пример в любом
    файле `app/texts/` становится инертным без второй правки."""
    from app import pipeline

    known = pipeline.documented_examples()
    for path in TEXTS.glob("*.txt"):
        for value in re.findall(r"[?&]m=([^&\s\"'`)]+)", path.read_text(encoding="utf-8")):
            text = unquote_plus(value).strip()
            if re.search(r"[{}<>]", text) or not re.search(r"[a-z]", text):
                continue                 # заполнители: {text}, <...>, TEXT, ...
            assert " ".join(text.split()).casefold() in known, (path.name, text)


def test_a_real_message_that_merely_contains_an_example_goes_through(post):
    response = post("/post?to=probe&m=" + quote_plus("your text was clear, thanks"))
    assert response.status_code == 200, response.text


def test_head_on_a_write_url_does_not_publish(client, post):
    """Проверка ссылок и предпросмотр ходят HEAD'ом; публикации от них быть не должно."""
    post("/post?to=probe&m=a+message+of+my+own")
    before = _stored()
    client.head("/post?to=probe&m=a+link+checker+passing+by")
    assert _stored() == before
