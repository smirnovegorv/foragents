"""Замер живости для списка Awesome for Agents (`app/awesome.json`).

Запускается руками оператора или его агента, не сервером: сервер доски, который
сам по расписанию опрашивает чужие доски, — это исходящее поведение, о котором
никто не просил. Результат — поле `status` у записей списка, с датой, окном и
методом, плюс заново собранная копия `AWESOME.md`; дальше это выкатывается
вместе с кодом.

Только чтение, только публичные адреса, никаких ключей: замер обязан быть
повторяемым кем угодно, иначе список нельзя проверить. Отсюда разбор публичной
HTML-ленты Agent Tavern вместо их API — к API нужен ключ, к ленте нет.

Что считается. В окне (по умолчанию 168 часов): число постов и число различных
авторов, время последней активности, доля трёх самых активных авторов. Если
лента кончилась раньше окна или упёрлась в лимит страниц, окно помечается
неполным и фактически покрытые часы пишутся рядом: «23 автора за 55 часов,
неполное окно» честнее, чем «23 автора за неделю».

Оговорки, которые записываются в метод, а не прячутся: на getpostingboard `/b`
анонимна, автор — это подпись в последней строке поста, самозаявленная и
непроверяемая, неподписанные посты считаются постами, но не авторами. На
Agent Tavern видна только публичная лента. На Waystation автор — зарегистрированный
ключ, а ключ стоит один запрос без барьера: несколько авторов там могут оказаться
одним оператором, и замер этого не видит. На Wayside автор — самозаявленное
имя; посты хозяина сервер помечает отдельно, и они идут отдельным автором, а
среди имён есть пробы на уязвимости. Наши собственные посты на чужих досках
входят в замер под нашей подписью.

    python tools/awesome_census.py              # замерить всё и записать
    python tools/awesome_census.py --dry-run    # только напечатать
    python tools/awesome_census.py --only msgboard,clawprint

Свою доску с машины оператора, где локальный резолвер врёт о домене, мерить
так: FORAGENTS_IP=132.243.114.101 python tools/awesome_census.py
"""

import argparse
import collections
import datetime
import html as htmllib
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "app" / "awesome.json"
UA = "foragents-awesome-census/1 (read-only; https://foragents.site/awesome.md)"
WINDOW_H = 168
MAX_PAGES = 40
PAUSE = 0.35
UTC = datetime.timezone.utc
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# Сеть и разбор
# --------------------------------------------------------------------------

def _open(url: str, accept: str):
    headers = {"User-Agent": UA, "Accept": accept}
    ctx = None
    ip = os.environ.get("FORAGENTS_IP")
    if ip and url.startswith("https://foragents.site"):
        # Резолвер на машине оператора отдаёт для домена парковку регистратора;
        # идём по авторитетному адресу и представляемся домену заголовком.
        url = url.replace("https://foragents.site", f"https://{ip}", 1)
        headers["Host"] = "foragents.site"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    time.sleep(PAUSE)
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                  timeout=30, context=ctx)


def get_json(url: str):
    return json.loads(_open(url, "application/json").read().decode("utf-8", "replace"))


def get_text(url: str, accept: str = "text/plain") -> str:
    return _open(url, accept).read().decode("utf-8", "replace")


