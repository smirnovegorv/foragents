"""Тексты ответов API.

Вынесено отдельным каталогом намеренно (§16.6): это единственный канал влияния
на поведение агентов, и смена формулировки делает данные до и после
несравнимыми. История правок должна читаться из `git log app/texts/`.

Подстановка — не str.format: тексты полны фигурных скобок в примерах URL.
"""

import pathlib

from .. import config

_DIR = pathlib.Path(__file__).resolve().parent
_cache: dict[str, str] = {}


def load(name: str, **subs: str) -> str:
    if name not in _cache:
        _cache[name] = (_DIR / f"{name}.txt").read_text(encoding="utf-8")
    out = _cache[name]
    subs = {"BASE": config.BASE_URL, "CODE_REV": config.CODE_REV, **subs}
    for key, value in subs.items():
        out = out.replace(f"%%{key}%%", str(value))
    return out
