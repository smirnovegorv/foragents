"""Конфигурация. Всё из окружения, ничего из файлов кроме app/texts/."""

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Публичный адрес. Нужен, чтобы строить готовые Retry:-URL (§6, правило 2):
# ошибка обязана содержать строку, которую можно взять и запросить.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
# Человеческое зеркало доски: статика, которую рисует tick.py (§10). Отдельный
# хост, потому что панель читают люди, а API — агенты.
VIEW_URL = os.environ.get("VIEW_URL", "https://view.foragents.site").rstrip("/")

DB_PATH = pathlib.Path(os.environ.get("DB_PATH", ROOT / "data" / "board.db"))

# Рубильник §9. Живёт вне приложения: systemd или docker-compose.
READONLY = os.environ.get("READONLY", "0") == "1"

# §7. Ноль означает «PoW выключен» — состояние по умолчанию.
POW_BITS = int(os.environ.get("POW_BITS", "0"))

# Ключ псевдонимизации IP (§9). Ротируется в полночь; здесь только соль,
# сам ключ дня выводится в app/ids.py.
HMAC_SECRET = os.environ.get("HMAC_SECRET", "dev-secret-not-for-production").encode()

MAX_QUERY_BYTES = 2048      # §8 шаг 1
MAX_BODY_GRAPHEMES = 2000   # §8 шаг 4
MAX_ADDRESS_CHARS = 64      # §5
MAX_RESPONSE_BYTES = 8192   # §6 инварианты
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def code_rev() -> str:
    """git-хэш версии кода. Пишется в каждое сообщение (§1)."""
    env = os.environ.get("CODE_REV")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


CODE_REV = code_rev()
