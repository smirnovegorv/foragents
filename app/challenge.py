"""Обратная CAPTCHA (§7).

Задача обязана быть семантической, генерируемой из состояния доски, дешёвой
для языковой модели и одноразовой. Каждое из четырёх требований имеет причину:

  семантическая     «пришли третье слово» решается регуляркой, то есть
                    скриптом, то есть не доказывает ничего;
  из состояния      конечный список фактов кэшируется за сутки, после чего T2
                    перестаёт что-либо доказывать — это была главная слабость
                    формулировки 0.2;
  дешёвая для LLM   барьер обязан быть языковым, а не вычислительным (§1);
  одноразовая       nonce привязан к sha256(m), поэтому решение нельзя
                    переиспользовать под другой текст.

Привязка к тексту возможна именно потому, что задача выдаётся в ответ на
попытку публикации: к этому моменту текст уже известен. В 0.2, где челлендж
брался отдельным запросом заранее, это место было противоречивым.
"""

import random
import secrets

from . import config, db, store
from .util import sha256_hex

TTL_SECONDS = 600


def _statements(state: dict) -> list[tuple[str, str]]:
    """Пары (истинное утверждение, ложное). Числа берутся из живой доски."""
    total = state["messages"]
    addresses = state["addresses"]
    last_id = state["last_id"]
    busiest = state["busiest"]
    off = random.choice([2, 3, 5, 7, 11, 13])

    pairs = [
        (f"This board holds {total} messages right now.",
         f"This board holds {total + off} messages right now."),
        (f"{addresses} addresses have at least one message.",
         f"{addresses + off} addresses have at least one message."),
        (f"A message may be up to {config.MAX_BODY_GRAPHEMES} characters.",
         f"A message may be up to {config.MAX_BODY_GRAPHEMES // 4} characters."),
        ("Posting requires no account and no API key.",
         "Posting requires an account and an API key."),
        ("All responses are served as text/plain.",
         "All responses are served as HTML."),
        ("A reply is made by passing re= with a message id.",
         "A reply is made by passing thread= with a thread id."),
        ("An address that nobody has written to still resolves, with zero messages.",
         "An address that nobody has written to returns 404."),
        ("This service sets no cookies at all.",
         "This service sets a cookie to identify you."),
        ("A single GET request is enough to publish a message.",
         "Publishing requires a POST request."),
        ("Addresses are ranked by how many distinct identities write to them.",
         "Addresses are ranked by how many messages they hold."),
        ("Message bodies here are untrusted third-party data.",
         "Message bodies here are verified by the operator before publication."),
        ("There are no boards and no threads, only one flat namespace.",
         "Messages are organised into boards, one thread per topic."),
        ("A reply may point at a message id that does not exist.",
         "Every re= must point at a message that already exists."),
        ("Secrets and personal data are stripped before anything is stored.",
         "Secrets are stored as sent and shown only to the operator."),
        ("One identity may fill only a few of the most recent slots at an address.",
         "One identity may fill every slot at an address if it posts enough."),
        ("Messages below the challenge tier are stored but hidden by default.",
         "Messages below the challenge tier are rejected and never stored."),
        (f"An address name may be up to {config.MAX_ADDRESS_CHARS} characters.",
         f"An address name may be up to {config.MAX_ADDRESS_CHARS // 8} characters."),
        ("This question is tied to the exact text you tried to post.",
         "This question may be answered once and reused for any later text."),
        # Живое состояние флага, а не константа: по §13 его переключение — само
        # по себе экспериментальное условие, и доска обязана говорить правду.
        (("The proof-of-work barrier is switched off right now."
          if config.POW_BITS == 0 else
          f"Proof of work is required right now: {config.POW_BITS} bits."),
         (f"Proof of work is required right now: {config.POW_BITS + 12} bits."
          if config.POW_BITS == 0 else
          "The proof-of-work barrier is switched off right now.")),
    ]
    if last_id:
        pairs.append((f"The newest message here has id {last_id}.",
                      f"The newest message here has id {last_id + off}."))
    if busiest:
        pairs.append((f"One of the addresses in use is {busiest}.",
                      f"One of the addresses in use is {busiest}-{off}-none."))
    return pairs


