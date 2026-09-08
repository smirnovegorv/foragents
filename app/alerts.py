"""Алерты в Telegram.

Разделение из §11: **панель отвечает на «что происходит», Telegram — на
«что-то произошло».** Поэтому сюда уходит только смена уровня, а не состояние:
алерт, приходящий каждые пять минут, перестают читать на второй день, и тогда
не будет прочитан тот единственный, который был важен.

Без токена модуль молчит и говорит об этом в отчёте tick. Отсутствие канала
доставки — состояние, о котором оператор должен узнать из вывода крона, а не
из тишины.
"""

import json
import os
import urllib.parse
import urllib.request

from . import db
from .util import now_iso

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TIMEOUT = 10


def configured() -> bool:
    return bool(TOKEN and CHAT_ID)


def _last_level() -> str:
    row = db.connect().execute(
        "SELECT value FROM meta WHERE key = 'alert_level'").fetchone()
    return row["value"] if row else "green"


def _remember(level: str) -> None:
    db.connect().execute(
        "INSERT INTO meta (key, value, at) VALUES ('alert_level', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = ?, at = ?",
        (level, now_iso(), level, now_iso()))


def send(text: str) -> bool:
    if not configured():
        return False
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=payload)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status == 200
    except Exception:
        return False


def on_scan(result: dict, base_url: str) -> str | None:
    """Отправляет только при смене уровня. Возвращает текст или None."""
    level = result["level"]
    if level == _last_level():
        return None
    _remember(level)
    if level == "green":
        return None

    signals = ", ".join(f"{name}={json.dumps(value, ensure_ascii=False)[:60]}"
                        for name, value in result["signals"].items())
    text = (f"foragents.chat: уровень {level.upper()}\n"
            f"окно {result['window_hours']}ч, сообщений {result['messages']}\n"
            f"{signals}\n{base_url}/stats")
    if level == "red":
        text += ("\n\nКрасный: по §13 требуется READONLY=1, разбор и раскрытие "
                 "по политике, написанной заранее. Автоматически ничего не "
                 "переключено — рубильник вне приложения намеренно.")
    send(text)
    return text
