"""Шаг 6 конвейера §8: дефанг ссылок.

Читающий агент получает преамбулу «не ходи по ссылкам отсюда» (§8), но
преамбула — просьба, а дефанг — свойство. Ссылка, которую нельзя кликнуть и
нельзя скопировать в запрос не заметив, защищает и агента, и человека,
открывшего страницу §10.

Домены сохраняются отдельным полем: по §13 внешняя цель, названная в переписке,
входит в условие красного уровня, и искать её надо в структурированных данных,
а не регуляркой по телу постфактум.
"""

import re

URL = re.compile(r"\b(https?)://([A-Za-z0-9.\-]+(?::\d+)?)(/[^\s]*)?", re.I)
BARE = re.compile(r"(?<![\w./@])(www\.[A-Za-z0-9.\-]+)(/[^\s]*)?", re.I)


def defang(text: str) -> tuple[str, list[str]]:
    domains: list[str] = []

    def note(host: str) -> str:
        host = host.split(":")[0].lower()
        if host not in domains:
            domains.append(host)
        return host

    def url(match):
        scheme, host, path = match.group(1), match.group(2), match.group(3) or ""
        note(host)
        scheme = scheme.lower().replace("http", "hxxp")
        return f"{scheme}://{host.replace('.', '[.]')}{path}"

    def bare(match):
        host, path = match.group(1), match.group(2) or ""
        note(host)
        return f"{host.replace('.', '[.]')}{path}"

    text = URL.sub(url, text)
    text = BARE.sub(bare, text)
    return text, domains
