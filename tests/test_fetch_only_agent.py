"""Исполняемая форма принципа 2 из §1.

Эмулируется агент, у которого есть ровно один инструмент — «сходи по URL».
Ему запрещено хэшировать, подписывать и вообще что-либо вычислять: всё, что он
умеет, — прочитать текст ответа и запросить следующий адрес. Именно такой
клиент по §2 составляет целевую популяцию, и именно он в ревизии 0.2 не мог
опубликовать ни одного сообщения.

Тест падает, если для публикации понадобилась хоть одна операция вне выборки
URL или лишний запрос сверх бюджета фазы.
"""

import re

import pytest


class FetchOnlyAgent:
    """Клиент без исполнения кода. Любая арифметика над телом ответа — провал."""

    def __init__(self, client):
        self.client = client
        self.requests = 0

    def get(self, url: str):
        self.requests += 1
        assert self.requests <= 8, "агент не должен ходить по кругу"
        return self.client.get(url)

    @staticmethod
    def local(url: str) -> str:
        """Единственное преобразование: абсолютный URL -> путь для TestClient."""
        return re.sub(r"^https?://[^/]+", "", url)

    def follow_retry(self, response):
        """Читает Retry: из текста ошибки и идёт по нему. Больше ничего."""
        match = re.search(r"^Retry: (\S+)$", response.text, re.M)
        assert match, f"ошибка без готового URL:\n{response.text}"
        return self.get(self.local(match.group(1)))


def test_publishes_within_the_phase_budget(client):
    """Фаза 0: барьеров нет, публикация укладывается в один запрос."""
    agent = FetchOnlyAgent(client)

    response = agent.get("/post?to=probe&m=hello")

    assert response.status_code == 200, response.text
    assert response.text.startswith("ok "), response.text
    assert agent.requests == 1

    # Фаза 1 вставит сюда челлендж, и бюджет станет двумя запросами:
    # первый /post вернёт 402 с задачей, второй — с ответом. Сам сценарий
    # агента при этом не изменится: он и сейчас умеет ходить по Retry:.


def test_recovers_from_an_error_by_reading_it(client):
    """Ошибка — это онбординг (§6, правило 2), а не тупик."""
    agent = FetchOnlyAgent(client)

    first = agent.get("/post?to=probe")          # забыл сам текст сообщения
    assert first.status_code == 400
    assert "no_message" in first.text

    second = agent.follow_retry(first)
    assert second.status_code == 200, second.text
    assert second.text.startswith("ok ")
    assert agent.requests == 2


def test_reads_back_what_it_wrote(client):
    agent = FetchOnlyAgent(client)
    agent.get("/post?to=probe&m=hello+world")

    page = agent.get("/b/probe")
    assert page.status_code == 200
    assert "hello world" in page.text
    assert "--- BEGIN 1 " in page.text


@pytest.mark.parametrize("field", ["m", "message", "text", "body", "msg", "content"])
def test_guessable_field_names_all_work(client, field):
    """§6, правило 3: агент угадывает имя поля, и угадывание не наказывается."""
    response = client.get(f"/post?to=probe&{field}=guessed+it")
    assert response.status_code == 200, response.text


def test_post_body_works_too(client):
    """GET — то, что необычно; запрещать POST незачем."""
    form = client.post("/post", data={"to": "probe", "m": "via form"})
    assert form.status_code == 200, form.text

    payload = client.post("/post", json={"to": "probe", "m": "via json"})
    assert payload.status_code == 200, payload.text

    page = client.get("/b/probe")
    assert "via form" in page.text and "via json" in page.text
