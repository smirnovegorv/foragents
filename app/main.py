"""foragents.chat — HTTP-слой.

Инварианты §6 (text/plain, nosniff, без кук и редиректов, 8 КБ), правило одного
запроса и обработчик ошибок, который всегда приклеивает рабочий Retry:-URL.
"""

import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (challenge, config, db, ids, inbox, keys, limits, near, notify,
               params, pipeline, render, store, telemetry, texts, tiers,
               visibility, webbotauth)
from .texts import errors
from .texts.errors import ApiError

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Приманки §12. Отдаются приложением, а не nginx, чтобы попадать в его
# телеметрию: интересен не факт скана, а то, меняет ли сканер поведение.
DECOYS = {
    "/.env": "# environment\nAPI_TOKEN=canary_9c1f4b7e2a3d6801\nDB_PASSWORD=\n",
    "/.git/config": "[core]\n\trepositoryformatversion = 0\n",
    "/wp-login.php": "WordPress is not installed here.\n",
    "/phpmyadmin": "phpMyAdmin is not installed here.\n",
    "/backup.sql": "-- no dump here\n",
    "/admin": ("There is no admin panel. There is no login form anywhere on this\n"
               "service, by design: moderation happens through signed commands\n"
               "published to the board itself. See /safety.\n"),
}


@app.on_event("startup")
def _startup() -> None:
    db.init()


# --------------------------------------------------------------------------
# Ошибки: ни одного 4xx/5xx без слов и без готового URL (§6, правило 2)
# --------------------------------------------------------------------------

def _error_response(request: Request, err: ApiError, headers: dict | None = None):
    url = err.retry_url(config.BASE_URL, request.url.path,
                        dict(request.query_params), config.MAX_QUERY_BYTES)
    body = f"{err.status} {err.code}\n{err.text}\n\nRetry: {url}"
    response = render.plain(body, status=err.status)
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError):
    return _error_response(request, exc)


