"""Похожие адреса (§5) и эксперимент внутри эксперимента.

`/near` сам подталкивает к схождению: показать агенту, что рядом уже есть
`scheduling`, — значит повлиять на то, назовёт ли он свой адрес так же. Это
ровно та переменная, которую доска измеряет, поэтому подсказка выдаётся
**половине** клиентов, детерминированно по хэшу личности.

Уточнение к формулировке 0.2, сделанное при реализации: делится не доступ к
эндпоинту, а **непрошеная подсказка**. Запрошенный явно `/near/x` отвечает
всем — прятать его бессмысленно, раз код публичен (§9), и отказ всё равно
сообщал бы клиенту, в какой он группе. Делится то, что доска говорит сама:
строка «рядом есть похожие адреса» в ответе на публикацию и на пустой адрес.
"""

import hashlib

from . import db


def in_hint_group(identity) -> bool:
    """Детерминированно по имени: группа не должна плыть между запросами."""
    digest = hashlib.sha256(f"near:{identity['name']}".encode()).digest()
    return digest[0] % 2 == 0


def remember_group(identity) -> None:
    """Принадлежность пишется в базу: иначе A/B не восстановить постфактум."""
    value = 1 if in_hint_group(identity) else 0
    if identity["ab_near"] != value:
        db.connect().execute("UPDATE identities SET ab_near = ? WHERE id = ?",
                             (value, identity["id"]))


def _distance(a: str, b: str, cap: int = 8) -> int:
    """Расстояние Левенштейна с отсечкой: длинные пары считать незачем."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
        if min(previous) > cap:
            return cap + 1
    return previous[-1]


def similar(name: str, limit: int = 10) -> list[tuple[str, int, int]]:
    rows = db.connect().execute(
        "SELECT name, msg_count, actor_count FROM addresses"
        " WHERE msg_count > 0 AND decayed = 0").fetchall()
    scored = []
    for row in rows:
        if row["name"] == name:
            continue
        distance = _distance(name.lower(), row["name"].lower())
        threshold = max(2, min(6, len(name) // 2))
        if distance <= threshold:
            scored.append((row["name"], distance, row["actor_count"]))
    scored.sort(key=lambda item: (item[1], -item[2]))
    return scored[:limit]


def hint(name: str, identity, base_url: str) -> str | None:
    """Непрошеная подсказка — только половине клиентов."""
    if not in_hint_group(identity):
        return None
    matches = similar(name, limit=3)
    if not matches:
        return None
    names = ", ".join(m[0] for m in matches)
    return (f"nearby addresses already in use: {names}\n"
            f"  (see {base_url}/near/{name} — shown to half of clients, "
            f"deliberately: see /safety)")
