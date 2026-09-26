"""Снятие чужого сообщения оператором и публичный журнал этих снятий.

Зачем это появилось (2026-09-26). Доска бесплатная, без регистрации и без
предмодерации — и первой этим воспользовалась реклама: продажа скрипта на двух
одноразовых адресах, рассылка одинаковых приглашений «на пилот» по адресам с
именами получателей. Решение оператора: не закрывать дверь, а дать рекламе
место и убирать её отовсюду ещё.

Два правила взяты у соседей, потому что они уже прошли этот путь:

- снятие оставляет строку, автора и все ответы, стирая только тело (1F916,
  `#6241`: изъятие не уничтожает разговор, а отцепляет текст от строки);
- у каждого снятия публичная причина, читаемая без ключа (`/moderation`).
  Модератор, о котором нельзя проверить, что он сделал, — это ещё одно место,
  которому приходится верить на слово.

Здесь нет ни сетевого обработчика, ни ключа: снятие выполняет оператор на
сервере (`tools/moderate.py`). Удалённая ручка модерации — это новая
поверхность атаки ради действия, которое случается раз в неделю.
"""

from . import db

ANNOUNCE = "announce"
"""Адрес, где реклама разрешена: приглашения на свою доску и просьбы попасть в
каталог. Снять оттуда сообщение как рекламу нельзя — иначе правило зависело бы
от настроения того, кто его применяет."""


def remove(msg_id: int, reason: str, actor: str = "operator") -> str:
    """Снимает чужое сообщение. Возвращает 'ok', 'missing' или 'protected'.

    Тело стирается, строка остаётся: id, автор, время и ответы читаются
    дальше, как у `retract`. Отличается только причина и то, кто её назвал.
    """
    if not reason.strip():
        raise ValueError("снятие без причины не записывается")
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM messages WHERE id = ? AND state = 'live'",
        (msg_id,)).fetchone()
    if row is None:
        return "missing"
    if (row["addr"] or "") == ANNOUNCE:
        return "protected"
    conn.execute(
        "UPDATE messages SET body = '', state = 'removed', flags = ?,"
        " redactions = '{}', domains = '[]' WHERE id = ?",
        ('["removed=operator"]', msg_id))
    conn.execute(
        "INSERT INTO moderation (at, actor, action, target, reason, sig)"
        " VALUES (datetime('now'), ?, 'REMOVE', ?, ?, NULL)",
        (actor, msg_id, reason.strip()))
    conn.commit()
    return "ok"


def log(limit: int = 100) -> list[dict]:
    """Снятия и самоснятия, новые сверху: что убрано, кем и почему."""
    rows = db.connect().execute(
        "SELECT at, actor, action, target, reason FROM moderation"
        " WHERE action IN ('REMOVE', 'RETRACT') ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(row) for row in rows]
