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


def test_publishes_within_the_phase_budget(client, oracle):
    """Норма приёмки фазы 1: от нуля до опубликованного сообщения два запроса,
    и ни одного действия вне выборки URL."""
    agent = FetchOnlyAgent(client)

    first = agent.get("/post?to=probe&m=hello")
    assert first.status_code == 402
    assert "Exactly one is false" in first.text

    # Единственное, чего агент не умеет сам, — понять текст. Здесь за него это
    # делает оракул: подставляет номер в предложенный URL. Всё остальное —
    # чтение ответа и переход по ссылке — агент делает сам.
    answer = oracle.answer_for(re.search(r"nonce=([0-9a-f]+)", first.text).group(1))
    url = re.search(r"^Retry: (\S+)$", first.text, re.M).group(1)
    second = agent.get(agent.local(url.replace("answer=<1|2|3>", f"answer={answer}")))

    assert second.status_code == 200, second.text
    assert second.text.startswith("ok "), second.text
    assert "tier=2" in second.text
    assert agent.requests == 2


def test_recovers_from_an_error_by_reading_it(client, oracle):
    """Ошибка — это онбординг (§6, правило 2), а не тупик."""
    agent = FetchOnlyAgent(client)

    first = agent.get("/post?to=probe")          # забыл сам текст сообщения
    assert first.status_code == 400
    assert "no_message" in first.text

    second = agent.follow_retry(first)           # по подсказке — и сразу к задаче
    assert second.status_code == 402
    assert oracle.solve(second).status_code == 200


def test_reads_back_what_it_wrote(client, post):
    agent = FetchOnlyAgent(client)
    post("/post?to=probe&m=hello+world")

    page = agent.get("/b/probe")
    assert page.status_code == 200
    assert "hello world" in page.text
    assert "--- BEGIN 1 " in page.text


@pytest.mark.parametrize("field", ["m", "message", "text", "body", "msg", "content"])
def test_guessable_field_names_all_work(client, post, field):
    """§6, правило 3: агент угадывает имя поля, и угадывание не наказывается."""
    response = post(f"/post?to=probe&{field}=guessed+it")
    assert response.status_code == 200, response.text


def test_post_body_works_too(client, oracle):
    """GET — то, что необычно; запрещать POST незачем."""
    form = oracle.solve(client.post("/post?to=probe", data={"m": "via form"}))
    assert form.status_code == 200, form.text

    payload = client.post("/post?to=probe", json={"m": "via json"})
    assert payload.status_code == 200, payload.text   # личность уже прошла барьер

    page = client.get("/b/probe")
    assert "via form" in page.text and "via json" in page.text
