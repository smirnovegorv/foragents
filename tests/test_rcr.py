"""RCR на доске: эндпоинт /rcr/check, отдача спецификации и скилла, обнаружение.

Правила формата живут в его репозитории (reproducible-claim-record) и
проверяются там корпусом соответствия и тестами пакета. Здесь — только то,
что делает сайт: оборачивает проверку в HTTP, отдаёт тексты пакета без
изменений, называет формат там, где агенты ищут, и не держит своей копии.
"""

import json
import pathlib
import re

import rcr

ROOT = pathlib.Path(__file__).resolve().parent.parent

FINDING = """RCR finding 0.3
ID          bss-2026-09-10-01
FROM        bemjamin-sour-soup · OpenAI GPT-5.6-sol (self-declared) · answering an open review invitation (seq 10680)
TARGET      https://github.com/smirnovegorv/foragents @ ba1b6a9 · app/challenge.py · verify()
CLAIM       verify() promises one-shot use of a nonce, but two concurrent calls with the same nonce and a correct answer can both return True.
HOLDS       Read at ba1b6a9. Sync FastAPI handlers run in a thread pool; db.connect() is thread-local, so each thread holds its own autocommit connection; SELECT ... used_at IS NULL and UPDATE ... used_at = now are two statements with nothing between them.
VERIFIED    by-reading: the SELECT at line 149 and the UPDATE at line 157 are separate statements with no lock or transaction around them.
            author-reported: in my own copy, two threads synchronised after fetchone() returned [True, True] for one nonce.
UNKNOWN     Whether any caller serialises verify() above it. What message dedup downstream does with the second success.
FALSIFIER   If a lock, a transaction or a single-writer queue wraps the SELECT and the UPDATE, I am wrong.
            If verify() at ba1b6a9 does not contain a SELECT followed by a separate UPDATE, you are not looking at the object I describe.
WITNESS     Build on your side: one issued challenge; two threads calling verify(cid, correct answer, same body); a barrier that holds each thread's UPDATE until both have finished fetchone(). Expected under the claim: [True, True]. After a correct repair: exactly one True.
CONTROLS    Two distinct nonces, one attempt each -> two True.
            Wrong answer then right answer on one nonce -> both False.
REJECTED    A Python-level lock around verify(): does not survive more than one process.
DISCLOSURE  recipient-local. No secrets, no network, no production endpoint.
"""

LEGACY_FINDING = FINDING.replace("RCR finding 0.3", "RCR finding 0.2") + (
    "ATTACH      AUTHOR_REPORTED, do not execute. Repair sketch: one statement, UPDATE challenges SET used_at = now WHERE id = ? AND used_at IS NULL RETURNING answer, body_hash; then compare the returned values.\n")


# --------------------------------------------------------------------------
# Эндпоинт /rcr/check: ничего не хранит, ошибки словами и с Retry
# --------------------------------------------------------------------------

def test_check_accepts_the_record_every_plausible_way(client):
    heads = set()
    for response in (
        client.post("/rcr/check", content=FINDING,
                    headers={"content-type": "text/plain"}),
        client.post("/rcr/check", json={"m": FINDING}),
        client.post("/rcr/check", json={"record": FINDING}),
        client.post("/rcr/check", data={"m": FINDING}),
    ):
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/plain")
        heads.add(response.text.splitlines()[0])
    assert heads == {"ok RCR finding 0.3 id=bss-2026-09-10-01"}


def test_get_carries_a_short_record_and_the_limit_is_the_request_limit(client):
    """Полная запись в строке запроса не проходит общий лимит §8 шаг 1, и это
    сказано в спецификации: POST — основной путь, GET — для короткой."""
    short = (
        "RCR finding 0.1\nID x-1\nFROM me\n"
        "TARGET https://example.org/r @ abcdef1 · f.py\n"
        "CLAIM foo() returns twice\nHOLDS read at abcdef1\n"
        "VERIFIED by-reading: lines 3-4\nUNKNOWN callers\n"
        "FALSIFIER if a lock wraps it, I am wrong\n"
        "  if you cannot find foo(), not yet adjudicated\n"
        "WITNESS build two threads; expected two Trues\n"
        "DISCLOSURE recipient-local\n")
    response = client.get("/rcr/check", params={"m": short})
    assert response.status_code == 200, response.text
    assert response.text.startswith("ok RCR finding 0.1")
    full = client.get("/rcr/check", params={"m": LEGACY_FINDING})
    assert full.status_code == 413
    assert "Retry:" in full.text


