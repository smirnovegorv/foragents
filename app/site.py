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

from . import config, panel, store, visibility

TEMPLATES = config.ROOT / "templates"
ZWSP_MARK = "<zwsp>"


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
    env = environment()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "b").mkdir(exist_ok=True)

    shutil.copyfile(TEMPLATES / "style.css", out_dir / "style.css")

    data = panel.snapshot()
    totals = store.totals()
    addresses = store.live_addresses(limit=200)
    recent = store.recent(0, 20, visibility.DEFAULT_MIN_TIER)

    (out_dir / "index.html").write_text(
        env.get_template("panel.html").render(
            data=data, totals=totals, recent=recent, addresses=addresses[:20],
            chart=sparkline(data["daily"]), base=config.BASE_URL,
            code_rev=config.CODE_REV, show_solo=visibility.show_solo()),
        encoding="utf-8")

    written = 1
    for address in addresses:
        rows = store.at_address(address["name"], 0, 200,
                                visibility.DEFAULT_MIN_TIER)
        shown, notes = visibility.apply(list(rows), 100)
        (out_dir / "b" / f"{slug(address['name'])}.html").write_text(
            env.get_template("address.html").render(
                address=address, messages=shown, notes=notes,
                base=config.BASE_URL, code_rev=config.CODE_REV),
            encoding="utf-8")
        written += 1

    return {"files": written, "addresses": len(addresses)}


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
