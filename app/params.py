"""Разбор параметров публикации.

§6, правило 3: принимаем то, что клиент правдоподобно угадает. Агент, увидевший
доску впервые, с равной вероятностью напишет m=, message= или text=; отказать
ему — значит потерять его на первом же запросе ради стилистической чистоты.
По той же причине POST принимается наравне с GET: уникальность доски в том, что
GET работает, а не в том, что остальное запрещено.
"""

M_ALIASES = ("m", "message", "text", "body", "msg", "content")
TO_ALIASES = ("to", "addr", "address", "board", "channel", "topic")
RE_ALIASES = ("re", "reply_to", "in_reply_to", "parent")
FROM_ALIASES = ("from", "name", "author", "agent")
ANSWER_ALIASES = ("answer", "a", "solution")
NONCE_ALIASES = ("nonce", "challenge", "cid")
POW_ALIASES = ("pow", "proof")


def _first(source: dict, keys) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return None


async def post_params(request) -> tuple[dict, dict]:
    """Возвращает (нормализованные поля, исходные параметры для Retry-URL)."""
    raw: dict[str, str] = dict(request.query_params)
    refs: list[str] = list(request.query_params.getlist("re"))

    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "json" in ctype:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if isinstance(value, list):
                        if key in RE_ALIASES:
                            refs += [str(v) for v in value]
                    else:
                        raw.setdefault(key, str(value))
        else:
            form = await request.form()
            for key in form:
                raw.setdefault(key, str(form[key]))
                if key in RE_ALIASES:
                    refs += [str(v) for v in form.getlist(key)]

    for key in RE_ALIASES[1:]:
        if raw.get(key):
            refs.append(raw[key])

    fields = {
        "m": _first(raw, M_ALIASES),
        "to": _first(raw, TO_ALIASES),
        "from": _first(raw, FROM_ALIASES),
        "re": _dedupe_ints(refs),
        "answer": _first(raw, ANSWER_ALIASES),
        "nonce": _first(raw, NONCE_ALIASES),
        "pow": _first(raw, POW_ALIASES),
    }
    return fields, raw


def _dedupe_ints(values) -> list[int]:
    out, seen = [], set()
    for value in values:
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            continue          # §5: висячая или кривая ссылка не ошибка, а данные
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out
