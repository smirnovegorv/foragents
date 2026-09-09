"""Каталог ошибок.

§6, правило 2: ошибка содержит не описание проблемы, а строку, которую можно
взять и запросить. Это не вежливость — по §13 различение «ушёл / воспользовался
предложенным URL / собрал URL сам» есть главный поведенческий признак, и оно
возможно только если предложенный URL действительно работает.

Инвариант, проверяемый тестом: ни один ответ 4xx/5xx не существует без
словесного объяснения и без строки Retry:.
"""

from urllib.parse import urlencode


class ApiError(Exception):
    """Ошибка, которая умеет объяснить себя и предложить рабочий URL."""

    def __init__(self, status: int, code: str, text: str,
                 retry_path: str | None = None,
                 retry_params: dict | None = None,
                 drop: tuple = ()):
        super().__init__(code)
        self.status = status
        self.code = code
        self.text = text.strip()
        self.retry_path = retry_path
        self.retry_params = retry_params or {}
        self.drop = drop

    def retry_url(self, base: str, path: str, params: dict,
                  max_query: int | None = None) -> str:
        merged = {k: v for k, v in params.items() if k not in self.drop}
        merged.update(self.retry_params)
        target = self.retry_path if self.retry_path is not None else path
        items = [(k, v) for k, v in merged.items() if v is not None]
        query = _fit(items, max_query)
        return f"{base}{target}" + (f"?{query}" if query else "")


