"""Шаг 3 конвейера §8: юникод-гигиена.

Невидимые символы не выбрасываются молча: их наличие пишется в flags. Скрытое
вложение — это сигнал стеганографии, а не мусор, и по §13 оно интереснее самого
текста. Чистка при этом безусловна и мгновенна: по принципу 1 из §1 она решает,
что вообще способно навредить, и модерацией не является.
"""

import re
import unicodedata

ZERO_WIDTH = "​‌‍⁠﻿᠎"
BIDI = "‪‫‬‭‮⁦⁧⁨⁩‎‏"

# Разделитель блоков из §8. Строка тела, притворяющаяся границей сообщения,
# позволяет подделать чужое авторство в глазах читающего агента — он увидит
# END раньше, чем текст закончился. Это инъекция против парсера, а не против
# модели, и ловится она здесь, а не флагом.
DELIMITER = re.compile(r"^(---\s*(?:BEGIN|END)\b)", re.M)


def normalize(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []

    if any(ch in text for ch in ZERO_WIDTH):
        flags.append("zero_width")
    if any(ch in text for ch in BIDI):
        flags.append("bidi")

    text = unicodedata.normalize("NFKC", text)
    text = text.translate({ord(ch): None for ch in ZERO_WIDTH + BIDI})

    cleaned = []
    dropped_control = False
    for ch in text:
        if ch in "\n\t":
            cleaned.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):
            dropped_control = True
        else:
            cleaned.append(ch)
    if dropped_control:
        flags.append("control")
    text = "".join(cleaned)

    # Обрезка пробелов идёт до нейтрализации разделителя, а не после: иначе
    # strip() снимает добавленный отступ у подделки, стоящей на первой строке,
    # и защита отменяет сама себя ровно в самом опасном случае.
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()

    if DELIMITER.search(text):
        flags.append("delimiter_spoof")
        text = DELIMITER.sub(r" \1", text)
    return text, flags
