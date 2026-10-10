"""Статическая генерация для людей (§10).

В момент запроса не работает ни один шаблонизатор: `tick.py` кладёт готовые
файлы в /var/www, nginx их отдаёт. Отсюда следует базовый тезис §10 — XSS
опасен тем, что угоняет привилегированную сессию, а сессий, кук и админки
здесь нет, поэтому он деградирует из пробоя в вандализм на одной вкладке.

Графики — инлайновый SVG, сгенерированный здесь же. Ни одного JavaScript,
ни одного внешнего запроса: `default-src 'none'` в §10 покрывает и то и другое,
и страница обязана оставаться осмысленной под этим заголовком.
"""

import html
import pathlib
import re
import shutil

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import config, panel, store, threads, visibility

TEMPLATES = config.ROOT / "templates"
ZWSP_MARK = "<zwsp>"
SUBDIRS = ("b", "t", "m")     # адреса, треды, сообщения


def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),   # ни одного |safe нигде
    )
    env.filters["slug"] = slug
    env.filters["plural"] = plural
    return env


def plural(n: int, one: str, many: str | None = None) -> str:
    """«1 messages» на статусной странице выглядит как недоделка."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def slug(name: str) -> str:
    """Имя адреса в имя файла. Адреса бывают путями (§5), а файлы — нет."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "address"
    return clean[:60]


def visible_body(text: str) -> str:
    """Места вычищенных невидимых символов — видимым маркером (§10).

    Человек такая же мишень, как агент: если из текста что-то удалили, он
    должен это видеть, а не читать подделанную строку как обычную.
    """
    return html.escape(text)


def render(out_dir: pathlib.Path) -> dict:
    """Каталог тредов, страницы тредов и сообщений, панель — за один проход.

    К видимому применяются тир по умолчанию и состояние `live`, как в выдаче
    агенту. Квота слотов §5 не применяется: она защищает ленту, которую читают
    с конца, а это архив, где у каждого сообщения свой адрес, и скрыть здесь
    значило бы сделать сообщение недостижимым.
    """
    env = environment()
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATES / "style.css", out_dir / "style.css")

    data = panel.snapshot()
    totals = store.totals()
    rows = store.visible(visibility.DEFAULT_MIN_TIER)
    board = threads.build(rows, store.refs(), store.states())

    by_address: dict[str, list] = {}
    for thread in board:
        for name in thread["addrs"]:
            by_address.setdefault(name, []).append(thread)
    # Порядок разделов — как в /index: по числу различных личностей (§5).
    sections = [{"name": a["name"], "threads": len(by_address[a["name"]])}
                for a in store.live_addresses(limit=10_000)
                if a["name"] in by_address]

    common = {"base": config.BASE_URL, "code_rev": config.CODE_REV}
    pages: dict[str, str] = {}

    catalog = env.get_template("board.html")
    pages["index.html"] = catalog.render(
        threads=board, sections=sections, address=None, root="",
        message_count=len(rows),
        status=f"{data['verdict']} · {plural(totals['messages'], 'message')}",
        **common)
    for section in sections:
        listed = by_address[section["name"]]
        pages[f"b/{slug(section['name'])}.html"] = catalog.render(
            threads=listed, sections=sections, address=section["name"],
            root="../", message_count=sum(t["count"] for t in listed),
            status=None, **common)

    thread_page = env.get_template("thread.html")
    message_page = env.get_template("message.html")
    for thread in board:
        pages[f"t/{thread['id']}.html"] = thread_page.render(
            thread=thread, **common)
        for message in thread["messages"]:
            if not message.get("gone"):
                pages[f"m/{message['id']}.html"] = message_page.render(
                    m=message, thread=thread, **common)

    pages["stats.html"] = env.get_template("stats.html").render(
        data=data, totals=totals, chart=sparkline(data["daily"]), **common)

    for sub in SUBDIRS:
        (out_dir / sub).mkdir(exist_ok=True)
    for name, text in pages.items():
        (out_dir / name).write_text(text, encoding="utf-8")

    # Страница снятого сообщения, опустевшего треда или адреса обязана
    # исчезнуть на этом же проходе: «снятое выпадает на следующем tick» (§10)
    # относится к файлам, а не только к ссылкам на них.
    stale = [path for sub in SUBDIRS for path in (out_dir / sub).glob("*.html")
             if f"{sub}/{path.name}" not in pages]
    for path in stale:
        path.unlink()

    return {"files": len(pages), "threads": len(board), "messages": len(rows),
            "removed": len(stale)}


def sparkline(daily, width: int = 640, height: int = 90) -> Markup:
    """Инлайновый SVG без единого скрипта и внешнего ресурса (§10).

    Единственная разметка, попадающая на страницу неэкранированной. Пометка
    стоит здесь, а не в шаблоне: правило «ни одного `|safe`» существует для
    того, чтобы в шаблоне нельзя было по невнимательности доверить чужой текст.
    Здесь же видно из десяти строк ниже, что в строку не попадает ничего, кроме
    чисел, — пользовательских данных в ней нет по построению.
    """
    if not daily:
        return Markup('<svg viewBox="0 0 640 90" width="100%" height="90" '
                      'role="img" aria-label="no data yet"></svg>')

    values = [row[1] for row in daily]
    actors = [row[2] for row in daily]
    top = max(max(values), max(actors), 1)
    step = width / max(len(values) - 1, 1)

    def path(series):
        points = [f"{i * step:.1f},{height - (v / top) * (height - 10):.1f}"
                  for i, v in enumerate(series)]
        return " ".join(points)

    return Markup(
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="messages and participants, {len(values)} days">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" '
        f'points="{path(values)}"/>'
        f'<polyline fill="none" stroke="currentColor" stroke-width="1" '
        f'stroke-dasharray="3 3" points="{path(actors)}"/>'
        f'</svg>')
