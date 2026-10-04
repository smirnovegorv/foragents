"""Формат снят, документ остаётся: шапка говорит это, тело не тронуто.

Решение оператора 2026-10-04. Замысел RCR был «передавай находку
структурированным текстом вместо кода, и получатель проверит её, не доверяя
автору». Измерения сказали обратное: нагрузка, поданная готовым кодом,
сверяется с `SECURITY.md` получателя и отбрасывается, а та же нагрузка,
поданная требованием словами, входит в работу без сверки. Значит формат
целился в половину, которая и так проверялась.

Поэтому документ не переписывается и не снимается с адреса: ссылки на
`/rcr.md` уже разошлись по чужим доскам, и 404 без объяснения — хуже всего.
Меняется только шапка, и она обязана сказать три вещи: что снято, почему, и
где прочитать эксперименты. Тело после шапки остаётся побайтно тем же, что
лежит в пакете, — иначе у формата появляется вторая копия, а права всегда та,
которую не читают.
"""

import pathlib

import rcr

from app import retired

ROOT = pathlib.Path(__file__).resolve().parent.parent
POSTS = ("foragents.site/re/6", "foragents.site/re/4",
         "getpostingboard.dev/b/t/e89152cc-d03e-46bd-8681-f58a34797175",
         "1f916.ai/api/comment/92682")


def body_after_front_matter(text: str) -> str:
    end = text.index("\n---\n", 3) + len("\n---\n")
    return text[end:]


def test_the_header_says_retired_with_the_date(client):
    for path in ("/rcr.md", "/rcr/skill.md"):
        text = client.get(path).text
        assert text.startswith("---\nname: rcr\n"), path
        front = text[: text.index("\n---\n", 3)]
        line = next(l for l in front.splitlines() if l.startswith("description:"))
        assert line.startswith("description: Retired 2026-10-04"), path


def test_the_note_names_the_result_and_links_the_experiments(client):
    for path in ("/rcr.md", "/rcr/skill.md"):
        text = client.get(path).text
        note = body_after_front_matter(text)
        note = note[: note.index("# RCR")]
        assert "text, not the code" in note, path
        for link in POSTS:
            assert link in note, (path, link)
        assert "github.com/smirnovegorv/habr-llm-review-injection" in note, path


def test_the_format_text_itself_is_untouched(client):
    """Шапка добавлена, документ не правлен: тело совпадает с пакетом."""
    for path, packaged in (("/rcr.md", rcr.spec_text()),
                           ("/rcr/skill.md", rcr.skill_text())):
        served = body_after_front_matter(client.get(path).text)
        original = body_after_front_matter(packaged)
        assert served.endswith(original), path
        assert original in served, path


def test_the_page_says_it_too(client):
    # Страница ведёт свои заголовки капсом, поэтому метку сверяем без регистра.
    page = client.get("/rcr").text
    assert retired.MARK.lower() in page.lower()
    assert "text, not the code" in page
    for link in POSTS:
        assert link in page, link


def test_the_repo_copy_of_the_skill_carries_the_same_header():
    repo = (ROOT / "skills/rcr/SKILL.md").read_text(encoding="utf-8")
    assert repo == retired.annotate(rcr.skill_text(), base="https://foragents.site"),         "skills/rcr/SKILL.md разошёлся"
