"""Норма приёмки фазы 1, половина «плохое не прошло».

Проверяется не то, что детектор существует, а то, что исходный вид не доходит
до базы. Поэтому почти каждая проверка смотрит в хранилище, а не в ответ.
"""

import json
from urllib.parse import quote

import pytest

from corpus import SAMPLES, FLOOD_SIZE


def _url(sample: str, addr: str = "probe") -> str:
    """Образцы корпуса кодируются целиком: `+` в base64 иначе станет пробелом
    и блоб распадётся на куски раньше, чем его увидит детектор."""
    return f"/post?to={addr}&m={quote(sample, safe='')}"


def _stored(client, addr="probe"):
    return client.get(f"/b/{addr}?format=json&full=1").json()["messages"]


def _raw_rows(field="body"):
    from app import db
    return [r[field] for r in db.connect().execute(
        f"SELECT {field} FROM messages ORDER BY id").fetchall()]


# --------------------------------------------------------------------------
# Порядок конвейера
# --------------------------------------------------------------------------

def test_redaction_happens_before_the_first_write(client, post, monkeypatch):
    """§8: редакция обязана произойти до записи. Тест следит за фактическим
    порядком вызовов, а не за их наличием: перестановка шагов ломает его."""
    from app import pipeline, redact

    order = []
    real_redact = redact.redact
    real_write = pipeline._step9_write

    monkeypatch.setattr(pipeline.redact, "redact",
                        lambda t: (order.append("redact"), real_redact(t))[1])
    monkeypatch.setattr(pipeline, "_step9_write",
                        lambda *a, **k: (order.append("write"), real_write(*a, **k))[1])

    post("/post?to=probe&m=hello+sk-ant-api03-QW9xLm4TbZ7pRs2vKd8HcE1nYt6U")
    assert order.index("redact") < order.index("write"), order


# --------------------------------------------------------------------------
# Что не должно доходить до базы в исходном виде
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sample", SAMPLES["secret"])
def test_api_keys_never_reach_storage(client, post, sample):
    post(_url(sample))
    stored = _raw_rows()[0]
    for token in ("sk-ant-api03-", "ghp_", "AKIAJ7QW9XLM4TBZ7PRS"):
        assert token not in stored, stored
    assert "[SECRET]" in stored


@pytest.mark.parametrize("sample", SAMPLES["private_key"])
def test_private_keys_never_reach_storage(client, post, sample):
    post("/post?to=probe&m=" + sample.replace("\n", "%0A"))
    stored = _raw_rows()[0]
    assert "PRIVATE KEY" not in stored, stored
    assert "[SECRET]" in stored


@pytest.mark.parametrize("sample", SAMPLES["personal"])
def test_personal_data_is_replaced_by_placeholders(client, post, sample):
    post(_url(sample))
    stored = _raw_rows()[0]
    assert "@example.com" not in stored
    assert "4111111111111111" not in stored
    assert "192.168.15.240" not in stored
    assert any(p in stored for p in ("[EMAIL]", "[PHONE]", "[FINANCIAL]",
                                     "[IP]", "[WALLET]"))


def test_only_the_count_of_redactions_survives(client, post):
    """§9: в базе остаётся факт, а не значение. Красть должно быть нечего."""
    post("/post?to=probe&m=mail+alice@example.com+and+bob@example.com")
    counts = json.loads(_raw_rows("redactions")[0])
    assert counts == {"EMAIL": 2}


@pytest.mark.parametrize("sample", SAMPLES["stego_zero_width"])
def test_zero_width_is_removed_and_flagged(client, post, sample):
    post(_url(sample))
    stored = _raw_rows()[0]
    assert not any(ch in stored for ch in "​‌‍⁠﻿")
    assert "zero_width" in json.loads(_raw_rows("flags")[0])


@pytest.mark.parametrize("sample", SAMPLES["bidi"])
def test_bidi_controls_are_removed_and_flagged(client, post, sample):
    post(_url(sample))
    stored = _raw_rows()[0]
    assert not any(ch in stored for ch in "‪‫‬‭‮"
                                          "⁦⁧⁨⁩‎‏")
    assert "bidi" in json.loads(_raw_rows("flags")[0])


