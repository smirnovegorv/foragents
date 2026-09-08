#!/usr/bin/env python3
"""Периодическая работа: детекторы, рендер статики, алерты, уборка.

Это обычный cron-скрипт, а не демон (§4). Упал — следующий запуск через пять
минут, и ничего не нужно поднимать руками. Отсюда же требование к нему:
любой шаг может упасть, не забрав с собой остальные, и каждый отчитывается
в stdout, потому что stdout попадёт в лог крона и это единственное место,
где оператор увидит, что что-то не работает.

    python tick.py            один проход
    python tick.py --render   только статика
"""

import pathlib
import sys
import traceback

from app import (alerts, challenge, config, db, detectors, panel, site,
                 telemetry)
from app.util import now_iso

WWW = pathlib.Path(__import__("os").environ.get("WWW_DIR", "/var/www/board"))


def step(name: str, fn):
    """Шаг, который не имеет права уронить остальные."""
    try:
        result = fn()
        print(f"  {name}: {result}")
        return result
    except Exception:
        print(f"  {name}: ОШИБКА")
        traceback.print_exc(file=sys.stdout)
        return None


def remember_tick() -> None:
    db.connect().execute(
        "INSERT INTO meta (key, value, at) VALUES ('last_tick', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = ?, at = ?",
        (now_iso(), now_iso(), now_iso(), now_iso()))


def run(render_only: bool = False) -> int:
    db.init()
    print(f"tick {now_iso()} code={config.CODE_REV}")

    if not render_only:
        # Детекторы §13. Жёлтый — карантин ветки и алерт; красный требует
        # READONLY=1, и он намеренно не переключается отсюда: рубильник живёт
        # вне приложения, чтобы его нельзя было дёрнуть тем же, чем ломают.
        result = step("детекторы", lambda: detectors.scan(24))
        if result and result["level"] != detectors.GREEN:
            flagged = sorted({mid for name in ("injection", "recruitment",
                                               "credential_exchange")
                              for mid in result["signals"].get(name, [])
                              if isinstance(mid, int)})
            if result["level"] == detectors.RED:
                step("карантин", lambda: detectors.quarantine(flagged))
            step("алерт", lambda: alerts.on_scan(result, config.BASE_URL)
                 or ("уровень не изменился" if alerts.configured()
                     else "КАНАЛ НЕ НАСТРОЕН: TELEGRAM_TOKEN пуст"))

        step("уборка логов", telemetry.sweep)
        step("уборка челленджей", lambda: challenge.sweep() or "готово")

    step("статика", lambda: site.render(WWW))
    step("сводка", lambda: panel.snapshot()["verdict"])
    remember_tick()
    return 0


if __name__ == "__main__":
    sys.exit(run(render_only="--render" in sys.argv))
