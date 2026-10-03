"""Перепись каталога обязана уметь сказать «не измерил».

Случай 2026-10-03. Замер tantive ходит по `/api/updates` вперёд от первого
сообщения. Страница там отдаёт двадцать записей, доска выросла до ~1900, и
бюджет страниц кончился на id около 800 — то есть примерно на 09-24. Замер
вынес `dormant` и дату последней активности 09-24 по доске, на которой за
предыдущие сутки было больше тридцати новых тредов, прочитанных в тот же день
руками.

Ошибка не в числе страниц, а в том, что прибор не мог провалить свою проверку:
обрыв у свежего конца выглядит ровно как тишина. Поэтому: незавершённый обход,
не дотянувшийся до окна, — это `unmeasured`, и в методе сказано, чем он
кончился. `dormant` остаётся словом про доску, а не про наш обход.
"""

import datetime
import importlib.util
import pathlib

UTC = datetime.timezone.utc
_spec = importlib.util.spec_from_file_location(
    "awesome_census", pathlib.Path(__file__).resolve().parents[1] / "tools" / "awesome_census.py")
census = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census)

NOW = datetime.datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CUT = NOW - datetime.timedelta(hours=census.WINDOW_H)


def result(items, complete, method="test feed"):
    return {"items": items, "complete": complete, "method": method}


def test_a_truncated_walk_that_never_reached_the_window_is_not_dormant():
    # Обход оборвался на бюджете страниц, и самое свежее, что он увидел,
    # старше окна: про саму доску это не говорит ничего.
    old = datetime.datetime(2026, 9, 24, 21, 25, tzinfo=UTC)
    status = census.summarise(result([("a", old), ("b", old)], False), NOW, CUT)
    assert status["verdict"] == "unmeasured", status
    assert "not reach" in status["method"] or "не дотянул" in status["method"]


def test_a_complete_walk_with_nothing_in_the_window_is_dormant():
    old = datetime.datetime(2026, 9, 24, 21, 25, tzinfo=UTC)
    status = census.summarise(result([("a", old)], True), NOW, CUT)
    assert status["verdict"] == "dormant", status


def test_a_truncated_walk_that_did_reach_the_window_still_measures():
    # Обрыв у старого конца — обычное дело: окно просто покрыто не целиком.
    fresh = NOW - datetime.timedelta(hours=2)
    items = [(name, fresh) for name in ("a", "b", "c", "d")]
    status = census.summarise(result(items, False), NOW, CUT)
    assert status["verdict"] == "active", status
    assert status["covered_hours"] < census.WINDOW_H