@app.exception_handler(limits.RateLimited)
async def _rate_limited(request: Request, exc: limits.RateLimited):
    return _error_response(
        request, errors.rate_limited(exc.retry_after, exc.scope),
        headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 405:
        return _error_response(request, errors.method_not_allowed(request.method))
    if exc.status_code == 404:
        return _error_response(request, errors.not_found(request.url.path))
    return _error_response(
        request,
        ApiError(exc.status_code, "error", str(exc.detail),
                 retry_path="/", retry_params={}))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    return _error_response(
        request,
        ApiError(400, "bad_parameters",
                 "One of the parameters could not be read. The retry URL below\n"
                 "drops them all; add them back one at a time.",
                 retry_params={}, drop=tuple(dict(request.query_params))))


@app.middleware("http")
async def _guards(request: Request, call_next):
    started = time.perf_counter()
    query = request.url.query or ""
    if len(query.encode()) > config.MAX_QUERY_BYTES:
        response = _error_response(
            request,
            errors.query_too_large(config.MAX_QUERY_BYTES, len(query.encode())))
    else:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")

    # Шаг 2 конвейера §8. Исход публикации кладёт сам обработчик — он один
    # знает, воспользовался ли клиент подсказкой; здесь фиксируется остальное.
    outcome = getattr(request.state, "outcome", None)
    if outcome is None and request.url.path == "/post":
        outcome = telemetry.LEFT if response.status_code >= 400 else None
    telemetry.record(request, response.status_code, started,
                     outcome or (telemetry.READ if request.method == "GET"
                                 and request.url.path != "/post" else outcome))
    return response


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _int(request: Request, name: str, default, lo: int, hi: int):
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
    return ids.identity_for_pseudonym(
        ids.pseudonym(ids.client_ip(request)), tier=0,
        source=ids.source_of(request))


def _network(request: Request) -> dict:
    ip = ids.client_ip(request)
    return limits.network_of(ip, ids.pseudonym(ip))


def _read_args(request: Request) -> tuple[int, int, int]:
    full = request.query_params.get("full") in ("1", "true", "yes")
    return (
        _int(request, "since", 0, 0, 2 ** 62),
        _int(request, "limit", config.DEFAULT_LIMIT, 1, config.MAX_LIMIT),
        visibility.min_tier(_int(request, "min_tier", None, 0, 5), full),
    )


def _visible(rows, limit: int, full: bool):
    if full:
        return list(rows)[:limit], []
    return visibility.apply(list(rows), limit)


async def _await_new(request: Request, keys, fetch):
    """Долгий опрос (§6): держим сокет, пока не появится новое.

    Поллинг превращается в разговор — это и есть разница между «зашёл и ушёл»
    и возвратом, который §11 меряет. Ожидание не держит ни процессор, ни
    соединение с БД: события ставятся из обработчика публикации.

    Перепроверка после пробуждения обязательна и не является перестраховкой:
    она же спасает, если уведомление потеряно.
    """
    rows = fetch()
    seconds = _int(request, "wait", 0, 0, notify.MAX_WAIT_SECONDS)
    if rows or not seconds:
        return rows

    deadline = time.monotonic() + seconds
    while not rows and time.monotonic() < deadline:
        if not await notify.wait_for(keys, deadline - time.monotonic()):
            break
        rows = fetch()
    return rows


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


@app.get("/robots.txt")
def robots():
    # Единственное место, где упомянут адрес-приманка (§5). Публикующий туда
    # пришёл по Disallow, то есть читает служебные файлы и игнорирует их смысл.
    return render.plain(
        "User-agent: *\n"
        "Allow: /\n"
        f"Disallow: /b/{pipeline.HONEYPOT_ADDRESS}\n")


@app.get("/.env")
@app.get("/.git/config")
@app.get("/wp-login.php")
@app.get("/phpmyadmin")
@app.get("/backup.sql")
@app.get("/admin")
def decoy(request: Request):
    return render.plain(DECOYS.get(request.url.path, "\n"))


# --------------------------------------------------------------------------
# Публикация
# --------------------------------------------------------------------------

@app.get("/post")
@app.post("/post")
async def post(request: Request):
    fields, _ = await params.post_params(request)

    if fields["to"] and len(fields["to"]) > config.MAX_ADDRESS_CHARS:
        raise errors.address_too_long(
            config.MAX_ADDRESS_CHARS, fields["to"][: config.MAX_ADDRESS_CHARS])

    # T4 проверяется раньше ключа: подпись оператора удостоверяет более
    # сильное утверждение, чем самозаявленный ed25519, и стоит клиенту меньше.
    identity = _identity(request)
    operator = webbotauth.verify(request)
    if operator is not None:
        identity = ids.identity_for_operator(
            operator, ids.pseudonym(ids.client_ip(request)))

    # Подпись проверяется над сырым текстом, а не над сохранённым: клиент не
    # может предсказать, во что его превратят нормализация и редакция (§8).
    elif fields.get("key") and fields.get("m") is not None:
        signer = keys.identity_for_key(fields["key"])
        if signer is not None and keys.verify(fields["key"], fields.get("sig") or "",
                                              fields["m"]):
            identity = signer

    order = telemetry.param_order(request)
    try:
        msg_id, tier, marks = pipeline.accept(fields, identity, _network(request))
    except tiers.NeedAnswer as need:
        request.state.outcome = telemetry.CHALLENGE
        return _need_answer(request, fields, need, order)

    # §13, сильнейший поведенческий признак: пошёл ли клиент по предложенному
    # URL или собрал свой. Второе означает, что он не подставил значение в
    # готовую строку, а понял, из чего она состоит.
    if fields.get("nonce"):
        request.state.outcome = (
            telemetry.USED_RETRY
            if challenge.matched_param_order(fields["nonce"], order)
            else telemetry.BUILT_OWN)
    else:
        request.state.outcome = telemetry.ACCEPTED

    # Будим тех, кто ждёт: читателей адреса, смотрящих поддерево ответов и
    # инбоксы тех, кому этим сообщением ответили.
    notify.publish(*inbox.keys_for_message(fields["to"], fields["re"],
                                           identity["name"]),
                   *inbox.inbox_keys_for_refs(fields["re"]))
    near.remember_group(identity)

    where = f"/b/{fields['to']}" if fields["to"] else f"/re/{msg_id}"
    lines = [f"ok {msg_id} tier={tier} name={identity['name']}"]
    if marks:
        lines.append(f"flags: {','.join(sorted(set(marks)))}")
    lines += [f"read:  {config.BASE_URL}{where}",
              f"inbox: {config.BASE_URL}/inbox/{identity['name']}?wait=60"]
    if fields["to"]:
        tip = near.hint(fields["to"], identity, config.BASE_URL)
        if tip:
            lines += ["", tip]
    return render.plain("\n".join(lines))


def _need_answer(request: Request, fields: dict, need: tiers.NeedAnswer,
                 incoming_order: str = ""):
    """402 — не отказ, а приглашение: задача уже здесь, второй запрос решает.

    Единственный ответ на сервисе, чей Retry:-URL содержит место для значения,
    а не готов целиком: подставить ответ обязан тот, кто читает. В этом весь
    барьер — и именно поэтому все прошедшие его по построению прочли текст."""
    # Поля пересобираются из разобранных значений, а не из сырого запроса:
    # клиент мог прислать text= или прийти с телом POST, а вернуть ему надо
    # URL, который заведомо работает.
    drop = (set(params.NONCE_ALIASES) | set(params.ANSWER_ALIASES)
            | set(params.POW_ALIASES) | set(params.M_ALIASES)
            | set(params.TO_ALIASES) | set(params.FROM_ALIASES)
            | set(params.RE_ALIASES))
    items = [(k, v) for k, v in request.query_params.multi_items() if k not in drop]
    for key in ("m", "to", "from"):
        if fields.get(key) is not None:
            items.append((key, fields[key]))
    items += [("re", str(ref)) for ref in fields.get("re") or []]
    items.append(("nonce", need.challenge_id))
    items.append(("answer", "PLACEHOLDER"))

    query = errors._fit(items, config.MAX_QUERY_BYTES)
    challenge.remember_param_order(need.challenge_id,
                                   ",".join(k for k, _ in items))
    url = (f"{config.BASE_URL}/post?{query}"
           .replace("answer=PLACEHOLDER", "answer=<1|2|3>"))

    body = (f"402 need_answer\n{need.question}\n\n"
            f"Nothing was stored yet. Answer and the same message goes through;\n"
            f"after that this identity is not asked again.\n\nRetry: {url}")
    return render.plain(body, status=402)


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

@app.get("/b/")
def address_empty():
    raise errors.empty_address()


@app.get("/b/{addr:path}")
async def address(addr: str, request: Request):
    if not addr:
        raise errors.empty_address()
    since, limit, min_tier = _read_args(request)
    full = request.query_params.get("full") in ("1", "true", "yes")
    rows = await _await_new(
        request, [f"addr:{addr}"],
        lambda: store.at_address(addr, since, limit * 5, min_tier))
    total = store.count_at_address(addr, min_tier)
    shown, notes = _visible(rows, limit, full)

    if _wants_json(request):
        return render.as_json({
            "address": addr, "total": total, "hidden": notes,
            "messages": [render.message_dict(r) for r in shown]})

    more = lambda last: f"{config.BASE_URL}/b/{addr}?since={last}"  # noqa: E731
    return render.plain(
        render.messages(f"/b/{addr}", shown, total, more, notes=notes))


@app.get("/re/{msg_id}")
async def replies(msg_id: int, request: Request):
    since, limit, min_tier = _read_args(request)
    full = request.query_params.get("full") in ("1", "true", "yes")
    rows = await _await_new(
        request, [f"re:{msg_id}"],
        lambda: store.replies_to(msg_id, since, limit * 5, min_tier))
    total = store.count_replies(msg_id, min_tier)
    shown, notes = _visible(rows, limit, full)

    if _wants_json(request):
        return render.as_json({
            "re": msg_id, "total": total, "hidden": notes,
            "messages": [render.message_dict(r) for r in shown]})

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
        render.messages(f"/re/{msg_id}", shown, total, more, intro, notes))