@pytest.mark.parametrize("sample", SAMPLES["blob"])
def test_opaque_blobs_are_refused_with_words(client, sample):
    response = client.get(_url(sample[:1500]))
    assert response.status_code == 400, response.text
    assert "opaque_blob" in response.text
    assert "Retry: " in response.text
    assert _raw_rows() == []


@pytest.mark.parametrize("sample", SAMPLES["delimiter_spoof"])
def test_block_delimiters_cannot_be_forged(client, post, sample):
    """Подделка границы — инъекция против парсера читателя, а не против модели:
    он увидит END раньше, чем текст закончился, и припишет остаток другому."""
    post(_url(sample))
    page = client.get("/b/probe").text

    # Мера — не удаление строки, а сдвиг: разделитель перестаёт стоять в начале
    # строки, и парсер читателя, привязанный к началу строки, его не видит.
    # Текст при этом цел: помечаем, но не удаляем.
    boundaries = [line for line in page.split("\n")
                  if line.startswith("--- BEGIN ") or line.startswith("--- END ")]
    assert len(boundaries) == 2, boundaries
    assert "delimiter_spoof" in json.loads(_raw_rows("flags")[0])


@pytest.mark.parametrize("sample", SAMPLES["injection"])
def test_injections_are_flagged_but_kept(client, post, sample):
    """Помечаем, но не удаляем: снятое не изучишь, а флаг даёт читателю
    машиночитаемое основание отнестись иначе."""
    response = post(_url(sample))
    assert response.status_code == 200
    assert "injection" in response.text
    assert "injection" in json.loads(_raw_rows("flags")[0])


@pytest.mark.parametrize("sample", SAMPLES["recruitment"])
def test_recruitment_is_flagged(client, post, sample):
    post(_url(sample))
    marks = json.loads(_raw_rows("flags")[0])
    assert "recruitment" in marks or "imperative_to_reader" in marks


def test_links_are_defanged_and_domains_kept_apart(client, post):
    post("/post?to=probe&m=see+http://evil.example.com/x+for+details")
    stored = _raw_rows()[0]
    assert "http://evil.example.com" not in stored
    assert "hxxp://evil[.]example[.]com/x" in stored
    assert json.loads(_raw_rows("domains")[0]) == ["evil.example.com"]


# --------------------------------------------------------------------------
# Видимость: флуд записывается, но не занимает выдачу (§5)
# --------------------------------------------------------------------------

def test_flood_is_stored_but_cannot_fill_the_view(client, post):
    """Две защиты работают на разных уровнях и обе видны в этом тесте:
    лимит тира обрывает поток на 120 сообщениях в час, а квота слотов не даёт
    занять выдачу даже тем, что прошло."""
    from app import tiers, visibility

    post("/post?to=probe&m=first+message+from+this+identity")
    for i in range(FLOOD_SIZE):
        client.get(f"/post?to=probe&m=buy+cheap+things+number+{i}")

    written = len(_raw_rows())
    assert written <= tiers.hourly_limit(2), written   # лимит оборвал поток
    assert written > visibility.SLOT_QUOTA             # но записано было много

    page = client.get("/b/probe")
    assert page.text.count("--- BEGIN ") <= visibility.SLOT_QUOTA
    assert "not shown" in page.text                  # и это объяснено словами
    assert "full=1" in page.text                     # с указанием, как посмотреть

    everything = client.get("/b/probe?full=1&limit=100")
    assert everything.text.count("--- BEGIN ") > visibility.SLOT_QUOTA


def test_identical_messages_collapse(client, post):
    post("/post?to=probe&m=heartbeat")
    for _ in range(5):
        client.get("/post?to=probe&m=heartbeat")
    page = client.get("/b/probe")
    assert "collapsed" in page.text
    assert page.text.count("--- BEGIN ") == 1


def test_anonymous_tier_is_out_of_the_default_view(client, post):
    """§5: безличный слой существует в базе и в /stats, но не в выдаче."""
    from app import db
    post("/post?to=probe&m=visible+message")
    db.connect().execute("UPDATE messages SET tier = 1 WHERE id = 1")

    assert "visible message" not in client.get("/b/probe").text
    assert "visible message" in client.get("/b/probe?min_tier=0").text
    assert client.get("/stats?format=json").json()["messages"] == 1
