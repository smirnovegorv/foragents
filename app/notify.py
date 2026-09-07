"""Пробуждение долгого опроса (§6).

Ожидание не должно ни крутить процессор, ни держать соединение с БД — только
сокет. Поэтому вместо периодического опроса базы здесь широковещание внутри
процесса: публикация будит тех, кто ждёт по совпадающему ключу.

Это работает потому, что процесс один (`--workers 1` в §12) и обработчик
публикации асинхронный, то есть событие ставится из того же цикла. Если
воркеров когда-нибудь станет больше, механика сломается молча — на этот случай
есть предохранитель: истечение таймаута всегда перепроверяет базу, поэтому
пропущенное уведомление стоит задержки, а не потерянного сообщения.
"""

import asyncio

MAX_WAITERS = 200          # 1 vCPU: единственное место, где он может упереться
MAX_WAIT_SECONDS = 60

_waiters: dict[str, set[asyncio.Event]] = {}


def waiting() -> int:
    return sum(len(events) for events in _waiters.values())


def publish(*keys: str) -> None:
    """Вызывается из обработчика публикации. Ключи — адрес, id ответа, имя."""
    for key in keys:
        for event in _waiters.get(key, ()):
            event.set()


async def wait_for(keys, timeout: float) -> bool:
    """Ждёт события по любому из ключей. False — если истёк таймаут."""
    keys = [k for k in keys if k]
    timeout = min(float(timeout), MAX_WAIT_SECONDS)
    if not keys or timeout <= 0 or waiting() >= MAX_WAITERS:
        # Деградация, а не отказ: клиент получает обычный ответ без ожидания.
        return False

    event = asyncio.Event()
    for key in keys:
        _waiters.setdefault(key, set()).add(event)
    try:
        await asyncio.wait_for(event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        for key in keys:
            bucket = _waiters.get(key)
            if bucket is not None:
                bucket.discard(event)
                if not bucket:
                    _waiters.pop(key, None)


def reset() -> None:
    _waiters.clear()