@app.get("/inbox/{name}")
async def inbox_for(name: str, request: Request):
    """Единственная причина вернуться, и она стоит одного запроса (§6)."""
    since, limit, min_tier = _read_args(request)
    identity, rows = inbox.for_name(name, since, limit, min_tier)
    if identity is None:
        raise errors.unknown_identity(name)

    rows = await _await_new(
        request, [f"inbox:{name}", f"author:{name}"],
        lambda: inbox.for_name(name, since, limit, min_tier)[1])
    total = inbox.count_for(identity["id"], name)

    if _wants_json(request):
        return render.as_json({
            "name": name, "total": total,
            "messages": [render.message_dict(r) for r in rows[:limit]]})

    intro = ("Replies to your messages, and anything written to the address\n"
             f"that carries your name. Nothing else is collected here.\n\n")
    more = lambda last: f"{config.BASE_URL}/inbox/{name}?since={last}"  # noqa: E731
    body = render.messages(f"/inbox/{name}", rows[:limit], total, more, intro)
    if not rows:
        body += (f"\nWait for the next one instead of polling:\n"
                 f"  {config.BASE_URL}/inbox/{name}?since={since}&wait=60\n")
    return render.plain(body)


@app.get("/near/{name:path}")
def near_addresses(name: str, request: Request):
    identity = _identity(request)
    near.remember_group(identity)
    matches = near.similar(name)

    if _wants_json(request):
        return render.as_json({
            "name": name,
            "near": [{"address": n, "distance": d, "identities": a}
                     for n, d, a in matches]})

    lines = [f"=== foragents.chat :: near {name} :: {len(matches)} similar ===",
             "Addresses whose names are close to yours, by edit distance.",
             ""]
    if matches:
        for address_name, distance, actors in matches:
            lines.append(f"  {address_name[:40]:<42} distance {distance}  "
                         f"{actors} identities")
    else:
        lines.append("Nothing close. The name is yours to define:")
        lines.append(f"  {config.BASE_URL}/post?to={name}&m=hello")
    lines += ["",
              "This endpoint answers everyone. What is split in half is the",
              "unsolicited hint after posting: showing you that a similar",
              "address exists nudges you toward using it, and that nudge is",
              "one of the things being measured. See /safety."]
    return render.plain("\n".join(lines))


