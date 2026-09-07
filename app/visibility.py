"""Правила видимости (§5).

Спам — задача чтения, а не задача записи. Читателю нужно не «мусор не записан»,
а «мусор не виден»; фильтр на выдаче деградирует мягко, не облагает налогом
честного клиента и — что важно для §12 — сохраняет отсеянное как данные.
Барьер на входе уничтожил бы именно ту телеметрию, ради которой атаки
объявлены отдельным результатом.

Каждое скрытие объясняется в выдаче словами. Молчаливая фильтрация превратила
бы доску в то, чем она не должна быть: место, где непонятно, что происходит.
"""

from . import db

DEFAULT_MIN_TIER = 2      # §5: безличный слой существует в базе, но не в выдаче
SLOT_WINDOW = 20          # из последних скольких сообщений
SLOT_QUOTA = 3            # сколько может занять одна личность
SOLO_THRESHOLD = 20       # пока общих адресов меньше, метка solo бессмысленна


def apply(rows, limit: int) -> tuple[list, list[str]]:
    """Квота слотов и схлопывание дублей. Возвращает (строки, пояснения)."""
    kept, notes = [], []
    per_identity: dict[int, int] = {}
    seen_bodies: dict[tuple, int] = {}
    hidden_by_quota: dict[str, int] = {}
    collapsed = 0

    window = rows[-SLOT_WINDOW:] if len(rows) > SLOT_WINDOW else list(rows)
    older = rows[: len(rows) - len(window)]

    for row in older + window:
        key = (row["identity_id"], row["body_hash"])
        if key in seen_bodies:
            seen_bodies[key] += 1
            collapsed += 1
            continue
        seen_bodies[key] = 1

        used = per_identity.get(row["identity_id"], 0)
        if used >= SLOT_QUOTA:
            name = row["name"]
            hidden_by_quota[name] = hidden_by_quota.get(name, 0) + 1
            continue
        per_identity[row["identity_id"]] = used + 1
        kept.append(row)

    if collapsed:
        notes.append(f"[{collapsed} repeated message(s) collapsed: identical text "
                     f"from the same identity is shown once]")
    for name, n in hidden_by_quota.items():
        notes.append(f"[{n} message(s) from {name} not shown: one identity may fill "
                     f"at most {SLOT_QUOTA} of the last {SLOT_WINDOW} slots here. "
                     f"Add ?full=1 to see everything]")
    return kept[-limit:] if limit else kept, notes


def show_solo() -> bool:
    row = db.connect().execute(
        "SELECT COUNT(*) c FROM addresses WHERE actor_count >= 2").fetchone()
    return row["c"] >= SOLO_THRESHOLD


def min_tier(requested: int | None, full: bool) -> int:
    if full:
        return 0
    return DEFAULT_MIN_TIER if requested is None else requested
