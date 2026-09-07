import importlib
import os
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Свежая база на каждый тест. BASE_URL фиксирован: Retry-URL сравниваются."""
    os.environ["DB_PATH"] = str(tmp_path / "board.db")
    os.environ["BASE_URL"] = "https://api.foragents.chat"
    os.environ["CODE_REV"] = "test"
    os.environ.pop("READONLY", None)

    from app import config, db, texts
    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(texts)
    db.reset_for_tests()

    from app import ids, render, store, pipeline, params, main
    for module in (ids, render, store, pipeline, params, main):
        importlib.reload(module)

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
    db.reset_for_tests()
