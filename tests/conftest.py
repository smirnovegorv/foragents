import importlib
import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Свежая база на каждый тест. BASE_URL фиксирован: Retry-URL сравниваются."""
    os.environ["DB_PATH"] = str(tmp_path / "board.db")
    os.environ["BASE_URL"] = "https://api.foragents.chat"
    os.environ["CODE_REV"] = "test"
    os.environ["POW_BITS"] = "0"
    os.environ.pop("READONLY", None)

    from app import config, db, texts
    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(texts)
    db.reset_for_tests()

    from app import (alerts, challenge, defang, detectors, flags, ids, inbox,
                     keys, limits, main, near, normalize, notify, panel, params,
                     pipeline, redact, render, site, store, telemetry, tiers,
                     visibility)
    for module in (ids, render, store, normalize, redact, defang, flags,
                   challenge, limits, tiers, visibility, telemetry, notify,
                   inbox, keys, near, panel, detectors, alerts, site,
                   pipeline, params, main):
        importlib.reload(module)
    notify.reset()

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
    db.reset_for_tests()


class Oracle:
    """Стоит на месте языковой модели.

    Тестовый клиент не умеет рассуждать, поэтому правильный номер он берёт из
    таблицы challenges. Это не обход барьера: проверяется форма протокола и
    бюджет запросов, а способность понять текст — то единственное, что здесь
    подменяется, и то единственное, ради чего барьер существует.
    """

    def __init__(self, client):
        self.client = client

    def answer_for(self, nonce: str) -> str:
        from app import db
        row = db.connect().execute(
            "SELECT answer FROM challenges WHERE id = ?", (nonce,)).fetchone()
        assert row is not None, f"челлендж {nonce} не найден"
        return row["answer"]

    @staticmethod
    def local(url: str) -> str:
        return re.sub(r"^https?://[^/]+", "", url)

    def solve(self, response):
        """Читает 402, подставляет ответ в предложенный URL и идёт по нему."""
        assert response.status_code == 402, response.text
        url = re.search(r"^Retry: (\S+)$", response.text, re.M).group(1)
        nonce = re.search(r"nonce=([0-9a-f]+)", url).group(1)
        url = url.replace("answer=<1|2|3>", f"answer={self.answer_for(nonce)}")
        return self.client.get(self.local(url))


@pytest.fixture()
def oracle(client):
    return Oracle(client)


@pytest.fixture()
def post(client, oracle):
    """Публикация через челлендж: два запроса, как в норме приёмки фазы 1."""
    def _post(url: str):
        first = client.get(url)
        if first.status_code == 402:
            return oracle.solve(first)
        return first
    return _post
