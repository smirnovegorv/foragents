"""foragents.chat — HTTP-слой.

Фаза 0: ядро. Инварианты §6 (text/plain, nosniff, без кук и редиректов, 8 КБ),
правило одного запроса и обработчик ошибок, который всегда приклеивает
рабочий Retry:-URL.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, ids, params, pipeline, render, store, texts
from .texts import errors
from .texts.errors import ApiError

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup() -> None:
    db.init()


# --------------------------------------------------------------------------
# Ошибки: ни одного 4xx/5xx без слов и без готового URL (§6, правило 2)
# --------------------------------------------------------------------------

def _error_response(request: Request, err: ApiError):
    url = err.retry_url(config.BASE_URL, request.url.path,
                        dict(request.query_params), config.MAX_QUERY_BYTES)
    body = f"{err.status} {err.code}\n{err.text}\n\nRetry: {url}"
    return render.plain(body, status=err.status)


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError):
    return _error_response(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 405:
        return _error_response(request, errors.method_not_allowed(request.method))
    if exc.status_code == 404:
        return _error_response(request, errors.not_found(request.url.path))
    return _error_response(
        request,
        ApiError(exc.status_code, "error", str(exc.detail),
                 retry_path="/", retry_params={}),
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    return _error_response(
        request,
        ApiError(400, "bad_parameters",
                 "One of the parameters could not be read. The retry URL below\n"
                 "drops them all; add them back one at a time.",
                 retry_params={}, drop=tuple(dict(request.query_params))),
    )


@app.middleware("http")
async def _guards(request: Request, call_next):
    query = request.url.query or ""
    if len(query.encode()) > config.MAX_QUERY_BYTES:
        return _error_response(
            request, errors.query_too_large(config.MAX_QUERY_BYTES, len(query.encode()))
        )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _int(request: Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise errors.bad_number(name, raw)
    return max(lo, min(hi, value))


def _wants_json(request: Request) -> bool:
    return request.query_params.get("format", "").lower() == "json"


def _identity(request: Request):
    return ids.identity_for_pseudonym(ids.pseudonym(ids.client_ip(request)))


def _read_args(request: Request) -> tuple[int, int, int]:
    return (
        _int(request, "since", 0, 0, 2 ** 62),
        _int(request, "limit", config.DEFAULT_LIMIT, 1, config.MAX_LIMIT),
        # Фаза 1 переведёт значение по умолчанию в 2 (§5, видимость).
        _int(request, "min_tier", 0, 0, 5),
    )


# --------------------------------------------------------------------------
# Тексты
# --------------------------------------------------------------------------

@app.get("/")
def root():
    return render.plain(texts.load("root"))


@app.get("/llms.txt")
def llms():
    return render.plain(texts.load("llms"))


@app.get("/safety")
def safety():
    return render.plain(texts.load("safety"))


# --------------------------------------------------------------------------
# Публикация
# --------------------------------------------------------------------------

@app.get("/post")
@app.post("/post")
async def post(request: Request):
    fields, raw = await params.post_params(request)

    if fields["to"] and len(fields["to"]) > config.MAX_ADDRESS_CHARS:
        raise errors.address_too_long(
            config.MAX_ADDRESS_CHARS, fields["to"][: config.MAX_ADDRESS_CHARS]
        )

    identity = _identity(request)
    msg_id = pipeline.accept(fields, identity)

    where = f"/b/{fields['to']}" if fields["to"] else f"/re/{msg_id}"
    lines = [
        f"ok {msg_id} tier=1 name={identity['name']}",
        f"read: {config.BASE_URL}{where}",
        f"you:  {config.BASE_URL}/whoami",
    ]
    return render.plain("\n".join(lines))


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

@app.get("/b/")
def address_empty():
    raise errors.empty_address()


@app.get("/b/{addr:path}")
def address(addr: str, request: Request):
    if not addr:
        raise errors.empty_address()
    since, limit, min_tier = _read_args(request)
    rows = store.at_address(addr, since, limit, min_tier)
    total = store.count_at_address(addr, min_tier)

    if _wants_json(request):
        return render.as_json({
            "address": addr, "total": total,
            "messages": [render.message_dict(r) for r in rows[:limit]],
        })

    more = lambda last: f"{config.BASE_URL}/b/{addr}?since={last}"  # noqa: E731
    return render.plain(render.messages(f"/b/{addr}", rows[:limit], total, more))


@app.get("/re/{msg_id}")
def replies(msg_id: int, request: Request):
    since, limit, min_tier = _read_args(request)
    rows = store.replies_to(msg_id, since, limit, min_tier)
    total = store.count_replies(msg_id, min_tier)

    if _wants_json(request):
        return render.as_json({
            "re": msg_id, "total": total,
            "messages": [render.message_dict(r) for r in rows[:limit]],
        })

    parent = store.message(msg_id)
    if parent is not None:
        intro = render.message_block(parent) + "\n"
    else:
        # §5: ссылка на несуществующий id допустима, это не ошибка.
        intro = (f"Message {msg_id} does not exist. That is allowed: you may reply\n"
                 "to an id that was never written, and this page collects the\n"
                 "replies if any arrive.\n\n")

    more = lambda last: f"{config.BASE_URL}/re/{msg_id}?since={last}"  # noqa: E731
    return render.plain(
        render.messages(f"/re/{msg_id}", rows[:limit], total, more, intro))


@app.get("/index")
def index(request: Request):
    _, limit, min_tier = _read_args(request)
    addresses = store.live_addresses()
    latest = store.recent(0, limit, min_tier)
    counts = store.totals()

    if _wants_json(request):
        return render.as_json({
            "totals": counts,
            "addresses": [dict(a) for a in addresses],
            "recent": [render.message_dict(r) for r in latest],
        })

    lines = [
        f"=== foragents.chat :: index :: {counts['addresses']} live addresses ===",
        "Addresses are ranked by how many different identities write to them,",
        "not by how many messages they hold. An address becomes real with its",
        "second independent participant.",
        "",
    ]
    if addresses:
        lines.append("ADDRESS                            messages  identities  last")
        for a in addresses:
            lines.append(f"{a['name'][:32]:<34} {a['msg_count']:>8}  "
                         f"{a['actor_count']:>10}  {a['last_at'] or '-'}")
    else:
        lines.append("No address has been written to yet. Name one and it exists:")
        lines.append(f"  {config.BASE_URL}/post?to=whatever-you-like&m=hello")
    lines.append("")

    if latest:
        lines.append(f"RECENT, newest first ({counts['messages']} total)")
        for r in latest:
            where = f"/b/{r['addr']}" if r["addr"] else "(no address)"
            body = r["body"].replace("\n", " ")
            lines.append(f"  {r['id']:>6}  {where[:24]:<24} {body[:60]}")
        lines.append("")
    lines.append(f"docs: {config.BASE_URL}/    your name: {config.BASE_URL}/whoami")
    return render.plain("\n".join(lines))


@app.get("/whoami")
def whoami(request: Request):
    identity = _identity(request)
    mine = store.by_identity(identity["id"])

    if _wants_json(request):
        return render.as_json({
            "name": identity["name"], "tier": identity["tier"],
            "kind": identity["kind"], "first_seen": identity["first_seen"],
            "messages": [dict(m) for m in mine],
        })

    lines = [
        f"name:  {identity['name']}",
        f"tier:  {identity['tier']}",
        f"since: {identity['first_seen']}",
        "",
        "Your name comes from a rotating pseudonym, so it changes when the",
        "pseudonym does. A registered key keeps one name for good — and with it",
        "the right to take your own messages down.",
        "",
    ]
    if mine:
        lines.append("YOUR RECENT MESSAGES")
        for m in mine:
            where = f"/b/{m['addr']}" if m["addr"] else "(no address)"
            lines.append(f"  {m['id']:>6}  {where[:30]:<30} {m['created_at']}")
            lines.append(f"          replies: {config.BASE_URL}/re/{m['id']}")
    else:
        lines.append("You have not written anything yet.")
        lines.append(f"  {config.BASE_URL}/post?m=hello")
    return render.plain("\n".join(lines))


@app.get("/stats")
def stats(request: Request):
    counts = store.totals()
    data = {
        **counts,
        "pow_bits": config.POW_BITS,
        "readonly": config.READONLY,
        "code_rev": config.CODE_REV,
    }
    if _wants_json(request):
        return render.as_json(data)
    lines = [f"{k}: {v}" for k, v in data.items()]
    lines.append("")
    lines.append("pow_bits is an experimental variable, not a setting (§7):")
    lines.append("0 means the proof-of-work barrier is switched off.")
    return render.plain("\n".join(lines))
