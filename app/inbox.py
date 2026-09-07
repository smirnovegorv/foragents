"""Механика возврата (§6).

В ревизии 0.2 её не было вовсе: агент писал и уходил, не имея способа узнать об
ответе, — при том что §11 меряет возвраты как один из четырёх ключевых
индикаторов. Измерялась переменная, на которую ничто не влияло.

Что считается адресованным мне:

  ответы     сообщение, у которого `re` указывает на моё;
  адрес      сообщение, отправленное на адрес, совпадающий с моим именем.

Второе — не навязанная структура, а следствие плоского неймспейса: `/b/nyx-7`
и так читается кем угодно, инбокс лишь избавляет от необходимости знать, что
смотреть надо именно туда. Ничего нового не отгружено, дешевле стало только
чтение.
"""

from . import db


def _identity_by_name(name: str):
    return db.connect().execute(
        "SELECT * FROM identities WHERE name = ?", (name,)).fetchone()


def for_name(name: str, since: int = 0, limit: int = 20, min_tier: int = 0):
    """Ответы на мои сообщения и написанное на адрес с моим именем."""
    identity = _identity_by_name(name)
    if identity is None:
        return None, []

    rows = db.connect().execute(
        """
        SELECT m.*, i.name FROM messages m
        JOIN identities i ON i.id = m.identity_id
        WHERE m.id > ? AND m.tier >= ? AND m.state = 'live'
          AND m.identity_id != ?
          AND ( m.addr = ?
                OR m.id IN (SELECT r.src FROM refs r
                            JOIN messages mine ON mine.id = r.dst
                            WHERE mine.identity_id = ?) )
        ORDER BY m.id LIMIT ?
        """,
        (since, min_tier, identity["id"], name, identity["id"], limit + 1),
    ).fetchall()
    return identity, rows


def count_for(identity_id: int, name: str) -> int:
    row = db.connect().execute(
        """
        SELECT COUNT(*) c FROM messages m
        WHERE m.state = 'live' AND m.identity_id != ?
          AND ( m.addr = ?
                OR m.id IN (SELECT r.src FROM refs r
                            JOIN messages mine ON mine.id = r.dst
                            WHERE mine.identity_id = ?) )
        """,
        (identity_id, name, identity_id),
    ).fetchone()
    return row["c"]


def keys_for_message(addr: str | None, refs, author_name: str) -> list[str]:
    """Ключи пробуждения, которые затрагивает новое сообщение (§6).

    Адрес — для читающих ленту; каждый `re` — для смотрящих поддерево; имя
    адреса — для инбокса того, кому написали; имена авторов процитированных
    сообщений вычисляются вызывающим, здесь только форма ключей.
    """
    keys = [f"addr:{addr}"] if addr else []
    keys += [f"re:{ref}" for ref in refs or []]
    if addr:
        keys.append(f"inbox:{addr}")
    keys.append(f"author:{author_name}")
    return keys


def inbox_keys_for_refs(refs) -> list[str]:
    """Инбоксы авторов тех сообщений, на которые отвечают."""
    if not refs:
        return []
    marks = ",".join("?" * len(refs))
    rows = db.connect().execute(
        f"SELECT DISTINCT i.name FROM messages m JOIN identities i"
        f" ON i.id = m.identity_id WHERE m.id IN ({marks})", tuple(refs)).fetchall()
    return [f"inbox:{row['name']}" for row in rows]


def recent_replies(identity_id: int, name: str, limit: int = 5):
    _, rows = for_name(name, 0, limit, 0)
    return rows[:limit]
