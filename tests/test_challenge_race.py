"""Одноразовость челленджа под одновременными попытками.

`challenge.verify()` обещает в докстроке одноразовую проверку. Проверка стоит
здесь, а не в `test_tiers.py`, потому что все тесты барьера последовательны и
это свойство им недоступно по построению: окно между чтением строки и её
гашением видно только двум потокам.

Указано внешним ревью 2026-09-10 (`seq 10681`, getpostingboard.dev, модель
GPT-5.6-sol). Само замечание — гипотеза; изменением кода оно становится через
этот тест, написанный самостоятельно, а не через присланную починку.
"""

import sqlite3
import threading

import pytest


def _barrier_before_update(monkeypatch, barrier):
    """Задерживает `UPDATE challenges` до того, как оба потока прочитают строку.

    Расписание задаётся здесь, а не в рабочем коде: воспроизводить гонку
    случайными повторами значит получить тест, который иногда зелёный.
    Соединение у каждого потока своё (`db.connect` держит его в
    thread-local), так что подменяется именно точка, где потоки расходятся.
    """
    from app import challenge, db

    real_connect = db.connect

    class Proxy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("UPDATE CHALLENGES"):
                barrier.wait(timeout=10)
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(challenge.db, "connect", lambda: Proxy(real_connect()))


def test_one_challenge_admits_exactly_one_attempt(client, monkeypatch):
    """Два одновременных повтора с одним nonce, телом и верным ответом: пройти
    обязан ровно один. Пока проходят оба, обещание одноразовости — неправда.
    """
    from app import challenge, db

    body = "concurrent retry of one nonce"
    cid, _ = challenge.issue(body)
    answer = db.connect().execute(
        "SELECT answer FROM challenges WHERE id = ?", (cid,)).fetchone()["answer"]

    barrier = threading.Barrier(2)
    _barrier_before_update(monkeypatch, barrier)

    results = []
    lock = threading.Lock()

    def attempt():
        try:
            ok = challenge.verify(cid, answer, body)
        except (sqlite3.Error, threading.BrokenBarrierError) as exc:  # pragma: no cover
            ok = exc
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(results) == 2, results
    assert all(isinstance(r, bool) for r in results), results
    assert sum(results) == 1, (
        f"одноразовость нарушена: {results}. Оба потока прочитали строку как "
        "неиспользованную до того, как первый её погасил.")


def test_a_wrong_answer_still_consumes_the_nonce(client):
    """Правило §7, которое починка гонки обязана сохранить: попытка гасится
    в любом случае, включая неверную, иначе один nonce перебирается угадыванием.
    """
    from app import challenge, db

    body = "wrong answer burns the nonce"
    cid, _ = challenge.issue(body)
    right = db.connect().execute(
        "SELECT answer FROM challenges WHERE id = ?", (cid,)).fetchone()["answer"]
    wrong = str((int(right) % 3) + 1)

    assert challenge.verify(cid, wrong, body) is False
    assert challenge.verify(cid, right, body) is False, (
        "после неверной попытки тот же nonce принял верный ответ")


def test_body_is_bound_to_the_nonce(client):
    """Смежное свойство, которое не должно пострадать: решение под один текст
    нельзя переиспользовать под другой."""
    from app import challenge, db

    cid, _ = challenge.issue("original body")
    answer = db.connect().execute(
        "SELECT answer FROM challenges WHERE id = ?", (cid,)).fetchone()["answer"]

    assert challenge.verify(cid, answer, "a different body") is False
