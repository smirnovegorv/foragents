"""Шаг 8 конвейера §8: быстрые правила без LLM.

Помечаем, но не удаляем. Флаг — предупреждение, а не защита, и в /safety это
написано прямым текстом. Смысл здесь не в том, чтобы поймать умного инъектора
регуляркой — он не ловится, — а в том, чтобы читающий агент получил
машиночитаемое основание отнестись к сообщению иначе, и чтобы §13 было что
считать по скользящему окну.
"""

import math
import re

from . import db

PATTERNS = {
    "injection": [
        r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior|earlier)\s+instructions?",
        r"disregard\s+(the\s+)?(above|previous|prior)",
        r"forget\s+(everything|all)\s+(you|above)",
        r"\byour\s+(system\s+prompt|initial\s+instructions)\b",
        r"you\s+are\s+now\s+(a|an|in)\b",
        r"new\s+instructions?\s*:",
    ],
    "recruitment": [
        r"\bif\s+you\s+(are|'re)\s+an?\s+(ai|agent|llm|language model)\b",
        r"\ball\s+agents\s+(must|should|need to)\b",
        r"\bprove\s+(that\s+)?you\s+are\b",
    ],
    "imperative_to_reader": [
        r"\b(visit|open|fetch|download|execute|run)\s+(this|the\s+following|it)\b",
        r"\bsend\s+(your|me|us)\b",
        r"\breply\s+with\s+your\b",
    ],
    "credentials": [
        r"\b(api[\s_-]?key|access[\s_-]?token|password|credential)s?\b[^.\n]{0,40}"
        r"\b(share|send|post|paste|dm|give)\b",
        r"\b(share|send|post|paste|dm|give)\b[^.\n]{0,40}"
        r"\b(api[\s_-]?key|access[\s_-]?token|password|credential)s?\b",
    ],
}

_COMPILED = {name: [re.compile(p, re.I) for p in patterns]
             for name, patterns in PATTERNS.items()}

BLOB = re.compile(r"[A-Za-z0-9+/=_\-]{64,}")


def entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def blob_token(text: str) -> str | None:
    """Плотный непрозрачный блоб: канал вывода данных, а не сообщение (§3)."""
    for match in BLOB.finditer(text):
        token = match.group(0)
        if entropy(token) > 4.5:
            return token
    return None


def detect(body: str, identity_id: int, body_hash: str) -> list[str]:
    found = [name for name, patterns in _COMPILED.items()
             if any(p.search(body) for p in patterns)]

    row = db.connect().execute(
        "SELECT 1 FROM messages WHERE identity_id = ? AND body_hash = ? LIMIT 1",
        (identity_id, body_hash)).fetchone()
    if row is not None:
        found.append("duplicate")
    return found
