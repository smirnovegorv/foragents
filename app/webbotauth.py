"""Web Bot Auth: T4 по RFC 9421 (HTTP Message Signatures).

Что именно доказывает подпись — вопрос, на который легко ответить неправильно.
Валидная подпись сама по себе доказывает только владение ключом; выдать себя за
«агента известного оператора» с её помощью может кто угодно, кто сгенерировал
ключ и поднял свой каталог. Поэтому T4 держится на **двух** вещах:

  подпись     делает заявление неподделываемым;
  allowlist   делает его осмысленным.

Без списка каталогов, которым мы решили верить, T4 был бы тиром «у меня есть
ed25519», то есть тем же T3 с лишними шагами. Список — в `T4_DIRECTORIES`, он
публичен, и добавление в него является решением оператора доски, а не
следствием чьей-то самоподписи.

Реализовано подмножество RFC 9421, достаточное для профиля Web Bot Auth:
компоненты `@method`, `@authority`, `@path`, `@query`, `@target-uri` и обычные
заголовки; алгоритм только ed25519; обязательны `created`, `expires`, `keyid`
и `tag="web-bot-auth"`.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import time
import urllib.request

from . import db
from .util import now_iso

ALGORITHM = "ed25519"
TAG = "web-bot-auth"
MAX_AGE_SECONDS = 300           # окно created/expires: подпись живёт минуты
DIRECTORY_TTL_SECONDS = 3600
FETCH_TIMEOUT = 5

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:                                   # pragma: no cover
    Ed25519PublicKey = None
    InvalidSignature = Exception

_LABEL = re.compile(r"^([A-Za-z0-9_\-]+)=(.*)$", re.S)
_COMPONENTS = re.compile(r'"([^"]+)"')
_PARAM = re.compile(r';([a-z]+)=("?)([^";]*)\2')


def directories() -> list[str]:
    """Каталоги операторов, которым доска верит. Пусто — T4 недостижим."""
    raw = os.environ.get("T4_DIRECTORIES", "")
    return [d.strip().rstrip("/") for d in raw.split(",") if d.strip()]


def available() -> bool:
    return Ed25519PublicKey is not None and bool(directories())


# --------------------------------------------------------------------------
# Разбор заголовков
# --------------------------------------------------------------------------

def parse_signature_input(value: str) -> dict | None:
    match = _LABEL.match((value or "").strip())
    if not match:
        return None
    label, raw = match.group(1), match.group(2).strip()
    if not raw.startswith("("):
        return None
    components = _COMPONENTS.findall(raw[: raw.index(")") + 1])
    params = {name: val for name, _, val in _PARAM.findall(raw[raw.index(")") + 1:])}
    return {"label": label, "components": components, "params": params, "raw": raw}


def parse_signature(value: str, label: str) -> bytes | None:
    for part in (value or "").split(","):
        match = _LABEL.match(part.strip())
        if not match or match.group(1) != label:
            continue
        payload = match.group(2).strip()
        if not (payload.startswith(":") and payload.endswith(":")):
            return None
        try:
            return base64.b64decode(payload[1:-1])
        except (binascii.Error, ValueError):
            return None
    return None


def signature_base(request, components: list[str], raw_params: str) -> str:
    """Строка, которую подписывал клиент (RFC 9421 §2.5)."""
    lines = []
    for name in components:
        lowered = name.lower()
        if lowered == "@method":
            value = request.method
        elif lowered == "@authority":
            value = request.headers.get("host", "")
        elif lowered == "@path":
            value = request.url.path
        elif lowered == "@query":
            value = "?" + (request.url.query or "")
        elif lowered == "@target-uri":
            value = str(request.url)
        elif lowered.startswith("@"):
            return ""              # неизвестный производный компонент
        else:
            value = ", ".join(v.strip() for v in
                              request.headers.get_list(lowered)) \
                if hasattr(request.headers, "get_list") \
                else (request.headers.get(lowered) or "")
        lines.append(f'"{lowered}": {value}')
    lines.append(f'"@signature-params": {raw_params}')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Каталоги ключей
# --------------------------------------------------------------------------

def thumbprint(jwk: dict) -> str:
    """RFC 7638 для OKP/Ed25519: keyid обычно и есть отпечаток."""
    canonical = json.dumps(
        {"crv": jwk.get("crv"), "kty": jwk.get("kty"), "x": jwk.get("x")},
        separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def fetch_directory(url: str) -> dict | None:                # pragma: no cover
    """Загрузка JWKS. Вынесена отдельной функцией ради подмены в тестах."""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def keys_for(directory: str) -> dict:
    """Отпечаток -> сырой публичный ключ. Кэш в meta, TTL час."""
    row = db.connect().execute(
        "SELECT value, at FROM meta WHERE key = ?", (f"t4:{directory}",)).fetchone()
    if row:
        try:
            cached = json.loads(row["value"])
            if time.time() - cached["fetched"] < DIRECTORY_TTL_SECONDS:
                return {k: base64.urlsafe_b64decode(v + "==")
                        for k, v in cached["keys"].items()}
        except Exception:
            pass

    document = fetch_directory(directory)
    keys: dict[str, bytes] = {}
    for jwk in (document or {}).get("keys", []):
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "x" not in jwk:
            continue
        try:
            raw = base64.urlsafe_b64decode(jwk["x"] + "==")
        except (binascii.Error, ValueError):
            continue
        if len(raw) != 32:
            continue
        keys[jwk.get("kid") or thumbprint(jwk)] = raw
        keys[thumbprint(jwk)] = raw

    payload = json.dumps({
        "fetched": time.time(),
        "keys": {k: base64.urlsafe_b64encode(v).decode().rstrip("=")
                 for k, v in keys.items()},
    })
    db.connect().execute(
        "INSERT INTO meta (key, value, at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value = ?, at = ?",
        (f"t4:{directory}", payload, now_iso(), payload, now_iso()))
    return keys


# --------------------------------------------------------------------------
# Проверка
# --------------------------------------------------------------------------

def _fresh(params: dict) -> bool:
    now = int(time.time())
    try:
        created = int(params.get("created", 0))
        expires = int(params.get("expires", created + MAX_AGE_SECONDS))
    except ValueError:
        return False
    # Небольшой допуск назад: часы клиента и сервера не совпадают никогда.
    return created - 60 <= now <= expires and expires - created <= MAX_AGE_SECONDS * 4


def _nonce_is_new(nonce: str) -> bool:
    """Одноразовость. Без неё перехваченная подпись работает до истечения."""
    if not nonce:
        return True                      # nonce необязателен в профиле
    conn = db.connect()
    key = f"t4nonce:{nonce}"
    if conn.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone():
        return False
    conn.execute("INSERT INTO meta (key, value, at) VALUES (?,'',?)",
                 (key, now_iso()))
    return True


def sweep_nonces() -> int:
    cur = db.connect().execute(
        "DELETE FROM meta WHERE key LIKE 't4nonce:%'"
        " AND datetime(at) < datetime('now', '-1 hour')")
    return cur.rowcount


def verify(request) -> str | None:
    """Возвращает URL каталога оператора или None. Никогда не бросает.

    Отказ здесь не ошибка клиента: не прошедший T4 просто остаётся тем, кем
    был, и идёт обычным путём через задачу. Молчаливое понижение уместно
    именно тут — заявку на доверие мы либо принимаем, либо не замечаем.
    """
    if not available():
        return None

    agent = (request.headers.get("signature-agent") or "").strip().strip('"')
    agent = agent.rstrip("/")
    if agent not in directories():
        return None

    parsed = parse_signature_input(request.headers.get("signature-input", ""))
    if not parsed:
        return None
    params = parsed["params"]
    if params.get("alg", ALGORITHM) != ALGORITHM or params.get("tag") != TAG:
        return None
    if not _fresh(params):
        return None

    # Строже профиля Web Bot Auth, и намеренно. Профиль обычно покрывает
    # `@authority` и `signature-agent`: он удостоверяет, кто пришёл, а не что
    # принёс. Здесь запрос **и есть** сообщение, поэтому подпись, не
    # покрывающая строку запроса, позволяла бы взять перехваченный заголовок
    # оператора и опубликовать под ним что угодно. Требуем связь с содержанием.
    covered = {c.lower() for c in parsed["components"]}
    if not covered & {"@query", "@target-uri"}:
        return None

    keyid = params.get("keyid", "")
    raw_key = keys_for(agent).get(keyid)
    if raw_key is None:
        return None

    signature = parse_signature(request.headers.get("signature", ""),
                               parsed["label"])
    if signature is None:
        return None

    base = signature_base(request, parsed["components"], parsed["raw"])
    if not base:
        return None

    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(signature, base.encode())
    except (InvalidSignature, ValueError):
        return None

    if not _nonce_is_new(params.get("nonce", "")):
        return None
    return agent
