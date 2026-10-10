"""Треды для страницы человека (§10).

У доски нет понятия «тред»: есть сообщения и ссылки `re` между ними (§5).
Тред здесь — связная компонента этих ссылок, а его номер — наименьший id в
компоненте. Так сообщение с двумя `re` не приходится относить к одному из
родителей по произволу: оно сшивает оба разговора в один, что оно и сделало.

Невидимые сообщения (снятые, отозванные, ниже тира по умолчанию) остаются
узлами графа, хотя на страницу не попадают. Иначе снятие корня рассыпало бы
ответы на него по каталогу как несвязанные реплики, а номер треда менялся бы
вместе с видимостью. Ссылка на id, которого нет вовсе, узлом не становится:
§5 разрешает ссылаться на что угодно, и два чужих разговора не должны
склеиваться оттого, что оба сослались на несуществующее.
"""

TITLE_CHARS = 180


def _title(body: str) -> str:
    text = " ".join(body.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS].rstrip() + "…"


def build(rows, refs, known: dict[int, str]) -> list[dict]:
    """rows — видимые сообщения; refs — пары (src, dst); known — состояние
    каждого существующего id. Возвращает треды, свежие сверху."""
    parent = {msg_id: msg_id for msg_id in known}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    parents_of: dict[int, list[int]] = {}
    replies_to: dict[int, list[int]] = {}
    for src, dst in refs:
        if src not in known:
            continue
        parents_of.setdefault(src, []).append(dst)
        if dst in known and dst != src:
            replies_to.setdefault(dst, []).append(src)
            a, b = find(src), find(dst)
            if a != b:
                parent[max(a, b)] = min(a, b)   # корень — наименьший id

    visible = {row["id"] for row in rows}
    threads: dict[int, dict] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        root = find(row["id"])
        thread = threads.setdefault(root, {"id": root, "messages": []})
        message = dict(row)
        message["thread"] = root
        message["parents"] = [
            {"id": dst, "shown": dst in visible}
            for dst in sorted(parents_of.get(row["id"], []))]
        message["replies"] = sorted(
            src for src in replies_to.get(row["id"], []) if src in visible)
        thread["messages"].append(message)

    # Снятое и отозванное показывается заглушкой на своём месте: id и факт
    # снятия публичны (§9), скрыто только содержание. Сообщения ниже тира
    # заглушки не получают — безличный слой в выдаче не существует (§5).
    for msg_id, state in known.items():
        if state == "live" or msg_id in visible:
            continue
        root = find(msg_id)
        if root in threads:
            threads[root]["messages"].append(
                {"id": msg_id, "gone": True, "thread": root})

    out = []
    for thread in threads.values():
        thread["messages"].sort(key=lambda m: m["id"])
        shown = [m for m in thread["messages"] if not m.get("gone")]
        addrs = []
        for message in shown:
            if message["addr"] and message["addr"] not in addrs:
                addrs.append(message["addr"])
        thread.update(
            title=_title(shown[0]["body"]),
            addrs=addrs,
            count=len(shown),
            actors=len({m["identity_id"] for m in shown}),
            started_by=shown[0]["name"],
            last_at=shown[-1]["created_at"],
            last_id=shown[-1]["id"],
        )
        out.append(thread)
    out.sort(key=lambda t: -t["last_id"])
    return out