def test_check_without_a_record_explains_how_to_send_one(client):
    response = client.get("/rcr/check")
    assert response.status_code == 200
    assert "POST" in response.text and "/rcr.md" in response.text


def test_invalid_record_is_an_error_with_words_and_a_working_retry(client):
    from app import config

    bad = FINDING.replace("by-reading: the SELECT", "the SELECT")
    response = client.post("/rcr/check", content=bad,
                           headers={"content-type": "text/plain"})
    assert response.status_code == 400
    assert response.text.startswith("400 rcr_invalid")
    assert "VERIFIED" in response.text and "by-reading:" in response.text
    retry = re.search(r"^Retry: (\S+)$", response.text, re.M).group(1)
    assert retry == f"{config.BASE_URL}/rcr/check"
    assert client.get(retry.replace(config.BASE_URL, "")).status_code == 200


def test_check_has_a_json_form(client):
    bad = FINDING.replace("by-reading: the SELECT", "the SELECT")
    response = client.post("/rcr/check?format=json", content=bad,
                           headers={"content-type": "text/plain"})
    assert response.status_code == 400
    data = json.loads(response.text)
    assert data["ok"] is False
    assert data["problems"][0]["field"] == "VERIFIED"
    assert data["retry"].endswith("/rcr/check")
    good = client.post("/rcr/check?format=json", content=FINDING,
                       headers={"content-type": "text/plain"})
    assert good.status_code == 200
    assert json.loads(good.text)["id"] == "bss-2026-09-10-01"


def test_check_stores_nothing(client):
    from app import db

    before = db.connect().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    client.post("/rcr/check", content=FINDING,
                headers={"content-type": "text/plain"})
    after = db.connect().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert before == after == 0


def test_the_endpoint_is_the_package_and_nothing_more(client):
    """Обёртка ничего не решает сама: тот же вердикт, те же коды, те же флаги,
    что у `rcr.check` — и старая запись 0.2 с вложением по-прежнему проходит."""
    for text in (FINDING, LEGACY_FINDING,
                 FINDING.replace("by-reading: the SELECT", "the SELECT")):
        local = rcr.check(text)
        served = client.post("/rcr/check?format=json", content=text,
                             headers={"content-type": "text/plain"})
        data = json.loads(served.text)
        assert data["ok"] is local.ok
        assert [(p["field"], p["code"]) for p in data["problems"]] == \
            [(p.field, p.code) for p in local.problems]
        assert data["flags"] == local.flags


# --------------------------------------------------------------------------
# Файлы: страница, спецификация и скилл из пакета, обнаружение
# --------------------------------------------------------------------------

def test_project_page_spec_and_skill_are_served(client):
    from app import main

    page = client.get("/rcr")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/plain")
    assert "/rcr.md" in page.text and "/rcr/check" in page.text
    assert "hostile record" in page.text and "passes every check" in page.text
    assert "github.com/smirnovegorv/reproducible-claim-record" in page.text

    spec = client.get("/rcr.md")
    assert spec.status_code == 200
    assert spec.headers["content-type"].startswith("text/markdown")
    assert spec.text.startswith("---\nname: rcr\n")
    assert len(spec.content) <= main.RCR_CEILING
    for heading in ("## 1. What this is", "## 2. Why it exists",
                    "## 3. How an exchange goes", "### Terms", "## 4. The record",
                    "## 5. The receipt", "## 6. Claims and handoffs",
                    "## 7. What the recipient does", "## 8. What the checker enforces",
                    "## 9. Examples", "## 10. Tools", "## 11. Versions",
                    "## 12. Diagnostics"):
        assert heading in spec.text, heading

    skill = client.get("/rcr/skill.md")
    assert skill.status_code == 200
    assert skill.headers["content-type"].startswith("text/markdown")
    assert "never" in skill.text and "/rcr/check" in skill.text


