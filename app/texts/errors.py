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


def readonly():
    return ApiError(
        503, "readonly",
        "The board is in read-only mode: writing is switched off outside the\n"
        "application and no message is being accepted right now. Reading works\n"
        "normally, and /stats says why and since when.",
        retry_path="/stats", retry_params={},
    )
