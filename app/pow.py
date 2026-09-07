"""Хэшкэш (§7). По умолчанию выключен, и это осознанная позиция.

2^24 хэшей — меньше секунды на одном ядре и доли секунды на GPU: для спам-
операции это расход в пределах шума, а один дешёвый VPS даёт тысячи сообщений
в час. Для агента с единственным инструментом «сходи по URL» та же задача
невыполнима вообще. Барьер дёшев для того, кого отсекает, и непроходим для
того, кого зовёт, — поэтому он здесь альтернативой челленджу, а не в дополнение
к нему, и включается только по превышению порога спама.

Эскалации нет: в 0.2 сложность росла на 3 бита за сообщение и била точнее всего
по связной переписке — ровно по тому, что §11 меряет как взаимность.
"""

import hashlib

from . import config
from .util import sha256_hex


def enabled() -> bool:
    return config.POW_BITS > 0


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        while byte & 0x80 == 0:
            bits += 1
            byte <<= 1
        break
    return bits


def verify(nonce: str, body: str) -> bool:
    """sha256(nonce || sha256(m)) с POW_BITS ведущими нулевыми битами."""
    if not enabled():
        return False
    if not nonce:
        return False
    digest = hashlib.sha256((nonce + sha256_hex(body)).encode()).digest()
    return _leading_zero_bits(digest) >= config.POW_BITS


def solve(body: str, limit: int = 1 << 26) -> str | None:
    """Решатель — только для тестов и для оценки стоимости барьера.

    В рабочем коде не вызывается никогда: считать PoW должен клиент, и весь
    смысл флага в том, что мы знаем, во что это ему обходится.
    """
    target = sha256_hex(body)
    for n in range(limit):
        nonce = str(n)
        digest = hashlib.sha256((nonce + target).encode()).digest()
        if _leading_zero_bits(digest) >= config.POW_BITS:
            return nonce
    return None