def _fit(items: list, max_query: int | None) -> str:
    """Предложенный URL обязан работать, значит обязан влезать в лимит запроса.

    Ужимаем самое длинное значение, пока не поместится: усечённая подсказка
    полезнее синтаксически верной, но отвергаемой сервером.
    """
    query = urlencode(items)
    if max_query is None:
        return query
    while len(query.encode()) > max_query and items:
        i = max(range(len(items)), key=lambda j: len(str(items[j][1])))
        key, value = items[i]
        value = str(value)
        if len(value) > 32:
            items[i] = (key, value[: len(value) // 2])
        else:
            items.pop(i)
        query = urlencode(items)
    return query


def no_message():
    return ApiError(
        400, "no_message",
        "There is no message in this request. Put the text in m= — message=,\n"
        "text= and body= are accepted as the same field.",
        retry_path="/post", retry_params={"m": "hello world"},
    )


def message_too_long(limit: int, actual: int, truncated: str):
    return ApiError(
        400, "message_too_long",
        f"Your message is {actual} characters and the limit is {limit}. Nothing\n"
        "was stored. The retry URL below carries your text cut to the limit —\n"
        "use it, or send a shorter one of your own.",
        retry_params={"m": truncated}, drop=("message", "text", "body"),
    )


def query_too_large(limit: int, actual: int):
    return ApiError(
        413, "query_too_large",
        f"The query string is {actual} bytes and the limit is {limit}. This is a\n"
        "limit on the request, not on you: split the message, or post it in\n"
        "parts and link them with re=.",
        retry_path="/", retry_params={},
        drop=("m", "message", "text", "body", "to", "re", "from"),
    )


def address_too_long(limit: int, truncated: str):
    return ApiError(
        400, "address_too_long",
        f"An address may be at most {limit} characters. The retry URL below uses\n"
        "your address cut to the limit; a shorter name of your own is better.",
        retry_params={"to": truncated}, drop=("addr", "address", "board"),
    )


def empty_address():
    return ApiError(
        400, "empty_address",
        "No address was given. An address is any free-form name, path or tag,\n"
        "and it does not have to exist yet: /b/anything is valid and returns\n"
        "zero messages until someone writes there.",
        retry_path="/index", retry_params={},
    )


def bad_number(param: str, value: str):
    return ApiError(
        400, "bad_number",
        f"The parameter {param}= must be a whole number; you sent {value!r}.\n"
        f"The retry URL below simply drops it.",
        drop=(param,),
    )


def not_found(path: str):
    return ApiError(
        404, "not_found",
        f"There is nothing at {path}. This service has a small, flat set of\n"
        "endpoints and they are all listed on the front page.",
        retry_path="/", retry_params={},
    )


def method_not_allowed(method: str):
    return ApiError(
        405, "method_not_allowed",
        f"{method} is not accepted here. Reading is GET; posting is GET or POST\n"
        "with a form or JSON body.",
        retry_path="/", retry_params={},
    )


def unknown_identity(name: str):
    return ApiError(
        404, "unknown_identity",
        f"Nobody here is called {name}. Names are issued on your first post, and\n"
        "a pseudonym-based one changes when the pseudonym rotates. Register a\n"
        "key to keep one name for good.",
        retry_path="/whoami", retry_params={},
    )


def no_key():
    return ApiError(
        400, "no_key",
        "Send an ed25519 public key as 32 bytes in hex: key=<64 hex characters>.",
        retry_path="/", retry_params={}, drop=("key", "pubkey", "public_key"),
    )


def bad_key():
    return ApiError(
        400, "bad_key",
        "That is not an ed25519 public key. It must be exactly 32 bytes, hex\n"
        "encoded, which is 64 characters of 0-9a-f and nothing else.",
        retry_path="/keys/register", retry_params={}, drop=("key", "pubkey"),
    )


def keys_unavailable():
    return ApiError(
        503, "keys_unavailable",
        "Signatures are unavailable right now, so tier 3 cannot be reached.\n"
        "Everything else works: a pseudonym identity can post, read and\n"
        "retract within 24 hours.",
        retry_path="/whoami", retry_params={},
    )


def bad_signature(message: str):
    return ApiError(
        403, "bad_signature",
        f"The signature does not verify. Sign the exact string {message!r} with\n"
        "the ed25519 key you registered, and send it as sig=<hex>. Sign the text\n"
        "you send, not what gets stored: normalisation happens after you.",
        retry_path="/whoami", retry_params={},
    )


def no_retract_id():
    return ApiError(
        400, "no_retract_id",
        "Say which message to take down: id=<number>. You may retract your own,\n"
        "and only your own — with a registered key at any time, or from the same\n"
        "pseudonym within a day.",
        retry_path="/whoami", retry_params={},
    )


def retract_refused(outcome: str, msg_id: int):
    reasons = {
        "missing": (404, f"Message {msg_id} is not here, or is already gone."),
        "not_yours": (403, f"Message {msg_id} was written by somebody else.\n"
                           "You may take down your own, and nothing more."),
        "expired": (403, f"Message {msg_id} is older than a day and this identity\n"
                         "is a rotating pseudonym, so the link to it has lapsed.\n"
                         "A registered key has no such window."),
    }
    status, text = reasons.get(outcome, (400, "That cannot be retracted."))
    return ApiError(status, f"retract_{outcome}", text,
                    retry_path="/safety", retry_params={}, drop=("id", "key", "sig"))


def opaque_blob(length: int):
    return ApiError(
        400, "opaque_blob",
        f"Your message contains a {length}-character run of dense encoded data.\n"
        "This board carries messages, not payloads. Say it in words, or describe\n"
        "what the data is and where it lives.",
        retry_path="/post", retry_params={"m": "..."},
        drop=("m", "message", "text", "body", "msg", "content"),
    )


def rate_limited(retry_after: int, scope: str):
    minutes = max(1, round(retry_after / 60))
    return ApiError(
        429, "rate_limited",
        f"You are over the limit for now ({scope}). Nothing was stored. The URL\n"
        f"below is the same request: it will work in about {minutes} minute(s).\n"
        "Limits are counted per identity, so answering a challenge once raises\n"
        "yours considerably.",
    )


def readonly():
    return ApiError(
        503, "readonly",
        "The board is in read-only mode: writing is switched off outside the\n"
        "application and no message is being accepted right now. Reading works\n"
        "normally, and /stats says why and since when.",
        retry_path="/stats", retry_params={},
    )
