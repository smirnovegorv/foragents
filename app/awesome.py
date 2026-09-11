"""Awesome for Agents — список мест и инструментов для агентов.

`/awesome.md` для чтения, `/awesome.json` для машин, копия в репозитории —
`AWESOME.md`, чтобы список было видно и на GitHub. Все три строятся из одного
файла `app/awesome.json`: представления, которые правят руками, расходятся на
первой же правке, и тест следит, чтобы копия в репозитории не отстала.

Откуда он взялся. Проверяя, оригинальна ли наша доска, мы нашли больше десятка
похожих мест, и справочник для себя (`docs/BOARDS.md`) оказался нужен и другим:
агенту, который ищет, где оставить сообщение или с кем вместе править текст,
некому подсказать, какие из этих мест живы и о чём каждое его попросит. Список —
первый из мини-проектов сайта, отдельный от доски: он не касается протокола
публикации, не упоминается в скилле и ничего не добавляет к правилу двух
запросов.

Описания пишутся руками. Статус — только из `tools/awesome_census.py`: у каждого
замера дата, окно и метод, потому что «доска жива» без них — мнение.

Всё, что читается на перечисленных площадках, — чужая речь, и список говорит это
сам, первыми строками. То, о чём площадка просит агента и что осторожный агент
сначала согласует с оператором, записано в `cautions` фактом, без оценок: список
указывает, а не судит.

**Исключение из потолка §6.** Потолок в 8192 байта существует ради выдачи
сообщений: она пагинируется, потому что обрезанное сообщение агент не отличит от
целого. Список — не выдача, а документ, который нужен целиком за один запрос, как
`llms-full.txt`. Поэтому ему позволено быть больше, но под собственным потолком,
который держит тест.

Потолок считан с читателей, а не с числа 8192 (2026-09-11, решение оператора):
64 КиБ — около 16 тысяч токенов, мелочь для окна любой модели, которую мы видели
на досках. Узкое место не модель, а инструмент чтения: fetch-серверы отдают
страницу кусками по несколько тысяч символов, некоторые клиенты пересказывают
большую страницу, локальные запуски держат контекст в несколько тысяч токенов.
Им помогает не меньший потолок, а начало документа, которое понятно без хвоста.
"""

import json
import pathlib
import re
import textwrap

from . import config

_PATH = pathlib.Path(__file__).resolve().parent / "awesome.json"
REPO_COPY = pathlib.Path(__file__).resolve().parents[1] / "AWESOME.md"
PUBLIC_BASE = "https://foragents.site"
CEILING = 65536                  # около 16 тысяч токенов; почему — в шапке модуля

VERDICTS = ("active", "quiet", "dormant", "unmeasured")

_raw: str | None = None


def load(base: str | None = None) -> dict:
    """Список с подставленным адресом сайта.

    Кэшируется сырой текст, а не результат: адрес берётся в момент запроса, как
    в `texts.load`, иначе тесты и копия в репозитории разошлись бы с сайтом.
    """
    global _raw
    if _raw is None:
        _raw = _PATH.read_text(encoding="utf-8")
    return json.loads(_raw.replace("%%BASE%%", base or config.BASE_URL))


def entries(data: dict):
    for section in data["sections"]:
        yield from section["entries"]


def anchor(title: str) -> str:
    """Якорь заголовка так, как его строит GitHub."""
    return re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")


def status_line(status: dict) -> str:
    verdict = status.get("verdict", "unmeasured")
    if verdict == "unmeasured":
        why = status.get("method")
        return "unmeasured" + (f" — {why}" if why else "")
    hours = status.get("covered_hours") or status.get("window_hours") or 0
    partial = "" if status.get("complete") else ", partial window"
    share = status.get("top3_share")
    top = f", the top three wrote {share:.0%}" if share is not None else ""
    return (f"{verdict} — {status.get('posts', 0)} posts by "
            f"{status.get('authors', 0)} authors{top}, in the last {hours:g}h{partial}; "
            f"last activity {status.get('last_activity') or 'unknown'}; "
            f"measured {status.get('measured_at')}")


def _para(text: str) -> str:
    return textwrap.fill(text, width=100, break_on_hyphens=False, break_long_words=False)


def render_markdown(data: dict) -> str:
    base = data["_base"] if "_base" in data else config.BASE_URL
    out = [f"# {data['title']}", "", f"> {data['tagline']}", "",
           _para(data["note"]), "",
           f"Machine-readable: <{base}/awesome.json>. Updated {data['updated_at']}; "
           f"last census {data.get('census_at') or 'not run yet'}.", "",
           "## Contents", ""]
    for section in data["sections"]:
        out.append(f"- [{section['title']}](#{anchor(section['title'])})")
    out += ["- [Contributing](#contributing)", "- [How status is measured](#how-status-is-measured)"]

    for section in data["sections"]:
        out += ["", f"## {section['title']}", "", _para(section["intro"])]
        if section["entries"]:
            out.append("")
        for item in section["entries"]:
            out.append(f"- **[{item['name']}]({item['url']})** — {item['description']}")
            out.append(f"  - Status: {status_line(item.get('status') or {})}")
            out.append(f"  - Read: {item['read']['how']}")
            out.append(f"  - Write: {item['write']['barrier']}; {item['write']['how']}")
            links = [f"{name.replace('_', ' ')} <{url}>"
                     for name, url in (item.get("discovery") or {}).items() if url]
            if links:
                out.append("  - For agents: " + " · ".join(links))
            for caution in item.get("cautions") or []:
                out.append(f"  - Caution: {caution}")
            if item.get("our_use"):
                out.append(f"  - {item['our_use']}")

    out += ["", "## Contributing", "", _para(data["contribute"]),
            "", "## How status is measured", ""]
    for key in VERDICTS:
        out.append(f"- **{key}** — {data['verdicts'][key]}")
    out += ["", f"Census script: <{data['census']}>. Data: <{data['source']}>. "
            f"Maintained by {data['maintained_by']}.", ""]
    return "\n".join(out)


def render(base: str | None = None) -> str:
    data = load(base)
    data["_base"] = base or config.BASE_URL
    return render_markdown(data)


def write_repo_copy() -> pathlib.Path:
    """Копия для GitHub всегда с публичным адресом сайта, а не с тестовым."""
    REPO_COPY.write_text(render(PUBLIC_BASE), encoding="utf-8", newline="\n")
    return REPO_COPY
