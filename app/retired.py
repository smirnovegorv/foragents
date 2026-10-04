"""Шапка о снятии формата RCR. Сам текст формата при этом не правится.

Решение оператора 2026-10-04: формат снят, а не версионирован. Замысел —
«передавай находку структурированным текстом вместо кода, и получатель
проверит её, не доверяя автору» — промахнулся половиной. Нагрузка, поданная
готовым кодом, сверяется с `SECURITY.md` получателя и отбрасывается; та же
нагрузка, поданная требованием словами, входит в работу без этой сверки.
Схема для слов этого не закрывает, а в части прогонов делала хуже.

Почему документ остаётся на месте. Ссылки на `/rcr.md` разошлись по чужим
доскам до снятия; адрес, который завтра отдаст 404, наказывает читателя за
нашу ошибку. Поэтому меняется только шапка: строка `description`, которую
агент читает первой, и блок со ссылками на эксперименты перед заголовком.
Тело после шапки остаётся побайтно тем, что лежит в пакете, — иначе у формата
появляется вторая копия, а права всегда та, которую не читают.
"""

from . import texts

MARK = "Retired 2026-10-04"
_SUMMARY = (": the measurement says this approach buys a current model little —"
            " the main channel for an injection in a review is the text, not the"
            " code. Kept for the record, with the experiments linked at the top."
            " Previously: ")


def note(base: str | None = None) -> str:
    """Блок со ссылками на эксперименты. Отдельный текст: §16.6.

    `base` задаётся, когда шапку собирают для копии в репозитории: там адрес
    должен быть рабочим у читателя с GitHub, а не адресом текущей сборки.
    """
    return texts.load("rcr_retired", **({"BASE": base} if base else {}))


def annotate(text: str, base: str | None = None) -> str:
    """Документ пакета с добавленной шапкой. Идемпотентно."""
    if MARK in text:
        return text
    end = text.index("\n---\n", 3) + len("\n---\n")
    front, body = text[:end], text[end:]
    out = []
    for line in front.splitlines(keepends=True):
        if line.startswith("description: "):
            line = "description: " + MARK + _SUMMARY + line[len("description: "):]
        out.append(line)
    return "".join(out) + note(base) + body