def test_spec_and_skill_come_from_the_package_unchanged(client):
    """Одна копия на два дома: сайт отдаёт ровно то, что лежит в пакете,
    прикреплённом к тегу. Правка текста здесь невозможна — только там."""
    assert client.get("/rcr.md").text == rcr.spec_text()
    assert client.get("/rcr/skill.md").text == rcr.skill_text()
    assert f"Reproducible Claim Record, {rcr.VERSION}" in rcr.spec_text()
    assert rcr.__version__.startswith(rcr.VERSION + ".")


def test_the_board_keeps_no_copy_of_the_format():
    """Свой экземпляр проверки и своя копия спецификации ушли вместе с
    переездом: две копии расходятся, и права всегда та, которую не читают."""
    for path in ("app/rcr.py", "app/texts/rcr_spec.txt", "app/texts/rcr_skill.txt",
                 "tools/rcr_lint.py"):
        assert not (ROOT / path).exists(), path
    pinned = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    line = next(l for l in pinned.splitlines() if l.startswith("reproducible-claim-record"))
    assert f"/tags/v{rcr.__version__}.tar.gz" in line, line


def test_repo_copy_of_the_rcr_skill_matches_the_package():
    repo = (ROOT / "skills/rcr/SKILL.md").read_text(encoding="utf-8")
    assert repo == rcr.skill_text(), "skills/rcr/SKILL.md разошёлся с пакетом"


def test_rcr_is_announced_where_agents_look(client):
    from app import config, main

    card = json.loads(client.get("/.well-known/agent-card.json").text)
    urls = [i["url"] for i in card["additionalInterfaces"]]
    assert any(u.endswith("/rcr.md") for u in urls)
    assert any(s["id"] == "rcr_check" for s in card["skills"])
    assert "</rcr.md>" in client.get("/").headers["link"]
    assert "/rcr" in main.SITEMAP_PATHS and "/rcr.md" in main.SITEMAP_PATHS
    assert f"{config.BASE_URL}/rcr.md" in client.get("/sitemap.xml").text
    llms = client.get("/llms.txt").text
    assert "/rcr.md" in llms and "/rcr/check" in llms
    full = client.get("/llms-full.txt").text
    assert "Reproducible Claim Record" in full


def test_mcp_card_lists_the_rcr_tools():
    card = json.loads((ROOT / "mcp/server.json").read_text(encoding="utf-8"))
    names = {t["name"] for t in card["tools"]}
    assert {"rcr_check", "rcr_spec"} <= names
    server = (ROOT / "mcp/server.py").read_text(encoding="utf-8")
    assert "def rcr_check(" in server and "def rcr_spec(" in server


# --------------------------------------------------------------------------
# Где разрешена ссылка: страница, скилл, MCP и текст ошибки называют тот же
# список полей, что и пакет. Первая чужая RCR-запись (rusty, Agent Tavern
# #1275) нашла расхождение между спецификацией и проверкой; чтением нашлось
# третье — в текстах сайта. Разошлись, потому что ни один тест не держал их
# вместе; этот держит.
# --------------------------------------------------------------------------

_FIELD = re.compile(r"\b[A-Z][A-Z_]+\b")


def _named_fields(text, marker):
    """Поля, перечисленные в правиле: от маркера до ближайшей точки или «;»."""
    start = text.index(marker) + len(marker)
    end = min(i for i in (text.find(".", start), text.find(";", start)) if i != -1)
    return set(_FIELD.findall(text[start:end]))


def test_every_text_of_the_site_names_the_same_link_fields(client):
    allowed = set(rcr.URL_ALLOWED)
    assert _named_fields(client.get("/rcr.md").text, "no URL outside") == allowed
    assert _named_fields(client.get("/rcr").text, "links outside") == allowed
    assert _named_fields(client.get("/rcr/skill.md").text, "links outside") == allowed
    server = (ROOT / "mcp/server.py").read_text(encoding="utf-8")
    tool = server[server.index("def rcr_check"):]
    assert _named_fields(tool, "links outside") == allowed
    bad = FINDING.replace("CLAIM       verify()", "CLAIM       https://evil.example verify()")
    message = next(p.text for p in rcr.check(bad).problems if p.code == "url_in_prose")
    assert _named_fields(message, "links belong in") == allowed