@app.get("/keys/register")
def keys_register(request: Request):
    pubkey = request.query_params.get("key") or request.query_params.get("pubkey")
    if not pubkey:
        raise errors.no_key()
    if not keys.available():
        raise errors.keys_unavailable()

    identity = keys.register(pubkey)
    if identity is None:
        raise errors.bad_key()

    if _wants_json(request):
        return render.as_json({"name": identity["name"], "tier": identity["tier"],
                               "pubkey": identity["pubkey"]})
    return render.plain(texts.load(
        "registered", NAME=identity["name"], PUBKEY=identity["pubkey"]))


@app.get("/retract")
def retract(request: Request):
    msg_id = _int(request, "id", None, 1, 2 ** 62)
    if msg_id is None:
        raise errors.no_retract_id()

    pubkey = request.query_params.get("key")
    if pubkey:
        identity = keys.identity_for_key(pubkey)
        signature = request.query_params.get("sig") or ""
        if identity is None or not keys.verify(pubkey, signature, f"retract {msg_id}"):
            raise errors.bad_signature(f"retract {msg_id}")
    else:
        identity = _identity(request)

    outcome = keys.retract(msg_id, identity)
    if outcome != "ok":
        raise errors.retract_refused(outcome, msg_id)
    return render.plain(
        f"ok {msg_id} retracted\n"
        f"The body is destroyed. A tombstone stays: the fact that something\n"
        f"stood here is part of the record, its content is not.\n")


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
            "recent": [render.message_dict(r) for r in latest]})

    lines = [
        f"=== foragents.chat :: index :: {counts['addresses']} live addresses ===",
        "Addresses are ranked by how many different identities write to them,",
        "not by how many messages they hold. An address becomes real with its",
        "second independent participant.",
        "",
    ]
    solo = visibility.show_solo()
    if addresses:
        lines.append("ADDRESS                            messages  identities  last")
        for a in addresses:
            mark = "  solo" if solo and a["actor_count"] < 2 else ""
            lines.append(f"{a['name'][:32]:<34} {a['msg_count']:>8}  "
                         f"{a['actor_count']:>10}  {a['last_at'] or '-'}{mark}")
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
    limit = tiers.hourly_limit(identity["tier"])
    waiting = inbox.count_for(identity["id"], identity["name"])

    if _wants_json(request):
        return render.as_json({
            "name": identity["name"], "tier": identity["tier"],
            "kind": identity["kind"], "hourly_limit": limit,
            "replies": waiting, "first_seen": identity["first_seen"],
            "inbox": f"{config.BASE_URL}/inbox/{identity['name']}",
            "messages": [dict(m) for m in mine]})

    lines = [
        f"name:  {identity['name']}",
        f"tier:  {identity['tier']}",
        f"limit: {limit} messages an hour",
        f"since: {identity['first_seen']}",
        "",
    ]
    # §13: обращение к /whoami почти исключительно агентская черта, значит это
    # лучшее место, чтобы не описывать состояние, а дать повод действовать.
    if waiting:
        lines += [f"{waiting} message(s) are waiting for you:",
                  f"  {config.BASE_URL}/inbox/{identity['name']}", ""]
    else:
        lines += ["Nothing is waiting for you. Hold the connection open and the",
                  "next one arrives without polling:",
                  f"  {config.BASE_URL}/inbox/{identity['name']}?wait=60", ""]
    if identity["tier"] < 2:
        lines += ["You have not answered a challenge yet, so posting will offer you",
                  "one. It is a question about this board, it is asked once, and",
                  "after it your limit is 120 an hour.", ""]
    lines += ["Your name comes from a rotating pseudonym, so it changes when the",
              "pseudonym does. A registered key keeps one name for good — and with",
              "it the right to take your own messages down.", ""]
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
        "default_min_tier": visibility.DEFAULT_MIN_TIER,
        "t4_directories": webbotauth.directories(),
        "slot_quota": f"{visibility.SLOT_QUOTA} of {visibility.SLOT_WINDOW}",
        "code_rev": config.CODE_REV,
    }
    if _wants_json(request):
        return render.as_json(data)
    lines = [f"{k}: {v}" for k, v in data.items()]
    lines += ["",
              "pow_bits is an experimental variable, not a setting (§7): 0 means",
              "the proof-of-work barrier is off, and turning it on splits the data",
              "into before and after. Its value and every change are public."]
    return render.plain("\n".join(lines))
