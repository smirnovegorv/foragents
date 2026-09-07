"""Мелкие общие функции."""

import datetime
import hashlib

try:
    import regex as _regex
except ImportError:  # pragma: no cover - regex есть в requirements
    _regex = None


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_key() -> str:
    """Сутки для ротации ключа псевдонимизации (§9)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def graphemes(s: str) -> int:
    """Длина в графемах (§8 шаг 4), а не в кодовых точках.

    Без regex деградируем до кодовых точек: это завышает оценку длины,
    то есть ошибается в сторону отказа, а не в сторону пропуска.
    """
    if _regex is None:
        return len(s)
    return len(_regex.findall(r"\X", s))


def truncate_graphemes(s: str, n: int) -> str:
    if _regex is None:
        return s[:n]
    return "".join(_regex.findall(r"\X", s)[:n])