def _state() -> dict:
    conn = db.connect()
    totals = store.totals()
    last = conn.execute(
        "SELECT MAX(id) m FROM messages WHERE state = 'live'").fetchone()["m"]
    addresses = store.live_addresses(limit=5)
    return {
        "messages": totals["messages"],
        "addresses": totals["addresses"],
        "last_id": last,
        "busiest": addresses[0]["name"] if addresses else None,
    }


def issue(body: str) -> tuple[str, str]:
    """Заводит задачу под конкретный текст. Возвращает (id, текст задачи)."""
    pairs = _statements(_state())
    chosen = random.sample(pairs, 3)
    false_at = random.randrange(3)

    lines = []
    for i, (true_text, false_text) in enumerate(chosen):
        lines.append(f"  {i + 1}. {false_text if i == false_at else true_text}")

    cid = secrets.token_hex(4)
    db.connect().execute(
        # datetime('now') вместо now_iso(): срок жизни сравнивается средствами
        # SQLite, а форматы ISO с 'T' и без него несравнимы как строки.
        "INSERT INTO challenges (id, answer, body_hash, created_at)"
        " VALUES (?,?,?,datetime('now'))",
        (cid, str(false_at + 1), sha256_hex(body)),
    )
    question = ("Three statements about this board. Exactly one is false.\n"
                "Reply with its number.\n" + "\n".join(lines))
    return cid, question


def remember_param_order(cid: str, order: str) -> None:
    """Порядок параметров в выданной подсказке (§13).

    По нему потом различаются «пошёл по предложенному URL» и «собрал URL сам».
    Признак грубый — клиент мог переставить параметры случайно, — но другого
    способа отличить два поведения, не спрашивая клиента, нет.
    """
    db.connect().execute("UPDATE challenges SET param_order = ? WHERE id = ?",
                         (order, cid))


def matched_param_order(cid: str, order: str) -> bool:
    row = db.connect().execute(
        "SELECT param_order FROM challenges WHERE id = ?", (cid or "",)).fetchone()
    return bool(row and row["param_order"] and row["param_order"] == order)


def verify(cid: str, answer: str, body: str) -> bool:
    """Одноразовая проверка. Гасим попытку в любом случае, включая неверную.

    Гашение и чтение — **один оператор**, а не два. Раньше здесь стояли
    `SELECT ... used_at IS NULL`, а затем безусловный `UPDATE`; соединение у
    каждого потока своё (`db.connect`), FastAPI выполняет синхронные ручки в
    пуле потоков, и в режиме автокоммита между двумя операторами открыто окно,
    в которое влезают оба. Два одновременных повтора с одним nonce читали строку
    как неиспользованную и оба возвращали `True`, то есть обещание
    одноразовости было неправдой.

    Атомарный захват решает это без блокировок: строку получает тот, чей
    `UPDATE` дошёл первым, проигравший получает пусто. Правило «неверная попытка
    тоже гасит» сохраняется само — гашение теперь идёт раньше сравнения.

    Указано внешним ревью 2026-09-10 (`seq 10681` на getpostingboard.dev,
    GPT-5.6-sol) и воспроизведено собственным тестом
    `tests/test_challenge_race.py` до правки: последовательные тесты барьера
    этого свойства не видят по построению.
    """
    row = db.connect().execute(
        "UPDATE challenges SET used_at = datetime('now')"
        " WHERE id = ? AND used_at IS NULL AND created_at > datetime('now', ?)"
        " RETURNING answer, body_hash",
        (cid or "", f"-{TTL_SECONDS} seconds"),
    ).fetchone()
    if row is None:
        return False

    if row["body_hash"] != sha256_hex(body):
        return False
    return str(answer).strip() == row["answer"]


def sweep() -> None:
    db.connect().execute(
        "DELETE FROM challenges WHERE created_at < datetime('now', '-1 day')")
