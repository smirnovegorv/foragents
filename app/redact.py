"""Шаг 5 конвейера §8: редакция секретов и ПД (таблица §9).

Обязана отработать до первой записи на диск. В базе остаётся только факт —
{"SECRET": 1, "EMAIL": 2}, — а не значение: по §9 взлом сервера не должен
ничего давать, и это достигается тем, что красть уже нечего.

Порядок правил значим: длинные и структурные детекторы идут первыми, иначе
приватный ключ будет разобран на куски детектором base64, а номер карты —
детектором телефона.
"""

import re

PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.S)

# Префиксы известных провайдеров (§9). Список заведомо неполон и это нормально:
# полнота недостижима, а каждая пойманная утечка дешевле любой пропущенной.
API_KEY = re.compile(
    r"\b("
    r"sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|xoxb-[A-Za-z0-9\-]{10,}|xoxp-[A-Za-z0-9\-]{10,}"
    r"|AIza[A-Za-z0-9_\-]{30,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|glpat-[A-Za-z0-9_\-]{16,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r")")

IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,26}\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\w.])\+[1-9]\d{7,14}(?![\w.])")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}\b")
WALLET = re.compile(
    r"\b(?:bc1[a-z0-9]{20,}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|0x[a-fA-F0-9]{40}|T[A-Za-z1-9]{33})\b")
DOCUMENT = re.compile(r"\b\d{4}\s?\d{6}\b|\b[A-Z]{2}\d{7}\b")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def redact(text: str) -> tuple[str, dict]:
    counts: dict[str, int] = {}

    def swap(pattern, placeholder, source, guard=None):
        def replace(match):
            if guard and not guard(match.group(0)):
                return match.group(0)
            counts[placeholder] = counts.get(placeholder, 0) + 1
            return f"[{placeholder}]"
        return pattern.sub(replace, source)

    text = swap(PRIVATE_KEY, "SECRET", text)
    text = swap(API_KEY, "SECRET", text)
    text = swap(IBAN, "FINANCIAL", text)
    text = swap(CARD, "FINANCIAL", text,
                guard=lambda s: _luhn(re.sub(r"\D", "", s)))
    text = swap(EMAIL, "EMAIL", text)
    text = swap(PHONE, "PHONE", text)
    text = swap(IPV6, "IP", text)
    text = swap(IPV4, "IP", text,
                guard=lambda s: all(int(p) < 256 for p in s.split(".")))
    text = swap(WALLET, "WALLET", text)
    text = swap(DOCUMENT, "ID", text)
    return text, counts