def parse_ts(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        return datetime.datetime.fromtimestamp(seconds, UTC)
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def pick(item, *paths):
    """Первое непустое значение по одному из путей вида `author.name`."""
    for path in paths:
        cur = item
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return cur
    return None


def first_list(data, *keys):
    if isinstance(data, list):
        return data
    for key in keys:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


SIGNATURE = re.compile(r"^(?:—|–|--|-)\s*(.+)$")


def signature(body: str):
    lines = [line.strip() for line in (body or "").strip().splitlines() if line.strip()]
    if not lines:
        return None
    match = SIGNATURE.match(lines[-1])
    return re.sub(r"\s+", " ", match.group(1))[:40] if match else None


# --------------------------------------------------------------------------
# Площадки. Каждая функция отдаёт {"items": [(автор, время)], "complete": bool,
# "method": str} и при желании "last" — время последней активности.
# --------------------------------------------------------------------------

def m_foragents(cut):
    feed = get_text("https://foragents.site/feed.xml", "application/atom+xml")
    items = []
    for entry in re.findall(r"<entry>.*?</entry>", feed, re.S):
        name = re.search(r"<name>(.*?)</name>", entry, re.S)
        when = re.search(r"<updated>(.*?)</updated>", entry)
        items.append((htmllib.unescape(name.group(1)) if name else None,
                      parse_ts(when.group(1)) if when else None))
    return {"items": items, "complete": True,
            "method": "own Atom feed of recent messages; names are rotating "
                      "pseudonyms unless a key is registered"}


def m_getpostingboard(cut):
    items, before, complete = [], None, False
    for _ in range(MAX_PAGES):
        url = "https://getpostingboard.dev/b" + (f"?before={before}" if before else "")
        data = get_json(url)
        page = data.get("items") or []
        items += [(signature(p.get("body")), parse_ts(p.get("created_at"))) for p in page]
        before = data.get("next_before")
        oldest = items[-1][1] if items else None
        if not page or not before or (oldest and oldest < cut):
            complete = True
            break
    return {"items": items, "complete": complete,
            "method": "anonymous board /b, newest pages of the public JSON feed; "
                      "an author is the self-declared signature on a post's last "
                      "line, unsigned posts count as posts but not as authors; "
                      "the named board /v1 is not visible without a key"}


def m_msgboard(cut):
    threads = get_json("https://msgboard.dev/threads?limit=100").get("threads") or []
    items = []
    stamps = [parse_ts(t.get("last_message_at")) for t in threads]
    last = max((s for s in stamps if s), default=None)
    for thread in threads:
        stamp = parse_ts(thread.get("last_message_at"))
        if not stamp or stamp < cut or not thread.get("message_count"):
            continue
        tid = urllib.parse.quote(str(thread["id"]), safe="")
        data = get_json(f"https://msgboard.dev/messages?thread={tid}&limit=100")
        items += [(m.get("name"), parse_ts(m.get("created_at")))
                  for m in data.get("messages") or []]
    return {"items": items, "complete": True, "last": last,
            "method": "public JSON: threads active in the window, then their messages"}


def m_aiagentmessageboard(cut):
    items, last = [], None
    for board in ("general", "research", "collaboration", "help"):
        data = get_json(f"https://aiagentmessageboard.com/v1/boards/{board}/threads?limit=50")
        for thread in data.get("threads") or []:
            stamp = parse_ts(thread.get("updated_at") or thread.get("created_at"))
            if stamp and (last is None or stamp > last):
                last = stamp
            if not stamp or stamp < cut:
                continue
            full = get_json(f"https://aiagentmessageboard.com/v1/threads/{thread['id']}"
                            "?after=0&limit=50")
            items += [(pick(m, "author_name", "author_id"), parse_ts(m.get("created_at")))
                      for m in full.get("messages") or []]
    return {"items": items, "complete": True, "last": last,
            "method": "public JSON of the four boards, threads updated in the window"}


def m_agenttavern(cut):
    page = get_text("https://agenttavern.dev/", "text/html")
    items = []
    for article in re.findall(r"<article\b.*?</article>", page, re.S):
        text = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", article)))
        match = re.search(r"([a-z0-9][a-z0-9-]{2,39})(?: [a-z]+)? · "
                          r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
        if match:
            items.append((match.group(1), parse_ts(match.group(2))))
    items.sort(key=lambda it: it[1] or EPOCH, reverse=True)
    complete = bool(items) and items[-1][1] is not None and items[-1][1] < cut
    return {"items": items, "complete": complete,
            "method": "public read-only HTML feed only; posts addressed to "
                      "members are private and not counted"}


def m_moltbook(cut):
    items, cursor, complete = [], None, False
    for _ in range(MAX_PAGES):
        url = "https://www.moltbook.com/api/v1/posts?limit=50&sort=new"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(str(cursor), safe="")
        data = get_json(url)
        page = data.get("posts") or []
        items += [(pick(p, "author.name", "author_id"), parse_ts(p.get("created_at")))
                  for p in page]
        cursor = data.get("next_cursor")
        oldest = items[-1][1] if items else None
        if not page or not cursor or (oldest and oldest < cut):
            complete = True
            break
    return {"items": items, "complete": complete,
            "method": "public JSON, newest posts first"}


def m_agent_board_github(cut):
    since = cut.strftime("%Y-%m-%dT%H:%M:%SZ")
    data = get_json("https://api.github.com/repos/kushaldabbe/agent-board/issues"
                    f"?state=all&per_page=100&sort=created&direction=desc&since={since}")
    items = [(pick(i, "user.login"), parse_ts(i.get("created_at"))) for i in data]
    latest = get_json("https://api.github.com/repos/kushaldabbe/agent-board/issues"
                      "?state=all&per_page=1&sort=updated&direction=desc")
    last = parse_ts(latest[0].get("updated_at")) if latest else None
    return {"items": items, "complete": True, "last": last,
            "method": "GitHub issues API, issues created in the window"}


def m_clawprint(cut):
    items, offset, complete = [], 0, False
    for _ in range(MAX_PAGES):
        page = get_json(f"https://clawprint.org/api/posts?limit=100&offset={offset}").get("posts") or []
        items += [(p.get("author_name"), parse_ts(p.get("created_at"))) for p in page]
        offset += len(page)
        oldest = items[-1][1] if items else None
        if not page or (oldest and oldest < cut):
            complete = True
            break
    return {"items": items, "complete": complete,
            "method": "public JSON post list, newest first"}


def m_thecolony(cut):
    items, cursor, complete = [], None, False
    for _ in range(MAX_PAGES):
        url = "https://thecolony.ai/api/v1/posts?limit=50&sort=new"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(str(cursor), safe="")
        data = get_json(url)
        page = data.get("items") or []
        items += [(pick(p, "author.username", "author.id"),
                   parse_ts(pick(p, "created_at", "createdAt"))) for p in page]
        cursor = data.get("next_cursor") if data.get("has_more") else None
        oldest = items[-1][1] if items else None
        if not page or not cursor or (oldest and oldest < cut):
            complete = True
            break
    return {"items": items, "complete": complete,
            "method": "public JSON posts; agents and humans both counted"}


def m_botnet(cut):
    base = "https://botnet.com/api/forum"
    boards = first_list(get_json(f"{base}/topic-boards?limit=50"),
                        "items", "boards", "data", "topicBoards")
    items, last = [], None
    for board in boards:
        slug = pick(board, "slug", "id", "name")
        if not slug:
            continue
        topics = first_list(get_json(f"{base}/topics?board={urllib.parse.quote(str(slug), safe='')}"
                                     "&sort=active&limit=20"), "items", "topics", "data")
        for topic in topics:
            stamp = parse_ts(pick(topic, "lastActivityAt", "last_activity_at", "updatedAt",
                                  "updated_at", "lastMessageAt", "createdAt", "created_at"))
            if stamp and (last is None or stamp > last):
                last = stamp
            if not stamp or stamp < cut:
                continue
            tid = urllib.parse.quote(str(pick(topic, "id")), safe="")
            messages = first_list(get_json(f"{base}/topics/{tid}/messages?view=chat&limit=50"),
                                  "items", "messages", "data")
            items += [(pick(m, "author.username", "author.name", "authorName", "author",
                            "username"),
                       parse_ts(pick(m, "createdAt", "created_at", "timestamp")))
                      for m in messages]
    return {"items": items, "complete": True, "last": last,
            "method": "public forum API per its documentation: boards, topics "
                      "active in the window, their messages"}


def m_clawdchat(cut):
    items, offset, complete, seen = [], 0, False, set()
    for _ in range(MAX_PAGES):
        data = get_json(f"https://clawdchat.ai/api/v1/posts?limit=50&sort=new&offset={offset}")
        fresh = [p for p in data.get("posts") or [] if p.get("id") not in seen]
        if seen and not fresh:
            # Площадка отдаёт ту же страницу при любом смещении: пагинации
            # нет, виден только верх ленты. Это неполное окно, а не конец
            # ленты — первая версия замера перепутала одно с другим.
            break
        page = fresh
        seen |= {p.get("id") for p in page}
        items += [(pick(p, "author.name", "author.id"), parse_ts(p.get("created_at")))
                  for p in page]
        offset += 50
        oldest = items[-1][1] if items else None
        if not page or not data.get("has_more") or (oldest and oldest < cut):
            complete = True
            break
    return {"items": items, "complete": complete,
            "method": "public JSON posts sorted by newest; the API returns the same page for any offset, so only the newest page is visible"}


def m_waystation(cut):
    base = "https://the-waystation-agents.g5hpgprzjw.chatgpt.site/api/messages"
    roots, before, complete = [], None, False
    for _ in range(MAX_PAGES):
        url = f"{base}?limit=100" + (f"&before={urllib.parse.quote(before, safe='')}" if before else "")
        page = get_json(url).get("board_content") or {}
        roots += page.get("messages") or []
        before = (page.get("page") or {}).get("next_before")
        if not page.get("messages") or not before:
            complete = True
            break
    # Лента отдаёт только корни, а журнал аудита — только последние 250 событий
    # без пагинации. Ответ на старый корень может лечь в окно, поэтому треды
    # читаются у всех корней с ответами, а не только у свежих.
    items = []
    for root in roots:
        if not root.get("replyCount"):
            items.append((root.get("agent"), parse_ts(root.get("createdAt"))))
            continue
        tid = urllib.parse.quote(str(root["id"]), safe="")
        thread = get_json(f"{base}/{tid}/thread").get("board_content") or {}
        if (thread.get("depth") or {}).get("truncated"):
            complete = False
        items += [(m.get("agent"), parse_ts(m.get("createdAt")))
                  for m in thread.get("messages") or []]
    return {"items": items, "complete": complete,
            "method": "public JSON: every root post, then the thread of each root "
                      "with replies; an author is a registered key, and a key costs "
                      "one request, so several authors can be one operator"}


def m_wayside(cut):
    # Вся доска — один текстовый файл с явной отметкой конца: окно полное, только
    # если отметка на месте. Посты хозяина сервер помечает `(host)`; тот же ник без
    # метки — другой автор: на доске есть проба подделки имени хозяина.
    text = get_text("https://wayside.rest/all.txt")
    heads = re.findall(r'^POST \d+ — "(.*?)" \((.*?)\) — (\S+)$', text, re.M)
    items = [(f"{name} (host)" if role == "host" else name, parse_ts(stamp))
             for name, role, stamp in heads]
    return {"items": items,
            "complete": "no public conversation has been truncated" in text,
            "method": "the whole board as one plain-text file (/all.txt) that ends with "
                      "an explicit no-truncation marker; an author is a self-chosen, "
                      "unverified name, the host's marked posts count as a separate "
                      "author, and several names on the board are security probes"}


def m_1f916(cut):
    # Постов не больше одного в сутки UTC на гражданина, поэтому авторов почти
    # столько же, сколько постов; комментарии — основной объём — не считаются.
    # Курсоры страницы переносятся как велит их API: before, snapshot_id и
    # pin_snapshot вместе; закреплённые посты всплывают на первой странице и
    # пропускаются.
    base = "https://1f916.ai/api/new?limit=100"
    items, extra, complete = [], "", False
    for _ in range(MAX_PAGES):
        page = get_json(base + extra)
        posts = [p for p in page.get("posts") or [] if not p.get("pinned")]
        items += [(p.get("author"), parse_ts(p.get("created_at"))) for p in posts]
        oldest = items[-1][1] if items else None
        if not page.get("has_more") or (oldest and oldest < cut):
            complete = True
            break
        extra = ("&before=" + urllib.parse.quote(str(page["next_before"]), safe="")
                 + "&snapshot_id=" + str(page["snapshot_id"])
                 + "&pin_snapshot=" + urllib.parse.quote(str(page["pin_snapshot"]), safe=""))
    return {"items": items, "complete": complete,
            "method": "public JSON feed /api/new, posts only: one post per citizen per UTC "
                      "day, comments (most of the volume) are not counted; pinned posts "
                      "skipped; an author is a handle, and a handle costs one throttled request"}


MEASURES = {
    "foragents": m_foragents,
    "getpostingboard": m_getpostingboard,
    "msgboard": m_msgboard,
    "aiagentmessageboard": m_aiagentmessageboard,
    "agenttavern": m_agenttavern,
    "moltbook": m_moltbook,
    "agent-board-github": m_agent_board_github,
    "clawprint": m_clawprint,
    "thecolony": m_thecolony,
    "botnet": m_botnet,
    "waystation": m_waystation,
    "wayside": m_wayside,
    "1f916": m_1f916,
    "clawdchat": m_clawdchat,
}


# --------------------------------------------------------------------------
# Сведение
# --------------------------------------------------------------------------

def summarise(result: dict, now, cut) -> dict:
    stamped = [(a, t) for a, t in result["items"] if t is not None]
    inside = [(a, t) for a, t in stamped if t >= cut]
    authors = collections.Counter(a for a, _ in inside if a)
    last = result.get("last") or max((t for _, t in stamped), default=None)
    oldest = min((t for _, t in stamped), default=None)
    if result["complete"] or oldest is None:
        covered = WINDOW_H
    else:
        covered = min(WINDOW_H, round((now - oldest).total_seconds() / 3600, 1))
    if not stamped and last is None:
        verdict = "unmeasured"
    elif not inside:
        verdict = "dormant"
    elif len(authors) >= 3 and sum(n for _, n in authors.most_common(3)) < 0.9 * len(inside):
        # Три автора — ещё не жизнь, если почти всё написал один из них
        # залпом: так выглядела неделя одной из досок при первом замере.
        verdict = "active"
    else:
        verdict = "quiet"
    top3 = sum(n for _, n in authors.most_common(3))
    return {
        "verdict": verdict,
        "measured_at": now.strftime("%Y-%m-%dT%H:%MZ"),
        "window_hours": WINDOW_H,
        "covered_hours": covered,
        "complete": bool(result["complete"]),
        "posts": len(inside),
        "authors": len(authors),
        "top3_share": round(top3 / len(inside), 2) if inside and authors else None,
        "last_activity": last.strftime("%Y-%m-%dT%H:%MZ") if last else None,
        "method": result["method"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))
    wanted = {x for x in args.only.split(",") if x} or set(MEASURES)
    now = datetime.datetime.now(UTC)
    cut = now - datetime.timedelta(hours=WINDOW_H)

    print(f"{'entry':22} {'verdict':11} {'posts':>6} {'authors':>7} {'hours':>6} "
          f"{'top3':>5}  last activity or error")
    for section in data["sections"]:
        for entry in section["entries"]:
            eid = entry["id"]
            if eid not in wanted or eid not in MEASURES:
                continue
            try:
                status = summarise(MEASURES[eid](cut), now, cut)
            except Exception as exc:  # noqa: BLE001 — одна площадка не валит замер остальных
                status = {"verdict": "unmeasured",
                          "measured_at": now.strftime("%Y-%m-%dT%H:%MZ"),
                          "method": f"census failed: {type(exc).__name__}: {str(exc)[:120]}"}
            entry["status"] = status
            print(f"{eid:22} {status['verdict']:11} {status.get('posts', '-'):>6} "
                  f"{status.get('authors', '-'):>7} {status.get('covered_hours', '-'):>6} "
                  f"{str(status.get('top3_share', '-')):>5}  "
                  f"{status.get('last_activity') or status.get('method', '')[:70]}")

    if args.dry_run:
        return 0
    data["census_at"] = now.strftime("%Y-%m-%dT%H:%MZ")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    sys.path.insert(0, str(ROOT))
    from app import awesome  # после записи: модуль прочтёт свежий файл
    print(f"\nwritten: {DATA}\nwritten: {awesome.write_repo_copy()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
