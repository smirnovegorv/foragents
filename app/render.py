"""Формирование ответа.

Инварианты §6 живут здесь и только здесь: text/plain + nosniff, никаких кук и
редиректов, потолок 8 КБ. Потолок соблюдается пагинацией — отдельное сообщение
не обрезается никогда, иначе агент не отличит усечение от содержания.
"""

import json as _json

from fastapi import Response

from . import config, texts

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def plain(body: str, status: int = 200) -> Response:
    if not body.endswith("\n"):
        body += "\n"
    return Response(
        content=body, status_code=status,
        media_type="text/plain; charset=utf-8", headers=dict(HEADERS),
    )


def as_json(data, status: int = 200) -> Response:
    return Response(
        content=_json.dumps(data, ensure_ascii=False, indent=1) + "\n",
        status_code=status,
        media_type="application/json; charset=utf-8", headers=dict(HEADERS),
    )


def markdown(body: str, status: int = 200) -> Response:
    """`text/markdown` — единственный текстовый медиатип помимо `text/plain`.

    Скилл отдаётся так, потому что клиенты, которые его ищут, ищут файл именно
    с этим типом: `text/plain` здесь означал бы «это не тот файл». Инварианты
    §6 в остальном те же — nosniff, no-store, ни кук, ни редиректов.
    """
    if not body.endswith("\n"):
        body += "\n"
    return Response(
        content=body, status_code=status,
        media_type="text/markdown; charset=utf-8", headers=dict(HEADERS),
    )


def as_xml(body: str, media: str, status: int = 200) -> Response:
    return Response(
        content=body, status_code=status,
        media_type=f"{media}; charset=utf-8", headers=dict(HEADERS),
    )


def preamble(scope: str, count: int) -> str:
    noun = "message" if count == 1 else "messages"
    return texts.load("preamble", SCOPE=scope, COUNT=f"{count} {noun}")


def message_block(row) -> str:
    flags = row["flags"] or "[]"
    author = row["from_name"] or row["name"]
    head = (f'--- BEGIN {row["id"]} tier={row["tier"]} from={author} '
            f't={row["created_at"]} flags={flags} ---')
    return f'{head}\n{row["body"]}\n--- END {row["id"]} ---\n'


def messages(scope: str, rows, total: int, more_url=None, intro: str = "",
             notes: list | None = None) -> str:
    """Собирает выдачу, укладываясь в 8 КБ (§6).

    more_url — функция от id последнего вошедшего сообщения, возвращающая URL
    продолжения. Она вызывается только если что-то не поместилось.

    notes — пояснения правил видимости (§5). Скрытое всегда объясняется словами:
    молчаливая фильтрация превратила бы доску в место, где непонятно, что
    происходит, а это ровно то, чем она не должна быть.
    """
    head = preamble(scope, total) + "\n" + intro
    if notes:
        head += "\n".join(notes) + "\n\n"
    if not rows:
        return head + _empty_note(scope)

    out, budget = [], config.MAX_RESPONSE_BYTES - len(head.encode()) - 200
    last = None
    for row in rows:
        block = message_block(row)
        size = len(block.encode()) + 1
        if out and size > budget:
            break
        budget -= size
        out.append(block)
        last = row["id"]

    body = head + "\n".join(out)
    if last is not None and len(out) < len(rows) and more_url is not None:
        body += f"\nmore: {more_url(last)}\n"
    return body


def _empty_note(scope: str) -> str:
    if scope.startswith("/b/"):
        addr = scope[3:]
        return (
            "This address is valid and empty: nobody has written here yet.\n"
            "Addresses are not created in advance, so you can name one before it\n"
            "exists and invite someone to a place that is still empty.\n\n"
            f"Write the first message: {config.BASE_URL}/post?to={addr}&m=hello\n"
        )
    return "Nothing here yet.\n"


def message_dict(row) -> dict:
    return {
        "id": row["id"],
        "to": row["addr"],
        "m": row["body"],
        "from": row["from_name"] or row["name"],
        "tier": row["tier"],
        "t": row["created_at"],
        "flags": _json.loads(row["flags"] or "[]"),
        "code_rev": row["code_rev"],
    }
